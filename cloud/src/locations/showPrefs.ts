/** Saved musical preferences for one location. Not tempo, not the audio device. */

import { listOperatorLocations } from "../db/locations";
import type { Env } from "../env";

const MODES = new Set(["smooth", "bar", "beat"]);
const SCOPES = new Set(["preset", "auto"]);
const PALETTES = new Set([
  "orange",
  "red",
  "blue",
  "violet",
  "amber",
  "white",
  "ember",
  "sunset",
  "ice",
  "night",
  "contrast",
  "forest",
  "carnival",
  "traffic",
]);
const LEGACY_PALETTES = new Set(["bars", "heads"]);
const CASTS = new Set(["all", "bars", "heads"]);
const CHARACTERS = new Set(["smooth", "rhythmic", "impact"]);
const INTENSITIES = new Set(["soft", "normal", "full"]);
const PERIODS = new Set(["auto", "0.5", "1", "2", "4", "8"]);
const PERIODS_FOR_CHARACTER: Record<string, Set<string>> = {
  smooth: new Set(["auto", "2", "4", "8"]),
  rhythmic: new Set(["auto", "0.5", "1", "2", "4", "8"]),
  impact: new Set(["auto", "0.5", "1", "2", "4"]),
};

export type LocationShowPrefs = {
  mode: "smooth" | "bar" | "beat";
  scope: "preset" | "auto";
  palette: string;
  cast: "all" | "bars" | "heads";
  character: "smooth" | "rhythmic" | "impact";
  intensity: "soft" | "normal" | "full";
  period: string;
  par_rhythm: boolean;
  accent: number;
  updated_at: string;
};

export function showPrefsKey(locationId: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}$/.test(locationId)) {
    throw new Error("bad_location");
  }
  return `location-prefs/${locationId}.json`;
}

const MUSICAL = ["mode", "scope", "palette", "cast", "character", "intensity", "period"] as const;

function pick(value: unknown, allowed: Set<string>, fallback: string): string {
  return typeof value === "string" && allowed.has(value) ? value : fallback;
}

function asPeriod(value: unknown, fallback: string): string {
  if (value == null || value === "") return fallback;
  const raw = typeof value === "number" ? String(value) : String(value);
  if (raw === "auto") return "auto";
  if (PERIODS.has(raw)) return raw;
  const asNumber = Number(raw);
  if (asNumber === 0.5) return "0.5";
  if (asNumber === 1) return "1";
  if (asNumber === 2) return "2";
  if (asNumber === 4) return "4";
  if (asNumber === 8) return "8";
  return fallback;
}

function allowedFor(field: (typeof MUSICAL)[number]): Set<string> {
  if (field === "mode") return MODES;
  if (field === "scope") return SCOPES;
  if (field === "palette") return new Set([...PALETTES, ...LEGACY_PALETTES]);
  if (field === "cast") return CASTS;
  if (field === "character") return CHARACTERS;
  if (field === "intensity") return INTENSITIES;
  return PERIODS;
}

function migratePalette(palette: string, cast: string): { palette: string; cast: string } {
  if (palette === "bars") return { palette: "orange", cast: cast === "all" ? "bars" : cast };
  if (palette === "heads") return { palette: "white", cast: cast === "all" ? "heads" : cast };
  if (!PALETTES.has(palette)) return { palette: "orange", cast };
  return { palette, cast };
}

