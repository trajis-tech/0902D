# -*- coding: utf-8 -*-
"""Pipeline: product registration → shifted ideal ROIs → geometry QA → result.

Only analytic stadiums are used. Product registration is followed by X/Y
void minimization, pair-distance FAIL detection, and relative-shift WARNING.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, Tuple

import cv2
import numpy as np

from find_rect import find_package_rect
from config_loader import config_sha256, load_config, section, user_defaults, user_limits
from registration import (
    RegistrationError,
    canonicalize,
    detect_orientation,
    empty_capsules,
    registered_capsules,
    restore_orientation,
    map_contours_from_canonical,
)
from single_gauss import single_gaussian_map
from solder_indication import (
    DEFAULT_SURE_SOLDER_OFFSET_PCT,
    DEFAULT_SURE_VOID_OFFSET_PCT,
    DEFAULT_REFERENCE_CEILING_PCT,
    DEFAULT_IDEAL_SHIFT_MAX_PX,
    DEFAULT_PAIR_MIN_DISTANCE_PX,
    DEFAULT_RELATIVE_SHIFT_WARNING_PX,
    evaluate_geometry_quality,
    measure_solder_indication_shifted_ideal,
    render_solder_indication_stage,
)


_DEFAULT_PARAMS = user_defaults()
_PARAM_LIMITS = user_limits()
_PRODUCT_DETECTION_CFG = section("productDetection")
_POSITION_OPTIMIZATION_CFG = section("positionOptimization")

_FLOAT_KEYS = {
    "rect_mu_pct",
    "rect_sigma2",
    "min_area_ratio",
    "crop_ratio",
    "rect_bin_pct",
    "solder_reference_ceiling_pct",
    "solder_full_weight_offset_pct",
    "void_zero_weight_offset_pct",
    "ideal_shift_max_px",
    "pair_min_distance_px",
    "relative_shift_warning_px",
}


def coerce_params(params: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(params or {})
    for key in _FLOAT_KEYS:
        if key not in out or out[key] in ("", None):
            continue
        try:
            val = float(out[key])
        except (TypeError, ValueError):
            continue
        if val != val:
            continue
        out[key] = val

    defaults = dict(_DEFAULT_PARAMS)
    for key, default in defaults.items():
        if key not in out or out[key] in ("", None):
            out[key] = default
    for key, default in (
        ("solder_reference_ceiling_pct", DEFAULT_REFERENCE_CEILING_PCT),
        ("solder_full_weight_offset_pct", DEFAULT_SURE_SOLDER_OFFSET_PCT),
        ("void_zero_weight_offset_pct", DEFAULT_SURE_VOID_OFFSET_PCT),
    ):
        try:
            value = float(out[key])
        except (TypeError, ValueError):
            value = default
        if not np.isfinite(value):
            value = default
        out[key] = value
    for key, limits in _PARAM_LIMITS.items():
        if key not in out or key not in defaults:
            continue
        try:
            value = float(out.get(key, defaults[key]))
        except (TypeError, ValueError):
            value = float(defaults[key])
        if not np.isfinite(value):
            value = float(defaults[key])
        lower, upper = float(limits[0]), float(limits[1])
        out[key] = float(np.clip(value, lower, upper))
    out.pop("line_fill", None)
    out.pop("T_pct", None)
    out.pop("invert", None)
    out.pop("min_run", None)
    out.pop("solder_threshold_pct", None)
    return out


def load_gray(path: str) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        raise ValueError("影像檔為空。")
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("無法解碼影像。")
    if img.ndim == 2:
        gray = img
    else:
        gray = img[:, :, 0]
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(gray)


def _rect_corners(rect: Dict[str, Any]) -> np.ndarray:
    corners = np.asarray(rect["corners"], dtype=np.float64).reshape(-1, 2)
    if corners.shape[0] < 3:
        raise ValueError("矩形缺少四角座標。")
    return np.round(corners).astype(np.int32)


def outside_mean(gray: np.ndarray, rect: Dict[str, Any]) -> float:
    inside = np.zeros(gray.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(inside, _rect_corners(rect), 255)
    k = int(_PRODUCT_DETECTION_CFG["outsideDilateKernelPx"])
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    inside = cv2.dilate(inside, kernel)
    outside = inside == 0
    if not np.any(outside):
        raise ValueError("矩形外部沒有可計算的像素。")
    return float(np.mean(gray[outside].astype(np.float64)))


def strip_mean_g(gray: np.ndarray) -> Tuple[float, int]:
    """G = arithmetic mean of configurable top/bottom row bands."""
    height = int(gray.shape[0])
    band = max(
        1,
        int(round(height * float(_PRODUCT_DETECTION_CFG["backgroundStripFraction"]))),
    )
    top = gray[:band, :].ravel()
    bot = gray[-band:, :].ravel()
    g = float(np.concatenate((top, bot)).astype(np.float64).mean())
    return g, band


def _apply_pct(base: float, pct: float) -> float:
    return float(base) * (1.0 + float(pct) / 100.0)


def scaled_levels(gray_mean: float, params: Dict[str, Any]) -> Dict[str, float]:
    """Absolute μ_rect and find-rect thresh from G. Chain B scales itself."""
    thresh = min(255.0, max(0.0, _apply_pct(gray_mean, params["rect_bin_pct"])))
    return {
        "mu_rect": _apply_pct(gray_mean, params["rect_mu_pct"]),
        "rect_bin_thresh": thresh,
    }


def _find_rect_with_mu(
    gray: np.ndarray,
    params: Dict[str, Any],
    mu_rect: float,
) -> Tuple[np.ndarray, Dict[str, Any], bool]:
    y_rect = single_gaussian_map(gray, mu_rect, params["rect_sigma2"])
    rect = find_package_rect(gray, y_rect, params)
    from_binary = str(rect.get("source")) == "binary"
    return y_rect, rect, from_binary


def rotate_around_center(
    image: np.ndarray,
    cx: float,
    cy: float,
    angle_deg: float,
    interp: int = cv2.INTER_LINEAR,
) -> np.ndarray:
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((float(cx), float(cy)), float(angle_deg), 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=int(interp),
        borderMode=cv2.BORDER_REPLICATE,
    )


def center_crop(
    gray: np.ndarray,
    cx: float,
    cy: float,
    crop_w: int,
    crop_h: int,
) -> Tuple[np.ndarray, int, int, int, int]:
    height, width = gray.shape[:2]
    crop_w = max(1, int(crop_w))
    crop_h = max(1, int(crop_h))
    x0 = int(round(cx - crop_w / 2.0))
    y0 = int(round(cy - crop_h / 2.0))
    x1 = x0 + crop_w
    y1 = y0 + crop_h
    x0c = max(0, x0)
    y0c = max(0, y0)
    x1c = min(width, x1)
    y1c = min(height, y1)
    if x1c <= x0c or y1c <= y0c:
        raise ValueError("裁剪區域落在影像外。")
    crop = gray[y0c:y1c, x0c:x1c]
    return crop, x0c, y0c, int(crop.shape[1]), int(crop.shape[0])


def encode_png_b64(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("無法編碼 PNG。")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def save_png(path: str, image: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("無法編碼 PNG。")
    buf.tofile(path)


def to_bgr(gray: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def run_pipeline(image_path: str, param_values: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    params = coerce_params(param_values)
    gray = load_gray(image_path)

    g, strip_h = strip_mean_g(gray)
    scaled = scaled_levels(g, params)
    work_params = dict(params)
    work_params["rect_bin_thresh"] = float(scaled["rect_bin_thresh"])
    _rect_response, rect, from_binary = _find_rect_with_mu(
        gray, work_params, float(scaled["mu_rect"]),
    )
    outside = outside_mean(gray, rect)

    cx = float(rect["cx"])
    cy = float(rect["cy"])
    package_w = float(rect["w"])
    package_h = float(rect["h"])
    angle = float(rect["angle"])
    rotated_linear = rotate_around_center(gray, cx, cy, angle, cv2.INTER_LINEAR)
    rotated_raw = rotate_around_center(gray, cx, cy, angle, cv2.INTER_NEAREST)
    rotated_bgr = to_bgr(rotated_raw)
    full_rect_gray, _x, _y, _w, _h = center_crop(
        rotated_linear, cx, cy, int(round(package_w)), int(round(package_h)),
    )
    orientation = detect_orientation(full_rect_gray)
    full_rect_canonical = canonicalize(full_rect_gray, orientation)
    crop_w = int(round(float(params["crop_ratio"]) * package_w))
    crop_h = int(round(float(params["crop_ratio"]) * package_h))
    crop_gray, crop_x, crop_y, actual_w, actual_h = center_crop(
        rotated_raw, cx, cy, crop_w, crop_h,
    )
    measurement_canonical = canonicalize(np.ascontiguousarray(crop_gray), orientation).copy()

    if orientation.certain:
        try:
            capsules = registered_capsules(
                measurement_canonical,
                rotated_bgr,
                (crop_x, crop_y),
                orientation,
                full_rect_canonical,
                background_reference_g=g,
            )
        except RegistrationError as exc:
            capsules = empty_capsules(rotated_bgr, exc.code, str(exc))
    else:
        capsules = empty_capsules(
            rotated_bgr,
            "orientation_uncertain",
            (
                f"方向不確定：最佳分數 {max(orientation.score_a, orientation.score_b):.4f}，"
                f"分差 {orientation.margin:.4f}"
            ),
        )

    registered_ideal = capsules.get("ideal") or {}
    registered_contours = [
        np.asarray(contour, dtype=np.float64)
        for contour in registered_ideal.get("contoursCanonical", [])
    ]
    sure_solder_offset_pct = float(params["solder_full_weight_offset_pct"])
    sure_void_offset_pct = float(params["void_zero_weight_offset_pct"])
    center_offset_pct = (sure_solder_offset_pct + sure_void_offset_pct) / 2.0
    transition_pct = sure_void_offset_pct - sure_solder_offset_pct

    (
        solder_metric,
        solder_weight_canonical,
        solder_masks_canonical,
        shifted_canonical,
        shift_diagnostics,
    ) = measure_solder_indication_shifted_ideal(
        measurement_canonical,
        registered_contours,
        g,
        float(params["solder_reference_ceiling_pct"]),
        center_offset_pct,
        transition_pct,
        max_shift_px=float(params["ideal_shift_max_px"]),
        step_px=float(_POSITION_OPTIMIZATION_CFG["stepPx"]),
    )
    solder_metric["configuredSolderFullWeightOffsetPctG"] = sure_solder_offset_pct
    solder_metric["configuredVoidZeroWeightOffsetPctG"] = sure_void_offset_pct
    if len(shifted_canonical) != 4:
        shifted_canonical = [contour.copy() for contour in registered_contours]
        shift_diagnostics = [
            {"id": index, "dxPx": 0.0, "dyPx": 0.0, "fallback": True}
            for index in range(1, len(shifted_canonical) + 1)
        ]

    quality = evaluate_geometry_quality(
        shifted_canonical,
        shift_diagnostics,
        float(params["pair_min_distance_px"]),
        float(params["relative_shift_warning_px"]),
    )
    analysis_ok = (
        str(registered_ideal.get("status")) == "ok"
        and str(solder_metric.get("status")) == "ok"
        and len(shifted_canonical) == 4
    )
    quality["analysis"] = {
        "status": "PASS" if analysis_ok else "FAIL",
        "reasonCode": "" if analysis_ok else str(
            solder_metric.get("reasonCode")
            or registered_ideal.get("reasonCode")
            or "analysis_unavailable"
        ),
    }
    if not analysis_ok:
        quality["overallStatus"] = "FAIL"
        quality["reasonCode"] = quality["analysis"]["reasonCode"]

    shifted_display = map_contours_from_canonical(
        shifted_canonical, measurement_canonical.shape, orientation,
    )
    roi_canonical = np.zeros(measurement_canonical.shape, dtype=np.uint8)
    for region_mask in solder_masks_canonical:
        roi_canonical[np.asarray(region_mask, dtype=bool)] = 255
    weight_display = restore_orientation(
        solder_weight_canonical
        if solder_weight_canonical.shape == measurement_canonical.shape
        else np.zeros(measurement_canonical.shape, dtype=np.float32),
        orientation,
    )
    roi_display = restore_orientation(roi_canonical, orientation)
    solder_weight_full = np.zeros(rotated_raw.shape[:2], dtype=np.float32)
    solder_roi_full = np.zeros(rotated_raw.shape[:2], dtype=np.uint8)
    display_h, display_w = weight_display.shape[:2]
    target = np.s_[crop_y : crop_y + display_h, crop_x : crop_x + display_w]
    solder_weight_full[target] = weight_display
    solder_roi_full[target] = roi_display
    result_image = render_solder_indication_stage(
        rotated_bgr,
        solder_weight_full,
        solder_roi_full,
        shifted_display,
        (crop_x, crop_y),
        quality_checks=quality,
        solder_metric=solder_metric,
        shift_diagnostics=shift_diagnostics,
        registration_score=float(registered_ideal.get("registrationScore") or 0.0),
    )

    base_diagnostics = list(registered_ideal.get("diagnostics") or [])
    shift_by_id = {int(item.get("id", 0)): item for item in shift_diagnostics}
    diagnostics = []
    for region_id in range(1, 5):
        item = dict(base_diagnostics[region_id - 1]) if region_id <= len(base_diagnostics) else {"ok": False}
        item["regionId"] = region_id
        item["positionShift"] = dict(shift_by_id.get(region_id) or {})
        diagnostics.append(item)
    ideal_summary = {
        "status": str(registered_ideal.get("status") or "unavailable"),
        "reasonCode": str(registered_ideal.get("reasonCode") or ""),
        "confidence": float(registered_ideal.get("confidence") or 0.0),
        "regionCount": int(len(shifted_canonical)),
        "okCount": int(len(shifted_canonical)) if analysis_ok else 0,
        "algorithmVersion": "analytic-stadium-xy-void-min-v32",
        "registrationScore": float(registered_ideal.get("registrationScore") or 0.0),
        "registrationTransform": registered_ideal.get("registrationTransform") or [],
        "registrationLocalTransform": registered_ideal.get("registrationLocalTransform") or [],
        "registrationPhaseResponse": float(registered_ideal.get("registrationPhaseResponse") or 0.0),
        "poseSource": str(registered_ideal.get("poseSource") or ""),
        "positionOptimization": solder_metric.get("positionOptimization") or {},
        "diagnostics": diagnostics,
    }
    alerts = list(capsules.get("warnings") or [])
    if quality["pairDistance"]["status"] == "FAIL":
        alerts.append("同排 R 間距過近：" + ", ".join(quality["pairDistance"]["failedPairs"]))
    if quality["relativeShift"]["status"] == "WARNING":
        alerts.append("四個 R 的 X/Y 相對位移差過大")
    aabb = rect.get("aabb") or {
        "x": int(rect["x"]),
        "y": int(rect["y"]),
        "w": int(round(package_w)),
        "h": int(round(package_h)),
    }
    processing_ms = 1000.0 * (time.perf_counter() - started)
    metrics = {
        "overallStatus": quality["overallStatus"],
        "qualityChecks": quality,
        "G": g,
        "stripH": strip_h,
        "A": float(outside),
        "muRectPrime": float(scaled["mu_rect"]),
        "solderIndication": solder_metric,
        "idealShiftMaxPx": float(params["ideal_shift_max_px"]),
        "pairMinDistancePx": float(params["pair_min_distance_px"]),
        "relativeShiftWarningPx": float(params["relative_shift_warning_px"]),
        "contours": {"ideal": ideal_summary},
        "rectBinThresh": float(scaled["rect_bin_thresh"]),
        "fromBinary": bool(from_binary),
        "rect": {
            "x": int(aabb["x"]),
            "y": int(aabb["y"]),
            "w": package_w,
            "h": package_h,
            "angle": angle,
            "corners": rect["corners"],
        },
        "angle": angle,
        "aspect": float(rect["aspect"]),
        "candidateCount": int(rect["candidateCount"]),
        "center": {"x": cx, "y": cy},
        "crop": {
            "x": crop_x,
            "y": crop_y,
            "w": actual_w,
            "h": actual_h,
            "requestedW": crop_w,
            "requestedH": crop_h,
        },
        "regionCount": int(len(shifted_canonical)),
        "okCount": int(len(shifted_canonical)) if analysis_ok else 0,
        "algorithmVersion": "ideal-only-geometry-qa-v32",
        "warning": "；".join(alerts),
        "rectSource": rect.get("source"),
        **orientation.as_metrics(),
        "registrationScore": float(registered_ideal.get("registrationScore") or 0.0),
        "registrationTransform": registered_ideal.get("registrationTransform") or [],
        "registrationPhaseResponse": float(registered_ideal.get("registrationPhaseResponse") or 0.0),
        "failureCode": str(quality.get("reasonCode") or ""),
        "processingMs": processing_ms,
        "configVersion": int(load_config()["configVersion"]),
        "configSha256": config_sha256(),
    }
    return {
        "ok": True,
        "metrics": metrics,
        "stageImages": {"result": encode_png_b64(result_image)},
        "resultImageBgr": result_image,
    }
