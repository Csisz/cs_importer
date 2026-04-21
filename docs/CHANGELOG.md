# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Planned
- Config validation with user-friendly error messages
- GUI wrapper for non-technical operators
- Token-based authentication support

---

## [0.2.0] – 2026-04-21

### Added
- `file_resolver.py` — dedicated path resolution module with strategies A–D
  - **A**: absolute path used directly
  - **B**: relative path resolved against each configured root
  - **C1**: folder hint column + filename
  - **C2**: basename searched across root folders
  - **C3**: recursive directory walk (opt-in via `recursive_file_search: true`)
  - **D**: legacy fallback — `local_file_root` + `basename(src)`
- Support for `local_file_roots` list alongside legacy `local_file_root` string
- `auto_detect_file_columns` — scans up to 30 XLSX rows to find the header row automatically
- `file_column_aliases` — custom header name mappings for non-standard XLSX layouts
- `recursive_file_search` config option
- Richer file row metadata: `original_src`, `resolved_by`, `exists`
- Improved file preview and per-file log output (source, resolved path, strategy)
- 24 pytest tests covering all resolution strategies and auto-detect scenarios
- Three annotated example configs: legacy, modern multi-root, full auto-detect

### Changed
- `parse_files()` rewritten to use `file_resolver`; no longer crashes on unresolved rows
- Unresolved rows are kept in the result with `exists=False` and logged as warnings
- `file_columns` is no longer a required key in JSON (empty `{}` enables full auto-detect)
- Banner now shows all configured root folders and resolution flags

### Backward compatible
- Old JSON configs with `local_file_root` and explicit `file_columns` work unchanged
- Old XLSX files work unchanged
- CLI interface unchanged

---

## [0.1.0] – 2026-02-23

### Added
- Initial working importer: workspace creation + file upload driven by XLSX + JSON
- Business Workspace creation from template with category attribute injection
- Workspace name remapping when OTCS assigns a sequential name different from the XLSX value
- Dual log output: full debug log + errors-only log in `logs\`
- Dry-run mode
- Progress bar display
- PyInstaller spec for single-file EXE packaging
- `--dry-run` CLI flag
