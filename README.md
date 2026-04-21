# cs_importer

Python-based OpenText Content Server importer driven by XLSX and JSON configuration.

Reads workspace and file metadata from an Excel spreadsheet, creates Business Workspaces in OpenText Content Server via its REST API, and uploads associated files — all without requiring manual interaction. Designed to run offline on company servers as a self-contained Windows EXE built with PyInstaller.

---

## Features

- Create Business Workspaces from a template with category attributes applied at creation
- Upload files into the correct CS locations with duplicate handling (`skip` or `new_version`)
- Flexible source-file path resolution: absolute paths, relative paths, multiple root folders, optional recursive search
- Auto-detect XLSX column layout from header text (English and Hungarian aliases built-in)
- Manual column overrides in JSON take priority over auto-detection
- Dual log output: full debug log + errors-only log in a `logs\` subfolder
- Dry-run mode: previews everything without touching the server
- Workspace name remapping when OTCS assigns a different name than the XLSX (e.g. sequential numbering)
- Packaged as a single Windows EXE via PyInstaller — no Python required on the target machine

---

## Typical use case

A batch migration of document packages into an OTCS Business Workspace hierarchy. Each row in the XLSX represents either a workspace or a file to upload. A companion JSON file provides server credentials, category mappings, and path resolution config. The operator runs:

```
cs_importer.exe migration_data.xlsx --dry-run
cs_importer.exe migration_data.xlsx
```

---

## High-level workflow

```
XLSX + JSON config
       │
       ▼
 parse_workspaces()     ──► create/find Business Workspaces in OTCS
 parse_files()
   └─ detect_file_layout()   auto-detect header row & column positions
   └─ resolve_source_path()  locate each file on disk (strategies A → D)
       │
       ▼
 CSClient.upload_file()  ──► upload to correct OTCS folder
       │
       ▼
 logs\<name>_<ts>_full.log
 logs\<name>_<ts>_errors.log
```

### Path resolution strategies (in order)

| Strategy | When used |
|---|---|
| **A** absolute path | XLSX cell contains a full absolute path and the file exists |
| **B** relative to root | XLSX cell contains a relative path; resolved against each configured root |
| **C1** folder + filename | XLSX row has a separate folder column; combined with each root |
| **C2** basename search | Filename only; looked up directly under each root |
| **C3** recursive walk | `recursive_file_search: true`; walks subdirectories under each root |
| **D** legacy fallback | `local_file_root` + `basename(src)` — original behaviour |

---

## Project structure

```
cs_importer/
├── cs_importer.py              Main application entry point
├── file_resolver.py            File path resolution helper (strategies A–D)
├── test_file_resolver.py       pytest tests for file_resolver
├── cs_importer.spec            PyInstaller build spec (intentionally maintained)
├── config.example.json         Sanitized example config (copy and adapt)
├── cs_importer_example_legacy.json      Legacy/compat example
├── cs_importer_example_modern.json      Multi-root example
├── cs_importer_example_autodetect.json  Full auto-detect example
├── requirements.txt
├── LICENSE
├── README.md
└── docs/
    ├── PROJECT_STRUCTURE.md
    └── CHANGELOG.md
```

---

## Installation (development)

```bash
git clone https://github.com/Csisz/cs_importer.git
cd cs_importer
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

---

## Usage

```bash
# Dry run — preview without touching the server
python cs_importer.py your_data.xlsx --dry-run

# Real run
python cs_importer.py your_data.xlsx
```

Or with the packaged EXE:

```
cs_importer.exe your_data.xlsx --dry-run
cs_importer.exe your_data.xlsx
```

The JSON config must be in the same folder as the XLSX, with the same filename stem:

```
migration_data.xlsx
migration_data.json   ← companion config
```

---

## Configuration overview

Copy `config.example.json` to `<your_data>.json` and fill in the values.

| Key | Description |
|---|---|
| `base_url` | Content Server base URL |
| `username` / `password` | OTCS credentials |
| `enterprise_node_id` | Root node ID in CS |
| `template_id` | Business Workspace template node ID |
| `wksp_type_id` | Workspace type ID |
| `category_id` | Category definition ID |
| `ws_sheet` / `file_sheet` | Sheet names in the XLSX |
| `ws_columns` | Column indices for workspace sheet |
| `file_columns` | Column indices for file sheet (overrides auto-detect per field) |
| `local_file_root` | Single root folder (legacy) |
| `local_file_roots` | List of root folders (multi-root mode) |
| `auto_detect_file_columns` | Scan XLSX header row automatically (default `true`) |
| `recursive_file_search` | Walk subdirectories when looking for files (default `false`) |
| `file_column_aliases` | Custom header name mappings not in the built-in list |
| `on_duplicate` | `skip` or `new_version` |
| `dry_run` | `true` to preview without writing |

See `config.example.json` and the three `cs_importer_example_*.json` files for annotated examples.

> **Warning:** Never commit a JSON config file containing real credentials, server URLs, or company file paths. Add those files to `.gitignore`.

---

## PyInstaller packaging

The `cs_importer.spec` file is maintained in the repo and produces a single-file EXE:

```bash
pip install pyinstaller
pyinstaller cs_importer.spec
# Output: dist\cs_importer.exe
```

`file_resolver.py` is automatically bundled because `cs_importer.py` imports it explicitly.

---

## Offline / company-server usage notes

- The EXE includes the Python runtime and all dependencies — no installation needed on the target machine.
- Place `cs_importer.exe` and the XLSX + JSON pair in the same folder.
- Log files are written to a `logs\` subfolder next to the EXE.
- SSL verification is disabled by default (`ssl_verify: false`) for internal corporate certificates.
- Tested on Windows 10 Enterprise.

---

## Planned improvements

- [ ] Richer column auto-detection with confidence scoring
- [ ] Support for XLSX files where file metadata is split across multiple sheets
- [ ] Config validation with user-friendly error messages for missing/wrong keys
- [ ] GUI wrapper for non-technical operators
- [ ] Support for token-based authentication in addition to username/password

---

## Security notice

This tool is designed for internal corporate use. Do not commit:
- Real OTCS credentials (`username`, `password`)
- Real server URLs
- Real local file paths containing company data
- Customer-specific XLSX files

Use the `config.example.json` as a template and keep real configs out of version control.
