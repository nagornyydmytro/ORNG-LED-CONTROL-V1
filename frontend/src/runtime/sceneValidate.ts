/** Same patch rules the backend uses before it accepts a scene. */

export interface FieldIssue {
  id: string;
  field: string;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  fields: FieldIssue[];
}

function isInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

export function profileFootprint(profile: Record<string, unknown> | undefined): number | null {
  if (!profile) return null;
  const channels = Array.isArray(profile.channels) ? profile.channels.length : 0;
  if (profile.footprint == null) return Math.max(channels, 1);
  if (!isInt(profile.footprint) || profile.footprint < 1) return null;
  return profile.footprint;
}

function numberIn(value: unknown, min: number, max: number): boolean {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}

const UNIT_KEYS = ["pan_min", "pan_max", "tilt_min", "tilt_max", "home_pan", "home_tilt"] as const;
const OFFSET_KEYS = ["pan_offset", "tilt_offset"] as const;
const SPEED_KEYS = ["max_pan_speed", "max_tilt_speed"] as const;

export function validatePatchShape(
  patch: Record<string, unknown>,
  profiles: Record<string, Record<string, unknown>>,
): ValidationResult {
  const errors: string[] = [];
  const fields: FieldIssue[] = [];
  const fixtures = Array.isArray(patch.fixtures) ? patch.fixtures : [];
  const seen = new Set<string>();
  const ranges: Array<{ id: string; start: number; end: number }> = [];

  for (const raw of fixtures) {
    if (!raw || typeof raw !== "object") {
      errors.push("прилад без id");
      continue;
    }
    const fixture = raw as Record<string, unknown>;
    const id = String(fixture.id ?? "");
    if (!id) {
      errors.push("прилад без id");
      continue;
    }
    if (seen.has(id)) {
      const message = `дубль приладу ${id}`;
      errors.push(message);
      fields.push({ id, field: "id", message });
    }
    seen.add(id);
    const profileId = String(fixture.profile_id ?? "");
    const profile = profiles[profileId];
    const footprint = profileFootprint(profile);
    if (!profileId || footprint == null) {
      const message = `${id}: невідомий профіль ${profileId || "?"}`;
      errors.push(message);
      fields.push({ id, field: "profile_id", message });
      continue;
    }
    if (fixture.kind != null && profile.kind != null && String(fixture.kind) !== String(profile.kind)) {
      const message = `${id}: тип ${String(fixture.kind)} не збігається з профілем`;
      errors.push(message);
      fields.push({ id, field: "kind", message });
    }
    const start = fixture.start_address;
    if (!isInt(start)) {
      const message = `${id}: адреса має бути цілим числом`;
      errors.push(message);
      fields.push({ id, field: "start_address", message });
      continue;
    }
    const end = start + footprint - 1;
    if (start < 1 || end > 512) {
      const message = `${id}: адреса ${start} поза 1..512`;
      errors.push(message);
      fields.push({ id, field: "start_address", message });
      continue;
    }
    ranges.push({ id, start, end });
    const spatial = fixture.spatial;
    if (!spatial || typeof spatial !== "object") continue;
    const place = spatial as Record<string, unknown>;
    for (const key of OFFSET_KEYS) {
      if (place[key] == null) continue;
      if (!numberIn(place[key], -0.5, 0.5)) {
        const message = `${id}: ${key} поза -0.5..0.5`;
        errors.push(message);
        fields.push({ id, field: key, message });
      }
    }
    for (const key of UNIT_KEYS) {
      if (place[key] == null) continue;
      if (!numberIn(place[key], 0, 1)) {
        const message = `${id}: ${key} поза 0..1`;
        errors.push(message);
        fields.push({ id, field: key, message });
      }
    }
    for (const key of SPEED_KEYS) {
      if (place[key] == null) continue;
      if (typeof place[key] !== "number" || !Number.isFinite(place[key] as number) || (place[key] as number) <= 0 || (place[key] as number) > 2) {
        const message = `${id}: ${key} має бути більшим за 0 і не більшим за 2`;
        errors.push(message);
        fields.push({ id, field: key, message });
      }
    }
    if (typeof place.pan_min === "number" && typeof place.pan_max === "number" && place.pan_min >= place.pan_max) {
      const message = `${id}: pan_min має бути меншим за pan_max`;
      errors.push(message);
      fields.push({ id, field: "pan_min", message });
    }
    if (typeof place.tilt_min === "number" && typeof place.tilt_max === "number" && place.tilt_min >= place.tilt_max) {
      const message = `${id}: tilt_min має бути меншим за tilt_max`;
      errors.push(message);
      fields.push({ id, field: "tilt_min", message });
    }
  }

  ranges.sort((a, b) => a.start - b.start);
  for (let index = 1; index < ranges.length; index += 1) {
    const prev = ranges[index - 1]!;
    const cur = ranges[index]!;
    if (cur.start <= prev.end) {
      errors.push(`${prev.id} і ${cur.id} перетинаються`);
    }
  }
  return { ok: errors.length === 0, errors, fields };
}

