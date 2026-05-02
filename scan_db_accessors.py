#!/usr/bin/env python3
"""Scan a repository to find usages of database field accessors in Go code.

Heuristics:
- Parse SQL files for table and column names (CREATE TABLE / ALTER TABLE / simple SELECT aliases).
- Map snake_case column names to CamelCase Go field names (with common ID handling).
- Scan Go files for `.Field` access patterns and map fields back to SQL columns.

Usage: python scan_db_accessors.py [--repo PATH]
"""
import argparse
import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Set, Tuple


def find_files(root: str, exts: Tuple[str, ...]):
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.endswith(exts):
                yield os.path.join(dirpath, fn)


def normalize_identifier(name: str) -> str:
    name = name.strip()
    if name.startswith('\"') and name.endswith('\"'):
        name = name[1:-1]
    return name.lower()


def extract_sql_columns_from_schema(sql_text: str) -> Dict[str, Set[str]]:
    tables: Dict[str, Set[str]] = {}
    create_re = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\."]+)\s*\((.*?)\)\s*;', re.S | re.I)
    for m in create_re.finditer(sql_text):
        tname = normalize_identifier(m.group(1))
        body = m.group(2)
        cols = set()
        for col_def in re.split(r',\s*', body):
            line = col_def.strip()
            if not line or line.upper().startswith('CONSTRAINT') or line.upper().startswith('PRIMARY KEY'):
                continue
            parts = re.split(r"\s+", line, maxsplit=1)
            col = parts[0].rstrip(',')
            col = normalize_identifier(col)
            if col:
                cols.add(col)
        if cols:
            tables[tname] = cols

    alter_re = re.compile(r'ALTER\s+TABLE\s+([\w\."]+)\s+ADD\s+COLUMN\s+([\w\."]+)', re.I)
    for m in alter_re.finditer(sql_text):
        tname = normalize_identifier(m.group(1))
        col = normalize_identifier(m.group(2))
        tables.setdefault(tname, set()).add(col)

    return tables


def extract_table_column_defs(sql_text: str) -> Dict[str, Dict[str, str]]:
    tables: Dict[str, Dict[str, str]] = {}
    create_re = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\."]+)\s*\((.*?)\)\s*;', re.S | re.I)
    for m in create_re.finditer(sql_text):
        tname = normalize_identifier(m.group(1))
        body = m.group(2)
        cols: Dict[str, str] = {}
        for col_def in re.split(r',\s*', body):
            line = col_def.strip()
            if not line or line.upper().startswith('CONSTRAINT') or line.upper().startswith('PRIMARY KEY'):
                continue
            parts = re.split(r"\s+", line, maxsplit=1)
            col = parts[0].rstrip(',')
            coln = normalize_identifier(col)
            if coln:
                cols[coln] = line
        if cols:
            tables[tname] = cols

    alter_re = re.compile(r'ALTER\s+TABLE\s+([\w\."]+)\s+ADD\s+COLUMN\s+([\w\."]+)(.*?);', re.I | re.S)
    for m in alter_re.finditer(sql_text):
        tname = normalize_identifier(m.group(1))
        col = normalize_identifier(m.group(2))
        rest = m.group(3).strip()
        tables.setdefault(tname, {})[col] = (m.group(2) + ' ' + rest).strip()

    return tables


def has_data_type_annotation(head_sql: str, table: str, column: str, col_def: str) -> bool:
    if not column:
        return False
    if col_def and re.search(r'data_type\s*[:=]', col_def, re.I):
        return True

    com_re = re.compile(r"COMMENT\s+ON\s+COLUMN\s+([\w\.\"]+\.)?([\w\"]+)\s+IS\s+'([^']*)'", re.I)
    for m in com_re.finditer(head_sql):
        col_name = normalize_identifier(m.group(2))
        comment = m.group(3)
        if col_name == column and re.search(r'data_type\s*[:=]', comment, re.I):
            return True

    if col_def:
        idx = head_sql.find(col_def)
        if idx != -1:
            before = head_sql[max(0, idx-200):idx+len(col_def)+200]
            if re.search(r'--.*data_type\s*[:=]', before, re.I) or re.search(r'/\*.*data_type\s*[:=].*\*/', before, re.I | re.S):
                return True

    pat = re.compile(re.escape(column) + r"[^\n]*--[^\n]*data_type\s*[:=]", re.I)
    if pat.search(head_sql):
        return True

    return False


