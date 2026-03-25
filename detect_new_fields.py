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
    for path in sorted(files):
        new = detect_new_fields_in_file(args.base, path)
        if new:
            any_new = True
            print(f'New fields detected in {path}:')
            for t, cols in new.items():
                print(f'  Table {t}: added columns: {", ".join(sorted(cols))}')

    if any_new:
        print('\nFailure: new database fields were added.')
        sys.exit(2)
    else:
        print('No new fields detected.')
        sys.exit(0)


if __name__ == '__main__':
    main()
