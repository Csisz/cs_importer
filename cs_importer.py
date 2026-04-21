#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  OpenText Content Server – Generic Importer                     ║
║  • Workspaces + file upload driven by companion JSON config     ║
║  • Dual logging: full log + errors-only log in logs\ folder     ║
║                                                                  ║
║  Usage:                                                          ║
║    cs_importer.exe data.xlsx                                    ║
║    cs_importer.exe data.xlsx --dry-run                          ║
║                                                                  ║
║  Config: data.json  (same folder as data.xlsx)                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import sys, os, time, json, mimetypes, logging, re, argparse, traceback
import requests, urllib3, pandas as pd
from pathlib import Path
from datetime import datetime
from file_resolver import detect_file_layout, resolve_source_path, get_roots

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Resolved once at import time; safe for both script and frozen EXE.
BASE_DIR: Path = (
    Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
)

_REQUEST_TIMEOUT = 30  # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Logging  –  logs\<stem>_<ts>_full.log  +  logs\<stem>_<ts>_errors.log
# ─────────────────────────────────────────────────────────────────────────────
_ANSI = re.compile(r'\x1b\[[0-9;]*m')

class StripAnsiFormatter(logging.Formatter):
    def format(self, record):
        return _ANSI.sub('', super().format(record))

LOG: logging.Logger = None