export function validatePadSlots(
  app: Record<string, unknown> | null | undefined,
  presetIds: Iterable<string>,
): string[] {
  const slots = app?.pad_presets;
  if (!Array.isArray(slots)) return [];
  const known = new Set(presetIds);
  const errors: string[] = [];
  for (const slot of slots) {
    if (!slot || slot === "NONE") continue;
    if (!known.has(String(slot))) errors.push(`слот пульта ${String(slot)} не має пресета`);
  }
  return errors;
}

const GROUP_NAMES = new Set([
  "all_rear",
  "par",
  "bar",
  "beam",
  "outer",
  "inner",
  "left",
  "right",
  "rear",
]);
const TYPE_TAGS = new Set(["par", "bar", "beam", "face"]);
const SIDE_TAGS = new Set(["left", "right"]);
const RING_TAGS = new Set(["inner", "outer"]);
const PATTERN_EFFECTS = new Set([
  "chase",
  "wave",
  "mirror_sweep",
  "glow_wave",
  "glow_rise",
  "glow_line",
  "glow_pinch",
  "glow_eq",
]);
const COLOR_ROLES = new Set([
  "red",
  "green",
  "blue",
  "white",
  "amber",
  "color",
  "whole_color",
  "segment_color",
]);

type Row = Record<string, unknown>;

export function matchesGroups(fixtureGroups: string[], episodeGroups: string[]): boolean {
  if (episodeGroups.length === 0) return false;
  const fx = new Set(fixtureGroups);
  const rear = ["all_rear", "rear", "par", "bar", "beam"].some((tag) => fx.has(tag));
  if (episodeGroups.length === 1 && episodeGroups[0] === "all_rear") return rear && !fx.has("face");
  const wantedTypes = episodeGroups.filter((tag) => TYPE_TAGS.has(tag));
  const wantedSides = episodeGroups.filter((tag) => SIDE_TAGS.has(tag));
  const wantedRings = episodeGroups.filter((tag) => RING_TAGS.has(tag));
  if (wantedTypes.length === 0 && wantedSides.length === 0 && wantedRings.length === 0) {
    return episodeGroups.includes("all_rear") && rear && !fx.has("face");
  }
  if (fx.has("face") && !wantedTypes.includes("face")) return false;
  if (wantedTypes.length > 0 && !wantedTypes.some((tag) => fx.has(tag))) return false;
  if (wantedSides.length > 0 && !wantedSides.some((tag) => fx.has(tag))) return false;
  if (wantedRings.length > 0 && !wantedRings.some((tag) => fx.has(tag))) return false;
  return true;
}

function channelsOf(profile: Row | undefined): Row[] {
  const raw = profile?.channels;
  return Array.isArray(raw) ? (raw.filter((item) => item && typeof item === "object") as Row[]) : [];
}

function segmentCount(profile: Row | undefined): number {
  const raw = profile?.segment_count;
  return typeof raw === "number" && Number.isInteger(raw) && raw > 0 ? raw : 0;
}

function segmentsDescribed(profile: Row | undefined): boolean {
  const count = segmentCount(profile);
  if (count < 1) return false;
  const found = new Set<number>();
  for (const channel of channelsOf(profile)) {
    const role = String(channel.role ?? "");
    const index = channel.segment_index;
    if (
      (role === "segment" || role === "segment_color") &&
      typeof index === "number" &&
      Number.isInteger(index) &&
      index >= 1
    ) {
      found.add(index);
    }
  }
  for (let index = 1; index <= count; index += 1) {
    if (!found.has(index)) return false;
  }
  return true;
}

function rolesOf(profile: Row | undefined): Set<string> {
  return new Set(channelsOf(profile).map((channel) => String(channel.role ?? "")));
}

