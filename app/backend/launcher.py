# -*- coding: utf-8 -*-
"""Crash-safe entry point for the portable one-file EXE."""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path


def _preferred_log_dir() -> Path:
    if os.name == "nt":
        return Path(r"D:\XrayRegistrationData")
    return Path(tempfile.gettempdir()) / "XrayRegistrationData"


def _write_startup_error(text: str) -> Path | None:
    candidates = [_preferred_log_dir(), Path(tempfile.gettempdir())]
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "startup_error.log"
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[{stamp}]\n{text}\n")
            return path
        except Exception:
            continue
    return None


def _show_error_dialog(message: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "XrayRegistration v31 startup error", 0x10)
    except Exception:
        pass


def main() -> int:
    try:
        from server import main as server_main
        server_main()
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            return 0
        detail = traceback.format_exc()
    except BaseException:
        code = 1
        detail = traceback.format_exc()

    log_path = _write_startup_error(detail)
    message = "程式啟動失敗。"
    if log_path is not None:
        message += f"\n錯誤紀錄：{log_path}"
    else:
        message += "\n無法寫入 startup_error.log。"
    message += "\n\n" + detail.splitlines()[-1] if detail.splitlines() else ""
    print(detail, file=sys.stderr)
    print(message, file=sys.stderr)
    _show_error_dialog(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
