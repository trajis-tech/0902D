# -*- coding: utf-8 -*-
"""Product registration for the approved four analytic stadiums.

Only approved ideal geometry exists in the production path. The package
rectangle resolves orientation, non-solder product structure estimates one
global affine transform, and the stadiums are transported to the current crop.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import cv2
import numpy as np

from contour_render import render_mask, render_overlay_color
from config_loader import section

_ORIENTATION_CFG = section("orientation")
_REGISTRATION_CFG = section("registration")
_IDEAL_GEOMETRY = section("idealGeometry")


if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    ASSET_DIR = Path(sys._MEIPASS) / "app" / "assets" / "board_v1"
else:
    ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "board_v1"

ALGORITHM_VERSION = "ideal-geometry-registration-v31"
IDEAL_ALGORITHM_VERSION = "analytic-stadium-prior-v2"


class RegistrationError(RuntimeError):
    """Raised when registration cannot produce a trustworthy ideal set."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class OrientationResult:
    orientation: str
    score_a: float
    score_b: float
    margin: float
    applied: int
    min_score: float
    min_margin: float

    @property
    def certain(self) -> bool:
        return self.orientation in {"A", "B"}

    def as_metrics(self) -> Dict[str, Any]:
        return {
            "orientation": self.orientation,
            "orientationScoreA": float(self.score_a),
            "orientationScoreB": float(self.score_b),
            "orientationMargin": float(self.margin),
            "orientationApplied": int(self.applied),
        }


def _read_gray(path: Path) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RegistrationError("assets_missing", f"找不到或無法解碼註冊資產：{path}")
    return np.ascontiguousarray(image)


