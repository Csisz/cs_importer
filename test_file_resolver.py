"""
pytest tests for file_resolver.py

Run with:  pytest test_file_resolver.py -v
"""
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from file_resolver import detect_file_layout, get_roots, resolve_source_path


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def tmp_root(tmp_path):
    """A temp directory with a couple of sample files."""
    (tmp_path / "file_a.pdf").write_bytes(b"%PDF")
    (tmp_path / "file_b.docx").write_bytes(b"PK")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.pdf").write_bytes(b"%PDF")
    return tmp_path


@pytest.fixture()
def tmp_root2(tmp_path):
    """A second temp root (used for multi-root tests)."""
    r2 = tmp_path / "root2"
    r2.mkdir()
    (r2 / "only_in_root2.pdf").write_bytes(b"%PDF")
    return r2


# ─────────────────────────────────────────────────────────────────────────────
# get_roots
# ─────────────────────────────────────────────────────────────────────────────
def test_get_roots_legacy_only():
    cfg = {"local_file_root": "C:\\import\\files"}
    assert get_roots(cfg) == ["C:\\import\\files"]


def test_get_roots_list_only():
    cfg = {"local_file_roots": ["C:\\a", "C:\\b"]}
    assert get_roots(cfg) == ["C:\\a", "C:\\b"]


def test_get_roots_merges_legacy_into_list():
    cfg = {"local_file_roots": ["C:\\a"], "local_file_root": "C:\\b"}
    assert get_roots(cfg) == ["C:\\a", "C:\\b"]


def test_get_roots_deduplicates_legacy():
    cfg = {"local_file_roots": ["C:\\a"], "local_file_root": "C:\\a"}
    assert get_roots(cfg) == ["C:\\a"]


def test_get_roots_empty():
    assert get_roots({}) == []


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – strategy A: absolute path
# ─────────────────────────────────────────────────────────────────────────────
def test_absolute_existing_path(tmp_root):
    src = str(tmp_root / "file_a.pdf")
    res = resolve_source_path(src, "", {})
    assert res["resolved_by"] == "absolute_path"
    assert res["exists"] is True
    assert res["local_path"] == src


def test_absolute_missing_path(tmp_root):
    src = str(tmp_root / "ghost.pdf")
    res = resolve_source_path(src, "", {})
    assert res["resolved_by"] == "absolute_path"
    assert res["exists"] is False


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – strategy B: relative path with directory component
# ─────────────────────────────────────────────────────────────────────────────
def test_relative_path_with_subdir(tmp_root):
    cfg = {"local_file_roots": [str(tmp_root)]}
    res = resolve_source_path("subdir/nested.pdf", "", cfg)
    assert res["resolved_by"] == "relative_to_root"
    assert res["exists"] is True


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – strategy C1: folder_hint + filename
# ─────────────────────────────────────────────────────────────────────────────
def test_folder_hint_plus_filename(tmp_root):
    cfg = {"local_file_roots": [str(tmp_root)]}
    res = resolve_source_path("nested.pdf", "subdir", cfg)
    assert res["resolved_by"] == "folder_plus_filename"
    assert res["exists"] is True


def test_folder_hint_absolute(tmp_root):
    abs_folder = str(tmp_root / "subdir")
    cfg = {}
    res = resolve_source_path("nested.pdf", abs_folder, cfg)
    assert res["resolved_by"] == "folder_plus_filename"
    assert res["exists"] is True


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – strategy C2: basename against roots
# ─────────────────────────────────────────────────────────────────────────────
def test_basename_found_in_root(tmp_root):
    cfg = {"local_file_roots": [str(tmp_root)]}
    res = resolve_source_path("file_b.docx", "", cfg)
    assert res["resolved_by"] == "relative_to_root"
    assert res["exists"] is True


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – multiple roots, file only in second root
# ─────────────────────────────────────────────────────────────────────────────
def test_multiple_roots_second_wins(tmp_root, tmp_root2):
    cfg = {"local_file_roots": [str(tmp_root), str(tmp_root2)]}
    res = resolve_source_path("only_in_root2.pdf", "", cfg)
    assert res["resolved_by"] == "relative_to_root"
    assert res["exists"] is True
    assert "root2" in res["local_path"]


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – strategy D: legacy fallback
# ─────────────────────────────────────────────────────────────────────────────
def test_legacy_basename_join(tmp_root):
    # Use a filename that does NOT exist in tmp_root so C2 misses, forcing D to fire.
    # Strategy D: local_file_root + basename(src), returned even when file is absent.
    cfg = {"local_file_root": str(tmp_root)}
    res = resolve_source_path("old_prefix\\missing_file.pdf", "", cfg)
    assert res["resolved_by"] == "legacy_basename_join"
    assert "missing_file.pdf" in res["local_path"]
    assert res["exists"] is False


def test_legacy_single_root_basename(tmp_root):
    cfg = {"local_file_root": str(tmp_root)}
    res = resolve_source_path("file_a.pdf", "", cfg)
    # C2 (relative_to_root) picks it up before falling to D
    assert res["exists"] is True
    assert res["resolved_by"] in ("relative_to_root", "legacy_basename_join")


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – unresolved row
# ─────────────────────────────────────────────────────────────────────────────
def test_unresolved_empty_src():
    res = resolve_source_path("", "", {})
    assert res["resolved_by"] == "unresolved"
    assert res["exists"] is False
    assert res["local_path"] == ""


def test_unresolved_no_roots():
    res = resolve_source_path("mystery.pdf", "", {})
    assert res["resolved_by"] == "unresolved"
    assert res["exists"] is False


