#!/usr/bin/env python3
import re
import subprocess
import sys
from typing import Dict, Set


def run_git_show(ref: str, path: str) -> str:
    try:
        out = subprocess.run(["git", "show", f"{ref}:{path}"], check=True, capture_output=True)
        return out.stdout.decode("utf-8")
    except subprocess.CalledProcessError:
        return ""


def git_changed_sql_files(base_ref: str) -> Set[str]:
    cmd = ["git", "diff", "--name-only", f"{base_ref}..HEAD", "--", "*.sql"]
    out = subprocess.run(cmd, check=False, capture_output=True)
    files = out.stdout.decode("utf-8").strip().splitlines()
    return set(f for f in files if f)


def normalize_identifier(name: str) -> str:
    name = name.strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name.lower()


def extract_table_columns(sql: str) -> Dict[str, Set[str]]:
    tables: Dict[str, Set[str]] = {}

    # Find CREATE TABLE blocks
    create_re = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\.\"]+)\s*\((.*?)\)\s*;', re.S | re.I)
    for m in create_re.finditer(sql):
        tname = normalize_identifier(m.group(1))
        body = m.group(2)
        cols = set()
        # split column definitions by commas (simple heuristic)
        for col_def in re.split(r',\s*', body):
            line = col_def.strip()
            if not line or line.upper().startswith("CONSTRAINT") or line.upper().startswith("PRIMARY KEY") or line.startswith("--"):
                continue
            parts = re.split(r"\s+", line, maxsplit=1)
            col = parts[0].rstrip(',')
            col = normalize_identifier(col)
            if col:
                cols.add(col)
        tables[tname] = cols

    # Find ALTER TABLE ADD COLUMN
    alter_re = re.compile(r'ALTER\s+TABLE\s+([\w\.\"]+)\s+ADD\s+COLUMN\s+([\w\.\"]+)', re.I)
    for m in alter_re.finditer(sql):
        tname = normalize_identifier(m.group(1))
        col = normalize_identifier(m.group(2))
        tables.setdefault(tname, set()).add(col)

    return tables


def extract_table_column_defs(sql: str) -> Dict[str, Dict[str, str]]:
    """Return mapping table -> column -> original definition text (for annotation checks)."""
    tables: Dict[str, Dict[str, str]] = {}

    create_re = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\.\"]+)\s*\((.*?)\)\s*;', re.S | re.I)
    for m in create_re.finditer(sql):
        tname = normalize_identifier(m.group(1))
        body = m.group(2)
        cols: Dict[str, str] = {}
        # keep original column definition snippets
        for col_def in re.split(r',\s*', body):
            line = col_def.strip()
            if not line or line.upper().startswith("CONSTRAINT") or line.upper().startswith("PRIMARY KEY") or line.startswith("--"):
                continue
            parts = re.split(r"\s+", line, maxsplit=1)
            col = parts[0].rstrip(',')
            coln = normalize_identifier(col)
            if coln:
                cols[coln] = line
        if cols:
            tables[tname] = cols

    # ALTER TABLE ADD COLUMN - capture the rest of the column definition text until semicolon
    alter_re = re.compile(r'ALTER\s+TABLE\s+([\w\.\"]+)\s+ADD\s+COLUMN\s+([\w\.\"]+)(.*?);', re.I | re.S)
    for m in alter_re.finditer(sql):
        tname = normalize_identifier(m.group(1))
        col = normalize_identifier(m.group(2))
        rest = m.group(3).strip()
        tables.setdefault(tname, {})[col] = (m.group(2) + ' ' + rest).strip()

    return tables


def detect_new_fields_in_file(base_ref: str, path: str) -> Dict[str, Set[str]]:
    base = run_git_show(base_ref, path)
    head = run_git_show('HEAD', path)
    base_tables = extract_table_columns(base)
    head_tables = extract_table_columns(head)
    new_fields: Dict[str, Set[str]] = {}
    keys = set(base_tables.keys()) | set(head_tables.keys())
    for t in keys:
        base_cols = base_tables.get(t, set())
        head_cols = head_tables.get(t, set())
        added = head_cols - base_cols
        if added:
            new_fields[t] = added
    return new_fields


