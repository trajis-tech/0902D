# -*- coding: utf-8 -*-
"""Chain A: single-Gaussian map on the full original grayscale."""

from __future__ import annotations

import numpy as np

from config_loader import user_defaults

_DEFAULT_SIGMA2 = float(user_defaults()["rect_sigma2"])

def single_gaussian_map(gray: np.ndarray, mu: float, sigma2: float = _DEFAULT_SIGMA2) -> np.ndarray:
    """y_rect(x) = 255 * (1 - exp(-(x - mu)^2 / (2 * sigma2))).

    Caller passes mu = G × (1 + rect_mu_pct/100).
    Writes 8-bit by rounding only; no per-step quantization LUT.
    """
    x = gray.astype(np.float64)
    var = max(float(sigma2), 1e-12)
    y = 255.0 * (1.0 - np.exp(-np.square(x - float(mu)) / (2.0 * var)))
    return np.clip(np.round(y), 0, 255).astype(np.uint8)
