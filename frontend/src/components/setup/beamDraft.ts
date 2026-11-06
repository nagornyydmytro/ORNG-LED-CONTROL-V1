export type BeamDraftKey =
  | "pan_offset"
  | "tilt_offset"
  | "pan_min"
  | "pan_max"
  | "tilt_min"
  | "tilt_max"
  | "home_pan"
  | "home_tilt"
  | "max_pan_speed"
  | "max_tilt_speed";

export interface BeamDraft {
  pan_invert: boolean;
  tilt_invert: boolean;
  pan_offset: string;
  tilt_offset: string;
  pan_min: string;
  pan_max: string;
  tilt_min: string;
  tilt_max: string;
  home_pan: string;
  home_tilt: string;
  max_pan_speed: string;
  max_tilt_speed: string;
  beam_calibration_confirmed: boolean;
  beam_calibration_notes: string;
}

export interface BeamNumericField {
  key: BeamDraftKey;
  label: string;
  min: number;
  max: number;
  step: number;
}

export const BEAM_NUMERIC_FIELDS: BeamNumericField[] = [
  { key: "pan_offset", label: "Pan offset", min: -0.5, max: 0.5, step: 0.01 },
  { key: "tilt_offset", label: "Tilt offset", min: -0.5, max: 0.5, step: 0.01 },
  { key: "pan_min", label: "Pan min", min: 0, max: 1, step: 0.01 },
  { key: "pan_max", label: "Pan max", min: 0, max: 1, step: 0.01 },
  { key: "tilt_min", label: "Tilt min", min: 0, max: 1, step: 0.01 },
  { key: "tilt_max", label: "Tilt max", min: 0, max: 1, step: 0.01 },
  { key: "home_pan", label: "Home Pan", min: 0, max: 1, step: 0.01 },
  { key: "home_tilt", label: "Home Tilt", min: 0, max: 1, step: 0.01 },
  { key: "max_pan_speed", label: "Max Pan Speed", min: 0.01, max: 2, step: 0.01 },
  { key: "max_tilt_speed", label: "Max Tilt Speed", min: 0.01, max: 2, step: 0.01 },
];

function textOf(value: unknown, fallback: string): string {
  if (value == null || value === "") return fallback;
  return String(value);
}

export function beamDraftFromSpatial(spatial: Record<string, unknown> | undefined): BeamDraft {
  return {
    pan_invert: Boolean(spatial?.pan_invert),
    tilt_invert: Boolean(spatial?.tilt_invert),
    pan_offset: textOf(spatial?.pan_offset, "0"),
    tilt_offset: textOf(spatial?.tilt_offset, "0"),
    pan_min: textOf(spatial?.pan_min, "0"),
    pan_max: textOf(spatial?.pan_max, "1"),
    tilt_min: textOf(spatial?.tilt_min, "0"),
    tilt_max: textOf(spatial?.tilt_max, "1"),
    home_pan: textOf(spatial?.home_pan, "0.5"),
    home_tilt: textOf(spatial?.home_tilt, "0.5"),
    max_pan_speed: textOf(spatial?.max_pan_speed, "0.35"),
    max_tilt_speed: textOf(spatial?.max_tilt_speed, "0.25"),
    beam_calibration_confirmed: Boolean(spatial?.beam_calibration_confirmed),
    beam_calibration_notes: spatial?.beam_calibration_notes == null
      ? ""
      : String(spatial.beam_calibration_notes),
  };
}

export function applyBeamDraft(
  spatial: Record<string, unknown>,
  draft: BeamDraft,
): Record<string, unknown> {
  const next: Record<string, unknown> = {
    ...spatial,
    pan_invert: draft.pan_invert,
    tilt_invert: draft.tilt_invert,
    beam_calibration_confirmed: draft.beam_calibration_confirmed,
    beam_calibration_notes:
      draft.beam_calibration_notes === "" && spatial.beam_calibration_notes == null
        ? spatial.beam_calibration_notes
        : draft.beam_calibration_notes,
  };
  for (const field of BEAM_NUMERIC_FIELDS) {
    const raw = draft[field.key].trim();
    const value = Number(raw);
    next[field.key] = raw !== "" && Number.isFinite(value) ? value : raw;
  }
  return next;
}
