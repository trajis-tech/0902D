# -*- coding: utf-8 -*-
"""Solder indication and geometry checks inside the four ideal ROIs.

This metric is deliberately an image-domain proxy.  It does not claim pad
coverage, solder volume, or thickness.  The source image must be the raw
nearest-neighbour canonical gray crop; filtering is reserved for ySharp and
must never alter the pixels used for area measurement.

The metric first estimates the solder gray peak S inside the four ideal ROIs.
Classification then uses the exposure-normalized difference (gray - S) / G.
G is only the unit used to express a difference from solder; it is not the
classification anchor.  No median blur, morphology, or connectivity growth is
allowed on the measurement branch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import cv2
import numpy as np

from contour_render import render_mask
from config_loader import section, user_defaults

_USER_DEFAULTS = user_defaults()
_SOLDER_REFERENCE_CFG = section("solderReference")
_POSITION_CFG = section("positionOptimization")
_RESULT_RENDER_CFG = section("resultRender")

ALGORITHM_VERSION = "solder-indication-ideal-v31"
DISPLAY_NAME = "焊錫顯影占比"
POLARITY = "dark"
CLASSIFICATION_MODE = "solder-relative-linear-gray-membership"
REFERENCE_METHOD = "pooled-roi-conservative-dark-mode"
DEFAULT_REFERENCE_CEILING_PCT = float(_USER_DEFAULTS["solder_reference_ceiling_pct"])
DEFAULT_CENTER_OFFSET_PCT = float(_USER_DEFAULTS["solder_center_offset_pct"])
DEFAULT_TRANSITION_PCT = float(_USER_DEFAULTS["solder_transition_pct"])
MIN_REFERENCE_SEED_PIXELS = int(_SOLDER_REFERENCE_CFG["minSeedPixels"])
MIN_REFERENCE_SEED_FRACTION = float(_SOLDER_REFERENCE_CFG["minSeedFraction"])
MIN_REFERENCE_PEAK_FRACTION = float(_SOLDER_REFERENCE_CFG["minPeakFraction"])


def _finite_number(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def unavailable_result(
    reason_code: str,
    reference_ceiling_pct: float = DEFAULT_REFERENCE_CEILING_PCT,
    center_offset_pct: float = DEFAULT_CENTER_OFFSET_PCT,
    transition_pct: float = DEFAULT_TRANSITION_PCT,
    reference_ceiling_gray: float | None = None,
) -> Dict[str, Any]:
    """Return a fail-closed metric payload; unavailable is never reported as 0%."""
    return {
        "algorithmVersion": ALGORITHM_VERSION,
        "displayName": DISPLAY_NAME,
        "status": "unavailable",
        "reasonCode": str(reason_code),
        "polarity": POLARITY,
        "graySource": "raw-canonical-nearest",
        "filterApplied": "none",
        "schemaVersion": 4,
        "classificationMode": CLASSIFICATION_MODE,
        "referenceMethod": REFERENCE_METHOD,
        "referenceStatus": "unavailable",
        "referenceCeilingPctG": float(reference_ceiling_pct),
        "referenceCeilingGray": (
            None if reference_ceiling_gray is None else float(reference_ceiling_gray)
        ),
        "centerOffsetPctG": float(center_offset_pct),
        "transitionPct": float(transition_pct),
        "solderReferenceGray": None,
        "solderReferenceRatioG": None,
        "solderReferencePctG": None,
        "referenceSeedPixels": 0,
        "referenceSeedFraction": 0.0,
        "referencePeakFraction": 0.0,
        "thresholdGray": None,
        "sureSolderOffsetPctG": None,
        "sureSolderThresholdGray": None,
        "sureVoidOffsetPctG": None,
        "sureVoidThresholdGray": None,
        "overallRate": None,
        "rateLower": None,
        "rateUpper": None,
        "totalEffectiveSolderPixels": 0.0,
        "sureSolderPixels": 0,
        "sureVoidPixels": 0,
        "uncertainPixels": 0,
        "totalRoiPixels": 0,
        "regions": [],
    }


def _estimate_solder_reference(
    gray_float: np.ndarray,
    roi: np.ndarray,
    gray_reference: float,
    reference_ceiling_pct: float,
) -> Dict[str, Any]:
    """Estimate the solder-color mode from conservative dark ROI pixels."""
    values = np.asarray(gray_float[roi], dtype=np.float64)
    ceiling_gray = float(np.clip(
        gray_reference * (1.0 + reference_ceiling_pct / 100.0),
        0.0,
        255.0,
    ))
    seed_values = values[values <= ceiling_gray]
    seed_pixels = int(seed_values.size)
    roi_pixels = int(values.size)
    seed_fraction = (
        float(seed_pixels) / float(roi_pixels) if roi_pixels > 0 else 0.0
    )
    common = {
        "ceilingGray": ceiling_gray,
        "seedPixels": seed_pixels,
        "seedFraction": seed_fraction,
        "peakFraction": 0.0,
        "referenceGray": None,
    }
    if seed_pixels == 0:
        return {"status": "absent", **common}
    minimum_seed = max(
        MIN_REFERENCE_SEED_PIXELS,
        int(np.ceil(MIN_REFERENCE_SEED_FRACTION * float(roi_pixels))),
    )
    if seed_pixels < minimum_seed:
        return {"status": "insufficient", **common}

    seed_u8 = np.rint(seed_values).clip(0, 255).astype(np.uint8)
    histogram = np.bincount(seed_u8, minlength=256).astype(np.float64)
    sigma_gray = max(
        float(_SOLDER_REFERENCE_CFG["histogramSigmaMinGray"]),
        float(_SOLDER_REFERENCE_CFG["histogramSigmaRatioOfG"]) * float(gray_reference),
    )
    smooth = cv2.GaussianBlur(
        histogram.reshape(1, -1),
        (0, 0),
        sigmaX=sigma_gray,
        borderType=cv2.BORDER_REPLICATE,
    ).ravel()
    upper = min(255, max(0, int(np.floor(ceiling_gray))))
    mode_gray = int(np.argmax(smooth[: upper + 1]))
    radius = max(
        1,
        int(np.ceil(float(_SOLDER_REFERENCE_CFG["peakRadiusSigmaMultiplier"]) * sigma_gray)),
    )
    peak_values = seed_values[np.abs(seed_values - float(mode_gray)) <= radius]
    peak_fraction = float(peak_values.size) / float(seed_pixels)
    common["peakFraction"] = peak_fraction
    if peak_values.size == 0 or peak_fraction < MIN_REFERENCE_PEAK_FRACTION:
        return {"status": "peak_weak", **common}

    reference_gray = float(np.median(peak_values))
    return {
        "status": "ok",
        **common,
        "referenceGray": reference_gray,
        "modeGray": mode_gray,
        "peakRadiusGray": radius,
    }


def _contour_mask(shape_hw: Tuple[int, int], contour: np.ndarray) -> np.ndarray:
    return render_mask(shape_hw, [contour], (0, 0)) > 0


def measure_solder_indication(
    gray_canonical: np.ndarray,
    contours_canonical: Sequence[np.ndarray],
    gray_reference: float,
    reference_ceiling_pct: float = DEFAULT_REFERENCE_CEILING_PCT,
    center_offset_pct: float = DEFAULT_CENTER_OFFSET_PCT,
    transition_pct: float = DEFAULT_TRANSITION_PCT,
) -> Tuple[Dict[str, Any], np.ndarray, List[np.ndarray]]:
    """Measure solder membership relative to the ROI solder-color mode S.

    S is the pooled dark-mode estimate inside all four ROIs.  Thresholds are
    S plus offsets expressed in units of G.  Pixels at the solder-side end have
    weight 1, pixels at the white/void-side end have weight 0, and pixels in
    between are interpolated linearly.

    No smoothing, erosion, morphology, automatic clustering, connectivity
    growth, or forced two-class split is applied.  The returned float32 map
    is the exact per-pixel solder weight used for the reported area.
    """
    gray = np.asarray(gray_canonical)
    empty_shape = gray.shape[:2] if gray.ndim >= 2 else (0, 0)
    empty = np.zeros(empty_shape, dtype=np.uint8)

    if gray.ndim != 2 or gray.size == 0:
        return (
            unavailable_result(
                "gray_shape_invalid",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )
    if not _finite_number(reference_ceiling_pct):
        return (
            unavailable_result(
                "reference_ceiling_pct_invalid",
                DEFAULT_REFERENCE_CEILING_PCT,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )
    reference_ceiling_pct = float(reference_ceiling_pct)
    if not _finite_number(center_offset_pct):
        return (
            unavailable_result(
                "center_offset_pct_invalid",
                reference_ceiling_pct,
                DEFAULT_CENTER_OFFSET_PCT,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )
    center_offset_pct = float(center_offset_pct)
    if not _finite_number(transition_pct) or float(transition_pct) <= 0.0:
        return (
            unavailable_result(
                "transition_pct_invalid",
                reference_ceiling_pct,
                center_offset_pct,
                DEFAULT_TRANSITION_PCT,
            ),
            empty.astype(np.float32),
            [],
        )
    transition_pct = float(transition_pct)
    if not _finite_number(gray_reference) or float(gray_reference) <= 0.0:
        return (
            unavailable_result(
                "g_invalid",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )
    gray_reference = float(gray_reference)
    sure_solder_offset_pct = center_offset_pct - transition_pct / 2.0
    sure_void_offset_pct = center_offset_pct + transition_pct / 2.0
    if sure_solder_offset_pct < 0.0 or sure_void_offset_pct <= sure_solder_offset_pct:
        return (
            unavailable_result(
                "offset_range_invalid",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )
    if len(contours_canonical) != 4:
        return (
            unavailable_result(
                "contour_count_invalid",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )

    masks: List[np.ndarray] = []
    occupancy = np.zeros(gray.shape, dtype=np.uint8)
    for contour in contours_canonical:
        points = np.asarray(contour, dtype=np.float64)
        if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
            return (
                unavailable_result(
                    "contour_shape_invalid",
                    reference_ceiling_pct,
                    center_offset_pct,
                    transition_pct,
                ),
                empty.astype(np.float32),
                [],
            )
        mask = _contour_mask(gray.shape, points)
        if not np.any(mask):
            return (
                unavailable_result(
                    "mask_empty",
                    reference_ceiling_pct,
                    center_offset_pct,
                    transition_pct,
                ),
                empty.astype(np.float32),
                [],
            )
        occupancy += mask.astype(np.uint8)
        masks.append(mask)
    if np.any(occupancy > 1):
        return (
            unavailable_result(
                "roi_masks_overlap",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )

    gray_float = gray.astype(np.float64)
    roi_union = occupancy > 0
    total_roi = int(np.count_nonzero(roi_union))
    if total_roi <= 0:
        return (
            unavailable_result(
                "mask_empty",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
            ),
            empty.astype(np.float32),
            [],
        )

    reference = _estimate_solder_reference(
        gray_float,
        roi_union,
        gray_reference,
        reference_ceiling_pct,
    )
    reference_status = str(reference["status"])
    if reference_status == "absent":
        regions = []
        for region_id, mask in enumerate(masks, start=1):
            roi_pixels = int(np.count_nonzero(mask))
            regions.append({
                "id": int(region_id),
                "rate": 0.0,
                "rateLower": 0.0,
                "rateUpper": 0.0,
                "effectiveSolderPixels": 0.0,
                "sureSolderPixels": 0,
                "sureVoidPixels": roi_pixels,
                "uncertainPixels": 0,
                "roiPixels": roi_pixels,
                "meanGray": float(np.mean(gray_float[mask])),
            })
        result = {
            "algorithmVersion": ALGORITHM_VERSION,
            "displayName": DISPLAY_NAME,
            "status": "ok",
            "reasonCode": "",
            "polarity": POLARITY,
            "graySource": "raw-canonical-nearest",
            "filterApplied": "none",
            "schemaVersion": 4,
            "classificationMode": CLASSIFICATION_MODE,
            "referenceMethod": REFERENCE_METHOD,
            "referenceStatus": "absent_all_above_ceiling",
            "referenceCeilingPctG": reference_ceiling_pct,
            "referenceCeilingGray": float(reference["ceilingGray"]),
            "centerOffsetPctG": center_offset_pct,
            "transitionPct": transition_pct,
            "solderReferenceGray": None,
            "solderReferenceRatioG": None,
            "solderReferencePctG": None,
            "referenceSeedPixels": 0,
            "referenceSeedFraction": 0.0,
            "referencePeakFraction": 0.0,
            "thresholdGray": None,
            "sureSolderOffsetPctG": sure_solder_offset_pct,
            "sureSolderThresholdGray": None,
            "sureVoidOffsetPctG": sure_void_offset_pct,
            "sureVoidThresholdGray": None,
            "overallRate": 0.0,
            "rateLower": 0.0,
            "rateUpper": 0.0,
            "totalEffectiveSolderPixels": 0.0,
            "sureSolderPixels": 0,
            "sureVoidPixels": total_roi,
            "uncertainPixels": 0,
            "totalRoiPixels": total_roi,
            "regions": regions,
        }
        return result, np.zeros(gray.shape, dtype=np.float32), masks
    if reference_status != "ok":
        reason = {
            "insufficient": "solder_reference_insufficient",
            "peak_weak": "solder_reference_peak_weak",
        }.get(reference_status, "solder_reference_invalid")
        result = unavailable_result(
            reason,
            reference_ceiling_pct,
            center_offset_pct,
            transition_pct,
            float(reference["ceilingGray"]),
        )
        result.update({
            "referenceStatus": reference_status,
            "referenceSeedPixels": int(reference["seedPixels"]),
            "referenceSeedFraction": float(reference["seedFraction"]),
            "referencePeakFraction": float(reference["peakFraction"]),
        })
        return result, empty.astype(np.float32), []

    solder_reference_gray = float(reference["referenceGray"])
    threshold_gray = float(np.clip(
        solder_reference_gray + gray_reference * center_offset_pct / 100.0,
        0.0,
        255.0,
    ))
    sure_solder_threshold_gray = float(np.clip(
        solder_reference_gray + gray_reference * sure_solder_offset_pct / 100.0,
        0.0,
        255.0,
    ))
    sure_void_threshold_gray = float(np.clip(
        solder_reference_gray + gray_reference * sure_void_offset_pct / 100.0,
        0.0,
        255.0,
    ))
    if sure_void_threshold_gray <= sure_solder_threshold_gray:
        return (
            unavailable_result(
                "threshold_range_invalid",
                reference_ceiling_pct,
                center_offset_pct,
                transition_pct,
                float(reference["ceilingGray"]),
            ),
            empty.astype(np.float32),
            [],
        )

    denominator = sure_void_threshold_gray - sure_solder_threshold_gray
    solder_weight = np.clip(
        (sure_void_threshold_gray - gray_float) / denominator,
        0.0,
        1.0,
    )
    sure_solder = gray_float <= sure_solder_threshold_gray
    sure_void = gray_float >= sure_void_threshold_gray
    uncertain = ~(sure_solder | sure_void)
    solder_union = np.zeros(gray.shape, dtype=np.float32)
    regions: List[Dict[str, Any]] = []
    total_effective_solder = 0.0
    total_sure_solder = 0
    total_sure_void = 0
    total_uncertain = 0
    accumulated_roi = 0
    for region_id, mask in enumerate(masks, start=1):
        roi_pixels = int(np.count_nonzero(mask))
        region_weights = solder_weight[mask]
        effective_solder = float(np.sum(region_weights, dtype=np.float64))
        sure_solder_pixels = int(np.count_nonzero(mask & sure_solder))
        sure_void_pixels = int(np.count_nonzero(mask & sure_void))
        uncertain_pixels = int(np.count_nonzero(mask & uncertain))
        solder_union[mask] = region_weights.astype(np.float32)
        total_effective_solder += effective_solder
        total_sure_solder += sure_solder_pixels
        total_sure_void += sure_void_pixels
        total_uncertain += uncertain_pixels
        accumulated_roi += roi_pixels
        regions.append({
            "id": int(region_id),
            "rate": effective_solder / float(roi_pixels),
            "rateLower": float(sure_solder_pixels) / float(roi_pixels),
            "rateUpper": (
                float(sure_solder_pixels + uncertain_pixels) / float(roi_pixels)
            ),
            "effectiveSolderPixels": effective_solder,
            "sureSolderPixels": sure_solder_pixels,
            "sureVoidPixels": sure_void_pixels,
            "uncertainPixels": uncertain_pixels,
            "roiPixels": roi_pixels,
            "meanGray": float(np.mean(gray_float[mask])),
        })

    if accumulated_roi != total_roi:
        raise RuntimeError("ROI 像素累計不一致。")
    result = {
        "algorithmVersion": ALGORITHM_VERSION,
        "displayName": DISPLAY_NAME,
        "status": "ok",
        "reasonCode": "",
        "polarity": POLARITY,
        "graySource": "raw-canonical-nearest",
        "filterApplied": "none",
        "schemaVersion": 4,
        "classificationMode": CLASSIFICATION_MODE,
        "referenceMethod": REFERENCE_METHOD,
        "referenceStatus": "ok",
        "referenceCeilingPctG": reference_ceiling_pct,
        "referenceCeilingGray": float(reference["ceilingGray"]),
        "centerOffsetPctG": center_offset_pct,
        "transitionPct": transition_pct,
        "solderReferenceGray": solder_reference_gray,
        "solderReferenceRatioG": solder_reference_gray / gray_reference,
        "solderReferencePctG": (
            100.0 * (solder_reference_gray / gray_reference - 1.0)
        ),
        "referenceSeedPixels": int(reference["seedPixels"]),
        "referenceSeedFraction": float(reference["seedFraction"]),
        "referencePeakFraction": float(reference["peakFraction"]),
        "thresholdGray": threshold_gray,
        "sureSolderOffsetPctG": sure_solder_offset_pct,
        "sureSolderThresholdGray": sure_solder_threshold_gray,
        "sureVoidOffsetPctG": sure_void_offset_pct,
        "sureVoidThresholdGray": sure_void_threshold_gray,
        "overallRate": total_effective_solder / float(total_roi),
        "rateLower": float(total_sure_solder) / float(total_roi),
        "rateUpper": (
            float(total_sure_solder + total_uncertain) / float(total_roi)
        ),
        "totalEffectiveSolderPixels": total_effective_solder,
        "sureSolderPixels": total_sure_solder,
        "sureVoidPixels": total_sure_void,
        "uncertainPixels": total_uncertain,
        "totalRoiPixels": total_roi,
        "regions": regions,
    }
    return result, solder_union, masks


def _variant_unavailable(reason_code: str, basis: str) -> Dict[str, Any]:
    return {
        "basis": str(basis),
        "status": "unavailable",
        "reasonCode": str(reason_code),
        "overallRate": None,
        "rateLower": None,
        "rateUpper": None,
        "totalEffectiveSolderPixels": 0.0,
        "sureSolderPixels": 0,
        "sureVoidPixels": 0,
        "uncertainPixels": 0,
        "totalRoiPixels": 0,
        "regions": [],
    }


def _variant_from_metric(metric: Dict[str, Any], basis: str) -> Dict[str, Any]:
    keys = (
        "status",
        "reasonCode",
        "overallRate",
        "rateLower",
        "rateUpper",
        "totalEffectiveSolderPixels",
        "sureSolderPixels",
        "sureVoidPixels",
        "uncertainPixels",
        "totalRoiPixels",
        "regions",
    )
    return {"basis": str(basis), **{key: metric.get(key) for key in keys}}


def _shared_weight_from_metric(
    gray_canonical: np.ndarray,
    primary_metric: Dict[str, Any],
) -> np.ndarray:
    gray = np.asarray(gray_canonical, dtype=np.float64)
    if primary_metric.get("status") != "ok":
        return np.zeros(gray.shape, dtype=np.float32)
    sure_solder = primary_metric.get("sureSolderThresholdGray")
    sure_void = primary_metric.get("sureVoidThresholdGray")
    if sure_solder is None or sure_void is None:
        return np.zeros(gray.shape, dtype=np.float32)
    denominator = float(sure_void) - float(sure_solder)
    if denominator <= 0.0:
        return np.zeros(gray.shape, dtype=np.float32)
    return np.clip(
        (float(sure_void) - gray) / denominator,
        0.0,
        1.0,
    ).astype(np.float32)


def _summarize_shared_weight(
    gray_canonical: np.ndarray,
    contours_canonical: Sequence[np.ndarray],
    shared_weight: np.ndarray,
    primary_metric: Dict[str, Any],
    basis: str,
) -> Tuple[Dict[str, Any], np.ndarray, List[np.ndarray]]:
    gray = np.asarray(gray_canonical, dtype=np.float64)
    weight = np.asarray(shared_weight, dtype=np.float32)
    empty = np.zeros(gray.shape, dtype=np.float32)
    if primary_metric.get("status") != "ok":
        reason = str(primary_metric.get("reasonCode") or "shared_reference_unavailable")
        return _variant_unavailable(reason, basis), empty, []
    if gray.ndim != 2 or weight.shape != gray.shape or len(contours_canonical) != 4:
        return _variant_unavailable("contours_unavailable", basis), empty, []
    masks: List[np.ndarray] = []
    occupancy = np.zeros(gray.shape, dtype=np.uint8)
    for contour in contours_canonical:
        points = np.asarray(contour, dtype=np.float64)
        if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
            return _variant_unavailable("contour_shape_invalid", basis), empty, []
        mask = _contour_mask(gray.shape, points)
        if not np.any(mask):
            return _variant_unavailable("mask_empty", basis), empty, []
        occupancy += mask.astype(np.uint8)
        masks.append(mask)
    if np.any(occupancy > 1):
        return _variant_unavailable("roi_masks_overlap", basis), empty, []
    sure_solder_gray = primary_metric.get("sureSolderThresholdGray")
    sure_void_gray = primary_metric.get("sureVoidThresholdGray")
    if sure_solder_gray is None or sure_void_gray is None:
        sure_solder = np.zeros(gray.shape, dtype=bool)
        sure_void = np.ones(gray.shape, dtype=bool)
    else:
        sure_solder = gray <= float(sure_solder_gray)
        sure_void = gray >= float(sure_void_gray)
    uncertain = ~(sure_solder | sure_void)
    union_weight = np.zeros(gray.shape, dtype=np.float32)
    regions: List[Dict[str, Any]] = []
    total_roi = 0
    total_effective = 0.0
    total_sure_solder = 0
    total_sure_void = 0
    total_uncertain = 0
    for region_id, mask in enumerate(masks, start=1):
        roi_pixels = int(np.count_nonzero(mask))
        effective = float(np.sum(weight[mask], dtype=np.float64))
        sure_solder_pixels = int(np.count_nonzero(mask & sure_solder))
        sure_void_pixels = int(np.count_nonzero(mask & sure_void))
        uncertain_pixels = int(np.count_nonzero(mask & uncertain))
        union_weight[mask] = weight[mask]
        total_roi += roi_pixels
        total_effective += effective
        total_sure_solder += sure_solder_pixels
        total_sure_void += sure_void_pixels
        total_uncertain += uncertain_pixels
        regions.append({
            "id": int(region_id),
            "rate": effective / float(roi_pixels),
            "rateLower": float(sure_solder_pixels) / float(roi_pixels),
            "rateUpper": float(sure_solder_pixels + uncertain_pixels) / float(roi_pixels),
            "effectiveSolderPixels": effective,
            "sureSolderPixels": sure_solder_pixels,
            "sureVoidPixels": sure_void_pixels,
            "uncertainPixels": uncertain_pixels,
            "roiPixels": roi_pixels,
            "meanGray": float(np.mean(gray[mask])),
        })
    result = {
        "basis": str(basis),
        "status": "ok",
        "reasonCode": "",
        "overallRate": total_effective / float(total_roi),
        "rateLower": float(total_sure_solder) / float(total_roi),
        "rateUpper": float(total_sure_solder + total_uncertain) / float(total_roi),
        "totalEffectiveSolderPixels": total_effective,
        "sureSolderPixels": total_sure_solder,
        "sureVoidPixels": total_sure_void,
        "uncertainPixels": total_uncertain,
        "totalRoiPixels": total_roi,
        "regions": regions,
    }
    return result, union_weight, masks


def render_solder_indication_stage(
    canvas_bgr: np.ndarray,
    solder_weight: np.ndarray,
    roi_mask: np.ndarray,
    contours: Sequence[np.ndarray],
    origin: Tuple[int, int],
    contour_color: Tuple[int, int, int] | None = None,
    quality_checks: Dict[str, Any] | None = None,
    solder_metric: Dict[str, Any] | None = None,
    shift_diagnostics: Sequence[Dict[str, Any]] | None = None,
    registration_score: float | None = None,
) -> np.ndarray:
    """Render the only retained result image with a compact data panel.

    The raw ROI stays visible.  Warm orange alpha increases continuously from
    solder weight 1 to 0, so a transition halo is displayed rather than erased
    by an opaque binary class colour.  No geometry-changing operation is used.
    """
    source = np.asarray(canvas_bgr, dtype=np.uint8)
    weights = np.asarray(solder_weight, dtype=np.float32)
    roi = np.asarray(roi_mask) > 0
    if weights.shape != source.shape[:2] or roi.shape != source.shape[:2]:
        raise ValueError("焊錫顯影遮罩尺寸與顯示畫布不同。")
    if not np.all(np.isfinite(weights)):
        raise ValueError("焊錫顯影權重包含非有限值。")
    if np.any(weights < 0.0) or np.any(weights > 1.0):
        raise ValueError("焊錫顯影權重必須介於 0 與 1。")
    if np.any((weights > 0.0) & ~roi):
        raise ValueError("焊錫顯影權重落在 ROI 外。")
    source_f = source.astype(np.float32)
    stage = np.rint(source_f * float(_RESULT_RENDER_CFG["outsideDimFactor"])).astype(np.uint8)
    stage[roi] = source[roi]
    if np.any(roi):
        void_weight = 1.0 - weights[roi]
        alpha = (float(_RESULT_RENDER_CFG["voidOverlayMaxAlpha"]) * void_weight)[:, None]
        void_color = np.asarray(_RESULT_RENDER_CFG["voidColorBgr"], dtype=np.float32)
        stage[roi] = np.rint(
            source_f[roi] * (1.0 - alpha) + void_color * alpha
        ).astype(np.uint8)
    overlay = stage.copy()
    if contour_color is None:
        contour_color = tuple(int(v) for v in _RESULT_RENDER_CFG["contourColorBgr"])
    offset = np.asarray([int(origin[0]), int(origin[1])], dtype=np.float64)
    for contour in contours:
        points = np.rint(np.asarray(contour, dtype=np.float64) + offset).astype(np.int32)
        cv2.polylines(
            overlay, [points], True, contour_color,
            int(_RESULT_RENDER_CFG["contourLineThicknessPx"]), cv2.LINE_AA,
        )
    checks = quality_checks or {}
    pair = checks.get("pairDistance") or {}
    shift = checks.get("relativeShift") or {}
    metric = solder_metric or {}
    status = str(checks.get("overallStatus") or "UNAVAILABLE").upper()
    status_colors = _RESULT_RENDER_CFG["statusColorsBgr"]
    status_color = tuple(
        int(v) for v in status_colors.get(status, status_colors["UNAVAILABLE"])
    )
    text_color = tuple(int(v) for v in _RESULT_RENDER_CFG["textColorBgr"])
    dx_values = [
        float(item.get("dxPx", 0.0))
        for item in (shift_diagnostics or [])
        if isinstance(item, dict)
    ]
    dy_values = [
        float(item.get("dyPx", 0.0))
        for item in (shift_diagnostics or [])
        if isinstance(item, dict)
    ]
    void_rate = metric.get("emptyAreaRate")
    top_gap = pair.get("topDistancePx")
    bottom_gap = pair.get("bottomDistancePx")
    spread = shift.get("spreadPx")

    def _number(value: Any, digits: int = 1) -> str:
        return f"{float(value):.{digits}f}" if _finite_number(value) else "--"

    lines = [
        (f"STATUS  {status}", status_color),
        (f"VOID    {_number(100.0 * float(void_rate), 1) if _finite_number(void_rate) else '--'}%", text_color),
        ("DX      " + (" / ".join(f"{value:+.0f}" for value in dx_values) if dx_values else "--"), text_color),
        ("DY      " + (" / ".join(f"{value:+.0f}" for value in dy_values) if dy_values else "--"), text_color),
        (f"GAP T/B {_number(top_gap)} / {_number(bottom_gap)} px", text_color),
        (f"SPREAD  {_number(spread)} px", text_color),
        (f"REG     {_number(registration_score, 3)}", text_color),
    ]
    scale = float(_RESULT_RENDER_CFG["fontScale"])
    line_h = int(_RESULT_RENDER_CFG["lineHeightPx"])
    panel_w = min(
        max(int(_RESULT_RENDER_CFG["panelMinWidthPx"]), int(float(_RESULT_RENDER_CFG["panelWidthFraction"]) * overlay.shape[1])),
        overlay.shape[1],
    )
    panel_h = min(int(_RESULT_RENDER_CFG["panelTopPaddingPx"]) + line_h * len(lines), overlay.shape[0])
    panel = overlay[:panel_h, :panel_w].astype(np.float32)
    panel[:] = np.rint(float(_RESULT_RENDER_CFG["panelDimFactor"]) * panel).astype(np.uint8)
    overlay[:panel_h, :panel_w] = panel.astype(np.uint8)
    for index, (text, color) in enumerate(lines):
        cv2.putText(
            overlay,
            text,
            (int(_RESULT_RENDER_CFG["textOriginXPx"]), int(_RESULT_RENDER_CFG["textFirstBaselineYPx"]) + index * line_h),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            color,
            int(_RESULT_RENDER_CFG["fontThicknessPx"]),
            cv2.LINE_AA,
        )
    return overlay

# ---------------------------------------------------------------------------
# v31 ideal-only X/Y optimization and geometry QA
# ---------------------------------------------------------------------------
IDEAL_SHIFT_ALGORITHM_VERSION = "ideal-xy-void-min-v3"
DEFAULT_IDEAL_SHIFT_MAX_PX = float(_USER_DEFAULTS["ideal_shift_max_px"])
DEFAULT_IDEAL_SHIFT_STEP_PX = float(_POSITION_CFG["stepPx"])
DEFAULT_PAIR_MIN_DISTANCE_PX = float(_USER_DEFAULTS["pair_min_distance_px"])
DEFAULT_RELATIVE_SHIFT_WARNING_PX = float(_USER_DEFAULTS["relative_shift_warning_px"])


def _shift_contour(contour: np.ndarray, dx: float, dy: float) -> np.ndarray:
    out = np.asarray(contour, dtype=np.float64).copy()
    out[:, 0] += float(dx)
    out[:, 1] += float(dy)
    return out


def _candidate_shift_records(
    shape_hw: Tuple[int, int],
    contour: np.ndarray,
    shared_weight: np.ndarray,
    max_shift_px: float,
    step_px: float,
) -> List[Dict[str, Any]]:
    """Enumerate X/Y translations without rasterizing a mask per candidate.

    The source ideal ROI is rasterized once and scored over the complete image
    with one 2-D correlation. Candidate evaluation is then only a lookup.
    """
    h, w = int(shape_hw[0]), int(shape_hw[1])
    c = np.asarray(contour, dtype=np.float64)
    max_shift = max(0, int(np.floor(float(max_shift_px) + 1e-9)))
    step = max(1, int(round(float(step_px))))
    shifts = list(range(-max_shift, max_shift + 1, step))
    if 0 not in shifts:
        shifts.append(0)
    # Stable order makes the tie-break deterministic and prefers small moves.
    shifts = sorted(set(shifts), key=lambda x: (abs(x), x))
    base_mask = _contour_mask((h, w), c)
    ys, xs = np.nonzero(base_mask)
    roi_pixels = int(xs.size)
    if roi_pixels <= 0:
        return []
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    template = base_mask[y0 : y1 + 1, x0 : x1 + 1].astype(np.float32)
    score_map = cv2.matchTemplate(
        np.asarray(shared_weight, dtype=np.float32),
        template,
        cv2.TM_CCORR,
    )
    records: List[Dict[str, Any]] = []
    for dy in shifts:
        target_y = y0 + int(dy)
        if target_y < 0 or target_y >= score_map.shape[0]:
            continue
        for dx in shifts:
            target_x = x0 + int(dx)
            if target_x < 0 or target_x >= score_map.shape[1]:
                continue
            shifted = _shift_contour(c, dx, dy)
            effective = float(score_map[target_y, target_x])
            rate = effective / float(roi_pixels)
            records.append({
                "dx": float(dx),
                "dy": float(dy),
                "contour": shifted,
                "roiPixels": roi_pixels,
                "effectiveSolderPixels": effective,
                "solderRate": rate,
                "voidRate": 1.0 - rate,
            })
    return records


def _best_nonoverlap_pair(
    left_records: Sequence[Dict[str, Any]],
    right_records: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Choose the best non-overlapping pair without an O(N²) grid scan."""
    import heapq

    def movement(record: Dict[str, Any]) -> Tuple[float, float]:
        dx, dy = abs(float(record["dx"])), abs(float(record["dy"]))
        return dx + dy, max(dx, dy)

    def ordered(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            records,
            key=lambda record: (
                -float(record["effectiveSolderPixels"]),
                movement(record),
                abs(float(record["dy"])),
                abs(float(record["dx"])),
                float(record["dy"]),
                float(record["dx"]),
            ),
        )

    left, right = ordered(left_records), ordered(right_records)
    if not left or not right:
        raise ValueError("no_xy_shift_candidates")

    def priority(i: int, j: int) -> Tuple[float, float, float, int, int]:
        a, b = left[i], right[j]
        a_move, a_peak = movement(a)
        b_move, b_peak = movement(b)
        return (
            -(float(a["effectiveSolderPixels"]) + float(b["effectiveSolderPixels"])),
            a_move + b_move,
            max(a_peak, b_peak),
            i,
            j,
        )

    queue = [priority(0, 0)]
    visited = {(0, 0)}
    while queue:
        _score, _move, _peak, i, j = heapq.heappop(queue)
        a, b = left[i], right[j]
        area, _ = cv2.intersectConvexConvex(
            np.asarray(a["contour"], dtype=np.float32),
            np.asarray(b["contour"], dtype=np.float32),
        )
        if float(area) <= 0.0:
            return a, b
        for next_i, next_j in ((i + 1, j), (i, j + 1)):
            if next_i < len(left) and next_j < len(right) and (next_i, next_j) not in visited:
                visited.add((next_i, next_j))
                heapq.heappush(queue, priority(next_i, next_j))
    raise ValueError("no_nonoverlap_xy_shift_pair")


