from pathlib import Path

import pandas as pd

from bathymetry.models import ProcessingConfig
from bathymetry.quality_control import _sustained_sentinel_mask


def _config(tmp_path: Path) -> ProcessingConfig:
    return ProcessingConfig(
        input_csv=tmp_path / "input.csv",
        output_dir=tmp_path / "out",
    )


def test_sustained_kogger_sentinel_is_detected(tmp_path):
    config = _config(tmp_path)
    values = pd.Series([1.4, 0.1144, 0.1144, 0.1144, 1.5])
    assert _sustained_sentinel_mask(values, config).tolist() == [
        False, True, True, True, False
    ]


def test_single_sentinel_is_not_rejected(tmp_path):
    config = _config(tmp_path)
    values = pd.Series([1.4, 0.1144, 1.5])
    assert not _sustained_sentinel_mask(values, config).any()


def test_sentinel_rule_can_be_disabled(tmp_path):
    config = _config(tmp_path)
    config.beam_invalid_sentinel_m = None
    values = pd.Series([0.1144, 0.1144, 0.1144])
    assert not _sustained_sentinel_mask(values, config).any()
