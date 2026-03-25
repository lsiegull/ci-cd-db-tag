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