def missing_data_type_annotations_in_file(base_ref: str, path: str) -> Dict[str, Set[str]]:
    """Return mapping table->set(columns) for newly added columns that lack a data_type annotation."""
    base = run_git_show(base_ref, path)
    head = run_git_show('HEAD', path)
    base_tables = extract_table_columns(base)
    head_tables = extract_table_columns(head)
    head_defs = extract_table_column_defs(head)

    missing: Dict[str, Set[str]] = {}
    keys = set(base_tables.keys()) | set(head_tables.keys())
    for t in keys:
        base_cols = base_tables.get(t, set())
        head_cols = head_tables.get(t, set())
        added = head_cols - base_cols
        for col in added:
            # try to find definition snippet
            col_def = ''
            if t in head_defs and col in head_defs[t]:
                col_def = head_defs[t][col]

            if not has_data_type_annotation(head, t, col, col_def):
                missing.setdefault(t, set()).add(col)

    return missing


def has_data_type_annotation(head_sql: str, table: str, column: str, col_def: str) -> bool:
    """Heuristic checks for presence of a `data_type` annotation.

    Checks inline column definition, nearby inline comments, and COMMENT ON COLUMN statements.
    """
    if not column:
        return False
    # quick check inside the column definition
    if col_def and re.search(r'data_type\s*[:=]', col_def, re.I):
        return True

    # Check COMMENT ON COLUMN ... IS '...data_type...'
    # match patterns like: COMMENT ON COLUMN schema.table.column IS 'data_type: uuid';
    com_re = re.compile(r"COMMENT\s+ON\s+COLUMN\s+([\w\.\"]+\.)?([\w\"]+)\s+IS\s+'([^']*)'", re.I)
    for m in com_re.finditer(head_sql):
        col_name = normalize_identifier(m.group(2))
        comment = m.group(3)
        if col_name == column and re.search(r'data_type\s*[:=]', comment, re.I):
            return True

    # look for inline SQL comments near the column definition
    if col_def:
        idx = head_sql.find(col_def)
        if idx != -1:
            before = head_sql[max(0, idx-200):idx+len(col_def)+200]
            if re.search(r'--.*data_type\s*[:=]', before, re.I) or re.search(r'/\*.*data_type\s*[:=].*\*/', before, re.I | re.S):
                return True

    # look for data_type inline comments
    pat = re.compile(re.escape(column) + r"[^\n]*--[^\n]*data_type\s*[:=]", re.I)
    if pat.search(head_sql):
        return True

    return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Detect new DB fields added to SQL files compared to a base ref')
    parser.add_argument('--base', default='origin/main', help='Base git ref to compare against')
    parser.add_argument('--files', nargs='*', help='Specific SQL files to check (defaults to changed sql files)')
    args = parser.parse_args()

    if args.files:
        files = set(args.files)
    else:
        files = git_changed_sql_files(args.base)

    if not files:
        print('No changed SQL files detected.')
        sys.exit(0)

    any_new = False
    any_missing_annotation = False
    for path in sorted(files):
        new = detect_new_fields_in_file(args.base, path)
        missing = missing_data_type_annotations_in_file(args.base, path)
        if new:
            any_new = True
            print(f'New fields detected in {path}:')
            for t, cols in new.items():
                print(f'  Table {t}: added columns: {", ".join(sorted(cols))}')
        if missing:
            any_missing_annotation = True
            print(f'Missing data_type annotations in {path}:')
            for t, cols in missing.items():
                print(f'  Table {t}: columns missing data_type: {", ".join(sorted(cols))}')

    if any_missing_annotation:
        print('\nFailure: some newly added fields are missing data_type annotations.')
        sys.exit(2)
    if any_new:
        print('\nNew fields were added and are properly annotated (or annotations present).')
        sys.exit(0)
    else:
        print('No new fields detected.')
        sys.exit(0)


if __name__ == '__main__':
    main()