def _pixel_sha256(image: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(str(tuple(int(v) for v in image.shape)).encode("ascii"))
    digest.update(str(image.dtype).encode("ascii"))
    digest.update(np.ascontiguousarray(image).tobytes())
    return digest.hexdigest().upper()


@lru_cache(maxsize=1)
def _assets() -> Dict[str, Any]:
    meta_path = ASSET_DIR / "metadata.json"
    if not meta_path.is_file():
        raise RegistrationError("assets_missing", f"找不到註冊資產：{ASSET_DIR}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    ideal_asset = _IDEAL_GEOMETRY
    orientation_reference = _read_gray(ASSET_DIR / "orientation_reference.png")
    canonical_crop_gray = _read_gray(ASSET_DIR / "canonical_crop_gray.png")
    expected_shape = tuple(int(v) for v in meta["cropShape"])
    if canonical_crop_gray.shape != expected_shape:
        raise RegistrationError(
            "assets_invalid",
            f"canonical raw crop 尺寸錯誤：{canonical_crop_gray.shape} != {expected_shape}",
        )
    expected_hash = str(meta.get("canonicalRawCropPixelSha256") or "")
    if expected_hash and _pixel_sha256(canonical_crop_gray) != expected_hash:
        raise RegistrationError("assets_invalid", "canonical raw crop 雜湊不符")
    capsules = list(ideal_asset.get("capsules") or [])
    if ideal_asset.get("geometry") != "analytic-stadium" or len(capsules) != 4:
        raise RegistrationError("assets_invalid", "解析式膠囊資產格式錯誤")
    if tuple(int(v) for v in ideal_asset.get("cropShape") or ()) != expected_shape:
        raise RegistrationError("assets_invalid", "解析式膠囊資產尺寸錯誤")
    if [int(item.get("id") or 0) for item in capsules] != [1, 2, 3, 4]:
        raise RegistrationError("assets_invalid", "解析式膠囊實體 ID 錯誤")
    return {
        "meta": meta,
        "orientationReference": orientation_reference,
        "canonicalCropGray": canonical_crop_gray,
        "idealCapsules": capsules,
    }


def _robust_unit(
    gray: np.ndarray,
    size_wh: Tuple[int, int],
    sigma: float,
    cfg: Dict[str, Any],
) -> np.ndarray:
    resized = cv2.resize(gray, size_wh, interpolation=cv2.INTER_AREA).astype(np.float32)
    lo, hi = np.percentile(
        resized,
        (float(cfg["normalizePercentileLow"]), float(cfg["normalizePercentileHigh"])),
    )
    if float(hi - lo) < float(cfg["normalizeMinRange"]):
        return np.zeros(resized.shape, dtype=np.float32)
    unit = np.clip((resized - float(lo)) / float(hi - lo), 0.0, 1.0)
    if sigma > 0:
        unit = cv2.GaussianBlur(unit, (0, 0), float(sigma))
    return np.ascontiguousarray(unit.astype(np.float32))


def _orientation_feature(gray: np.ndarray, reference_shape: Tuple[int, int], max_width: int) -> np.ndarray:
    ref_h, ref_w = int(reference_shape[0]), int(reference_shape[1])
    width = min(max(int(_ORIENTATION_CFG["featureMinWidth"]), int(max_width)), ref_w)
    height = max(
        int(_ORIENTATION_CFG["featureMinHeight"]),
        int(round(width * ref_h / max(ref_w, 1))),
    )
    return _robust_unit(
        gray,
        (width, height),
        sigma=float(_ORIENTATION_CFG["featureSigma"]),
        cfg=_ORIENTATION_CFG,
    )


def _ecc_translation(reference: np.ndarray, candidate: np.ndarray) -> float:
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        int(_ORIENTATION_CFG["eccMaxIterations"]),
        float(_ORIENTATION_CFG["eccEpsilon"]),
    )
    try:
        score, _ = cv2.findTransformECC(
            reference, candidate, warp, cv2.MOTION_TRANSLATION, criteria, None,
            int(_ORIENTATION_CFG["eccGaussianFilterSize"]),
        )
        return float(score)
    except cv2.error:
        return -1.0


def detect_orientation(full_rect_gray: np.ndarray) -> OrientationResult:
    assets = _assets()
    cfg = _ORIENTATION_CFG
    min_score = float(cfg["minScore"])
    min_margin = float(cfg["minMargin"])
    max_width = int(cfg["maxWidth"])
    reference_raw = assets["orientationReference"]
    reference = _orientation_feature(reference_raw, reference_raw.shape, max_width)
    candidate_a = _orientation_feature(full_rect_gray, reference_raw.shape, max_width)
    candidate_b = cv2.rotate(candidate_a, cv2.ROTATE_180)
    score_a = _ecc_translation(reference, candidate_a)
    score_b = _ecc_translation(reference, candidate_b)
    best = max(score_a, score_b)
    margin = abs(score_a - score_b)
    if best < min_score or margin < min_margin:
        return OrientationResult("uncertain", score_a, score_b, margin, 0, min_score, min_margin)
    if score_a >= score_b:
        return OrientationResult("A", score_a, score_b, margin, 0, min_score, min_margin)
    return OrientationResult("B", score_a, score_b, margin, 180, min_score, min_margin)


def canonicalize(image: np.ndarray, orientation: OrientationResult) -> np.ndarray:
    if orientation.orientation == "B":
        return cv2.rotate(image, cv2.ROTATE_180)
    return np.ascontiguousarray(image)


def restore_orientation(image: np.ndarray, orientation: OrientationResult) -> np.ndarray:
    return canonicalize(image, orientation)


def _normalized_correlation(a: np.ndarray, b: np.ndarray) -> float:
    aa = a.astype(np.float64) - float(np.mean(a))
    bb = b.astype(np.float64) - float(np.mean(b))
    denominator = float(np.linalg.norm(aa) * np.linalg.norm(bb))
    return float(np.sum(aa * bb) / denominator) if denominator > 1e-12 else -1.0


def _forward_matrix_from_ecc(target: np.ndarray, reference: np.ndarray, warp: np.ndarray) -> np.ndarray:
    h, w = target.shape
    direct = cv2.warpAffine(
        reference, warp, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
    )
    inverse = cv2.warpAffine(
        reference,
        warp,
        (w, h),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    if _normalized_correlation(target, inverse) > _normalized_correlation(target, direct):
        return np.asarray(cv2.invertAffineTransform(warp), dtype=np.float64)
    return np.asarray(warp, dtype=np.float64)


def _validate_transform(matrix: np.ndarray, shape_hw: Tuple[int, int], cfg: Dict[str, Any]) -> None:
    h, w = int(shape_hw[0]), int(shape_hw[1])
    linear = np.asarray(matrix[:, :2], dtype=np.float64)
    singular = np.linalg.svd(linear, compute_uv=False)
    if float(np.min(singular)) < float(cfg["minScale"]) or float(np.max(singular)) > float(cfg["maxScale"]):
        raise RegistrationError("registration_transform_invalid", f"配準縮放超出範圍：{singular.tolist()}")
    limit = float(cfg["maxTranslationRatio"]) * float(max(h, w))
    if float(np.linalg.norm(matrix[:, 2])) > limit:
        raise RegistrationError("registration_transform_invalid", f"配準位移過大：{matrix[:, 2].tolist()}")
    if float(np.linalg.det(linear)) <= 0:
        raise RegistrationError("registration_transform_invalid", "配準發生鏡射或退化")


def _affine_points(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    return np.hstack([pts, np.ones((pts.shape[0], 1), dtype=np.float64)]) @ np.asarray(matrix, dtype=np.float64).T


def _scale_to_target(contour: np.ndarray, ref_shape: Tuple[int, int], target_shape: Tuple[int, int]) -> np.ndarray:
    ref_h, ref_w = int(ref_shape[0]), int(ref_shape[1])
    target_h, target_w = int(target_shape[0]), int(target_shape[1])
    out = np.asarray(contour, dtype=np.float64).copy()
    out[:, 0] *= float(max(target_w - 1, 1)) / float(max(ref_w - 1, 1))
    out[:, 1] *= float(max(target_h - 1, 1)) / float(max(ref_h - 1, 1))
    return out


def _map_from_canonical(contour: np.ndarray, shape_hw: Tuple[int, int], orientation: OrientationResult) -> np.ndarray:
    out = np.asarray(contour, dtype=np.float64).copy()
    if orientation.orientation == "B":
        h, w = int(shape_hw[0]), int(shape_hw[1])
        out[:, 0] = float(w - 1) - out[:, 0]
        out[:, 1] = float(h - 1) - out[:, 1]
    return out


def map_contours_from_canonical(
    contours: Sequence[np.ndarray],
    shape_hw: Tuple[int, int],
    orientation: OrientationResult,
) -> List[np.ndarray]:
    return [_map_from_canonical(contour, shape_hw, orientation) for contour in contours]


def _stadium_points(
    spec: Dict[str, Any],
    cap_samples: int | None = None,
) -> np.ndarray:
    if cap_samples is None:
        cap_samples = int(_REGISTRATION_CFG["capSamples"])
    center = np.asarray(spec["center"], dtype=np.float64)
    axis = np.asarray(spec["axis"], dtype=np.float64)
    axis /= max(float(np.linalg.norm(axis)), 1e-12)
    normal = np.asarray([-axis[1], axis[0]], dtype=np.float64)
    straight_half = max(0.0, float(spec["straightHalfLength"]))
    radius = max(0.5, float(spec["radius"]))
    right = center + straight_half * axis
    left = center - straight_half * axis
    angle_right = np.linspace(-0.5 * np.pi, 0.5 * np.pi, int(cap_samples))
    angle_left = np.linspace(0.5 * np.pi, 1.5 * np.pi, int(cap_samples))
    right_cap = right + radius * (np.cos(angle_right)[:, None] * axis + np.sin(angle_right)[:, None] * normal)
    left_cap = left + radius * (np.cos(angle_left)[:, None] * axis + np.sin(angle_left)[:, None] * normal)
    return np.vstack([right_cap, left_cap])


def _central_offset(outer_shape: Tuple[int, int], inner_shape: Tuple[int, int]) -> Tuple[float, float]:
    return (
        0.5 * float(int(outer_shape[1]) - int(inner_shape[1])),
        0.5 * float(int(outer_shape[0]) - int(inner_shape[0])),
    )


def _product_registration_mask(
    full_shape: Tuple[int, int],
    crop_shape: Tuple[int, int],
    ideal_specs: Sequence[Dict[str, Any]],
) -> np.ndarray:
    mask = np.full(tuple(int(v) for v in full_shape), 255, dtype=np.uint8)
    offset = np.asarray(_central_offset(full_shape, crop_shape), dtype=np.float64)
    for spec in ideal_specs:
        expanded = dict(spec)
        radius = float(spec["radius"])
        expanded["radius"] = float(_REGISTRATION_CFG["maskRadiusScale"]) * radius
        expanded["straightHalfLength"] = (
            float(spec["straightHalfLength"])
            + float(_REGISTRATION_CFG["maskStraightExtensionRadiusScale"]) * radius
        )
        points = _stadium_points(expanded) + offset
        cv2.fillPoly(mask, [np.rint(points).astype(np.int32)], 0, lineType=cv2.LINE_8)
    border = max(
        int(_REGISTRATION_CFG["maskMinBorderPx"]),
        int(round(float(_REGISTRATION_CFG["maskBorderRatio"]) * float(min(full_shape)))),
    )
    mask[:border, :] = mask[-border:, :] = 0
    mask[:, :border] = mask[:, -border:] = 0
    return mask


def _estimate_product_registration(
    full_rect_canonical: np.ndarray,
    reference_full: np.ndarray,
    valid_mask: np.ndarray,
    max_width: int | None = None,
    max_iterations: int | None = None,
) -> Tuple[float, np.ndarray, float]:
    ref_h, ref_w = reference_full.shape
    if max_width is None:
        max_width = int(_REGISTRATION_CFG["productPoseMaxWidth"])
    if max_iterations is None:
        max_iterations = int(_REGISTRATION_CFG["productPoseMaxIterations"])
    work_w = min(ref_w, max(int(_REGISTRATION_CFG["productPoseMinWorkWidth"]), int(max_width)))
    work_h = max(
        int(_REGISTRATION_CFG["productPoseMinWorkHeight"]),
        int(round(float(work_w) * float(ref_h) / float(max(ref_w, 1)))),
    )
    size = (work_w, work_h)
    target = _robust_unit(
        full_rect_canonical, size, sigma=float(_REGISTRATION_CFG["featureSigma"]), cfg=_REGISTRATION_CFG,
    )
    reference = _robust_unit(
        reference_full, size, sigma=float(_REGISTRATION_CFG["featureSigma"]), cfg=_REGISTRATION_CFG,
    )
    mask = cv2.resize(valid_mask, size, interpolation=cv2.INTER_NEAREST)
    valid = mask > 0
    if int(np.count_nonzero(valid)) < int(float(_REGISTRATION_CFG["maskMinValidFraction"]) * mask.size):
        raise RegistrationError("product_pose_mask_invalid", "產品級配準有效區域不足")
    target_phase = np.where(valid, target, float(np.mean(target[valid]))).astype(np.float32)
    reference_phase = np.where(valid, reference, float(np.mean(reference[valid]))).astype(np.float32)
    warp = np.eye(2, 3, dtype=np.float32)
    phase_response = 0.0
    try:
        shift, phase_response = cv2.phaseCorrelate(reference_phase, target_phase)
        if np.all(np.isfinite(shift)):
            warp[0, 2], warp[1, 2] = float(shift[0]), float(shift[1])
    except cv2.error:
        pass
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        max(int(_REGISTRATION_CFG["eccMinIterations"]), int(max_iterations)),
        float(_REGISTRATION_CFG["eccEpsilon"]),
    )
    try:
        score, ecc_warp = cv2.findTransformECC(
            target, reference, warp, cv2.MOTION_AFFINE, criteria, mask,
            int(_REGISTRATION_CFG["eccGaussianFilterSize"]),
        )
    except cv2.error as exc:
        raise RegistrationError("product_pose_ecc_failed", f"產品級 ECC 配準失敗：{exc}") from exc
    work_matrix = _forward_matrix_from_ecc(
        target, reference, np.asarray(ecc_warp, dtype=np.float64),
    )
    scale_x = float(ref_w) / float(work_w)
    scale_y = float(ref_h) / float(work_h)
    to_full = np.asarray([[scale_x, 0.0, 0.0], [0.0, scale_y, 0.0], [0.0, 0.0, 1.0]])
    to_work = np.asarray([[1.0 / scale_x, 0.0, 0.0], [0.0, 1.0 / scale_y, 0.0], [0.0, 0.0, 1.0]])
    work_affine = np.vstack([work_matrix, [0.0, 0.0, 1.0]])
    full_matrix = (to_full @ work_affine @ to_work)[:2]
    return float(score), full_matrix, float(phase_response)


def _local_matrix_from_full(
    full_matrix: np.ndarray,
    full_shape: Tuple[int, int],
    crop_shape: Tuple[int, int],
) -> np.ndarray:
    ox, oy = _central_offset(full_shape, crop_shape)
    to_full = np.asarray([[1.0, 0.0, ox], [0.0, 1.0, oy], [0.0, 0.0, 1.0]])
    from_full = np.asarray([[1.0, 0.0, -ox], [0.0, 1.0, -oy], [0.0, 0.0, 1.0]])
    affine = np.vstack([np.asarray(full_matrix, dtype=np.float64), [0.0, 0.0, 1.0]])
    return (from_full @ affine @ to_full)[:2]


def _validate_ideal_contours(contours: Sequence[np.ndarray], shape_hw: Tuple[int, int]) -> None:
    if len(contours) != 4:
        raise RegistrationError("ideal_count_invalid", "理想膠囊數不是 4")
    h, w = int(shape_hw[0]), int(shape_hw[1])
    occupancy = np.zeros((h, w), dtype=np.uint8)
    for index, contour in enumerate(contours, start=1):
        points = np.asarray(contour, dtype=np.float64)
        if (
            points.ndim != 2
            or points.shape[0] < int(_REGISTRATION_CFG["idealMinContourPoints"])
            or points.shape[1] != 2
        ):
            raise RegistrationError("ideal_geometry_invalid", f"第 {index} 顆理想膠囊幾何錯誤")
        if float(np.min(points[:, 0])) < 0 or float(np.max(points[:, 0])) > w - 1 or float(np.min(points[:, 1])) < 0 or float(np.max(points[:, 1])) > h - 1:
            raise RegistrationError("ideal_out_of_bounds", f"第 {index} 顆理想膠囊超出裁剪區")
        layer = render_mask((h, w), [points], (0, 0)) > 0
        if int(np.count_nonzero(layer)) < int(_REGISTRATION_CFG["idealMinAreaPixels"]):
            raise RegistrationError("ideal_area_invalid", f"第 {index} 顆理想膠囊面積過小")
        occupancy += layer.astype(np.uint8)
    if np.any(occupancy > 1):
        raise RegistrationError("ideal_overlap", "理想膠囊互相重疊")


def _contour_support(gray: np.ndarray, contour: np.ndarray) -> float:
    image = np.asarray(gray, dtype=np.float64) / 255.0
    values = []
    for point in np.asarray(contour, dtype=np.float64)[:: int(_REGISTRATION_CFG["contourSupportStride"])]:
        x = int(np.clip(np.rint(point[0]), 0, image.shape[1] - 1))
        y = int(np.clip(np.rint(point[1]), 0, image.shape[0] - 1))
        values.append(float(image[y, x]))
    return float(np.mean(values)) if values else 0.0


def _empty_ideal_set(canvas_bgr: np.ndarray, code: str, message: str = "") -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "reasonCode": str(code),
        "contours": [],
        "contoursCanonical": [],
        "overlayBgr": canvas_bgr.copy(),
        "mask": np.zeros(canvas_bgr.shape[:2], dtype=np.uint8),
        "regionCount": 0,
        "okCount": 0,
        "confidence": 0.0,
        "algorithmVersion": IDEAL_ALGORITHM_VERSION,
        "diagnostics": [{"ok": False, "rejectionReason": str(code), "message": str(message)}],
        "registrationScore": 0.0,
        "registrationTransform": [],
        "registrationLocalTransform": [],
        "registrationPhaseResponse": 0.0,
        "poseSource": "",
    }


def registered_capsules(
    raw_canonical: np.ndarray,
    canvas_bgr: np.ndarray,
    origin: Tuple[int, int],
    orientation: OrientationResult,
    full_rect_canonical: np.ndarray | None = None,
    background_reference_g: float | None = None,
) -> Dict[str, Any]:
    del background_reference_g
    if not orientation.certain:
        raise RegistrationError("orientation_uncertain", "方向分數不足或兩方向過於接近")
    raw = np.asarray(raw_canonical)
    if raw.ndim != 2 or raw.size == 0:
        raise RegistrationError("raw_crop_invalid", "canonical raw crop 無效")
    assets = _assets()
    meta = assets["meta"]
    cfg = _REGISTRATION_CFG
    reference_crop = assets["canonicalCropGray"]
    reference_full = assets["orientationReference"]
    reference_ideal = [_stadium_points(spec) for spec in assets["idealCapsules"]]
    score = 1.0
    phase_response = 1.0
    pose_source = "rect_normalized_identity"
    full_matrix = np.eye(2, 3, dtype=np.float64)
    local_matrix = np.eye(2, 3, dtype=np.float64)
    if full_rect_canonical is not None:
        pose_source = "full_rect_non_measurement_affine"
        valid_mask = _product_registration_mask(reference_full.shape, reference_crop.shape, assets["idealCapsules"])
        score, full_matrix, phase_response = _estimate_product_registration(
            np.asarray(full_rect_canonical),
            reference_full,
            valid_mask,
            int(cfg["productPoseMaxWidth"]),
            int(cfg["productPoseMaxIterations"]),
        )
        min_score = float(cfg["productPoseMinScore"])
        if score < min_score:
            raise RegistrationError("product_pose_low_score", f"產品級配準分數 {score:.4f} 低於 {min_score:.4f}")
        _validate_transform(full_matrix, reference_full.shape, cfg)
        local_matrix = _local_matrix_from_full(full_matrix, reference_full.shape, reference_crop.shape)
        _validate_transform(local_matrix, reference_crop.shape, cfg)
    registered_ref = [_affine_points(local_matrix, contour) for contour in reference_ideal]
    _validate_ideal_contours(registered_ref, reference_crop.shape)
    canonical = [_scale_to_target(contour, reference_crop.shape, raw.shape) for contour in registered_ref]
    _validate_ideal_contours(canonical, raw.shape)
    display = map_contours_from_canonical(canonical, raw.shape, orientation)
    overlay = render_overlay_color(canvas_bgr, display, origin, (255, 255, 0))
    mask = render_mask(canvas_bgr.shape[:2], display, origin)
    diagnostics = [
        {
            "ok": True,
            "regionId": index,
            "geometry": "analytic_stadium",
            "shapeSource": "approved_prior_asset",
            "poseSource": pose_source,
            "localImageAffectsGeometry": False,
            "localRawMeanOnPrior": _contour_support(raw, contour),
        }
        for index, contour in enumerate(canonical, start=1)
    ]
    ideal = {
        "status": "ok",
        "reasonCode": "",
        "contours": [contour.tolist() for contour in display],
        "contoursCanonical": [contour.tolist() for contour in canonical],
        "overlayBgr": overlay,
        "mask": mask,
        "regionCount": 4,
        "okCount": 4,
        "confidence": float(np.clip(score, 0.0, 1.0)),
        "algorithmVersion": IDEAL_ALGORITHM_VERSION,
        "diagnostics": diagnostics,
        "registrationScore": float(score),
        "registrationTransform": full_matrix.tolist(),
        "registrationLocalTransform": local_matrix.tolist(),
        "registrationPhaseResponse": float(phase_response),
        "poseSource": pose_source,
    }
    return {
        "ideal": ideal,
        "warnings": [],
        "algorithmVersion": ALGORITHM_VERSION,
        "failureCode": "",
    }


def empty_capsules(canvas_bgr: np.ndarray, code: str, warning: str) -> Dict[str, Any]:
    return {
        "ideal": _empty_ideal_set(canvas_bgr, code, warning),
        "warnings": [str(warning)],
        "algorithmVersion": ALGORITHM_VERSION,
        "failureCode": str(code),
    }