def _contour_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Minimum Euclidean boundary distance for two convex stadium polygons."""
    aa = np.asarray(a, dtype=np.float32).reshape(-1, 2)
    bb = np.asarray(b, dtype=np.float32).reshape(-1, 2)
    if aa.shape[0] < 3 or bb.shape[0] < 3:
        return float("nan")
    intersection_area, _ = cv2.intersectConvexConvex(aa, bb)
    if float(intersection_area) > 0.0:
        return 0.0
    distances = [
        abs(float(cv2.pointPolygonTest(aa, (float(p[0]), float(p[1])), True)))
        for p in bb
    ]
    distances.extend(
        abs(float(cv2.pointPolygonTest(bb, (float(p[0]), float(p[1])), True)))
        for p in aa
    )
    return float(min(distances)) if distances else float("nan")


def evaluate_geometry_quality(
    contours_canonical: Sequence[np.ndarray],
    shift_diagnostics: Sequence[Dict[str, Any]],
    pair_min_distance_px: float = DEFAULT_PAIR_MIN_DISTANCE_PX,
    relative_shift_warning_px: float = DEFAULT_RELATIVE_SHIFT_WARNING_PX,
) -> Dict[str, Any]:
    """Evaluate bridge risk and four-R relative X/Y displacement.

    R1/R2 are the top-side pair and R3/R4 are the bottom-side pair. A pair
    closer than the configured boundary distance is an immediate FAIL. The
    spread of optimized X or Y shifts produces WARNING when either exceeds its
    threshold. FAIL always dominates WARNING.
    """
    pair_threshold = max(0.0, float(pair_min_distance_px))
    spread_threshold = max(0.0, float(relative_shift_warning_px))
    if len(contours_canonical) != 4:
        return {
            "overallStatus": "FAIL",
            "reasonCode": "ideal_contours_unavailable",
            "pairDistance": {
                "status": "FAIL",
                "thresholdPx": pair_threshold,
                "topDistancePx": None,
                "bottomDistancePx": None,
                "failedPairs": ["R1-R2", "R3-R4"],
            },
            "relativeShift": {
                "status": "UNAVAILABLE",
                "thresholdPx": spread_threshold,
                "spreadPx": None,
                "spreadXPx": None,
                "spreadYPx": None,
                "shiftsPx": [],
            },
        }

    top_distance = _contour_distance(contours_canonical[0], contours_canonical[1])
    bottom_distance = _contour_distance(contours_canonical[2], contours_canonical[3])
    failed_pairs = []
    if not np.isfinite(top_distance) or top_distance < pair_threshold:
        failed_pairs.append("R1-R2")
    if not np.isfinite(bottom_distance) or bottom_distance < pair_threshold:
        failed_pairs.append("R3-R4")
    pair_status = "FAIL" if failed_pairs else "PASS"

    by_id = {
        int(item.get("id", 0)): (
            float(item.get("dxPx", 0.0)),
            float(item.get("dyPx", 0.0)),
        )
        for item in shift_diagnostics
        if isinstance(item, dict)
        and _finite_number(item.get("dxPx"))
        and _finite_number(item.get("dyPx", 0.0))
    }
    shifts = [by_id[index] for index in (1, 2, 3, 4) if index in by_id]
    if len(shifts) == 4:
        dx_values = [value[0] for value in shifts]
        dy_values = [value[1] for value in shifts]
        min_shift = float(min(dx_values))
        max_shift = float(max(dx_values))
        spread_x = max_shift - min_shift
        spread_y = float(max(dy_values) - min(dy_values))
        spread = max(spread_x, spread_y)
        shift_status = "WARNING" if spread > spread_threshold else "PASS"
    else:
        min_shift = max_shift = spread_x = spread_y = spread = None
        shift_status = "UNAVAILABLE"
    overall = "FAIL" if pair_status == "FAIL" else (
        "WARNING" if shift_status in {"WARNING", "UNAVAILABLE"} else "PASS"
    )
    return {
        "overallStatus": overall,
        "reasonCode": "pair_distance_too_close" if pair_status == "FAIL" else (
            "relative_shift_excessive" if shift_status == "WARNING" else ""
        ),
        "pairDistance": {
            "status": pair_status,
            "thresholdPx": pair_threshold,
            "topDistancePx": float(top_distance) if np.isfinite(top_distance) else None,
            "bottomDistancePx": float(bottom_distance) if np.isfinite(bottom_distance) else None,
            "failedPairs": failed_pairs,
        },
        "relativeShift": {
            "status": shift_status,
            "thresholdPx": spread_threshold,
            "spreadPx": spread,
            "spreadXPx": spread_x,
            "spreadYPx": spread_y,
            "minDxPx": min_shift,
            "maxDxPx": max_shift,
            "shiftsPx": [
                {"dxPx": value[0], "dyPx": value[1]}
                for value in shifts
            ],
        },
    }


def measure_solder_indication_shifted_ideal(
    gray_canonical: np.ndarray,
    registered_ideal_contours_canonical: Sequence[np.ndarray],
    gray_reference: float,
    reference_ceiling_pct: float = DEFAULT_REFERENCE_CEILING_PCT,
    center_offset_pct: float = DEFAULT_CENTER_OFFSET_PCT,
    transition_pct: float = DEFAULT_TRANSITION_PCT,
    max_shift_px: float = DEFAULT_IDEAL_SHIFT_MAX_PX,
    step_px: float = DEFAULT_IDEAL_SHIFT_STEP_PX,
) -> Tuple[
    Dict[str, Any],
    np.ndarray,
    List[np.ndarray],
    List[np.ndarray],
    List[Dict[str, Any]],
]:
    """Shift each registered ideal stadium along X/Y to minimize void rate.

    The solder gray reference S and the per-pixel membership field are frozen
    from the *unshifted* globally registered ideal ROIs. X/Y searching
    therefore cannot game the classifier by changing S; it only chooses where
    the fixed ideal geometry sits on the existing raw-X-ray solder-weight map.

    Top and bottom left/right pairs are optimized jointly and are forbidden to
    overlap. Shape, width, height, angle, and area are unchanged; only X/Y
    translation is allowed.
    """
    gray = np.asarray(gray_canonical)
    if gray.ndim != 2 or len(registered_ideal_contours_canonical) != 4:
        unavailable = unavailable_result(
            "ideal_contours_unavailable",
            reference_ceiling_pct,
            center_offset_pct,
            transition_pct,
        )
        optimization = {
            "status": "unavailable",
            "reasonCode": "ideal_contours_unavailable",
        }
        unavailable.update({
            "algorithmVersion": IDEAL_SHIFT_ALGORITHM_VERSION,
            "primaryBasis": "ideal_shifted",
            "positionOptimization": optimization,
            "horizontalOptimization": optimization,
        })
        return unavailable, np.zeros(gray.shape[:2], np.float32), [], [], []

    base_metric, _base_union, _base_masks = measure_solder_indication(
        gray,
        registered_ideal_contours_canonical,
        gray_reference,
        reference_ceiling_pct,
        center_offset_pct,
        transition_pct,
    )
    if base_metric.get("status") != "ok":
        result = dict(base_metric)
        result["algorithmVersion"] = IDEAL_SHIFT_ALGORITHM_VERSION
        result["primaryBasis"] = "ideal_shifted"
        optimization = {
            "status": "unavailable",
            "reasonCode": str(base_metric.get("reasonCode") or "reference_unavailable"),
        }
        result["positionOptimization"] = optimization
        result["horizontalOptimization"] = optimization
        return result, np.zeros(gray.shape, np.float32), [], [], []

    shared_weight = _shared_weight_from_metric(gray, base_metric)
    candidate_sets = [
        _candidate_shift_records(
            gray.shape,
            np.asarray(contour, dtype=np.float64),
            shared_weight,
            max_shift_px,
            step_px,
        )
        for contour in registered_ideal_contours_canonical
    ]
    if any(len(records) == 0 for records in candidate_sets):
        result = dict(base_metric)
        result["status"] = "unavailable"
        result["reasonCode"] = "xy_shift_candidates_empty"
        result["algorithmVersion"] = IDEAL_SHIFT_ALGORITHM_VERSION
        result["primaryBasis"] = "ideal_shifted"
        return result, np.zeros(gray.shape, np.float32), [], [], []

    try:
        top_left, top_right = _best_nonoverlap_pair(candidate_sets[0], candidate_sets[1])
        bottom_left, bottom_right = _best_nonoverlap_pair(candidate_sets[2], candidate_sets[3])
    except ValueError as exc:
        result = dict(base_metric)
        result["status"] = "unavailable"
        result["reasonCode"] = str(exc)
        result["algorithmVersion"] = IDEAL_SHIFT_ALGORITHM_VERSION
        result["primaryBasis"] = "ideal_shifted"
        return result, np.zeros(gray.shape, np.float32), [], [], []

    selected = [top_left, top_right, bottom_left, bottom_right]
    shifted_contours = [np.asarray(item["contour"], dtype=np.float64) for item in selected]
    shifted_variant, shifted_union_weight, shifted_masks = _summarize_shared_weight(
        gray,
        shifted_contours,
        shared_weight,
        base_metric,
        "ideal_shifted",
    )
    if shifted_variant.get("status") != "ok":
        result = dict(base_metric)
        result.update({
            "status": "unavailable",
            "reasonCode": str(shifted_variant.get("reasonCode") or "shifted_summary_failed"),
            "algorithmVersion": IDEAL_SHIFT_ALGORITHM_VERSION,
            "primaryBasis": "ideal_shifted",
        })
        return result, np.zeros(gray.shape, np.float32), [], [], []

    base_masks = [_contour_mask(gray.shape, np.asarray(c, dtype=np.float64)) for c in registered_ideal_contours_canonical]
    diagnostics: List[Dict[str, Any]] = []
    for region_id, (base_mask, chosen, records) in enumerate(zip(base_masks, selected, candidate_sets), start=1):
        base_pixels = int(np.count_nonzero(base_mask))
        base_effective = float(np.sum(shared_weight[base_mask], dtype=np.float64))
        base_rate = base_effective / float(max(base_pixels, 1))
        diagnostics.append({
            "id": int(region_id),
            "dxPx": float(chosen["dx"]),
            "dyPx": float(chosen["dy"]),
            "searchMinXPx": float(min(float(x["dx"]) for x in records)),
            "searchMaxXPx": float(max(float(x["dx"]) for x in records)),
            "searchMinYPx": float(min(float(x["dy"]) for x in records)),
            "searchMaxYPx": float(max(float(x["dy"]) for x in records)),
            "searchStepPx": float(step_px),
            "candidateCount": int(len(records)),
            "solderRateBefore": float(base_rate),
            "solderRateAfter": float(chosen["solderRate"]),
            "voidRateBefore": float(1.0 - base_rate),
            "voidRateAfter": float(chosen["voidRate"]),
            "voidRateImprovement": float(chosen["solderRate"] - base_rate),
        })

    result = dict(base_metric)
    for key in (
        "overallRate", "rateLower", "rateUpper", "totalEffectiveSolderPixels",
        "sureSolderPixels", "sureVoidPixels", "uncertainPixels",
        "totalRoiPixels", "regions",
    ):
        result[key] = shifted_variant.get(key)
    result["algorithmVersion"] = IDEAL_SHIFT_ALGORITHM_VERSION
    result["schemaVersion"] = 8
    result["primaryBasis"] = "ideal_shifted"
    result["sharedReferenceBasis"] = "registered_ideal_unshifted"
    result["variants"] = {"idealShifted": shifted_variant}
    result["emptyAreaRate"] = 1.0 - float(shifted_variant["overallRate"])
    optimization = {
        "status": "ok",
        "reasonCode": "",
        "mode": "xy_translation",
        "objective": "minimize_weighted_void_area_rate",
        "maxShiftPx": float(max_shift_px),
        "stepPx": float(step_px),
        "referenceFrozenBeforeSearch": True,
        "regions": diagnostics,
    }
    result["positionOptimization"] = optimization
    result["horizontalOptimization"] = optimization
    for region in result.get("regions") or []:
        if isinstance(region, dict) and isinstance(region.get("rate"), (int, float)):
            region["emptyRate"] = 1.0 - float(region["rate"])

    return result, shifted_union_weight, shifted_masks, shifted_contours, diagnostics
