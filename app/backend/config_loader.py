# -*- coding: utf-8 -*-
"""Single-source runtime configuration for v32.

Source mode reads ``algorithm_config.json`` from the project root.
A frozen one-file EXE embeds the build-time JSON as its factory default. On the
first launch that default is materialized to
``D:\\XrayRegistrationData\\algorithm_config.json``; later launches always use
the existing external JSON. The EXE therefore has no sidecar-file dependency.
Configuration is loaded once per process, so edits take effect after restart.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


class ConfigError(RuntimeError):
    pass


FROZEN_DEFAULT_DATA_ROOT = Path(r"D:\XrayRegistrationData")


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_default_config_path() -> Path:
    """Return the immutable build-time default JSON bundled by PyInstaller."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "algorithm_config.json"
    return project_root() / "algorithm_config.json"


def config_path() -> Path:
    """Return the editable configuration path used by this process."""
    if getattr(sys, "frozen", False):
        return FROZEN_DEFAULT_DATA_ROOT / "algorithm_config.json"
    return project_root() / "algorithm_config.json"


def ensure_runtime_config() -> Path:
    """Materialize the bundled default JSON on the first frozen launch only."""
    path = config_path()
    if not getattr(sys, "frozen", False):
        return path

    if path.is_file():
        return path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Cannot create configuration folder: {path.parent}. "
            "The default portable location is D:\\XrayRegistrationData."
        ) from exc

    source = bundled_default_config_path()
    if not source.is_file():
        raise ConfigError(f"Bundled default configuration is missing: {source}")

    tmp = path.with_name(path.name + ".tmp")
    try:
        payload = source.read_bytes()
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"Cannot initialize configuration file: {path}") from exc
    return path


def _number(value: Any, path: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: expected number")
    value = float(value)
    if not math.isfinite(value):
        raise ConfigError(f"{path}: must be finite")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path}: must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{path}: must be <= {maximum}")
    return value


def _integer(value: Any, path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: expected integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path}: must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{path}: must be <= {maximum}")
    return int(value)


def _object(root: Mapping[str, Any], key: str) -> Dict[str, Any]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key}: expected object")
    return value


def _list(root: Mapping[str, Any], key: str) -> list:
    value = root.get(key)
    if not isinstance(value, list):
        raise ConfigError(f"{key}: expected array")
    return value


def _validate_percentile_pair(section: Mapping[str, Any], prefix: str) -> None:
    lo = _number(section.get("normalizePercentileLow"), f"{prefix}.normalizePercentileLow", minimum=0.0, maximum=100.0)
    hi = _number(section.get("normalizePercentileHigh"), f"{prefix}.normalizePercentileHigh", minimum=0.0, maximum=100.0)
    if lo >= hi:
        raise ConfigError(f"{prefix}: normalizePercentileLow must be < normalizePercentileHigh")


def _validate_color(value: Any, path: str) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise ConfigError(f"{path}: expected [B,G,R]")
    for index, channel in enumerate(value):
        _integer(channel, f"{path}[{index}]", minimum=0, maximum=255)


