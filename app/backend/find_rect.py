# -*- coding: utf-8 -*-
"""Package rectangle from original-gray THRESH_BINARY.

Primary: THRESH_BINARY at a G-scaled thresh (not inverted) → open specks →
largest external black contour → minAreaRect.
Fallback: inverted Otsu on y_rect → same largest-contour minAreaRect.
No aspect-ratio gate, no hull of all black pixels, no whole-image close.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from config_loader import section, user_defaults

_DETECT = section("productDetection")
_USER_DEFAULTS = user_defaults()


def _aspect(w: float, h: float) -> float:
    short = min(float(w), float(h))
    if short <= 0:
        return 0.0
    return float(max(float(w), float(h))) / short


def _clamp_box(x: int, y: int, w: int, h: int, width: int, height: int) -> Tuple[int, int, int, int]:
    x = max(0, min(int(x), width - 1))
    y = max(0, min(int(y), height - 1))
    w = max(1, min(int(w), width - x))
    h = max(1, min(int(h), height - y))
    return x, y, w, h


def _aabb_from_corners(corners: np.ndarray, width: int, height: int) -> Dict[str, int]:
    xs = corners[:, 0]
    ys = corners[:, 1]
    env_x = int(np.floor(xs.min()))
    env_y = int(np.floor(ys.min()))
    env_w = int(np.ceil(xs.max())) - env_x
    env_h = int(np.ceil(ys.max())) - env_y
    env_x, env_y, env_w, env_h = _clamp_box(env_x, env_y, env_w, env_h, width, height)
    return {"x": env_x, "y": env_y, "w": env_w, "h": env_h}


def _is_full_frame(aabb: Dict[str, int], width: int, height: int) -> bool:
    """Reject a box that is essentially the image itself."""
    img_area = float(max(1, width * height))
    area = float(aabb["w"] * aabb["h"])
    if area > float(_DETECT["fullFrameRejectAreaRatio"]) * img_area:
        return True
    if (
        aabb["w"] >= int(float(_DETECT["fullFrameRejectWidthRatio"]) * width)
        and aabb["h"] >= int(float(_DETECT["fullFrameRejectHeightRatio"]) * height)
    ):
        return True
    return False


def _normalize_min_area_rect(
    raw: Tuple[Tuple[float, float], Tuple[float, float], float],
) -> Tuple[float, float, float, float, float]:
    """Make W the long side; angle is that side vs image +x, in (-90, 90]."""
    (cx, cy), (rw, rh), ang = raw
    cx, cy = float(cx), float(cy)
    rw, rh, ang = float(rw), float(rh), float(ang)
    if rw < rh:
        rw, rh = rh, rw
        ang += 90.0
    while ang > 90.0:
        ang -= 180.0
    while ang <= -90.0:
        ang += 180.0
    return cx, cy, rw, rh, ang


def _pack_rect(
    contour: np.ndarray,
    width: int,
    height: int,
    min_area: float,
    source: str,
    candidate_count: int,
) -> Optional[Dict[str, Any]]:
    if contour is None or len(contour) < 3:
        return None
    if float(cv2.contourArea(contour)) < min_area:
        return None
    cx, cy, rw, rh, angle = _normalize_min_area_rect(cv2.minAreaRect(contour.astype(np.float32)))
    area = float(rw * rh)
    if area < min_area:
        return None
    corners = cv2.boxPoints(((cx, cy), (rw, rh), angle)).astype(np.float64)
    aabb = _aabb_from_corners(corners, width, height)
    if _is_full_frame(aabb, width, height):
        return None
    return {
        "x": int(aabb["x"]),
        "y": int(aabb["y"]),
        "w": float(rw),
        "h": float(rh),
        "cx": float(cx),
        "cy": float(cy),
        "angle": float(angle),
        "corners": corners.tolist(),
        "aspect": _aspect(rw, rh),
        "area": area,
        "candidateCount": int(candidate_count),
        "source": source,
        "aabb": aabb,
    }


_OPEN_RATIO = float(_DETECT["openKernelRatio"])
_OPEN_MIN_PX = int(_DETECT["openKernelMinPx"])


def _odd_kernel(ratio: float, min_side: int) -> int:
    k = int(round(float(ratio) * float(min_side)))
    k = max(_OPEN_MIN_PX, k)
    if k % 2 == 0:
        k += 1
    return k


def _rect_bin_thresh(params: Dict[str, Any]) -> float:
    """Absolute THRESH_BINARY level already scaled from G by the pipeline."""
    if params.get("rect_bin_thresh") in (None, ""):
        raise ValueError("缺少已縮放的找框門檻 rect_bin_thresh。")
    return min(255.0, max(0.0, float(params["rect_bin_thresh"])))


def rect_binary(gray: np.ndarray, params: Dict[str, Any]) -> np.ndarray:
    """Simple THRESH_BINARY on original gray. Package black, surround white."""
    if gray.ndim != 2:
        raise ValueError("原圖必須是單通道。")
    _t, binary = cv2.threshold(gray, _rect_bin_thresh(params), 255, cv2.THRESH_BINARY)
    return binary


def find_rect_from_binary(gray: np.ndarray, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Open specks, then minAreaRect of the largest black outer contour."""
    if gray.ndim != 2:
        raise ValueError("原圖必須是單通道。")
    height, width = gray.shape[:2]
    min_side = min(height, width)
    min_area_ratio = float(params.get("min_area_ratio", _USER_DEFAULTS["min_area_ratio"]))
    min_area = max(1.0, min_area_ratio * float(width * height))
    binary = rect_binary(gray, params)
    black = np.where(binary == 0, 255, 0).astype(np.uint8)
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (_odd_kernel(_OPEN_RATIO, min_side),) * 2)
    black = cv2.morphologyEx(black, cv2.MORPH_OPEN, open_k)

    contours, _ = cv2.findContours(black, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    return _pack_rect(largest, width, height, min_area, "binary", len(contours))


def find_large_rect(y_rect: np.ndarray, params: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback: largest dark blob on y_rect, then minAreaRect."""
    if y_rect.ndim != 2:
        raise ValueError("單高斯圖必須是單通道。")
    height, width = y_rect.shape[:2]
    min_area_ratio = float(params.get("min_area_ratio", _USER_DEFAULTS["min_area_ratio"]))
    min_area = max(1.0, min_area_ratio * float(width * height))
    _t, inv = cv2.threshold(y_rect, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError(f"單高斯圖上找不到面積至少 {min_area_ratio:.3f} 的矩形輪廓。")
    largest = max(contours, key=cv2.contourArea)
    packed = _pack_rect(largest, width, height, min_area, "y_rect", len(contours))
    if packed is None:
        raise ValueError(f"單高斯圖上找不到面積至少 {min_area_ratio:.3f} 的矩形輪廓。")
    return packed


def named_corners(corners: np.ndarray) -> Dict[str, np.ndarray]:
    pts = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    rest = list(range(4))
    tl = min(rest, key=lambda i: pts[i, 0] + pts[i, 1])
    rest.remove(tl)
    tr = min(rest, key=lambda i: -pts[i, 0] + pts[i, 1])
    rest.remove(tr)
    bl = min(rest, key=lambda i: pts[i, 0] - pts[i, 1])
    rest.remove(bl)
    br = rest[0]
    return {"TL": pts[tl], "TR": pts[tr], "BL": pts[bl], "BR": pts[br]}


def find_package_rect(gray: np.ndarray, y_rect: np.ndarray, params: Dict[str, Any]) -> Dict[str, Any]:
    """Largest black blob minAreaRect; y_rect contour fallback."""
    if gray.ndim != 2 or y_rect.ndim != 2:
        raise ValueError("原圖與單高斯圖必須是單通道。")
    if gray.shape[:2] != y_rect.shape[:2]:
        raise ValueError("原圖與單高斯圖尺寸不一致。")
    assembled = find_rect_from_binary(gray, params)
    if assembled is not None:
        return assembled
    return find_large_rect(y_rect, params)
