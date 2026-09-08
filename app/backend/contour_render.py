# -*- coding: utf-8 -*-
"""Small production-only contour raster/overlay helpers.

Kept separate from the legacy ySharp capsule tracer so the v10 runtime no longer
imports thousands of lines of obsolete L1/L2/L3/L4 logic.
"""
from __future__ import annotations

from typing import Sequence, Tuple

import cv2
import numpy as np

from config_loader import section

RED = (0, 0, 255)
_RASTER = section("rasterization")
SUPER = int(_RASTER["supersample"])
MASK_THRESHOLD = int(_RASTER["maskThreshold"])
OVERLAY_LINE_THICKNESS_SUPER = int(_RASTER["overlayLineThicknessSupersampled"])


def render_overlay_color(
    canvas_bgr: np.ndarray,
    contours: Sequence[np.ndarray],
    origin: Tuple[int, int],
    color: Tuple[int, int, int],
) -> np.ndarray:
    ox, oy = int(origin[0]), int(origin[1])
    source = np.asarray(canvas_bgr, dtype=np.uint8)
    big = cv2.resize(
        source,
        (source.shape[1] * SUPER, source.shape[0] * SUPER),
        interpolation=cv2.INTER_NEAREST,
    )
    offset = np.asarray([ox, oy], dtype=np.float64)
    for contour in contours:
        points = np.rint((np.asarray(contour, dtype=np.float64) + offset) * SUPER).astype(np.int32)
        cv2.polylines(big, [points], True, tuple(int(v) for v in color), OVERLAY_LINE_THICKNESS_SUPER, cv2.LINE_AA)
    return cv2.resize(
        big,
        (source.shape[1], source.shape[0]),
        interpolation=cv2.INTER_LANCZOS4,
    )


def render_overlay(
    canvas_bgr: np.ndarray,
    contours: Sequence[np.ndarray],
    origin: Tuple[int, int],
) -> np.ndarray:
    return render_overlay_color(canvas_bgr, contours, origin, RED)


def render_mask(
    shape_hw: Tuple[int, int],
    contours: Sequence[np.ndarray],
    origin: Tuple[int, int],
) -> np.ndarray:
    h, w = int(shape_hw[0]), int(shape_hw[1])
    ox, oy = int(origin[0]), int(origin[1])
    if not contours:
        return np.zeros((h, w), dtype=np.uint8)
    offset = np.asarray([ox, oy], dtype=np.float64)
    layers = []
    for contour in contours:
        big = np.zeros((h * SUPER, w * SUPER), dtype=np.uint8)
        points = np.rint((np.asarray(contour, dtype=np.float64) + offset) * SUPER).astype(np.int32)
        cv2.fillPoly(big, [points], 255)
        small = cv2.resize(big, (w, h), interpolation=cv2.INTER_LANCZOS4)
        layers.append(np.where(small >= MASK_THRESHOLD, 1, 0).astype(np.uint8))
    counts = np.sum(np.stack(layers, axis=0), axis=0)
    return np.where(counts >= 1, 255, 0).astype(np.uint8)
