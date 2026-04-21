"""
file_resolver.py  –  Flexible source-file path resolution for cs_importer.

Public API:
    get_roots(cfg)                          → list[str]
    detect_file_layout(df, cfg)             → (col_map, data_start_row)
    resolve_source_path(src, folder, cfg)   → dict
"""
import os
import re
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Column alias table  (semantic field → normalized header spellings)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_COLUMN_ALIASES: dict[str, list[str]] = {
    "location": [
        "location", "cs path", "cs elérési út", "cs mappa",
        "helyszín", "cél mappa", "destination", "target path", "target",
    ],
    "title": [
        "title", "name", "file name", "filename", "fájlnév", "file név",
        "megnevezés", "név", "document name", "dokumentum neve",
    ],
    "src": [
        "src", "source", "source path", "sourcepath", "file path", "filepath",
        "full path", "teljes elérési út", "forrás", "fájl elérési útja",
        "fájl neve", "file neve",
    ],
    "folder": [
        "folder", "base folder", "base path", "forrás mappa",
        "könyvtár", "directory", "dir", "mappa", "forras mappa",
    ],
    "mime": [
        "mime", "mime type", "mimetype", "file type", "file típus",
        "content type", "típus", "tipus",
    ],
    "version": [
        "version", "verzió", "verzio", "ver", "revision", "rev",
        "document version",
    ],
}

# Hungarian accent → ASCII  (equal-length strings, 18 chars each)
_FROM   = "áéíóöőúüűÁÉÍÓÖŐÚÜŰ"
_TO     = "aeiooouuuAEIOOOUUU"
_ACCENT = str.maketrans(_FROM, _TO)


def _norm(s: str) -> str:
    """Lowercase, strip, collapse whitespace, drop common Hungarian accents."""
    return re.sub(r"\s+", " ", str(s).translate(_ACCENT).lower().strip())


# ─────────────────────────────────────────────────────────────────────────────
# Root folders
# ─────────────────────────────────────────────────────────────────────────────
def get_roots(cfg: dict) -> list[str]:
    """Merge local_file_roots (list) + local_file_root (legacy string) into one list."""
    roots: list[str] = list(cfg.get("local_file_roots") or [])
    legacy: str = cfg.get("local_file_root", "")
    if legacy and legacy not in roots:
        roots.append(legacy)
    return [r for r in roots if r]


# ─────────────────────────────────────────────────────────────────────────────
# Auto-detect file sheet layout
# ─────────────────────────────────────────────────────────────────────────────
def detect_file_layout(df, cfg: dict) -> tuple[dict[str, Optional[int]], int]:
    """Scan the first rows of *df* to find a header row and map columns.

    Returns:
        col_map         dict[field → col_index | None]
        data_start_row  int  (first row of actual data)

    Priority: JSON file_columns > auto-detected header > None.
    When auto_detect_file_columns is False only JSON indices are used.
    """
    manual: dict = cfg.get("file_columns") or {}

    # Normalise alias lists, merging any custom aliases from config
    aliases: dict[str, list[str]] = {}
    for field, alias_list in DEFAULT_COLUMN_ALIASES.items():
        aliases[field] = [_norm(a) for a in alias_list]
    for field, alias_list in (cfg.get("file_column_aliases") or {}).items():
        extra = [_norm(a) for a in alias_list]
        aliases[field] = extra + aliases.get(field, [])

    if not cfg.get("auto_detect_file_columns", True):
        col_map = {field: (int(manual[field]) if manual.get(field) is not None else None)
                   for field in aliases}
        return col_map, int(cfg.get("file_data_start_row", 0))

    best_row   = -1
    best_score = 0
    best_map: dict[str, Optional[int]] = {}

    scan_limit = min(30, len(df))
    for row_idx in range(scan_limit):
        row = df.iloc[row_idx]
        candidate: dict[str, Optional[int]] = {}
        score = 0
        for field, alias_list in aliases.items():
            if manual.get(field) is not None:
                # JSON index pre-fills the slot; don't count toward header score
                candidate[field] = int(manual[field])
                continue
            for ci, cell in enumerate(row):
                if _norm(str(cell)) in alias_list:
                    candidate[field] = ci
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_row   = row_idx
            best_map   = dict(candidate)

    # Apply JSON overrides on top of whatever was auto-detected
    col_map: dict[str, Optional[int]] = dict(best_map)
    for field, idx in manual.items():
        if idx is not None:
            col_map[field] = int(idx)

    if best_score >= 1:
        data_start = best_row + 1
    else:
        data_start = int(cfg.get("file_data_start_row", 0))

    return col_map, data_start


# ─────────────────────────────────────────────────────────────────────────────
# Recursive search helper
# ─────────────────────────────────────────────────────────────────────────────
def _find_recursive(filename: str, roots: list[str]) -> Optional[str]:
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            if filename in files:
                return os.path.join(dirpath, filename)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution  (strategies A → D)
# ─────────────────────────────────────────────────────────────────────────────
def resolve_source_path(src: str, folder_hint: str, cfg: dict) -> dict:
    """Try strategies A → D to locate *src* on the local filesystem.

    Returns a dict:
        local_path   str   resolved path (may be empty / non-existent)
        original_src str   the raw value from the XLSX
        resolved_by  str   strategy label (see below)
        exists       bool  os.path.isfile(local_path)

    Strategy labels:
        absolute_path        A: src was an absolute path
        relative_to_root     B/C2: src (or its basename) found under a root
        folder_plus_filename C1: folder_hint + basename(src) found
        recursive_search     C3: recursive walk found filename
        legacy_basename_join D: local_file_root + basename(src) (fallback)
        unresolved               nothing worked
    """
    def _r(path: str, strategy: str) -> dict:
        p = str(Path(path)) if path else ""
        return {
            "local_path":   p,
            "original_src": src,
            "resolved_by":  strategy,
            "exists":       bool(p and os.path.isfile(p)),
        }

    if not src:
        return _r("", "unresolved")

    roots    = get_roots(cfg)
    p_src    = Path(src)
    filename = p_src.name

    # ── A: absolute path ─────────────────────────────────────────────────────
    if p_src.is_absolute():
        return _r(src, "absolute_path")   # exists flag set by _r

    # ── B: relative path with directory component ─────────────────────────────
    if p_src.parent != Path("."):
        for root in roots:
            candidate = os.path.join(root, src)
            if os.path.isfile(candidate):
                return _r(candidate, "relative_to_root")
        # Fall through: also try C2 with just the basename

    # ── C1: explicit folder hint from XLSX ────────────────────────────────────
    if filename and folder_hint:
        if os.path.isabs(folder_hint):
            candidate = os.path.join(folder_hint, filename)
            if os.path.isfile(candidate):
                return _r(candidate, "folder_plus_filename")
        else:
            for root in roots:
                candidate = os.path.join(root, folder_hint, filename)
                if os.path.isfile(candidate):
                    return _r(candidate, "folder_plus_filename")

    # ── C2: basename against each root ────────────────────────────────────────
    if filename:
        for root in roots:
            candidate = os.path.join(root, filename)
            if os.path.isfile(candidate):
                return _r(candidate, "relative_to_root")

    # ── C3: recursive walk ────────────────────────────────────────────────────
    if filename and cfg.get("recursive_file_search", False) and roots:
        found = _find_recursive(filename, roots)
        if found:
            return _r(found, "recursive_search")

    # ── D: legacy  local_file_root + basename ─────────────────────────────────
    legacy = cfg.get("local_file_root", "")
    if legacy and filename:
        return _r(os.path.join(legacy, filename), "legacy_basename_join")

    return _r(src, "unresolved")