def validate_config(cfg: Mapping[str, Any]) -> None:
    _integer(cfg.get("configVersion"), "configVersion", minimum=1)
    if not isinstance(cfg.get("appVersion"), str) or not cfg.get("appVersion"):
        raise ConfigError("appVersion: expected non-empty string")

    params = _object(cfg, "userParameters")
    required_user = {
        "rect_mu_pct", "rect_sigma2", "min_area_ratio", "rect_bin_pct",
        "solder_reference_ceiling_pct", "solder_full_weight_offset_pct",
        "void_zero_weight_offset_pct", "ideal_shift_max_px", "pair_min_distance_px",
        "relative_shift_warning_px", "crop_ratio",
    }
    missing = sorted(required_user - set(params))
    if missing:
        raise ConfigError("userParameters missing: " + ", ".join(missing))
    for key in required_user:
        _number(params[key], f"userParameters.{key}")
    if _number(params["rect_sigma2"], "userParameters.rect_sigma2") <= 0:
        raise ConfigError("userParameters.rect_sigma2 must be > 0")
    _number(params["min_area_ratio"], "userParameters.min_area_ratio", minimum=0.0, maximum=1.0)
    _number(params["crop_ratio"], "userParameters.crop_ratio", minimum=0.000001, maximum=1.0)
    _number(params["ideal_shift_max_px"], "userParameters.ideal_shift_max_px", minimum=0.0)
    _number(params["pair_min_distance_px"], "userParameters.pair_min_distance_px", minimum=0.0)
    _number(params["relative_shift_warning_px"], "userParameters.relative_shift_warning_px", minimum=0.0)
    _number(params["solder_full_weight_offset_pct"], "userParameters.solder_full_weight_offset_pct", minimum=0.0)
    _number(params["void_zero_weight_offset_pct"], "userParameters.void_zero_weight_offset_pct", minimum=0.0)
    if float(params["void_zero_weight_offset_pct"]) <= float(params["solder_full_weight_offset_pct"]):
        raise ConfigError(
            "userParameters.void_zero_weight_offset_pct must be > "
            "userParameters.solder_full_weight_offset_pct"
        )

    templates = _list(cfg, "uiParameterTemplates")
    keys = [item.get("key") for item in templates if isinstance(item, dict)]
    if set(required_user) - set(keys):
        raise ConfigError("uiParameterTemplates must describe every user parameter")

    limits = _object(cfg, "userParameterLimits")
    for key, pair in limits.items():
        if key not in params:
            raise ConfigError(f"userParameterLimits.{key}: unknown user parameter")
        if not isinstance(pair, list) or len(pair) != 2:
            raise ConfigError(f"userParameterLimits.{key}: expected [min,max]")
        lo = _number(pair[0], f"userParameterLimits.{key}[0]")
        hi = _number(pair[1], f"userParameterLimits.{key}[1]")
        if lo > hi:
            raise ConfigError(f"userParameterLimits.{key}: min must be <= max")
        value = float(params[key])
        if value < lo or value > hi:
            raise ConfigError(f"userParameters.{key}: default must be inside configured limits")

    pd = _object(cfg, "productDetection")
    _number(pd.get("backgroundStripFraction"), "productDetection.backgroundStripFraction", minimum=0.000001, maximum=0.49)
    _number(pd.get("openKernelRatio"), "productDetection.openKernelRatio", minimum=0.0)
    _integer(pd.get("openKernelMinPx"), "productDetection.openKernelMinPx", minimum=1)
    for key in ("fullFrameRejectAreaRatio", "fullFrameRejectWidthRatio", "fullFrameRejectHeightRatio"):
        _number(pd.get(key), f"productDetection.{key}", minimum=0.0, maximum=1.0)
    k = _integer(pd.get("outsideDilateKernelPx"), "productDetection.outsideDilateKernelPx", minimum=1)
    if k % 2 == 0:
        raise ConfigError("productDetection.outsideDilateKernelPx must be odd")

    ori = _object(cfg, "orientation")
    _number(ori.get("minScore"), "orientation.minScore", minimum=-1.0, maximum=1.0)
    _number(ori.get("minMargin"), "orientation.minMargin", minimum=0.0, maximum=2.0)
    _integer(ori.get("maxWidth"), "orientation.maxWidth", minimum=1)
    _integer(ori.get("featureMinWidth"), "orientation.featureMinWidth", minimum=1)
    _integer(ori.get("featureMinHeight"), "orientation.featureMinHeight", minimum=1)
    _number(ori.get("featureSigma"), "orientation.featureSigma", minimum=0.0)
    _validate_percentile_pair(ori, "orientation")
    _number(ori.get("normalizeMinRange"), "orientation.normalizeMinRange", minimum=0.0)
    _integer(ori.get("eccMaxIterations"), "orientation.eccMaxIterations", minimum=1)
    _number(ori.get("eccEpsilon"), "orientation.eccEpsilon", minimum=0.0)
    _integer(ori.get("eccGaussianFilterSize"), "orientation.eccGaussianFilterSize", minimum=1)

    reg = _object(cfg, "registration")
    _number(reg.get("productPoseMinScore"), "registration.productPoseMinScore", minimum=-1.0, maximum=1.0)
    _integer(reg.get("productPoseMaxWidth"), "registration.productPoseMaxWidth", minimum=1)
    _integer(reg.get("productPoseMinWorkWidth"), "registration.productPoseMinWorkWidth", minimum=1)
    _integer(reg.get("productPoseMinWorkHeight"), "registration.productPoseMinWorkHeight", minimum=1)
    _integer(reg.get("productPoseMaxIterations"), "registration.productPoseMaxIterations", minimum=1)
    _integer(reg.get("eccMinIterations"), "registration.eccMinIterations", minimum=1)
    _validate_percentile_pair(reg, "registration")
    _number(reg.get("normalizeMinRange"), "registration.normalizeMinRange", minimum=0.0)
    _number(reg.get("featureSigma"), "registration.featureSigma", minimum=0.0)
    _number(reg.get("eccEpsilon"), "registration.eccEpsilon", minimum=0.0)
    _integer(reg.get("eccGaussianFilterSize"), "registration.eccGaussianFilterSize", minimum=1)
    _number(reg.get("maskRadiusScale"), "registration.maskRadiusScale", minimum=0.0)
    _number(reg.get("maskStraightExtensionRadiusScale"), "registration.maskStraightExtensionRadiusScale", minimum=0.0)
    _number(reg.get("maskBorderRatio"), "registration.maskBorderRatio", minimum=0.0, maximum=0.5)
    _integer(reg.get("maskMinBorderPx"), "registration.maskMinBorderPx", minimum=0)
    _number(reg.get("maskMinValidFraction"), "registration.maskMinValidFraction", minimum=0.0, maximum=1.0)
    min_scale = _number(reg.get("minScale"), "registration.minScale", minimum=0.000001)
    max_scale = _number(reg.get("maxScale"), "registration.maxScale", minimum=0.000001)
    if min_scale > max_scale:
        raise ConfigError("registration.minScale must be <= registration.maxScale")
    _number(reg.get("maxTranslationRatio"), "registration.maxTranslationRatio", minimum=0.0)
    _integer(reg.get("capSamples"), "registration.capSamples", minimum=8)
    _integer(reg.get("idealMinContourPoints"), "registration.idealMinContourPoints", minimum=3)
    _integer(reg.get("idealMinAreaPixels"), "registration.idealMinAreaPixels", minimum=1)
    _integer(reg.get("contourSupportStride"), "registration.contourSupportStride", minimum=1)

    sr = _object(cfg, "solderReference")
    _integer(sr.get("minSeedPixels"), "solderReference.minSeedPixels", minimum=1)
    _number(sr.get("minSeedFraction"), "solderReference.minSeedFraction", minimum=0.0, maximum=1.0)
    _number(sr.get("histogramSigmaMinGray"), "solderReference.histogramSigmaMinGray", minimum=0.0)
    _number(sr.get("histogramSigmaRatioOfG"), "solderReference.histogramSigmaRatioOfG", minimum=0.0)
    _number(sr.get("peakRadiusSigmaMultiplier"), "solderReference.peakRadiusSigmaMultiplier", minimum=0.0)
    _number(sr.get("minPeakFraction"), "solderReference.minPeakFraction", minimum=0.0, maximum=1.0)

    pos = _object(cfg, "positionOptimization")
    _number(pos.get("stepPx"), "positionOptimization.stepPx", minimum=0.000001)

    ras = _object(cfg, "rasterization")
    _integer(ras.get("supersample"), "rasterization.supersample", minimum=1, maximum=16)
    _integer(ras.get("maskThreshold"), "rasterization.maskThreshold", minimum=0, maximum=255)
    _integer(ras.get("overlayLineThicknessSupersampled"), "rasterization.overlayLineThicknessSupersampled", minimum=1)

    render = _object(cfg, "resultRender")
    _number(render.get("outsideDimFactor"), "resultRender.outsideDimFactor", minimum=0.0, maximum=1.0)
    _number(render.get("voidOverlayMaxAlpha"), "resultRender.voidOverlayMaxAlpha", minimum=0.0, maximum=1.0)
    _validate_color(render.get("voidColorBgr"), "resultRender.voidColorBgr")
    _validate_color(render.get("contourColorBgr"), "resultRender.contourColorBgr")
    _integer(render.get("contourLineThicknessPx"), "resultRender.contourLineThicknessPx", minimum=1)
    colors = _object(render, "statusColorsBgr")
    for key in ("PASS", "WARNING", "FAIL", "UNAVAILABLE"):
        _validate_color(colors.get(key), f"resultRender.statusColorsBgr.{key}")
    _validate_color(render.get("textColorBgr"), "resultRender.textColorBgr")
    _number(render.get("fontScale"), "resultRender.fontScale", minimum=0.01)
    _integer(render.get("lineHeightPx"), "resultRender.lineHeightPx", minimum=1)
    _integer(render.get("panelMinWidthPx"), "resultRender.panelMinWidthPx", minimum=1)
    _number(render.get("panelWidthFraction"), "resultRender.panelWidthFraction", minimum=0.01, maximum=1.0)
    _integer(render.get("panelTopPaddingPx"), "resultRender.panelTopPaddingPx", minimum=0)
    _number(render.get("panelDimFactor"), "resultRender.panelDimFactor", minimum=0.0, maximum=1.0)
    _integer(render.get("textOriginXPx"), "resultRender.textOriginXPx", minimum=0)
    _integer(render.get("textFirstBaselineYPx"), "resultRender.textFirstBaselineYPx", minimum=0)
    _integer(render.get("fontThicknessPx"), "resultRender.fontThicknessPx", minimum=1)

    runtime = _object(cfg, "runtime")
    if not isinstance(runtime.get("dataRoot"), str) or not runtime.get("dataRoot").strip():
        raise ConfigError("runtime.dataRoot: expected non-empty string")
    if not isinstance(runtime.get("host"), str) or not runtime.get("host"):
        raise ConfigError("runtime.host: expected non-empty string")
    _integer(runtime.get("port"), "runtime.port", minimum=1, maximum=65535)
    if not isinstance(runtime.get("autoOpenBrowser"), bool):
        raise ConfigError("runtime.autoOpenBrowser: expected boolean")
    _number(runtime.get("maxSingleUploadMb"), "runtime.maxSingleUploadMb", minimum=0.001)
    _number(runtime.get("maxBatchUploadMb"), "runtime.maxBatchUploadMb", minimum=0.001)
    _integer(runtime.get("maxBatchFiles"), "runtime.maxBatchFiles", minimum=1)
    _number(runtime.get("maxJsonMb"), "runtime.maxJsonMb", minimum=0.001)
    _integer(runtime.get("defaultOutputListLimit"), "runtime.defaultOutputListLimit", minimum=1)

    geo = _object(cfg, "idealGeometry")
    if geo.get("geometry") != "analytic-stadium":
        raise ConfigError("idealGeometry.geometry must be 'analytic-stadium'")
    crop_shape = geo.get("cropShape")
    if not isinstance(crop_shape, list) or len(crop_shape) != 2:
        raise ConfigError("idealGeometry.cropShape: expected [height,width]")
    for i, value in enumerate(crop_shape):
        _integer(value, f"idealGeometry.cropShape[{i}]", minimum=1)
    capsules = geo.get("capsules")
    if not isinstance(capsules, list) or len(capsules) != 4:
        raise ConfigError("idealGeometry.capsules must contain exactly four stadiums")
    ids = []
    for index, spec in enumerate(capsules):
        if not isinstance(spec, dict):
            raise ConfigError(f"idealGeometry.capsules[{index}]: expected object")
        ids.append(_integer(spec.get("id"), f"idealGeometry.capsules[{index}].id", minimum=1))
        for key in ("center", "axis"):
            arr = spec.get(key)
            if not isinstance(arr, list) or len(arr) != 2:
                raise ConfigError(f"idealGeometry.capsules[{index}].{key}: expected [x,y]")
            for j, value in enumerate(arr):
                _number(value, f"idealGeometry.capsules[{index}].{key}[{j}]")
        _number(spec.get("straightHalfLength"), f"idealGeometry.capsules[{index}].straightHalfLength", minimum=0.0)
        _number(spec.get("radius"), f"idealGeometry.capsules[{index}].radius", minimum=0.000001)
    if ids != [1, 2, 3, 4]:
        raise ConfigError("idealGeometry capsule IDs must be [1,2,3,4]")


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    path = ensure_runtime_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing configuration file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in {path.name}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError("algorithm_config.json root must be an object")
    validate_config(raw)
    return raw


def section(name: str) -> Dict[str, Any]:
    return _object(load_config(), name)


def user_defaults() -> Dict[str, Any]:
    return dict(_object(load_config(), "userParameters"))


def user_limits() -> Dict[str, list]:
    return dict(_object(load_config(), "userParameterLimits"))


def ui_templates() -> list[Dict[str, Any]]:
    defaults = user_defaults()
    templates = []
    for item in _list(load_config(), "uiParameterTemplates"):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        key = str(row.get("key") or "")
        if key in defaults:
            row["default"] = defaults[key]
        templates.append(row)
    return templates


def app_version() -> str:
    return str(load_config()["appVersion"])


def config_sha256() -> str:
    payload = json.dumps(
        load_config(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()
