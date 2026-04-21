# Project Structure

## Runtime-critical files

| File | Purpose |
|---|---|
| `cs_importer.py` | Main entry point. Handles CLI args, config loading, XLSX parsing, workspace creation, file upload, logging, and the import summary. |
| `file_resolver.py` | Path resolution module. Provides `detect_file_layout()` (auto-detect XLSX header row and column positions) and `resolve_source_path()` (strategies A–D to locate a file on disk). No side effects — pure functions only. |

Both files must be present at runtime (or bundled via PyInstaller).

## Packaging

| File | Purpose |
|---|---|
| `cs_importer.spec` | PyInstaller build specification. Intentionally maintained. Produces `dist\cs_importer.exe`. Run: `pyinstaller cs_importer.spec` |

## Configuration examples

| File | Purpose |
|---|---|
| `config.example.json` | Canonical sanitized example config. Copy and adapt for each XLSX. |
| `cs_importer_example_legacy.json` | Legacy mode: fixed column indices, single `local_file_root`, auto-detect off. |
| `cs_importer_example_modern.json` | Multi-root mode: `local_file_roots` list, auto-detect on, custom Hungarian aliases. |
| `cs_importer_example_autodetect.json` | Full auto-detect: no fixed columns, recursive search enabled, `dry_run: true`. |

## Tests

| File | Purpose |
|---|---|
| `test_file_resolver.py` | pytest tests for `file_resolver.py`. Covers all path resolution strategies, header auto-detection, shifted columns, Hungarian headers, and custom aliases. Run: `pytest test_file_resolver.py -v` |

## Documentation

| File | Purpose |
|---|---|
| `README.md` | Project overview, usage, configuration, packaging notes. |
| `docs/PROJECT_STRUCTURE.md` | This file. |
| `docs/CHANGELOG.md` | Version history and unreleased changes. |
| `requirements.txt` | Python package dependencies. |
| `LICENSE` | MIT license. |

## Files excluded from version control

The following are excluded via `.gitignore` and must never be committed:

| Pattern | Reason |
|---|---|
| `SP_licin_*.json` | Real credentials and company paths |
| `SP_licin_*.xlsx` | Customer data |
| `cleaner.py`, `test.py`, `test_ws_create.py`, `test_files_create.py` | One-off dev scripts with hardcoded credentials |
| `cs_import_workspaces.py` | Early prototype with hardcoded credentials |
| `cs_importer.BAK`, `*.zip` | Backup / archive artifacts |
| `build/`, `dist/`, `installer/` | PyInstaller output and deployed packages |
| `__pycache__/`, `.pytest_cache/` | Python and test runner caches |
| `logs/` | Runtime log output |
