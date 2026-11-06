/** Same group and channel contract as the Python scene check. */

const GROUPS = new Set([
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
  if (episodeGroups.length === 1 && episodeGroups[0] === "all_rear") {
    return rear && !fx.has("face");
  }
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

export function sceneCapabilityProblems(parsed: Map<string, unknown>): string[] {
  const errors: string[] = [];
  const profiles = new Map<string, Row>();
  for (const [path, doc] of parsed) {
    if (!path.startsWith("profiles/") || !doc || typeof doc !== "object") continue;
    const profileId = path.split("/").pop()!.replace(/\.ya?ml$/, "");
    const profile = doc as Row;
    profiles.set(profileId, profile);
    const count = segmentCount(profile);
    if (count > 0 && !segmentsDescribed(profile)) {
      errors.push(`${profileId}: сегменти описані неповністю`);
    }
  }
  const patch = parsed.get("patch.yaml");
  const fixtures =
    patch && typeof patch === "object" && Array.isArray((patch as Row).fixtures)
      ? ((patch as Row).fixtures as Row[])
      : [];
  for (const [path, doc] of parsed) {
    if (!path.startsWith("presets/") || !doc || typeof doc !== "object") continue;
    const presetId = path.split("/").pop()!.replace(/\.ya?ml$/, "");
    const episodes = (doc as Row).episodes;
    if (Array.isArray(episodes) && episodes.length === 0) {
      errors.push(`${presetId}: епізоди мають бути списком`);
      continue;
    }
    if (!Array.isArray(episodes)) continue;
    for (const episode of episodes) {
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
        const label = `${presetId}/${ident}`;
        const unknown = groups.find((group) => !GROUPS.has(group));
        if (groups.length === 0) {
          errors.push(`${label}: дія не називає групу`);
          continue;
        }
        if (unknown) {
          errors.push(`${label}: невідома група ${unknown}`);
          continue;
        }
        const matched = fixtures.filter(
          (fixture) =>
            String(fixture.kind ?? "") !== "face_par" && matchesGroups(groupsOf(fixture), groups),
        );
        if (matched.length === 0) {
          errors.push(`${label}: група ${groups.join("+")} не має приладів`);
          continue;
        }
        for (const fixture of matched) {
          const profile = profiles.get(String(fixture.profile_id ?? ""));
          const kind = String(fixture.kind ?? "");
          if (kind === "bar" && PATTERN_EFFECTS.has(effect) && !segmentsDescribed(profile)) {
            errors.push(`${label}: ${String(fixture.id)}: ефект ${effect} потребує сегменти`);
          } else if (!lookSupported(profile, kind, effect)) {
            errors.push(`${label}: ${String(fixture.id)}: ефект ${effect} не має каналу`);
          }
        }
      }
    }
  }
  return errors;
}