function clampAccent(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

function clampPeriod(period: string, character: string): string {
  const allowed = PERIODS_FOR_CHARACTER[character] ?? PERIODS;
  return allowed.has(period) ? period : "auto";
}

function setMusicalField(
  prefs: LocationShowPrefs,
  field: (typeof MUSICAL)[number],
  value: string,
): void {
  switch (field) {
    case "mode":
      if (MODES.has(value)) prefs.mode = value as LocationShowPrefs["mode"];
      return;
    case "scope":
      if (SCOPES.has(value)) prefs.scope = value as LocationShowPrefs["scope"];
      return;
    case "palette":
      prefs.palette = value;
      return;
    case "cast":
      if (CASTS.has(value)) prefs.cast = value as LocationShowPrefs["cast"];
      return;
    case "character":
      if (CHARACTERS.has(value)) prefs.character = value as LocationShowPrefs["character"];
      return;
    case "intensity":
      if (INTENSITIES.has(value)) prefs.intensity = value as LocationShowPrefs["intensity"];
      return;
    case "period":
      prefs.period = value;
      return;
  }
}

function blankPrefs(): LocationShowPrefs {
  return {
    mode: "smooth",
    scope: "preset",
    palette: "orange",
    cast: "all",
    character: "rhythmic",
    intensity: "full",
    period: "auto",
    par_rhythm: false,
    accent: 0,
    updated_at: "",
  };
}

function storedJson(prefs: LocationShowPrefs): string {
  return JSON.stringify({
    mode: prefs.mode,
    scope: prefs.scope,
    palette: prefs.palette,
    cast: prefs.cast,
    character: prefs.character,
    intensity: prefs.intensity,
    period: prefs.period,
    par_rhythm: prefs.par_rhythm === true,
    accent: clampAccent(prefs.accent),
    updated_at: prefs.updated_at,
  });
}

function fromBody(
  body: Record<string, unknown>,
  base: LocationShowPrefs,
): LocationShowPrefs {
  const paletteIn = pick(body.palette, allowedFor("palette"), base.palette);
  const castIn = pick(body.cast, CASTS, base.cast) as LocationShowPrefs["cast"];
  const migrated = migratePalette(paletteIn, castIn);
  const character = pick(body.character, CHARACTERS, base.character) as LocationShowPrefs["character"];
  return {
    mode: pick(body.mode, MODES, base.mode) as LocationShowPrefs["mode"],
    scope: pick(body.scope, SCOPES, base.scope) as LocationShowPrefs["scope"],
    palette: migrated.palette,
    cast: migrated.cast as LocationShowPrefs["cast"],
    character,
    intensity: pick(body.intensity, INTENSITIES, base.intensity) as LocationShowPrefs["intensity"],
    period: clampPeriod(asPeriod(body.period, base.period), character),
    par_rhythm: typeof body.par_rhythm === "boolean" ? body.par_rhythm : base.par_rhythm,
    accent: clampAccent(body.accent ?? base.accent),
    updated_at: typeof body.updated_at === "string" ? body.updated_at : base.updated_at,
  };
}

/** Parse a stored document. Does not invent a new updated_at. */
export function parseStoredShowPrefs(raw: unknown): LocationShowPrefs | null {
  if (!raw || typeof raw !== "object") return null;
  const body = raw as Record<string, unknown>;
  const musical = MUSICAL.some((field) => field in body) || typeof body.updated_at === "string";
  if (!musical) return null;
  return fromBody(body, blankPrefs());
}

export function normalizeShowPrefs(
  raw: unknown,
  previous?: LocationShowPrefs | null,
): LocationShowPrefs {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return fromBody(body, previous ?? blankPrefs());
}

/**
 * Apply only musical fields present in the patch.
 * `base` is the snapshot the writer read. A field someone else already
 * changed is left as stored and reported in conflicts.
 */
export function mergeShowPrefs(
  current: LocationShowPrefs,
  patch: unknown,
): { next: LocationShowPrefs; conflicts: string[]; changed: boolean } {
  const body = patch && typeof patch === "object" ? (patch as Record<string, unknown>) : {};
  const base =
    body.base && typeof body.base === "object" ? (body.base as Record<string, unknown>) : null;
  const next: LocationShowPrefs = { ...blankPrefs(), ...current };
  const conflicts: string[] = [];
  let changed = false;
  for (const field of MUSICAL) {
    if (!Object.prototype.hasOwnProperty.call(body, field)) continue;
    let desired =
      field === "period"
        ? asPeriod(body[field], "")
        : pick(body[field], allowedFor(field), "");
    if (!desired) continue;
    if (field === "palette" && LEGACY_PALETTES.has(desired)) {
      const migrated = migratePalette(desired, next.cast);
      desired = migrated.palette;
      if (!Object.prototype.hasOwnProperty.call(body, "cast")) {
        if (next.cast !== migrated.cast) {
          next.cast = migrated.cast as LocationShowPrefs["cast"];
          changed = true;
        }
      }
    }
    if (desired === next[field]) continue;
    if (base && Object.prototype.hasOwnProperty.call(base, field)) {
      const seen =
        field === "period"
          ? asPeriod(base[field], "")
          : pick(base[field], allowedFor(field), "");
      const seenNow =
        field === "palette" && LEGACY_PALETTES.has(seen) ? migratePalette(seen, next.cast).palette : seen;
      if (seenNow && seenNow !== current[field]) {
        conflicts.push(field);
        continue;
      }
    }
    setMusicalField(next, field, desired);
    changed = true;
  }
  const clamped = clampPeriod(next.period, next.character);
  if (clamped !== next.period) {
    next.period = clamped;
    changed = true;
  }
  if (Object.prototype.hasOwnProperty.call(body, "par_rhythm") && typeof body.par_rhythm === "boolean") {
    if (body.par_rhythm !== next.par_rhythm) {
      next.par_rhythm = body.par_rhythm;
      changed = true;
    }
  }
  if (Object.prototype.hasOwnProperty.call(body, "accent")) {
    const desired = clampAccent(body.accent);
    if (desired !== next.accent) {
      next.accent = desired;
      changed = true;
    }
  }
  if (changed) next.updated_at = new Date().toISOString();
  return { next, conflicts, changed };
}

export async function readShowPrefs(
  env: Env,
  locationId: string,
): Promise<LocationShowPrefs | null> {
  const object = await env.SCENE_RELEASES.get(showPrefsKey(locationId));
  if (!object) return null;
  try {
    return parseStoredShowPrefs(JSON.parse(await object.text()));
  } catch {
    return null;
  }
}

export async function writeShowPrefs(
  env: Env,
  locationId: string,
  patch: unknown,
): Promise<LocationShowPrefs & { conflicts: string[] }> {
  const current = (await readShowPrefs(env, locationId)) ?? blankPrefs();
  const merged = mergeShowPrefs(current, patch);
  if (merged.changed) {
    await env.SCENE_RELEASES.put(showPrefsKey(locationId), storedJson(merged.next), {
      httpMetadata: { contentType: "application/json" },
    });
  }
  return { ...merged.next, conflicts: merged.conflicts };
}

export async function locationIdForInstallation(
  env: Env,
  installationId: string,
): Promise<string | null> {
  const locations = await listOperatorLocations(env);
  const match = locations.find(
    (location) =>
      location.installation_id === installationId ||
      location.installations.some((row) => row.id === installationId),
  );
  return match?.id ?? null;
}