export function lookSupported(profile: Row | undefined, kind: string, effect: string): boolean {
  const roles = rolesOf(profile);
  if (kind === "bar" && PATTERN_EFFECTS.has(effect)) return segmentsDescribed(profile);
  const hasColor = [...COLOR_ROLES].some((role) => roles.has(role));
  const hasDimmer = roles.has("dimmer");
  const hasAxes = roles.has("pan_coarse") && roles.has("tilt_coarse");
  if (kind === "beam") return hasColor || hasDimmer || hasAxes;
  if (kind === "bar") return hasColor || hasDimmer || segmentsDescribed(profile);
  if (kind === "par" || kind === "face_par") return hasColor || hasDimmer;
  return hasColor || hasDimmer;
}

function groupsOf(fixture: Row): string[] {
  return Array.isArray(fixture.groups) ? fixture.groups.map(String) : [];
}

export function lookCaption(
  fixtures: Row[],
  profiles: Record<string, Row>,
  groups: string[],
  effect: string,
): string {
  return lookProblems("дія", fixtures, profiles, groups, effect)[0] ?? "";
}

export function offeredGroups(fixtures: Row[], selected: string[] = []): string[] {
  const names = [
    "all_rear",
    "par",
    "bar",
    "beam",
    "outer",
    "inner",
    "left",
    "right",
    "rear",
  ];
  return names.filter(
    (group) =>
      selected.includes(group) ||
      fixtures.some(
        (fixture) => String(fixture.kind ?? "") !== "face_par" && matchesGroups(groupsOf(fixture), [group]),
      ),
  );
}

function lookProblems(
  label: string,
  fixtures: Row[],
  profiles: Record<string, Row>,
  groups: string[],
  effect: string,
): string[] {
  if (groups.length === 0) return [`${label}: дія не називає групу`];
  const unknown = groups.find((group) => !GROUP_NAMES.has(group));
  if (unknown) return [`${label}: невідома група ${unknown}`];
  const matched = fixtures.filter(
    (fixture) => String(fixture.kind ?? "") !== "face_par" && matchesGroups(groupsOf(fixture), groups),
  );
  if (matched.length === 0) return [`${label}: група ${groups.join("+")} не має приладів`];
  const errors: string[] = [];
  for (const fixture of matched) {
    const profile = profiles[String(fixture.profile_id ?? "")];
    const kind = String(fixture.kind ?? "");
    if (kind === "bar" && PATTERN_EFFECTS.has(effect) && !segmentsDescribed(profile)) {
      errors.push(`${label}: ${String(fixture.id)}: ефект ${effect} потребує сегменти`);
    } else if (!lookSupported(profile, kind, effect)) {
      errors.push(`${label}: ${String(fixture.id)}: ефект ${effect} не має каналу`);
    }
  }
  return errors;
}

export function sceneLookErrors(input: {
  fixtures: Row[];
  profiles: Record<string, Row>;
  presets: Array<{ id: string; episodes?: unknown }>;
}): string[] {
  const errors: string[] = [];
  for (const [profileId, profile] of Object.entries(input.profiles)) {
    if (segmentCount(profile) > 0 && !segmentsDescribed(profile)) {
      errors.push(`${profileId}: сегменти описані неповністю`);
    }
  }
  for (const preset of input.presets) {
    if (Array.isArray(preset.episodes) && preset.episodes.length === 0) {
      errors.push(`${preset.id}: епізоди мають бути списком`);
      continue;
    }
    if (!Array.isArray(preset.episodes)) continue;
    for (const episode of preset.episodes) {
      if (!episode || typeof episode !== "object") continue;
      const row = episode as Row;
      const actions = Array.isArray(row.actions) && row.actions.length > 0 ? row.actions : [row];
      for (const action of actions) {
        if (!action || typeof action !== "object") continue;
        const look = action as Row;
        const groups = Array.isArray(look.groups)
          ? look.groups.map(String)
          : Array.isArray(row.groups)
            ? row.groups.map(String)
            : [];
        const effect = String(look.effect ?? row.effect ?? "pulse");
        const ident = String(look.id ?? row.id ?? "епізод");
        errors.push(
          ...lookProblems(`${preset.id}/${ident}`, input.fixtures, input.profiles, groups, effect),
        );
      }
    }
  }
  return errors;
}

export function parseBoundedInt(raw: string, min: number, max: number): number | null {
  const text = raw.trim();
  if (!/^-?\d+$/.test(text)) return null;
  const value = Number(text);
  if (!isInt(value) || value < min || value > max) return null;
  return value;
}
