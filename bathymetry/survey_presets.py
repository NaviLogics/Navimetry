from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class SurveyPreset:
    key: str
    label: str
    geometry: str
    nominal_line_spacing_m: float | None
    strict_edge_factor: float
    presentation_radius_factor: float
    minimum_presentation_radius_m: float
    prefer_estimated_line_spacing: bool = True
    requires_georeferenced_swath_points: bool = False
    description: str = ""


SURVEY_PRESETS: dict[str, SurveyPreset] = {
    "AUTO": SurveyPreset(
        key="AUTO",
        label="Auto — estimate survey lines",
        geometry="single_beam_centerline",
        nominal_line_spacing_m=None,
        strict_edge_factor=1.35,
        presentation_radius_factor=0.75,
        minimum_presentation_radius_m=0.50,
        description="Estimate survey-line spacing from ordered accepted observations and derive surface thresholds from that geometry.",
    ),
    "SINGLE_BEAM_DENSE": SurveyPreset(
        key="SINGLE_BEAM_DENSE",
        label="Single beam — dense lines",
        geometry="single_beam_centerline",
        nominal_line_spacing_m=2.0,
        strict_edge_factor=1.30,
        presentation_radius_factor=0.70,
        minimum_presentation_radius_m=0.50,
        description="Dense single-beam geometry. Estimated spacing is preferred; 2 m is only a fallback assumption when estimation is unavailable.",
    ),
    "SINGLE_BEAM_NORMAL": SurveyPreset(
        key="SINGLE_BEAM_NORMAL",
        label="Single beam — normal lines",
        geometry="single_beam_centerline",
        nominal_line_spacing_m=5.0,
        strict_edge_factor=1.35,
        presentation_radius_factor=0.75,
        minimum_presentation_radius_m=0.75,
        description="General single-beam geometry. Estimated spacing is preferred; 5 m is only a fallback assumption when estimation is unavailable.",
    ),
    "WIDE_SPACING": SurveyPreset(
        key="WIDE_SPACING",
        label="Wide spacing / reconnaissance",
        geometry="single_beam_centerline",
        nominal_line_spacing_m=10.0,
        strict_edge_factor=1.25,
        presentation_radius_factor=0.65,
        minimum_presentation_radius_m=1.0,
        description="Sparse reconnaissance geometry with more conservative relative bridging. Estimated spacing is preferred; 10 m is a fallback assumption.",
    ),
    "MANUAL": SurveyPreset(
        key="MANUAL",
        label="Manual survey geometry",
        geometry="single_beam_centerline",
        nominal_line_spacing_m=None,
        strict_edge_factor=1.35,
        presentation_radius_factor=0.75,
        minimum_presentation_radius_m=0.50,
        prefer_estimated_line_spacing=False,
        description="Use an explicit expected line spacing and/or explicit triangle-edge and presentation-radius overrides.",
    ),
    "SWATH_SIDESCAN": SurveyPreset(
        key="SWATH_SIDESCAN",
        label="Swath / side-scan",
        geometry="swath",
        nominal_line_spacing_m=None,
        strict_edge_factor=1.20,
        presentation_radius_factor=0.60,
        minimum_presentation_radius_m=0.50,
        requires_georeferenced_swath_points=True,
        description="Reserved for georeferenced across-track/swath bottom observations. Vessel-centerline Beam distance alone cannot reconstruct port/starboard swath geometry.",
    ),
}


def get_survey_preset(key: str | None) -> SurveyPreset:
    normalized=(key or "AUTO").strip().upper()
    if normalized not in SURVEY_PRESETS:
        raise ValueError(f"Unknown survey preset: {key}")
    return SURVEY_PRESETS[normalized]


def preset_metadata(key: str | None) -> dict:
    return asdict(get_survey_preset(key))


def resolve_surface_geometry(config, preset: SurveyPreset, track_geometry: dict) -> dict:
    """Resolve effective survey spacing and derived gridding thresholds with explicit provenance."""
    estimated=track_geometry.get("estimated_line_spacing_m") if track_geometry else None
    if config.expected_line_spacing_m is not None:
        spacing=float(config.expected_line_spacing_m)
        spacing_source="manual_expected_line_spacing"
    elif preset.prefer_estimated_line_spacing and estimated is not None:
        spacing=float(estimated)
        spacing_source="estimated_from_track_segments"
    elif preset.nominal_line_spacing_m is not None:
        spacing=float(preset.nominal_line_spacing_m)
        spacing_source=f"preset_fallback:{preset.key}"
    elif estimated is not None:
        spacing=float(estimated)
        spacing_source="estimated_from_track_segments"
    else:
        spacing=None
        spacing_source="unavailable"

    strict_edge=(float(config.max_triangle_edge_m) if config.max_triangle_edge_m is not None
                 else (spacing*preset.strict_edge_factor if spacing is not None else None))
    strict_edge_source=("manual" if config.max_triangle_edge_m is not None
                        else (f"line_spacing_x_{preset.strict_edge_factor:.3f}" if strict_edge is not None else "fallback_required"))

    presentation_radius=(float(config.max_nearest_point_distance_m) if config.max_nearest_point_distance_m is not None
                         else (max(preset.minimum_presentation_radius_m, spacing*preset.presentation_radius_factor)
                               if spacing is not None else None))
    presentation_radius_source=("manual" if config.max_nearest_point_distance_m is not None
                                else (f"max(min_radius,{preset.presentation_radius_factor:.3f}x_line_spacing)"
                                      if presentation_radius is not None else "fallback_required"))
    return {
        "effective_line_spacing_m": spacing,
        "line_spacing_source": spacing_source,
        "strict_max_triangle_edge_m": strict_edge,
        "strict_max_triangle_edge_source": strict_edge_source,
        "presentation_radius_m": presentation_radius,
        "presentation_radius_source": presentation_radius_source,
        "preset_key": preset.key,
    }