# ─────────────────────────────────────────────────────────────────────────────
# resolve_source_path – recursive search (strategy C3)
# ─────────────────────────────────────────────────────────────────────────────
def test_recursive_search(tmp_root):
    cfg = {"local_file_roots": [str(tmp_root)], "recursive_file_search": True}
    res = resolve_source_path("nested.pdf", "", cfg)
    assert res["resolved_by"] in ("relative_to_root", "recursive_search")
    assert res["exists"] is True


def test_recursive_search_not_triggered_when_disabled(tmp_root):
    cfg = {"local_file_roots": [str(tmp_root)], "recursive_file_search": False}
    # nested.pdf is only in subdir; with recursive=False and no folder_hint it should be unresolved
    # (C2 tries root/filename = tmp_root/nested.pdf which doesn't exist at root level)
    res = resolve_source_path("nested.pdf", "", cfg)
    # file_a.pdf is at root so it resolves; nested.pdf is only in subdir
    assert res["exists"] is False


# ─────────────────────────────────────────────────────────────────────────────
# detect_file_layout – headers in row 0 (standard)
# ─────────────────────────────────────────────────────────────────────────────
def test_detect_standard_header_row():
    df = pd.DataFrame([
        ["Location", "Title", "Source", "MIME", "Version"],
        ["SPLIC\\Files", "doc1.pdf", "D:\\files\\doc1.pdf", "pdf", "1"],
        ["SPLIC\\Files", "doc2.docx", "D:\\files\\doc2.docx", "docx", "1"],
    ])
    cfg = {"file_columns": {}, "auto_detect_file_columns": True}
    col_map, data_start = detect_file_layout(df, cfg)

    assert data_start == 1
    assert col_map.get("location") == 0
    assert col_map.get("title") == 1
    assert col_map.get("src") == 2


# ─────────────────────────────────────────────────────────────────────────────
# detect_file_layout – shifted columns (headers not in row 0)
# ─────────────────────────────────────────────────────────────────────────────
def test_detect_shifted_header_row():
    """Header is in row 3; rows 0-2 are blank/title rows."""
    df = pd.DataFrame([
        ["Import batch 2025-04", None, None, None, None],
        [None, None, None, None, None],
        [None, None, None, None, None],
        ["Location", "Title", "Source path", "MIME", "Version"],
        ["SPLIC\\Files", "doc1.pdf", "doc1.pdf", "pdf", "1"],
    ])
    cfg = {"file_columns": {}, "auto_detect_file_columns": True}
    col_map, data_start = detect_file_layout(df, cfg)

    assert data_start == 4, f"Expected data_start=4, got {data_start}"
    assert col_map.get("location") == 0
    assert col_map.get("title") == 1
    assert col_map.get("src") == 2


# ─────────────────────────────────────────────────────────────────────────────
# detect_file_layout – Hungarian headers
# ─────────────────────────────────────────────────────────────────────────────
def test_detect_hungarian_headers():
    df = pd.DataFrame([
        ["Helyszín", "Fájlnév", "Teljes elérési út", "File típus"],
        ["SPLIC\\Files", "doc1.pdf", "D:\\files\\doc1.pdf", "pdf"],
    ])
    cfg = {"file_columns": {}, "auto_detect_file_columns": True}
    col_map, data_start = detect_file_layout(df, cfg)

    assert data_start == 1
    assert col_map.get("location") == 0
    assert col_map.get("title") == 1


# ─────────────────────────────────────────────────────────────────────────────
# detect_file_layout – JSON manual overrides auto-detect
# ─────────────────────────────────────────────────────────────────────────────
def test_manual_overrides_autodetect():
    """JSON file_columns should take priority over detected header positions."""
    df = pd.DataFrame([
        ["Extra", "Location", "Title", "Source", "MIME"],  # shifted by one
        ["x", "SPLIC\\Files", "doc1.pdf", "doc1.pdf", "pdf"],
    ])
    cfg = {
        "file_columns": {"location": 1, "title": 2, "src": 3, "mime": 4},
        "auto_detect_file_columns": True,
    }
    col_map, data_start = detect_file_layout(df, cfg)

    assert col_map["location"] == 1
    assert col_map["title"] == 2
    assert col_map["src"] == 3
    assert col_map["mime"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# detect_file_layout – auto_detect_file_columns=False (legacy mode)
# ─────────────────────────────────────────────────────────────────────────────
def test_autodetect_disabled_uses_manual_only():
    df = pd.DataFrame([
        ["Location", "Title", "Source"],
        ["SPLIC\\Files", "doc1.pdf", "doc1.pdf"],
    ])
    cfg = {
        "file_columns": {"location": 0, "title": 1, "src": 2},
        "auto_detect_file_columns": False,
        "file_data_start_row": 1,
    }
    col_map, data_start = detect_file_layout(df, cfg)

    assert data_start == 1
    assert col_map["location"] == 0
    assert col_map["title"] == 1
    assert col_map["src"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# detect_file_layout – custom aliases from config
# ─────────────────────────────────────────────────────────────────────────────
def test_custom_alias_in_config():
    df = pd.DataFrame([
        ["CS Mappa", "Dokumentum neve", "Forrás fájl", "Verzió"],
        ["SPLIC\\Files", "doc1.pdf", "doc1.pdf", "1"],
    ])
    cfg = {
        "file_columns": {},
        "auto_detect_file_columns": True,
        "file_column_aliases": {
            "src":     ["Forrás fájl"],
            "title":   ["Dokumentum neve"],
            "version": ["Verzió"],
        },
    }
    col_map, data_start = detect_file_layout(df, cfg)

    assert data_start == 1
    assert col_map.get("title") == 1
    assert col_map.get("src") == 2
    assert col_map.get("version") == 3
