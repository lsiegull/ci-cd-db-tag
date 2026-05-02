# DB Field Change Detector

Small prototype that detects added database fields (columns) in SQL files by comparing the repository HEAD against a base git ref.

Usage:

Run locally (compares to `origin/main` by default):

```bash
python detect_new_fields.py --base origin/main
```

Or check specific files:

```bash
python detect_new_fields.py --files migrations/001_init.sql schema/tables.sql
```

Exit codes:
- `0` no new fields detected
- `2` new fields detected
# ci-cd-db-tag
CI/CD tool for data labelling

Usage for the repository scanner
--------------------------------

A separate scanner is provided to find Go code that accesses database fields (useful when using `sqlc`-generated structs).

Run a quick scan from the repository root:

```bash
python scan_db_accessors.py --repo .
```

Print machine-readable JSON output:

```bash
python scan_db_accessors.py --repo . --json
```

Notes:
- The scanner uses simple heuristics: it parses SQL `CREATE TABLE` / `ALTER TABLE` statements and `SELECT` lists, maps snake_case column names to CamelCase Go field names, and looks for `.Field` access patterns in `.go` files.
- This is a lightweight tool intended as a starting point — it may need refinement for complex aliases, embedded structs, or custom naming rules used by `sqlc`.

