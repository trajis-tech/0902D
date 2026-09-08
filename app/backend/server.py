# -*- coding: utf-8 -*-
"""X-ray four-R geometry and solder inspection local backend (v32).

- No Flask
- Python standard library HTTP server
- OpenCV / NumPy pipeline

Run:
    python app/backend/server.py

Open:
    http://127.0.0.1:8768
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import traceback
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from config_loader import app_version, config_path, config_sha256, load_config, section, ui_templates

_CONFIG = load_config()
_RUNTIME_CFG = section("runtime")
HOST = str(_RUNTIME_CFG["host"])
PORT = int(_RUNTIME_CFG["port"])
APP_VERSION = app_version()
_PROCESS_LOCK = threading.Lock()

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BUNDLE_ROOT = Path(sys._MEIPASS)
    APP_DIR = BUNDLE_ROOT / "app"
    BACKEND_DIR = APP_DIR / "backend"
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    BACKEND_DIR = Path(__file__).resolve().parent
    APP_DIR = BACKEND_DIR.parent
    PROJECT_ROOT = APP_DIR.parent
PORTABLE_SITE_PACKAGES = PROJECT_ROOT / "portable_python" / "Lib" / "site-packages"
if os.name == "nt" and PORTABLE_SITE_PACKAGES.is_dir() and str(PORTABLE_SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(PORTABLE_SITE_PACKAGES))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from pipeline import run_pipeline, save_png  # noqa: E402
from batch_export import build_xlsx_bytes  # noqa: E402

FRONTEND_DIR = APP_DIR / "frontend"


def _runtime_data_root() -> Path:
    raw = os.path.expandvars(os.path.expanduser(str(_RUNTIME_CFG["dataRoot"]).strip()))
    root = Path(raw)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


DATA_ROOT = _runtime_data_root()
OUTPUT_DIR = DATA_ROOT / "results"
TEMP_DIR = DATA_ROOT / "temp"
UPLOADS_DIR = DATA_ROOT / "uploads"
SINGLE_UPLOADS_DIR = UPLOADS_DIR / "single"
BATCH_UPLOADS_DIR = UPLOADS_DIR / "batch"

try:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    SINGLE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    raise RuntimeError(
        f"Cannot create runtime data folders under {DATA_ROOT}. "
        "Edit runtime.dataRoot in algorithm_config.json and restart."
    ) from exc

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MAX_UPLOAD_BYTES = int(float(_RUNTIME_CFG["maxSingleUploadMb"]) * 1024 * 1024)
MAX_BATCH_UPLOAD_BYTES = int(float(_RUNTIME_CFG["maxBatchUploadMb"]) * 1024 * 1024)
MAX_BATCH_FILES = int(_RUNTIME_CFG["maxBatchFiles"])
MAX_JSON_BYTES = int(float(_RUNTIME_CFG["maxJsonMb"]) * 1024 * 1024)
DEFAULT_OUTPUT_LIST_LIMIT = int(_RUNTIME_CFG["defaultOutputListLimit"])


def json_ready(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_ready(v) for v in obj]
    mod = str(getattr(type(obj), "__module__", "") or "")
    if mod.startswith("numpy"):
        ndim = getattr(obj, "ndim", None)
        if ndim not in (None, 0) and hasattr(obj, "tolist"):
            return json_ready(obj.tolist())
        if hasattr(obj, "item"):
            return obj.item()
    return obj


def json_response(handler: BaseHTTPRequestHandler, data: Dict[str, Any], status: int = 200) -> None:
    payload = json.dumps(json_ready(data), ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.end_headers()
    handler.wfile.write(payload)


def xlsx_response(
    handler: BaseHTTPRequestHandler,
    payload: bytes,
    filename: str,
    processed_count: int,
) -> None:
    handler.send_response(200)
    handler.send_header(
        "Content-Type",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("X-Processed-Count", str(int(processed_count)))
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.end_headers()
    handler.wfile.write(payload)


def read_request_json(handler: BaseHTTPRequestHandler, max_bytes: int = MAX_JSON_BYTES) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length < 0:
        raise ValueError("Invalid Content-Length.")
    if length > int(max_bytes):
        raise ValueError(f"Request body too large ({length} bytes).")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def check_package(name: str) -> Dict[str, Any]:
    import_name = "cv2" if name == "opencv" else name
    try:
        mod = __import__(import_name)
        version = str(getattr(mod, "__version__", ""))
        if import_name == "cv2" and not version:
            version = str(getattr(mod, "version", ""))
        return {
            "ok": True,
            "version": version,
            "path": str(getattr(mod, "__file__", "")),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def sanitize_upload_name(name: str) -> str:
    text = Path(str(name or "upload")).name
    text = re.sub(r"[^\w.\u4e00-\u9fff\- ]+", "_", text).strip(" ._")
    return text or "upload"


def extract_multipart_parts(
    body: bytes,
    boundary: str,
) -> Tuple[Dict[str, str], List[Tuple[str, str, bytes]]]:
    if not boundary:
        raise ValueError("Missing multipart boundary.")
    boundary_bytes = ("--" + boundary).encode("utf-8")
    fields: Dict[str, str] = {}
    files: List[Tuple[str, str, bytes]] = []
    for raw_part in body.split(boundary_bytes):
        if not raw_part or raw_part.startswith(b"--"):
            continue
        if raw_part.startswith(b"\r\n"):
            raw_part = raw_part[2:]
        if raw_part.endswith(b"\r\n"):
            raw_part = raw_part[:-2]
        header_end = raw_part.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        header_blob = raw_part[:header_end].decode("utf-8", errors="replace")
        content = raw_part[header_end + 4 :]
        disp = ""
        for line in header_blob.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disp = line
                break
        if not disp:
            continue
        name_match = re.search(r'name="(?P<name>[^"]+)"', disp)
        if not name_match:
            continue
        field_name = name_match.group("name")
        fn_star = re.search(r"filename\*=(?:UTF-8''|utf-8'')(?P<fn>[^;]+)", disp)
        fn_plain = re.search(r'filename="(?P<fn>[^"]*)"', disp)
        if fn_star:
            filename = unquote(fn_star.group("fn"))
        elif fn_plain:
            filename = fn_plain.group("fn")
        else:
            fields[field_name] = content.decode("utf-8", errors="strict")
            continue
        files.append((field_name, filename or "upload", bytes(content)))
    return fields, files


def extract_multipart_file(body: bytes, boundary: str, field_name: str = "file") -> Tuple[str, bytes]:
    _fields, files = extract_multipart_parts(body, boundary)
    for name, filename, content in files:
        if name == field_name:
            return filename, content
    raise ValueError(f"Missing upload field: {field_name}")


def relative_posix(path: Path) -> str:
    return str(path.resolve().relative_to(DATA_ROOT.resolve())).replace("\\", "/")


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def sample_search_roots() -> list:
    roots = []
    xdir = PROJECT_ROOT.parent.resolve() / "x"
    if xdir.is_dir():
        roots.append(xdir)
    roots.append(PROJECT_ROOT.resolve())
    parent = PROJECT_ROOT.parent.resolve()
    if parent not in roots:
        roots.append(parent)
    return roots


def sample_source_path(name: str) -> Path:
    raw = str(name or "").replace("\\", "/").strip()
    if not raw or "/" in raw or raw in {".", ".."}:
        raise ValueError("範例檔名不合法。")
    base = Path(raw).name
    if base != raw:
        raise ValueError("範例檔名不合法。")
    for root in sample_search_roots():
        src = (root / base).resolve()
        if not is_within(src, root) or src.parent.resolve() != root:
            continue
        if src.is_file() and src.suffix.lower() in IMAGE_SUFFIXES:
            return src
    raise ValueError("找不到範例影像。")


def resolve_under(root: Path, rel: str) -> Path:
    text = str(rel or "").replace("\\", "/").lstrip("/")
    path = (DATA_ROOT / text).resolve()
    if not is_within(path, root):
        raise ValueError("Path is outside the allowed directory.")
    return path


def list_sample_images() -> list:
    items = []
    seen = set()
    for root in sample_search_roots():
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if p.name in seen:
                continue
            seen.add(p.name)
            items.append({"name": p.name, "path": p.name, "size": p.stat().st_size})
    def _sample_key(item: dict) -> tuple:
        name = str(item.get("name") or "")
        if name in {"1.jpg", "2.jpg", "3.jpg"}:
            return (0, name)
        return (1, name.lower())
    items.sort(key=_sample_key)
    return items


def list_output_images(limit: int | None = None) -> Dict[str, Any]:
    if limit is None:
        limit = DEFAULT_OUTPUT_LIST_LIMIT
    files = []
    for p in sorted(OUTPUT_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True):
        files.append(
            {
                "name": p.name,
                "path": relative_posix(p),
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
        if len(files) >= limit:
            break
    return {"ok": True, "files": files}


def _multipart_boundary(content_type: str) -> str:
    match = re.search(r"boundary=(?P<b>[^;]+)", str(content_type or ""))
    if not match:
        raise ValueError("Missing multipart boundary.")
    return match.group("b").strip().strip('"')


def _save_uploaded_image(
    filename: str,
    raw: bytes,
    destination_dir: Path,
) -> Tuple[str, Path]:
    original_name = sanitize_upload_name(filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"{original_name}：僅支援 jpg / png / bmp / tif / webp。")
    if not raw:
        raise ValueError(f"{original_name}：上傳檔為空。")
    unique = uuid.uuid4().hex[:12]
    saved_name = sanitize_upload_name(f"{Path(original_name).stem}_{unique}{suffix}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    save_path = (destination_dir / saved_name).resolve()
    if not is_within(save_path, destination_dir):
        raise ValueError("Invalid upload path.")
    save_path.write_bytes(raw)
    return original_name, save_path


def _run_batch(
    inputs: List[Tuple[str, Path]],
    param_values: Dict[str, Any],
) -> Tuple[bytes, str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    batch_dir = OUTPUT_DIR / f"batch_{stamp}"
    batch_dir.mkdir(parents=True, exist_ok=False)
    items: List[Dict[str, Any]] = []
    for index, (original_name, image_path) in enumerate(inputs, start=1):
        item: Dict[str, Any] = {"filename": original_name}
        try:
            result = run_pipeline(str(image_path), param_values)
            result_image = result.pop("resultImageBgr")
            result.pop("stageImages", None)
            output_name = sanitize_upload_name(
                f"{index:04d}_{Path(original_name).stem}_result.png",
            )
            output_path = batch_dir / output_name
            save_png(str(output_path), result_image)
            item.update({
                "result": result,
                "resultImagePath": relative_posix(output_path),
            })
        except Exception as exc:
            item["error"] = str(exc)
        items.append(item)
    report_params = dict(param_values or {})
    report_params["__configVersion"] = int(_CONFIG["configVersion"])
    report_params["__configSha256"] = config_sha256()
    workbook_bytes = build_xlsx_bytes(items, report_params)
    workbook_name = f"batch_results_{stamp}.xlsx"
    (OUTPUT_DIR / workbook_name).write_bytes(workbook_bytes)
    return workbook_bytes, workbook_name


class Handler(BaseHTTPRequestHandler):
    server_version = "XrayRegistration/0.4"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in ("/", "/index.html"):
            return self.serve_file(FRONTEND_DIR / "index.html")

        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path.startswith("/frontend/"):
            rel = path.removeprefix("/frontend/").lstrip("/")
            if not rel:
                return self.serve_file(FRONTEND_DIR / "index.html")
            return self.serve_file(FRONTEND_DIR / rel)

        if path.lstrip("/") in {"styles.css", "app.js"}:
            return self.serve_file(FRONTEND_DIR / path.lstrip("/"))

        if path == "/api/health":
            return json_response(self, {
                "ok": True,
                "appVersion": APP_VERSION,
                "version": APP_VERSION,
                "configVersion": int(_CONFIG["configVersion"]),
                "configSha256": config_sha256(),
                "python": {
                    "executable": sys.executable,
                    "version": sys.version,
                },
                "paths": {
                    "projectRoot": str(PROJECT_ROOT),
                    "dataRoot": str(DATA_ROOT),
                    "results": str(OUTPUT_DIR),
                    "temp": str(TEMP_DIR),
                    "uploads": str(UPLOADS_DIR),
                    "portableSitePackages": str(PORTABLE_SITE_PACKAGES),
                    "algorithmConfig": str(config_path()),
                },
                "packages": {
                    "numpy": check_package("numpy"),
                    "opencv": check_package("opencv"),
                    "xlsxwriter": check_package("xlsxwriter"),
                },
            })

        if path == "/api/param-templates":
            try:
                return json_response(self, {
                    "ok": True,
                    "templates": ui_templates(),
                    "version": int(_CONFIG["configVersion"]),
                    "configPath": str(config_path()),
                    "reloadPolicy": str(_CONFIG.get("reloadPolicy") or "restart_required"),
                })
            except Exception as exc:
                return json_response(self, {"ok": False, "error": str(exc), "templates": []}, status=500)

        if path == "/api/samples":
            return json_response(self, {"ok": True, "files": list_sample_images()})

        if path == "/api/output/images":
            qs = parse_qs(parsed.query or "")
            try:
                limit = int((qs.get("limit") or [str(DEFAULT_OUTPUT_LIST_LIMIT)])[0])
            except Exception:
                limit = DEFAULT_OUTPUT_LIST_LIMIT
            return json_response(self, list_output_images(limit=limit))

        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/upload-image":
            try:
                content_type = self.headers.get("Content-Type", "") or ""
                if "multipart/form-data" not in content_type:
                    return json_response(self, {
                        "ok": False,
                        "error": "Invalid content type. Expected multipart/form-data.",
                    }, status=400)
                boundary = _multipart_boundary(content_type)
                try:
                    content_length = int(self.headers.get("Content-Length", "") or 0)
                except Exception:
                    content_length = 0
                if content_length <= 0:
                    return json_response(self, {"ok": False, "error": "Missing Content-Length."}, status=400)
                if content_length > MAX_UPLOAD_BYTES:
                    return json_response(self, {"ok": False, "error": "Upload too large."}, status=413)
                body = self.rfile.read(content_length)
                _fields, uploaded_files = extract_multipart_parts(body, boundary)
                selected = [part for part in uploaded_files if part[0] == "file"]
                if len(uploaded_files) != 1 or len(selected) != 1:
                    raise ValueError("單張上傳只接受一個 file 欄位。")
                _field_name, filename, raw = selected[0]
                original_name, save_path = _save_uploaded_image(
                    filename, raw, SINGLE_UPLOADS_DIR,
                )
                saved_name = save_path.name
                rel_path = relative_posix(save_path)
                return json_response(self, {
                    "ok": True,
                    "path": rel_path,
                    "originalName": original_name,
                    "savedName": saved_name,
                })
            except Exception as exc:
                return json_response(self, {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }, status=400)

        if path == "/api/use-sample":
            try:
                payload = read_request_json(self)
                src = sample_source_path(str(payload.get("name") or ""))
                unique = uuid.uuid4().hex[:12]
                saved_name = sanitize_upload_name(f"{src.stem}_{unique}{src.suffix.lower()}")
                dest = SINGLE_UPLOADS_DIR / saved_name
                dest.write_bytes(src.read_bytes())
                return json_response(self, {
                    "ok": True,
                    "path": relative_posix(dest),
                    "originalName": src.name,
                    "savedName": saved_name,
                })
            except Exception as exc:
                return json_response(self, {"ok": False, "error": str(exc)}, status=400)

        if path == "/api/v1/batch":
            try:
                content_type = self.headers.get("Content-Type", "") or ""
                inputs: List[Tuple[str, Path]] = []
                param_values: Dict[str, Any] = {}
                if "multipart/form-data" in content_type:
                    boundary = _multipart_boundary(content_type)
                    content_length = int(self.headers.get("Content-Length", "0") or 0)
                    if content_length <= 0:
                        raise ValueError("Missing Content-Length.")
                    if content_length > MAX_BATCH_UPLOAD_BYTES:
                        return json_response(self, {
                            "ok": False,
                            "error": f"批量上傳超過 {_RUNTIME_CFG['maxBatchUploadMb']} MB。",
                        }, status=413)
                    fields, files = extract_multipart_parts(
                        self.rfile.read(content_length), boundary,
                    )
                    if any(part[0] != "files" for part in files):
                        raise ValueError("批量上傳只接受 files 欄位，不接受單張 file 欄位。")
                    selected = [part for part in files if part[0] == "files"]
                    if not selected:
                        raise ValueError("缺少批量圖片欄位 files。")
                    if len(selected) > MAX_BATCH_FILES:
                        raise ValueError(f"單批最多 {MAX_BATCH_FILES} 張圖片。")
                    raw_params = fields.get("paramValues", "{}")
                    parsed_params = json.loads(raw_params) if raw_params.strip() else {}
                    param_values = parsed_params if isinstance(parsed_params, dict) else {}
                    for _field_name, filename, raw in selected:
                        original_name, saved_path = _save_uploaded_image(
                            filename, raw, BATCH_UPLOADS_DIR,
                        )
                        inputs.append((original_name, saved_path))
                elif "application/json" in content_type:
                    payload = read_request_json(self)
                    raw_params = payload.get("paramValues") or {}
                    param_values = raw_params if isinstance(raw_params, dict) else {}
                    image_paths = payload.get("imagePaths") or []
                    if not isinstance(image_paths, list) or not image_paths:
                        raise ValueError("imagePaths 必須是非空陣列。")
                    if len(image_paths) > MAX_BATCH_FILES:
                        raise ValueError(f"單批最多 {MAX_BATCH_FILES} 張圖片。")
                    for entry in image_paths:
                        if isinstance(entry, dict):
                            relative = str(entry.get("path") or "")
                            display_name = sanitize_upload_name(
                                str(entry.get("name") or Path(relative).name),
                            )
                        else:
                            relative = str(entry or "")
                            display_name = sanitize_upload_name(Path(relative).name)
                        image_path = resolve_under(BATCH_UPLOADS_DIR, relative)
                        if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_SUFFIXES:
                            raise ValueError(f"找不到批量影像：{relative}")
                        inputs.append((display_name, image_path))
                else:
                    return json_response(self, {
                        "ok": False,
                        "error": "Expected multipart/form-data or application/json.",
                    }, status=415)
                with _PROCESS_LOCK:
                    workbook_bytes, workbook_name = _run_batch(inputs, param_values)
                return xlsx_response(self, workbook_bytes, workbook_name, len(inputs))
            except Exception as exc:
                return json_response(self, {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }, status=400)

        if path == "/api/process":
            try:
                payload = read_request_json(self)
                image_path = str(payload.get("imagePath") or "").strip()
                if not image_path:
                    return json_response(self, {"ok": False, "error": "缺少 imagePath。"}, status=400)
                try:
                    abs_path = resolve_under(SINGLE_UPLOADS_DIR, image_path)
                except Exception:
                    return json_response(self, {"ok": False, "error": "影像路徑不合法。"}, status=400)
                if not abs_path.is_file():
                    return json_response(self, {"ok": False, "error": "找不到已上傳的影像。"}, status=400)
                param_values = payload.get("paramValues") or {}
                if not isinstance(param_values, dict):
                    param_values = {}
                with _PROCESS_LOCK:
                    result = run_pipeline(str(abs_path), param_values)
                    result_image = result.pop("resultImageBgr")
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"inspection_{stamp}.png"
                    saved_path = OUTPUT_DIR / filename
                    save_png(str(saved_path), result_image)
                    save_png(str(TEMP_DIR / "latest_inspection.png"), result_image)
                result["filename"] = filename
                result["savedPath"] = relative_posix(saved_path)
                return json_response(self, result)
            except Exception as exc:
                return json_response(self, {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }, status=400)

        self.send_error(404, "Not found")

    def serve_file(self, path: Path) -> None:
        try:
            path = path.resolve()
            frontend_root = FRONTEND_DIR.resolve()
            if not is_within(path, frontend_root):
                self.send_error(403, "Forbidden")
                return
            if not path.exists() or not path.is_file():
                self.send_error(404, f"找不到檔案：{path.name}")
                return
            content = path.read_bytes()
            mime, _ = mimetypes.guess_type(str(path))
            if not mime:
                mime = "application/octet-stream"
            if path.suffix.lower() in {".html", ".css", ".js"}:
                mime += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            if path.suffix.lower() in {".html", ".css", ".js"}:
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_error(500, traceback.format_exc())


class ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = False


def main() -> None:
    index = FRONTEND_DIR / "index.html"
    if not index.is_file():
        print("=" * 72)
        print("ERROR: 找不到前端頁面")
        print(f"Expected : {index}")
        print("請在「Python Registration」資料夾執行 點此開始.bat")
        print("=" * 72)
        sys.stdout.flush()
        raise SystemExit(1)
    url = f"http://{HOST}:{PORT}"
    print("=" * 72)
    print("X-ray 四 R 幾何與焊錫檢查 v32")
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Data root    : {DATA_ROOT}")
    print(f"Python       : {sys.executable}")
    print(f"Open         : {url}")
    print("Stop         : Ctrl + C")
    print("=" * 72)
    sys.stdout.flush()
    try:
        httpd = ReusableServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"ERROR: cannot bind {url} ({exc})")
        print("若先前已啟動伺服器，請先關閉該視窗或改用已開啟的瀏覽器分頁。")
        sys.stdout.flush()
        raise SystemExit(1) from exc
    if bool(_RUNTIME_CFG["autoOpenBrowser"]):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
