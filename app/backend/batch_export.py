# -*- coding: utf-8 -*-
"""Create the batch inspection workbook returned by the local API."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Iterable, List

import xlsxwriter


RESULT_COLUMNS = [
    ("Filename / 檔名", "filename", "text"),
    ("Overall Status / 總狀態", "status", "status"),
    ("Pair Check / 間距檢查", "pair_status", "status"),
    ("R1-R2 Gap / 距離 (px)", "top_distance", "number"),
    ("R3-R4 Gap / 距離 (px)", "bottom_distance", "number"),
    ("Shift Check / 位移檢查", "shift_status", "status"),
    ("Max Shift Spread / 最大位移離散 (px)", "shift_spread", "number"),
    ("X Shift Spread / X 位移離散 (px)", "shift_spread_x", "number"),
    ("Y Shift Spread / Y 位移離散 (px)", "shift_spread_y", "number"),
    ("R1 dx (px)", "r1_dx", "number"),
    ("R1 dy (px)", "r1_dy", "number"),
    ("R2 dx (px)", "r2_dx", "number"),
    ("R2 dy (px)", "r2_dy", "number"),
    ("R3 dx (px)", "r3_dx", "number"),
    ("R3 dy (px)", "r3_dy", "number"),
    ("R4 dx (px)", "r4_dx", "number"),
    ("R4 dy (px)", "r4_dy", "number"),
    ("Overall Void / 空焊占比", "void_rate", "percent"),
    ("Solder Indication / 焊錫顯影", "solder_rate", "percent"),
    ("R1 空焊占比", "r1_void", "percent"),
    ("R2 空焊占比", "r2_void", "percent"),
    ("R3 空焊占比", "r3_void", "percent"),
    ("R4 空焊占比", "r4_void", "percent"),
    ("G", "g", "number3"),
    ("S", "s", "number3"),
    ("Orientation / 方向", "orientation", "text"),
    ("Registration Score / 配準分數", "registration_score", "number4"),
    ("Processing Time / 處理時間 (ms)", "processing_ms", "number"),
    ("Result Image / 結果圖", "result_image", "text"),
    ("Error or Warning / 錯誤警告", "message", "text"),
]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _row_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    error = str(item.get("error") or "")
    if error:
        return {
            "filename": str(item.get("filename") or ""),
            "status": "ERROR",
            "message": error,
            "processing_ms": _number(item.get("processingMs")),
            "result_image": str(item.get("resultImagePath") or ""),
        }
    result = item.get("result") or {}
    metrics = result.get("metrics") or {}
    checks = metrics.get("qualityChecks") or {}
    pair = checks.get("pairDistance") or {}
    relative = checks.get("relativeShift") or {}
    solder = metrics.get("solderIndication") or {}
    optimization = solder.get("positionOptimization") or solder.get("horizontalOptimization") or {}
    dx_by_id = {
        int(value.get("id", 0)): _number(value.get("dxPx"))
        for value in optimization.get("regions") or []
        if isinstance(value, dict)
    }
    dy_by_id = {
        int(value.get("id", 0)): _number(value.get("dyPx"))
        for value in optimization.get("regions") or []
        if isinstance(value, dict)
    }
    regions = {
        int(value.get("id", 0)): value
        for value in solder.get("regions") or []
        if isinstance(value, dict)
    }
    row: Dict[str, Any] = {
        "filename": str(item.get("filename") or ""),
        "status": str(metrics.get("overallStatus") or "ERROR"),
        "pair_status": str(pair.get("status") or "UNAVAILABLE"),
        "top_distance": _number(pair.get("topDistancePx")),
        "bottom_distance": _number(pair.get("bottomDistancePx")),
        "shift_status": str(relative.get("status") or "UNAVAILABLE"),
        "shift_spread": _number(relative.get("spreadPx")),
        "shift_spread_x": _number(relative.get("spreadXPx")),
        "shift_spread_y": _number(relative.get("spreadYPx")),
        "void_rate": _number(solder.get("emptyAreaRate")),
        "solder_rate": _number(solder.get("overallRate")),
        "g": _number(metrics.get("G")),
        "s": _number(solder.get("solderReferenceGray")),
        "orientation": str(metrics.get("orientation") or ""),
        "registration_score": _number(metrics.get("registrationScore")),
        "processing_ms": _number(metrics.get("processingMs") or item.get("processingMs")),
        "result_image": str(item.get("resultImagePath") or ""),
        "message": str(metrics.get("warning") or ""),
    }
    for region_id in (1, 2, 3, 4):
        row[f"r{region_id}_dx"] = dx_by_id.get(region_id)
        row[f"r{region_id}_dy"] = dy_by_id.get(region_id)
        rate = _number((regions.get(region_id) or {}).get("rate"))
        row[f"r{region_id}_void"] = None if rate is None else 1.0 - rate
    return row


def build_xlsx_bytes(
    batch_items: Iterable[Dict[str, Any]],
    param_values: Dict[str, Any],
) -> bytes:
    """Build a formatted in-memory XLSX with Summary and Results sheets."""
    rows: List[Dict[str, Any]] = [_row_from_item(item) for item in batch_items]
    counts = Counter(str(row.get("status") or "ERROR") for row in rows)
    formula_last_row = max(2, len(rows) + 1)
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties({
        "title": "X-ray Four-R Batch Inspection",
        "subject": "Batch inspection results",
        "author": "X-ray Registration v32",
        "comments": "Generated by the local batch API.",
    })
    title_fmt = workbook.add_format({
        "bold": True,
        "font_size": 16,
        "font_color": "#FFFFFF",
        "bg_color": "#1F4E78",
        "align": "left",
        "valign": "vcenter",
    })
    section_fmt = workbook.add_format({
        "bold": True,
        "font_color": "#FFFFFF",
        "bg_color": "#4472C4",
        "border": 1,
    })
    label_fmt = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
    value_fmt = workbook.add_format({"border": 1})
    header_fmt = workbook.add_format({
        "bold": True,
        "font_color": "#FFFFFF",
        "bg_color": "#1F4E78",
        "border": 1,
        "align": "center",
        "valign": "vcenter",
        "text_wrap": True,
    })
    text_fmt = workbook.add_format({"border": 1, "valign": "top"})
    number_fmt = workbook.add_format({"border": 1, "num_format": "0.0"})
    number3_fmt = workbook.add_format({"border": 1, "num_format": "0.000"})
    number4_fmt = workbook.add_format({"border": 1, "num_format": "0.0000"})
    percent_fmt = workbook.add_format({"border": 1, "num_format": "0.0%"})
    pass_fmt = workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})
    warning_fmt = workbook.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500"})
    fail_fmt = workbook.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})
    error_fmt = workbook.add_format({"bg_color": "#F4CCCC", "font_color": "#660000"})

    summary = workbook.add_worksheet("Summary")
    summary.hide_gridlines(2)
    summary.set_column("A:A", 30)
    summary.set_column("B:B", 25)
    summary.set_column("C:D", 18)
    summary.set_row(0, 26)
    summary.merge_range("A1:D1", "X-ray Four-R Batch Inspection / 四 R 批量檢查", title_fmt)
    summary.write("A3", "Generated at / 產生時間", label_fmt)
    summary.write_string("B3", datetime.now().astimezone().isoformat(timespec="seconds"), value_fmt)
    summary.write("A4", "Image count / 圖片總數", label_fmt)
    summary.write_number("B4", len(rows), value_fmt)
    summary.write("A6", "Status counts / 狀態統計", section_fmt)
    for row_index, status in enumerate(("PASS", "WARNING", "FAIL", "ERROR"), start=6):
        summary.write(row_index, 0, status, label_fmt)
        cached = int(counts.get(status, 0))
        summary.write_formula(
            row_index,
            1,
            f'=COUNTIF(Results!$B$2:$B${formula_last_row},"{status}")',
            value_fmt,
            cached,
        )
    param_start = 12
    summary.write(param_start, 0, "Batch parameters / 本批參數", section_fmt)
    summary.write(param_start + 1, 0, "Parameter / 參數鍵", header_fmt)
    summary.write(param_start + 1, 1, "Value / 值", header_fmt)
    for offset, (key, value) in enumerate(sorted((param_values or {}).items()), start=param_start + 2):
        summary.write_string(offset, 0, str(key), text_fmt)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            summary.write_number(offset, 1, float(value), number3_fmt)
        else:
            summary.write_string(offset, 1, str(value), text_fmt)

    results = workbook.add_worksheet("Results")
    results.hide_gridlines(2)
    results.freeze_panes(1, 2)
    results.set_row(0, 34)
    results.set_column(0, 0, 30)
    results.set_column(1, 2, 24)
    results.set_column(3, 6, 24)
    results.set_column(7, 10, 15)
    results.set_column(11, 16, 21)
    results.set_column(17, 18, 12)
    results.set_column(19, 19, 18)
    results.set_column(20, 21, 24)
    results.set_column(22, 22, 38)
    results.set_column(23, 23, 46)
    for column_index, (header, _key, _kind) in enumerate(RESULT_COLUMNS):
        results.write(0, column_index, header, header_fmt)
    for row_index, row in enumerate(rows, start=1):
        for column_index, (_header, key, kind) in enumerate(RESULT_COLUMNS):
            value = row.get(key)
            if value is None:
                results.write_blank(row_index, column_index, None, text_fmt)
            elif kind in {"number", "number3", "number4", "percent"}:
                fmt = {
                    "number": number_fmt,
                    "number3": number3_fmt,
                    "number4": number4_fmt,
                    "percent": percent_fmt,
                }[kind]
                results.write_number(row_index, column_index, float(value), fmt)
            else:
                results.write_string(row_index, column_index, str(value), text_fmt)
    last_row = max(1, len(rows))
    results.autofilter(0, 0, last_row, len(RESULT_COLUMNS) - 1)
    for first_col in (1, 2, 5):
        results.conditional_format(1, first_col, last_row, first_col, {
            "type": "text", "criteria": "containing", "value": "PASS", "format": pass_fmt,
        })
        results.conditional_format(1, first_col, last_row, first_col, {
            "type": "text", "criteria": "containing", "value": "WARNING", "format": warning_fmt,
        })
        results.conditional_format(1, first_col, last_row, first_col, {
            "type": "text", "criteria": "containing", "value": "FAIL", "format": fail_fmt,
        })
        results.conditional_format(1, first_col, last_row, first_col, {
            "type": "text", "criteria": "containing", "value": "ERROR", "format": error_fmt,
        })
    workbook.close()
    return output.getvalue()