def extract_select_columns(sql_text: str) -> Set[str]:
    """Find simple SELECT column lists and aliases: SELECT a, b as c FROM ..."""
    cols = set()
    sel_re = re.compile(r'SELECT\s+(.*?)\s+FROM', re.S | re.I)
    for m in sel_re.finditer(sql_text):
        body = m.group(1)
        # split on commas outside parentheses (simple)
        for part in re.split(r',\s*(?![^()]*\))', body):
            part = part.strip()
            if not part:
                continue
            # handle `col AS alias` or `table.col` or `col as alias`
            as_match = re.search(r'\bAS\b\s+([\w\"]+)$', part, re.I)
            if as_match:
                cols.add(normalize_identifier(as_match.group(1)))
            else:
                # take last identifier after dot
                simple = re.split(r'\s+', part)[0]
                simple = simple.split('.')[-1]
                simple = simple.rstrip(',')
                cols.add(normalize_identifier(simple))
    return cols


def snake_to_camel(s: str) -> str:
    parts = s.split('_')
    out = ''.join(p.capitalize() if p else '' for p in parts)
    # common special-case: id -> ID
    out = re.sub(r'(?<![A-Za-z])Id$', 'ID', out)
    if out == 'Id':
        out = 'ID'
    return out


def extract_go_field_accesses(path: str) -> List[Tuple[str, int]]:
    accesses: List[Tuple[str, int]] = []
    # pattern: .FieldName where FieldName starts with uppercase (exported)
    pat = re.compile(r'\.(?:\s*)([A-Z][A-Za-z0-9_]*)')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for i, line in enumerate(fh, start=1):
                for m in pat.finditer(line):
                    accesses.append((m.group(1), i))
    except Exception:
        pass
    return accesses


def scan_repo(root: str) -> Dict[str, Dict]:
    # gather SQL columns and definitions
    sql_columns: Set[str] = set()
    all_sql_text = ''
    col_defs: Dict[str, Dict[str, str]] = {}
    for f in find_files(root, ('.sql',)):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                txt = fh.read()
        except Exception:
            continue
        all_sql_text += '\n' + txt
        cols_schema = extract_sql_columns_from_schema(txt)
        for t, cols in cols_schema.items():
            sql_columns.update(cols)
        sel_cols = extract_select_columns(txt)
        sql_columns.update(sel_cols)
        defs = extract_table_column_defs(txt)
        for t, m in defs.items():
            col_defs.setdefault(t, {}).update(m)

    # prepare column -> expected Go field names
    col_to_field = {}
    for col in sql_columns:
        camel = snake_to_camel(col)
        variants = {camel}
        # also allow leading uppercase/lowercase variants
        variants.add(camel)
        # sometimes sqlc appends nullable suffix types but field name remains same - keep base
        col_to_field[col] = variants

    # scan go files for field accesses
    results: Dict[str, Dict] = defaultdict(lambda: {'usages': []})
    for g in find_files(root, ('.go',)):
        accesses = extract_go_field_accesses(g)
        for field, ln in accesses:
            # match against any column's expected field names
            for col, variants in col_to_field.items():
                for v in variants:
                    if field.startswith(v):
                        results[col]['usages'].append({'file': os.path.relpath(g, root), 'line': ln, 'field': field})
                        break
                        break

    # annotate whether columns have data_type annotations
    for t, cols in col_defs.items():
        for col, defn in cols.items():
            annotated = has_data_type_annotation(all_sql_text, t, col, defn)
            results.setdefault(col, {'usages': []})['annotated'] = annotated

    # For columns that have no explicit definition snippets, still try to find annotations via comments
    for col in list(sql_columns):
        if col not in results or 'annotated' not in results[col]:
            # unknown table context – pass empty defn and empty table
            annotated = has_data_type_annotation(all_sql_text, '', col, '')
            results.setdefault(col, {'usages': []})['annotated'] = annotated

    # Convert defaultdict to normal dict
    return dict(results)


def main():
    parser = argparse.ArgumentParser(description='Scan repo for DB field accessor usages')
    parser.add_argument('--repo', default='.', help='Repo root to scan')
    parser.add_argument('--json', action='store_true', help='Print JSON output')
    args = parser.parse_args()

    res = scan_repo(args.repo)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if not res:
            print('No DB column usages detected.')
            return
        for col, info in sorted(res.items()):
            print(f"Column: {col}")
            for u in info['usages']:
                print(f"  {u['file']}:{u['line']} -> field {u['field']}")


if __name__ == '__main__':
    main()