def setup_logging(xlsx_path: str) -> tuple[str, str]:
    global LOG
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    stem      = Path(xlsx_path).stem
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_path = str(logs_dir / f"{stem}_{ts}_full.log")
    err_path  = str(logs_dir / f"{stem}_{ts}_errors.log")

    fmt = StripAnsiFormatter("%(asctime)s  %(levelname)-8s  %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
    LOG = logging.getLogger("cs_importer")
    LOG.setLevel(logging.DEBUG)
    LOG.handlers.clear()

    fh = logging.FileHandler(full_path,  encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    LOG.addHandler(fh)

    eh = logging.FileHandler(err_path, encoding="utf-8")
    eh.setLevel(logging.WARNING)
    eh.setFormatter(fmt)
    LOG.addHandler(eh)

    return full_path, err_path


# ─────────────────────────────────────────────────────────────────────────────
# Console + log helpers
# ─────────────────────────────────────────────────────────────────────────────
USE_COLOR = sys.stdout.isatty()
def _c(code, t): return f"\033[{code}m{t}\033[0m" if USE_COLOR else t
def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def red(t):    return _c("31", t)
def cyan(t):   return _c("36", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)

def info(msg):
    print(msg)
    if LOG: LOG.info(_ANSI.sub('', str(msg)))

def warn(msg):
    print(yellow(str(msg)))
    if LOG: LOG.warning(_ANSI.sub('', str(msg)))

def err(msg):
    print(red(str(msg)))
    if LOG: LOG.error(_ANSI.sub('', str(msg)))

def dbg(msg):
    print(dim(str(msg)))
    if LOG: LOG.debug(_ANSI.sub('', str(msg)))

def progress_bar(cur, total, w=30):
    filled = int(w * cur / total) if total else 0
    return f"[{'█'*filled}{'░'*(w-filled)}] {int(100*cur/total) if total else 0:3d}%  ({cur}/{total})"


# ─────────────────────────────────────────────────────────────────────────────
# XLSX input validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_xlsx_path(raw: str) -> str:
    """Normalize, validate extension and existence; return canonical string path."""
    p = Path(raw)
    if p.suffix.lower() != ".xlsx":
        raise ValueError(f"Expected a .xlsx file, got: {raw!r}")
    if not p.exists():
        raise FileNotFoundError(f"XLSX not found: {str(p.resolve())!r}")
    return str(p)


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED = ["base_url","username","password","enterprise_node_id",
            "template_id","wksp_type_id","category_id",
            "ws_sheet","file_sheet","category_fields","ws_columns"]

DEFAULTS = {
    "local_file_root":         "",
    "local_file_roots":        [],
    "auto_detect_file_columns": True,
    "recursive_file_search":   False,
    "file_column_aliases":     {},
    "file_columns":            {},
    "on_duplicate":            "skip",
    "request_delay":           0.3,
    "ssl_verify":              False,
    "dry_run":                 False,
    "ws_data_start_row":       4,
    "file_data_start_row":     0,
}

def load_config(xlsx_path: str, dry_run_flag: bool) -> dict:
    # Config is always derived from the XLSX path, never from sys.executable.
    json_path = Path(xlsx_path).with_suffix(".json")
    if not json_path.exists():
        print(red(f"✖ Config not found: {json_path}"))
        print(red(f"  Create a .json alongside your .xlsx  (see template: cs_importer_template.json)"))
        sys.exit(1)
    with open(json_path, encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    if dry_run_flag:
        cfg["dry_run"] = True
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        print(red(f"✖ Config missing keys: {missing}"))
        sys.exit(1)
    cfg["xlsx_path"] = xlsx_path
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# XLSX parsers
# ─────────────────────────────────────────────────────────────────────────────
def _safe(row, col):
    if col is None or col >= len(row): return ""
    v = row[col]
    s = str(v).strip() if pd.notna(v) else ""
    return "" if s.lower() in ("nan","none") else s

def parse_workspaces(cfg: dict) -> list[dict]:
    cols       = cfg["ws_columns"]
    cat_fields = cfg["category_fields"]
    df         = pd.read_excel(cfg["xlsx_path"], sheet_name=cfg["ws_sheet"],
                               header=None, engine="openpyxl")
    result     = []
    for _, row in df.iloc[cfg["ws_data_start_row"]:].iterrows():
        title    = _safe(row, cols.get("title"))
        location = _safe(row, cols.get("location"))
        if not title: continue
        cat_values = {}
        for fkey, fdef in cat_fields.items():
            if fdef.get("multi_value"):
                s = fdef.get("col_start", fdef.get("col"))
                e = fdef.get("col_end",   fdef.get("col"))
                cat_values[fkey] = [_safe(row, c) for c in range(s, e+1) if _safe(row, c)]
            else:
                val  = _safe(row, fdef.get("col"))
                vmap = fdef.get("value_map", {})
                if vmap and val:
                    val = vmap.get(val, next((v for k,v in vmap.items()
                                              if k.lower()==val.lower()), val))
                cat_values[fkey] = val
        result.append({"location": location, "title": title, "cat_values": cat_values})
    return result

def parse_files(cfg: dict) -> list[dict]:
    skip_vals = {"location", "file adatok", "title", "file név", ""}
    df = pd.read_excel(cfg["xlsx_path"], sheet_name=cfg["file_sheet"],
                       header=None, engine="openpyxl")

    col_map, data_start = detect_file_layout(df, cfg)
    dbg(f"  File sheet layout → header_row={data_start - 1 if data_start > 0 else '(none)'}, "
        f"data_start={data_start}, col_map={col_map}")

    result = []
    for _, row in df.iloc[data_start:].iterrows():
        location    = _safe(row, col_map.get("location"))
        title       = _safe(row, col_map.get("title"))
        src         = _safe(row, col_map.get("src"))
        folder_hint = _safe(row, col_map.get("folder"))
        mime_hint   = _safe(row, col_map.get("mime"))
        version     = _safe(row, col_map.get("version"))

        if not title or location.lower() in skip_vals:
            continue

        res = resolve_source_path(src, folder_hint, cfg)

        if not res["exists"]:
            if src:
                warn(f"  ⚠ File not found: '{title}'  src='{src}'"
                     f"  → '{res['local_path']}'  [{res['resolved_by']}]")
            else:
                warn(f"  ⚠ No source path for: '{title}'  location='{location}'")

        result.append({
            "location":     location,
            "title":        title,
            "local_path":   res["local_path"],
            "original_src": res["original_src"],
            "resolved_by":  res["resolved_by"],
            "mime_hint":    mime_hint,
            "version":      version,
            "exists":       res["exists"],
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MIME helper
# ─────────────────────────────────────────────────────────────────────────────
MIME_MAP = {"pdf":"application/pdf","eml":"message/rfc822","msg":"application/vnd.ms-outlook",
            "docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "doc":"application/msword","xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xls":"application/vnd.ms-excel","pptx":"application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "txt":"text/plain","tif":"image/tiff","tiff":"image/tiff",
            "jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
            "zip":"application/zip","xml":"application/xml","html":"text/html"}

def get_mime(path: str, hint: str) -> str:
    if hint and hint.lower() in MIME_MAP: return MIME_MAP[hint.lower()]
    ext = Path(path).suffix.lstrip(".").lower()
    if ext in MIME_MAP: return MIME_MAP[ext]
    g, _ = mimetypes.guess_type(path)
    return g or "application/octet-stream"


# ─────────────────────────────────────────────────────────────────────────────
# CS REST client
# ─────────────────────────────────────────────────────────────────────────────
def normalize_ws_name(name: str) -> str:
    """Normalize a workspace name for comparison by collapsing all whitespace
    around hyphens and stripping.  'SPLIC-00042' == 'SPLIC - 00042'."""
    return re.sub(r'\s*-\s*', '-', name.strip())


class CSClient:
    def __init__(self, cfg):
        self.base    = cfg["base_url"].rstrip("/")
        self.cfg     = cfg
        self.ssl     = cfg["ssl_verify"]
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._cache  = {}
        self._stats  = {"ws_created":0,"ws_existing":0,"ws_failed":0,
                         "f_uploaded":0,"f_versioned":0,"f_skipped":0,"f_failed":0}
        self.ws_name_map: dict[str, str] = {}

    def authenticate(self):
        info(f"\n{bold('Authenticating')} → {self.base}")
        try:
            r = self.session.post(f"{self.base}/api/v1/auth", verify=self.ssl,
                                  timeout=_REQUEST_TIMEOUT,
                                  data={"username":self.cfg["username"],"password":self.cfg["password"]})
        except requests.Timeout:
            err(f"  ✖ Auth timed out after {_REQUEST_TIMEOUT}s"); sys.exit(1)
        except requests.RequestException as e:
            err(f"  ✖ Auth request failed: {e}"); sys.exit(1)
        if r.status_code != 200:
            err(f"  ✖ Auth failed HTTP {r.status_code}: {r.text[:200]}")
            sys.exit(1)
        self.session.headers["OTCSTicket"] = r.json()["ticket"]
        info(green("  ✔ Authenticated"))

    def _get(self, path, **kw):
        time.sleep(self.cfg["request_delay"])
        kw.setdefault("timeout", _REQUEST_TIMEOUT)
        try:
            r = self.session.get(f"{self.base}{path}", verify=self.ssl, **kw)
        except requests.Timeout:
            raise requests.HTTPError(f"GET {path} timed out after {_REQUEST_TIMEOUT}s")
        r.raise_for_status(); return r.json()

    def _post(self, path, **kw):
        time.sleep(self.cfg["request_delay"])
        kw.setdefault("timeout", _REQUEST_TIMEOUT)
        try:
            r = self.session.post(f"{self.base}{path}", verify=self.ssl, **kw)
        except requests.Timeout:
            raise requests.HTTPError(f"POST {path} timed out after {_REQUEST_TIMEOUT}s")
        r.raise_for_status(); return r.json()

    def _put(self, path, **kw):
        time.sleep(self.cfg["request_delay"])
        kw.setdefault("timeout", _REQUEST_TIMEOUT)
        try:
            r = self.session.put(f"{self.base}{path}", verify=self.ssl, **kw)
        except requests.Timeout:
            raise requests.HTTPError(f"PUT {path} timed out after {_REQUEST_TIMEOUT}s")
        r.raise_for_status(); return r.json()

    def _find_child(self, parent_id: int, name: str) -> int | None:
        try:
            data = self._get(f"/api/v2/nodes/{parent_id}/nodes", params={"where_name":name,"limit":10})
            for item in data.get("results",[]):
                if item["data"]["properties"]["name"] == name:
                    return item["data"]["properties"]["id"]
        except requests.HTTPError: pass
        return None

    def resolve_path(self, cs_path: str) -> int:
        if cs_path in self._cache: return self._cache[cs_path]
        parts      = [p for p in cs_path.replace("\\","/").split("/") if p]
        current_id = self.cfg["enterprise_node_id"]
        built      = parts[0]
        self._cache[built] = current_id
        for part in parts[1:]:
            key = built + "/" + part
            if key in self._cache:
                current_id = self._cache[key]; built = key; continue
            if self.cfg["dry_run"]:
                child = -1
                dbg(f"    ↳ [dry-run] Would resolve: {part}")
            else:
                child = self._find_child(current_id, part)
                if child is None:
                    child = self._post("/api/v2/nodes",
                                       data={"type":0,"parent_id":current_id,"name":part}
                                       )["results"]["data"]["properties"]["id"]
                    dbg(f"    ↳ Auto-created folder: {part} (id={child})")
            self._cache[key] = child; current_id = child; built = key
        self._cache[cs_path] = current_id
        return current_id

    def _build_cat_data(self, cat_values: dict) -> dict:
        cat_id = self.cfg["category_id"]
        result = {}
        for fkey, fdef in self.cfg["category_fields"].items():
            cs_key = f"{cat_id}_{fdef['attr_id']}"
            value  = cat_values.get(fkey, [] if fdef.get("multi_value") else "")
            result[cs_key] = (value if value else [""]) if fdef.get("multi_value") else value
        return result

    def create_or_get_workspace(self, parent_id: int, excel_name: str,
                                cat_values: dict) -> tuple[int, bool, str]:
        """Create or find a workspace.

        Returns (node_id, was_created, actual_name).
        actual_name is the real name in OTCS — which may differ from excel_name
        when the server enforces sequential numbering (e.g. SPLIC naming rules).
        """
        if self.cfg["dry_run"]:
            dbg(f"    ↳ [dry-run] Would create BW '{excel_name}'")
            self._register_name(excel_name, excel_name)
            return -1, True, excel_name

        existing = self._find_child(parent_id, excel_name)
        if existing:
            self._register_name(excel_name, excel_name)
            return existing, False, excel_name

        body = {"type":848,"parent_id":parent_id,"name":excel_name,
                "template_id":self.cfg["template_id"],"wksp_type_id":self.cfg["wksp_type_id"],
                "roles":{"categories":{str(self.cfg["category_id"]): self._build_cat_data(cat_values)}}}
        try:
            time.sleep(self.cfg["request_delay"])
            r = self.session.post(f"{self.base}/api/v2/businessworkspaces",
                                  verify=self.ssl, timeout=_REQUEST_TIMEOUT,
                                  data={"body": json.dumps(body)})
            if r.status_code in (200, 201):
                rj = r.json()["results"]
                nid = rj["id"]
                actual_name = (rj.get("data", {}).get("properties", {}).get("name")
                               or self._fetch_node_name(nid)
                               or excel_name)
                self._register_name(excel_name, actual_name)
                dbg(f"    ↳ Business Workspace created (id={nid}, actual='{actual_name}')")
                return nid, True, actual_name
            warn(f"    ⚠ /v2/businessworkspaces HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        except requests.Timeout:
            warn(f"    ⚠ BW creation timed out after {_REQUEST_TIMEOUT}s")
        except requests.HTTPError as e:
            warn(f"    ⚠ BW creation failed HTTP "
                 f"{e.response.status_code if e.response else '?'}: "
                 f"{e.response.text[:300] if e.response else str(e)}")

        warn("    ↳ Falling back to plain Folder")
        nid = self._post("/api/v2/nodes",
                         data={"type":0,"parent_id":parent_id,"name":excel_name}
                         )["results"]["data"]["properties"]["id"]
        actual_name = self._fetch_node_name(nid) or excel_name
        self._register_name(excel_name, actual_name)
        dbg(f"    ↳ Created as Folder (id={nid}, actual='{actual_name}')")
        return nid, True, actual_name

    def _fetch_node_name(self, node_id: int) -> str | None:
        """Fetch the actual name of a node from OTCS (used after workspace creation)."""
        try:
            data = self._get(f"/api/v2/nodes/{node_id}")
            return data["results"]["data"]["properties"]["name"]
        except Exception:
            return None

    def _register_name(self, excel_name: str, actual_name: str):
        """Store excel→actual name mapping (both normalized and raw keys)."""
        key = normalize_ws_name(excel_name)
        self.ws_name_map[key] = actual_name
        self.ws_name_map[excel_name] = actual_name
        if actual_name != excel_name:
            info(yellow(f"           ⚑ Name remapped: '{excel_name}' → '{actual_name}'"))

    def remap_file_location(self, location: str) -> str:
        """Replace any workspace name segment in a file location path with the
        actual name that was created/found in OTCS."""
        if not self.ws_name_map:
            return location
        parts = location.replace("\\", "/").split("/")
        remapped = []
        for part in parts:
            norm = normalize_ws_name(part)
            if norm in self.ws_name_map:
                remapped.append(self.ws_name_map[norm])
            elif part in self.ws_name_map:
                remapped.append(self.ws_name_map[part])
            else:
                remapped.append(part)
        result = "\\".join(remapped)
        if result != location:
            dbg(f"    ↳ Location remapped: '{location}' → '{result}'")
        return result

    def apply_category(self, node_id: int, cat_values: dict) -> bool:
        if self.cfg["dry_run"]: return True
        cat_id = self.cfg["category_id"]
        try:
            self._put(f"/api/v2/nodes/{node_id}/categories/{cat_id}",
                      data={"body": json.dumps({"categories": self._build_cat_data(cat_values)})})
            return True
        except requests.HTTPError as e:
            warn(f"    ⚠ Category update failed: HTTP "
                 f"{e.response.status_code if e.response else '?'}  "
                 f"{e.response.text[:200] if e.response else str(e)}")
            return False

    def upload_file(self, parent_id: int, f: dict) -> str:
        name, local_path = f["title"], f["local_path"]
        mime = get_mime(local_path, f["mime_hint"])
        if self.cfg["dry_run"]:
            dbg(f"    ↳ [dry-run] Would upload: {name}"); return "uploaded"
        if not os.path.isfile(local_path):
            err(f"    ✖ Source file not found: {local_path}"); return "failed"
        existing_id = self._find_child(parent_id, name)
        if existing_id:
            if self.cfg["on_duplicate"] == "skip":
                dbg(f"    ↳ Already exists, skipping: {name}"); return "skipped"
            if self.cfg["on_duplicate"] == "new_version":
                return self._add_version(existing_id, local_path, mime, name)
        try:
            time.sleep(self.cfg["request_delay"])
            with open(local_path, "rb") as fh:
                r = self.session.post(f"{self.base}/api/v2/nodes", verify=self.ssl,
                                      timeout=_REQUEST_TIMEOUT,
                                      data={"type":144,"parent_id":parent_id,"name":name},
                                      files={"file":(name, fh, mime)})
            if r.status_code in (200,201):
                nid = r.json()["results"]["data"]["properties"]["id"]
                dbg(f"    ↳ Uploaded (id={nid})"); return "uploaded"
            warn(f"    ⚠ Upload HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        except requests.Timeout:
            err(f"    ✖ Upload timed out after {_REQUEST_TIMEOUT}s")
        except requests.HTTPError as e:
            err(f"    ✖ Upload failed HTTP "
                f"{e.response.status_code if e.response else '?'}: "
                f"{e.response.text[:200] if e.response else str(e)}")
        except OSError as e:
            err(f"    ✖ Cannot read file: {e}")
        return "failed"

    def _add_version(self, node_id: int, local_path: str, mime: str, name: str) -> str:
        try:
            time.sleep(self.cfg["request_delay"])
            with open(local_path, "rb") as fh:
                r = self.session.post(f"{self.base}/api/v2/nodes/{node_id}/versions",
                                      verify=self.ssl, timeout=_REQUEST_TIMEOUT,
                                      files={"file":(name, fh, mime)})
            if r.status_code in (200,201):
                dbg(f"    ↳ New version added"); return "versioned"
            warn(f"    ⚠ Add version HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        except requests.Timeout:
            err(f"    ✖ Add version timed out after {_REQUEST_TIMEOUT}s")
        except requests.HTTPError as e:
            err(f"    ✖ Add version failed: {e.response.text[:200] if e.response else str(e)}")
        except OSError as e:
            err(f"    ✖ Cannot read file: {e}")
        return "failed"

    def record(self, key): self._stats[key] += 1
    def stats(self):       return self._stats


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def run():
    parser = argparse.ArgumentParser(
        prog="cs_importer",
        description="OpenText Content Server – Generic Importer (Workspaces + Files)",
    )
    parser.add_argument("xlsx", help="Path to the input XLSX file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate import without making any changes")
    args = parser.parse_args()

    # Validate XLSX before anything else
    try:
        xlsx_path = validate_xlsx_path(args.xlsx)
    except (FileNotFoundError, ValueError) as e:
        print(red(f"✖ {e}"))
        sys.exit(1)

    cfg = load_config(xlsx_path, args.dry_run)
    full_log, error_log = setup_logging(xlsx_path)
    t0 = datetime.now()

    json_path = str(Path(xlsx_path).with_suffix(".json"))

    # Banner
    info("")
    info(bold(cyan("╔══════════════════════════════════════════════════════════════════╗")))
    info(bold(cyan("║  OpenText CS – Generic Importer  (Workspaces + Files)           ║")))
    info(bold(cyan("╚══════════════════════════════════════════════════════════════════╝")))
    info(f"  BASE_DIR     : {BASE_DIR}")
    info(f"  XLSX         : {xlsx_path}")
    info(f"  Config       : {json_path}")
    info(f"  Full log     : {full_log}")
    info(f"  Error log    : {error_log}")
    info(f"  Target       : {cfg['base_url']}")
    info(f"  Template ID  : {cfg['template_id']}")
    info(f"  Wksp Type ID : {cfg['wksp_type_id']}")
    info(f"  Category ID  : {cfg['category_id']}")
    _roots = get_roots(cfg)
    info(f"  File root(s) : {'; '.join(_roots) if _roots else '(XLSX paths used as-is)'}")
    # Warn about missing file roots — do not hard-fail
    for root in _roots:
        if not Path(root).exists():
            warn(f"  ⚠ File root does not exist: {root}")
    if cfg.get("recursive_file_search"):
        info(f"  Recursive    : enabled")
    if not cfg.get("auto_detect_file_columns", True):
        info(f"  Col detect   : manual only")
    info(f"  On duplicate : {cfg['on_duplicate']}")
    if cfg["dry_run"]: info(bold(yellow("  MODE         : DRY RUN")))
    info("")

    # Step 1 – parse
    info(bold("Step 1/4  Reading XLSX …"))
    try:
        workspaces = parse_workspaces(cfg)
        files      = parse_files(cfg)
    except Exception as e:
        err(f"  ✖ Error reading XLSX: {e}")
        if LOG: LOG.debug(traceback.format_exc())
        sys.exit(1)
    info(green(f"  ✔ {len(workspaces)} workspace rows, {len(files)} file rows"))

    if workspaces:
        info(bold("\n  Workspaces preview (first 5):"))
        for ws in workspaces[:5]:
            info(f"    • {bold(ws['title'])}")
        if len(workspaces) > 5: info(dim(f"    … and {len(workspaces)-5} more"))

    if files:
        info(bold("\n  Files preview (first 5):"))
        for f in files[:5]:
            exists_tag = green("✔") if f["exists"] else red("✖ MISSING")
            info(f"    • {f['title'][:55]}  [{exists_tag}]")
            if f["original_src"] != f["local_path"]:
                info(dim(f"      src  : {f['original_src'] or '(none)'}"))
            info(dim(f"      path : {f['local_path'] or '(unresolved)'}"))
            info(dim(f"      via  : {f['resolved_by']}"))
        if len(files) > 5: info(dim(f"    … and {len(files)-5} more"))
    info("")

    if not workspaces and not files:
        info(yellow("  Nothing to import.")); sys.exit(0)

    # Step 2 – auth
    info(bold("Step 2/4  Connecting …"))
    client = CSClient(cfg)
    if not cfg["dry_run"]: client.authenticate()
    else: info(yellow("  [dry-run] Skipping auth"))
    info("")

    # Step 3 – workspaces
    if workspaces:
        total = len(workspaces)
        info(bold(f"Step 3/4  Creating {total} workspace(s) …"))
        info("─" * 68)
        for i, ws in enumerate(workspaces, 1):
            print(f"\r  {progress_bar(i-1, total)}", end="", flush=True); print()
            info(f"  [{i:>3}/{total}]  {bold(ws['title'])}")
            info(dim(f"           Location : {ws['location']}"))
            try:
                parent_id = client.resolve_path(ws["location"])
                nid, created, actual_name = client.create_or_get_workspace(parent_id, ws["title"], ws["cat_values"])
                if created:
                    info(green(f"           ✔ Created  node_id={nid}  name='{actual_name}'"))
                    info(green( "           ✔ Category set at creation"))
                    client.record("ws_created")
                else:
                    info(yellow(f"           ⚑ Already exists  node_id={nid}  name='{actual_name}'"))
                    client.record("ws_existing")
                    if nid != -1 and client.apply_category(nid, ws["cat_values"]):
                        info(green("           ✔ Category updated"))
            except Exception as e:
                err(f"           ✖ {e}")
                if LOG: LOG.debug(traceback.format_exc())
                client.record("ws_failed")
        print(f"\r  {progress_bar(total, total)}"); info("")
    else:
        info(bold("Step 3/4  No workspaces — skipping")); info("")

    # Step 4 – files
    if files:
        total = len(files)
        info(bold(f"Step 4/4  Uploading {total} file(s) …"))
        info("─" * 68)
        for i, f in enumerate(files, 1):
            print(f"\r  {progress_bar(i-1, total)}", end="", flush=True); print()
            info(f"  [{i:>3}/{total}]  {bold(f['title'][:60])}")
            original_loc = f["location"]
            remapped_loc = client.remap_file_location(original_loc)
            if remapped_loc != original_loc:
                info(yellow(f"           ↳ Location remapped to: {remapped_loc}"))
            info(dim(f"           Location : {remapped_loc}"))
            info(dim(f"           Source   : {f['local_path']}"))
            if f.get("original_src") and f["original_src"] != f["local_path"]:
                info(dim(f"           Original : {f['original_src']}  [{f.get('resolved_by','')}]"))
            elif f.get("resolved_by"):
                info(dim(f"           Resolved : [{f['resolved_by']}]"))
            try:
                parent_id = client.resolve_path(remapped_loc)
                result    = client.upload_file(parent_id, f)
                if   result == "uploaded":  info(green("           ✔ Uploaded"));          client.record("f_uploaded")
                elif result == "versioned": info(green("           ✔ New version added")); client.record("f_versioned")
                elif result == "skipped":   info(yellow("           ⚑ Skipped"));          client.record("f_skipped")
                else:                                                                       client.record("f_failed")
            except Exception as e:
                err(f"           ✖ {e}")
                if LOG: LOG.debug(traceback.format_exc())
                client.record("f_failed")
        print(f"\r  {progress_bar(total, total)}"); info("")
    else:
        info(bold("Step 4/4  No files — skipping")); info("")

    # Summary
    elapsed = (datetime.now() - t0).total_seconds()
    s = client.stats()
    info(bold(cyan("╔══════════════════════════════════════════════════════╗")))
    info(bold(cyan("║  Import Summary                                      ║")))
    info(bold(cyan("╠══════════════════════════════════════════════════════╣")))
    for label, key in [("Workspaces created","ws_created"),("Workspaces existed","ws_existing"),
                       ("Workspace errors","ws_failed"),("Files uploaded","f_uploaded"),
                       ("Files versioned","f_versioned"),("Files skipped","f_skipped"),
                       ("File errors","f_failed")]:
        info(f"║  {label:<30} {s[key]:>6}              ║")
    info(f"║  {'Elapsed':<30} {elapsed:>5.1f}s              ║")
    info(bold(cyan("╚══════════════════════════════════════════════════════╝")))
    info("")
    info(f"  Full log   : {full_log}")
    info(f"  Error log  : {error_log}")
    info("")

    total_errors = s["ws_failed"] + s["f_failed"]
    if total_errors:
        err(f"  ⚠ {total_errors} error(s) — check {error_log}")
        sys.exit(1)
    else:
        info(green("  ✔ All done!"))


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        if LOG:
            LOG.critical(f"Fatal unhandled error:\n{tb}")
        print(red(f"\n✖ Fatal error — please send the log file to support:\n{tb}"))
        if sys.stdout.isatty():
            input("Press ENTER to exit...")
        sys.exit(2)
