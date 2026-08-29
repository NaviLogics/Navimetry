from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class SurveyPreset:
    key: str
    label: str
    geometry: str
    expected_line_spacing_m: float | None
    presentation_radius_m: float | None
    max_triangle_edge_m: float | None
    requires_georeferenced_swath_points: bool = False
    description: str = ""


SURVEY_PRESETS: dict[str, SurveyPreset] = {
    "AUTO": SurveyPreset(
        key="AUTO",
        label="Auto / preserve current QC",
        geometry="single_beam_centerline",
        expected_line_spacing_m=None,
        presentation_radius_m=None,
        max_triangle_edge_m=None,
        description="No fixed survey-line assumption. Automatic values are derived by the processing pipeline.",
    ),
    "SINGLE_BEAM_DENSE": SurveyPreset(
        key="SINGLE_BEAM_DENSE",
        label="Single beam — dense lines",
        geometry="single_beam_centerline",
        expected_line_spacing_m=2.0,
        presentation_radius_m=2.0,
        max_triangle_edge_m=3.0,
        description="Starting point for closely spaced single-beam survey lines. Values remain auditable and can be overridden manually.",
    ),
    "SINGLE_BEAM_NORMAL": SurveyPreset(
        key="SINGLE_BEAM_NORMAL",
        label="Single beam — normal lines",
        geometry="single_beam_centerline",
        expected_line_spacing_m=5.0,
        presentation_radius_m=5.0,
        max_triangle_edge_m=7.5,
        description="Starting point for ordinary single-beam line spacing. Not a survey standard or accuracy claim.",
    ),
    "WIDE_SPACING": SurveyPreset(
        key="WIDE_SPACING",
        label="Wide spacing / reconnaissance",
        geometry="single_beam_centerline",
        expected_line_spacing_m=10.0,
        presentation_radius_m=10.0,
        max_triangle_edge_m=15.0,
        description="Permissive presentation preset for sparse reconnaissance lines; strict coverage evidence remains separate.",
    ),
    "SWATH_SIDESCAN": SurveyPreset(
        key="SWATH_SIDESCAN",
        label="Swath / side-scan",
        geometry="swath",
        expected_line_spacing_m=None,
        presentation_radius_m=None,
        max_triangle_edge_m=None,
        requires_georeferenced_swath_points=True,
        description="Reserved for measurements with across-track geometry or georeferenced port/starboard bottom points. A vessel-centerline Beam value alone is insufficient to reconstruct a swath.",
    ),
}


def get_survey_preset(key: str | None) -> SurveyPreset:
    normalized=(key or "AUTO").strip().upper()
    if normalized not in SURVEY_PRESETS:
        raise ValueError(f"Unknown survey preset: {key}")
    return SURVEY_PRESETS[normalized]


def preset_metadata(key: str | None) -> dict:
    return asdict(get_survey_preset(key))
