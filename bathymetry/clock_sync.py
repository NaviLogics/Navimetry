from __future__ import annotations
import numpy as np
import pandas as pd


def build_clock_model(attitude: pd.DataFrame) -> dict:
    required = {"kogger_ltime_ms", "px4_boot_time_ms"}
    if attitude.empty or not required.issubset(attitude.columns) or len(attitude) < 10:
        return {"model_type": "unavailable", "quality": "unavailable"}
    x = attitude["kogger_ltime_ms"].to_numpy(dtype=float)
    y = attitude["px4_boot_time_ms"].to_numpy(dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) < 10:
        return {"model_type": "unavailable", "quality": "unavailable"}
    const = float(np.median(y - x))
    rc = y - (x + const)
    a, b = np.linalg.lstsq(np.column_stack([x, np.ones_like(x)]), y, rcond=None)[0]
    rl = y - (a * x + b)
    def stats(r):
        return {
            "rmse_ms": float(np.sqrt(np.mean(r*r))),
            "median_abs_error_ms": float(np.median(np.abs(r))),
            "p95_abs_error_ms": float(np.percentile(np.abs(r), 95)),
            "max_abs_error_ms": float(np.max(np.abs(r))),
        }
    cs = stats(rc); ls = stats(rl)
    use_linear = ls["rmse_ms"] < cs["rmse_ms"] * 0.8
    chosen = "linear" if use_linear else "constant_offset"
    return {
        "model_type": chosen,
        "clock_scale": float(a) if use_linear else 1.0,
        "clock_offset_ms": float(b) if use_linear else const,
        "valid_from_kogger_ms": float(x.min()),
        "valid_to_kogger_ms": float(x.max()),
        "constant_model": {"offset_ms": const, **cs},
        "linear_model": {"scale": float(a), "offset_ms": float(b), **ls},
        "quality": "derived",
    }
