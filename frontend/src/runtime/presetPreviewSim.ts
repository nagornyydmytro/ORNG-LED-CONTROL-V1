import { PALETTE_CSS } from "../lib/presets";
import type { SimulatorView, StageLayout } from "../vite-env";
import { matchesGroups } from "./sceneValidate";

type PreviewEpisode = {
  palette?: string;
  groups?: string[];
  intensity?: number;
  speed?: number;
  effect?: string;
  duration_s?: number;
  look?: string | null;
  head?: string | null;
  mix_style?: "picture" | "classic" | null;
  transition?: string;
};

const SEGMENTS = 8;

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

export function paletteRgb(name: string): { r: number; g: number; b: number } {
  const css = PALETTE_CSS[name] ?? "rgb(120, 124, 136)";
  const match = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(css);
  if (!match) return { r: 120 / 255, g: 124 / 255, b: 136 / 255 };
  return {
    r: Number(match[1]) / 255,
    g: Number(match[2]) / 255,
    b: Number(match[3]) / 255,
  };
}

/** Semantic RGB the venue uses before the bar wheel snaps. Mirrors PALETTE_RGBW. */
const ENGINE_RGB: Record<string, [number, number, number]> = {
  warm_orange: [1, 0.35, 0.05],
  deep_red: [1, 0.05, 0.02],
  amber: [1, 0.55, 0.08],
  white_warm: [1, 0.85, 0.6],
  violet_orange: [0.85, 0.2, 0.75],
  cool_blue: [0.15, 0.35, 1],
  mint: [0.2, 0.95, 0.65],
  magenta: [1, 0.1, 0.55],
  rose: [1, 0.14, 0.1],
  deep_blue: [0.05, 0.18, 1],
  violet: [0.55, 0.08, 1],
  cyan: [0, 1, 1],
};

/** Saved bar wheel. Nearest of these is the DMX slot the bar can actually show. */
const BAR_WHEEL: Record<string, [number, number, number]> = {
  red: [1, 0, 0],
  orange: [1, 0.35, 0.05],
  green: [0, 1, 0],
  blue: [0, 0, 1],
  white: [1, 1, 1],
  amber: [1, 0.55, 0],
  cyan: [0, 1, 1],
  purple: [0.7, 0, 1],
};

/** Same venue readout as renderer._PALETTE_DISPLAY_RGB. Not the CSS swatch. */
const BAR_DISPLAY: Record<string, [number, number, number]> = {
  red: [1, 0.08, 0.02],
  orange: [1, 0.42, 0],
  green: [0.12, 1, 0.18],
  blue: [0.12, 0.28, 1],
  white: [1, 0.96, 0.9],
  amber: [1, 0.9, 0.12],
  cyan: [0, 0.95, 0.95],
  purple: [0.78, 0.12, 1],
};

/** Named slots the decoder matches before it picks a display colour. */
const MATCH_RGB: Record<string, [number, number, number]> = {
  red: [1, 0, 0],
  orange: [1, 0.35, 0.05],
  green: [0, 1, 0],
  blue: [0, 0, 1],
  white: [1, 1, 1],
  amber: [1, 0.55, 0],
  cyan: [0, 1, 1],
  purple: [0.7, 0, 1],
};

/** Bar preview colour after the same snap the renderer writes to DMX. */
export function barPreviewRgb(
  palette: string,
  mix: boolean,
): { r: number; g: number; b: number; slot: string } {
  const name = mix && palette === "magenta" ? "deep_red" : palette;
  const source = ENGINE_RGB[name] ?? [paletteRgb(name).r, paletteRgb(name).g, paletteRgb(name).b];
  let slot = "red";
  let best = 1e9;
  for (const [key, rgb] of Object.entries(BAR_WHEEL)) {
    const distance =
      (source[0] - rgb[0]) ** 2 + (source[1] - rgb[1]) ** 2 + (source[2] - rgb[2]) ** 2;
    if (distance < best) {
      best = distance;
      slot = key;
    }
  }
  const shown = BAR_DISPLAY[slot] ?? BAR_WHEEL[slot]!;
  return { r: shown[0], g: shown[1], b: shown[2], slot };
}

/** RGB fixture readout. Matches decode._preview_rgb_from_channels. */
export function fixturePreviewRgb(palette: string): { r: number; g: number; b: number } {
  const written = ENGINE_RGB[palette] ?? [paletteRgb(palette).r, paletteRgb(palette).g, paletteRgb(palette).b];
  if (written[0] + written[1] + written[2] < 0.02) {
    return { r: written[0], g: written[1], b: written[2] };
  }
  let name = "red";
  let best = 1e9;
  for (const [key, rgb] of Object.entries(MATCH_RGB)) {
    const distance =
      (written[0] - rgb[0]) ** 2 + (written[1] - rgb[1]) ** 2 + (written[2] - rgb[2]) ** 2;
    if (distance < best) {
      best = distance;
      name = key;
    }
  }
  const match = MATCH_RGB[name]!;
  const display = BAR_DISPLAY[name]!;
  const dist =
    (written[0] - match[0]) ** 2 + (written[1] - match[1]) ** 2 + (written[2] - match[2]) ** 2;
  if (dist > 0.08) return { r: written[0], g: written[1], b: written[2] };
  const src = Math.max(written[0], written[1], written[2], 1e-6);
  const dst = Math.max(display[0], display[1], display[2], 1e-6);
  const scale = src / dst;
  return {
    r: Math.min(1, display[0] * scale),
    g: Math.min(1, display[1] * scale),
    b: Math.min(1, display[2] * scale),
  };
}

function fixtureTags(place: { kind?: string; groups?: string[] }): string[] {
  const tags = Array.isArray(place.groups) ? place.groups.map(String) : [];
  const kind = String(place.kind || "");
  if (kind && !tags.includes(kind)) tags.push(kind);
  if ((kind === "face" || kind === "face_par") && !tags.includes("face")) tags.push("face");
  return tags;
}

export function episodeLightsFixture(
  place: { kind?: string; groups?: string[] },
  groups: string[],
): boolean {
  if (!groups.length) return true;
  return matchesGroups(fixtureTags(place), groups);
}

/** Visible mock motion for desk preview — not the venue renderer. */
export function effectMotion(
  effect: string,
  speed: number,
  localT: number,
  index: number,
  count: number,
): { level: number; seg: number } {
  const rate = 0.4 + Math.max(0.05, speed) * 2.4;
  const phase = localT * rate * Math.PI * 2;
  const pos = count <= 1 ? 0.5 : index / (count - 1);
  switch (effect) {
    case "static":
      return { level: 1, seg: 1 };
    case "glow":
      return { level: 0.72 + 0.28 * Math.sin(phase * 0.55), seg: 1 };
    case "breathe":
      return { level: 0.28 + 0.72 * (0.5 + 0.5 * Math.sin(phase)), seg: 1 };
    case "pulse":
      return { level: 0.18 + 0.82 * Math.max(0, Math.sin(phase)), seg: 1 };
    case "wave":
    case "glow_wave": {
      const v = 0.22 + 0.78 * (0.5 + 0.5 * Math.sin(phase - pos * Math.PI * 2));
      return { level: v, seg: v };
    }
    case "chase":
    case "glow_line": {
      const head = ((localT * rate) % 1) * count;
      const dist = Math.min(
        Math.abs(index - head),
        Math.abs(index - head + count),
        Math.abs(index - head - count),
      );
      const v = clamp01(1 - dist / 1.55);
      return { level: 0.2 + 0.8 * v, seg: v };
    }
    case "mirror_sweep":
    case "glow_pinch": {
      const center = 0.5 + 0.45 * Math.sin(phase);
      const v = clamp01(1 - Math.abs(pos - center) * 2.4);
      return { level: 0.18 + 0.82 * v, seg: v };
    }
    case "glow_rise": {
      const rise = (localT * rate) % 1;
      const v = pos <= rise ? 1 : clamp01(1 - (pos - rise) * 3);
      return { level: 0.25 + 0.75 * v, seg: v };
    }
    case "glow_eq": {
      const v = 0.25 + 0.75 * Math.abs(Math.sin(phase + index * 0.85));
      return { level: v, seg: v };
    }
    default:
      return { level: 0.55 + 0.45 * Math.sin(phase), seg: 1 };
  }
}

/** Desk picture of an auto look. Null keeps the ordinary effect motion. */
export function lookSegments(
  look: string,
  barIndex: number,
  barCount: number,
  localT: number,
): number[] | null {
  const phase = ((localT * 0.22) % 1 + 1) % 1;
  const count = Math.max(1, barCount);
  const x = count <= 1 ? 0.5 : barIndex / (count - 1);
  const cells = (on: (seg: number) => boolean) =>
    Array.from({ length: SEGMENTS }, (_, seg) => (on(seg) ? 1 : 0));
  if (look === "band_left" || look === "band_right" || look === "bands_2_left") {
    const heads =
      look === "bands_2_left"
        ? [phase, (phase + 0.5) % 1]
        : [look === "band_right" ? (1 - phase + 1) % 1 : phase];
    const near = heads.some((head) => {
      const distance = Math.min(Math.abs(x - head), 1 - Math.abs(x - head));
      return distance < 0.24;
    });
    return cells(() => near);
  }
  if (
    look === "band_diag" ||
    look === "band_diag_flip" ||
    look === "bands_2_diag" ||
    look === "meet_diag"
  ) {
    return cells((seg) => {
      const y = seg / 7;
      const target = look === "band_diag_flip" ? 1 - x : x;
      const primary = (target + phase) % 1;
      const secondary = (primary + 0.5) % 1;
      const close = (band: number) => Math.min(Math.abs(y - band), 1 - Math.abs(y - band)) < 0.2;
      if (look === "bands_2_diag" || look === "meet_diag") return close(primary) || close(secondary);
      return close(primary);
    });
  }
  if (look === "iris") {
    const open = 0.5 + 0.5 * Math.sin(phase * Math.PI * 2);
    const reach = 1 + Math.round(open * 3);
    return cells((seg) => seg < reach || seg >= SEGMENTS - reach);
  }
  if (look === "negative_pulse_hole") {
    const hole = Math.round(phase * 7) % SEGMENTS;
    return cells((seg) => seg !== hole);
  }
  if (look === "pulse_by_bar") {
    const on = Math.floor(phase * count) % count;
    return cells(() => barIndex === on);
  }
  if (look === "fill_down") {
    // Same geometry as the library look: segment 0 is the bottom, the saw
    // fills from the top and grows downward.
    const saw = phase;
    return cells((seg) => {
      const up = (seg + 0.5) / SEGMENTS;
      return 1 - up <= saw + 1 / 8;
    });
  }
  if (look === "breathe_bars" || look === "breathe_pars") {
    const level = 0.12 + 0.88 * (0.5 + 0.5 * Math.sin(phase * Math.PI * 2));
    return Array.from({ length: SEGMENTS }, () => level);
  }
  if (look === "hit_bars" || look === "hit_pars") {
    const hit = Math.sin(phase * Math.PI * 8) > 0.2 ? 1 : 0;
    return Array.from({ length: SEGMENTS }, () => hit);
  }
  return null;
}

function beatHit(phase: number, sharpness: number): number {
  const wrapped = ((phase % 1) + 1) % 1;
  return Math.exp(-wrapped * Math.max(0.4, sharpness));
}

function mixHeadLamp(episode: PreviewEpisode, localT: number, index: number): number {
  const base = clamp01(Number(episode.intensity) || 0.85);
  const look = String(episode.look || "");
  const effect = String(episode.effect || "");
  const t = Math.max(0, localT);
  if (look === "breathe_bars" || look === "breathe_pars" || effect === "breathe") {
    return base * (0.04 + 0.96 * (0.5 + 0.5 * Math.sin(2 * Math.PI * t * 0.29)));
  }
  if (look === "hit_bars" || look === "hit_pars" || effect === "pulse") {
    return base * beatHit(t * 0.85, 9);
  }
  if (look === "band_left" || look === "band_right" || look === "bands_2_left" || look === "bands_2_diag") {
    const onLeft = (t * 0.22) % 1 < 0.5;
    const mine = (index % 2 === 0) === onLeft;
    return base * (mine ? 1 : 0.12);
  }
  return base * (0.5 + 0.5 * Math.sin(2 * Math.PI * (t * 0.22 + index)));
}

function headPose(
  style: string,
  localT: number,
  index: number,
  mixStyle: "picture" | "classic" | null,
  episode?: PreviewEpisode,
): { pan: number; tilt: number; dimmer: number } {
  const turns = Math.max(0, localT) * 0.28;
  const flip = Math.floor(turns) % 2 === 0;
  const left = index % 2 === 0;
  const picture = mixStyle === "picture";
  const rules = mixStyle === "picture" || mixStyle === "classic";
  let pan = 0.5;
  let tilt = picture ? 0.48 : 0.5;
  if (style === "nod") {
    pan = left ? 0.42 : 0.58;
    tilt = picture ? (flip ? 0.28 : 0.64) : flip ? 0.16 : 0.84;
  } else if (style === "cross") {
    pan = (index + Math.floor(turns)) % 2 === 0 ? 0.14 : 0.86;
    tilt = 0.46;
  } else if (style === "rise") {
    pan = left ? 0.36 : 0.64;
    const climb = ((turns % 1) + 1) % 1;
    tilt = picture ? 0.24 + 0.4 * climb : climb;
  } else if (style === "pulse") {
    pan = left ? 0.22 : 0.78;
    tilt = picture ? (flip ? 0.4 : 0.6) : flip ? 0.62 : 0.38;
  } else {
    const toward = flip ? 0.1 : 0.9;
    pan = left ? 1 - toward : toward;
    tilt = picture ? 0.48 : 0.5;
  }
  if (rules) tilt = Math.min(0.64, Math.max(0.24, tilt));
  const base = clamp01(Number(episode?.intensity) || 0.85);
  const dimmer = picture
    ? mixHeadLamp(episode ?? {}, localT, index)
    : mixStyle === "classic"
      ? base * (0.28 + 0.72 * beatHit(localT * 1.15, 5.5))
      : clamp01(0.15 + 0.85 * Math.max(0, Math.sin(localT * 1.4 + index * 0.8)));
  return { pan: clamp01(pan), tilt: clamp01(tilt), dimmer: clamp01(dimmer) };
}

function glowEqSegments(localT: number, barIndex: number, barTotal: number, base: number): number[] {
  const phase = ((localT * 0.22) % 1 + 1) % 1;
  const barX = barTotal <= 1 ? 0.5 : barIndex / Math.max(1, barTotal - 1);
  const wave = 0.5 + 0.5 * Math.sin(2 * Math.PI * (phase - barX * 1.35));
  const height = 1.2 + 6.3 * wave;
  return Array.from({ length: SEGMENTS }, (_, index) => (index < height ? base : 0));
}

export function previewFrameAt(
  episode: PreviewEpisode,
  layout: StageLayout,
  localT: number,
  opts?: { mix?: boolean; mixStyle?: "picture" | "classic" | null },
): SimulatorView {
  const mixStyle =
    opts?.mixStyle === "picture" || opts?.mixStyle === "classic"
      ? opts.mixStyle
      : opts?.mix
        ? "picture"
        : null;
  const color = fixturePreviewRgb(String(episode.palette || ""));
  const barColor = barPreviewRgb(String(episode.palette || ""), mixStyle !== null);
  const groups = Array.isArray(episode.groups) ? episode.groups.map(String) : [];
  const base = Math.max(0.12, Math.min(1, Number(episode.intensity) || 0.85));
  const speed = Math.max(0.05, Math.min(1, Number(episode.speed) || 0.35));
  const effect = String(episode.effect || "glow");
  const look = String(episode.look || "");
  const head = typeof episode.head === "string" ? episode.head : "";
  const pars = [];
  const bars = [];
  const beams = [];
  const faces = [];
  let barIndex = 0;
  let parIndex = 0;
  let beamIndex = 0;
  const barTotal = layout.placements.filter(
    (place) => episodeLightsFixture(place, groups) && String(place.kind || "") === "bar",
  ).length;
  const parTotal = layout.placements.filter((place) => {
    if (!episodeLightsFixture(place, groups)) return false;
    const kind = String(place.kind || "");
    return kind !== "bar" && kind !== "beam";
  }).length;
  const beamTotal = layout.placements.filter(
    (place) => episodeLightsFixture(place, groups) && String(place.kind || "") === "beam",
  ).length;

  for (const place of layout.placements) {
    const kind = String(place.kind || "");
    const inPicture = episodeLightsFixture(place, groups);
    if (kind === "beam") {
      if (!head && !inPicture) continue;
    } else if (!inPicture) {
      continue;
    }
    if (kind === "bar") {
      const painted = look ? lookSegments(look, barIndex, Math.max(1, barTotal), localT) : null;
      const segments =
        !painted && effect === "glow_eq"
          ? glowEqSegments(localT, barIndex, Math.max(1, barTotal), base)
          : Array.from({ length: SEGMENTS }, (_, i) => {
              if (painted) return clamp01(painted[i]! * base);
              const motion = effectMotion(effect, speed, localT, i, SEGMENTS);
              return clamp01(motion.seg * base);
            });
      const motion = effectMotion(effect, speed, localT, barIndex, Math.max(1, barTotal));
      bars.push({
        id: place.fixture_id,
        label: place.label ?? place.fixture_id,
        kind,
        side: "",
        ring: "",
        order: null,
        dimmer: clamp01(motion.level * base),
        r: barColor.r,
        g: barColor.g,
        b: barColor.b,
        segments,
      });
      barIndex += 1;
    } else if (kind === "beam") {
      const motion = effectMotion(effect, speed, localT, beamIndex, Math.max(1, beamTotal));
      const sway = Math.sin(localT * (0.7 + speed * 2) + beamIndex) * 0.12;
      const pose = head ? headPose(head, localT, beamIndex, mixStyle, episode) : null;
      beams.push({
        id: place.fixture_id,
        label: place.label ?? place.fixture_id,
        kind,
        side: "",
        order: null,
        pan: pose ? pose.pan : clamp01(0.5 + sway),
        tilt: pose ? pose.tilt : clamp01(0.45 + Math.cos(localT * (0.55 + speed) + beamIndex) * 0.1),
        dimmer: pose ? pose.dimmer : clamp01(motion.level * base),
        shutter_open: true,
        r: color.r,
        g: color.g,
        b: color.b,
        strobe: 0,
        dir_x: sway * 0.8,
        dir_y: -1,
        dir_z: 0,
        hit_x: place.x + sway * 0.15,
        hit_z: place.z,
        throw: 1,
      });
      beamIndex += 1;
    } else {
      const motion = effectMotion(effect, speed, localT, parIndex, Math.max(1, parTotal));
      const fx = {
        id: place.fixture_id,
        label: place.label ?? place.fixture_id,
        kind,
        side: "",
        ring: "",
        face: kind === "face" || kind === "face_par",
        order: null,
        r: color.r,
        g: color.g,
        b: color.b,
        w: 0,
        intensity: clamp01(motion.level * base),
      };
      if (kind === "face" || kind === "face_par") faces.push(fx);
      else pars.push(fx);
      parIndex += 1;
    }
  }
  return {
    pars,
    bars,
    beams,
    faces,
    nonzero_channels: pars.length + bars.length + beams.length + faces.length,
    blackout_visual: false,
  };
}

type MixStyle = "picture" | "classic" | null;

function savedStyle(episode: PreviewEpisode, presetId: string): MixStyle {
  if (episode.mix_style === "picture" || episode.mix_style === "classic") return episode.mix_style;
  return presetId === "Mix" ? "picture" : null;
}

function transitionWindow(transition: string, duration: number): number {
  if (transition === "fade") return Math.min(2, Math.max(duration, 0.001));
  if (transition === "soft") return Math.min(3, Math.max(duration, 0.001));
  return 0;
}

function transitionBlend(transition: string, localT: number, duration: number): number {
  const window = transitionWindow(transition, duration);
  if (window <= 0 || localT >= window) return 0;
  if (transition === "fade") return 1 - localT / window;
  const t = localT / window;
  const eased = t * t * (3 - 2 * t);
  return 1 - eased;
}

function joinLevels(arrived: number, sameColor: boolean, transition: string): { prev: number; cur: number } {
  const t = clamp01(arrived);
  if (sameColor && transition === "soft") return { prev: 1 - t, cur: t };
  if (sameColor) return { prev: Math.max(0, 1 - t * 1.6), cur: Math.max(0, t * 1.6 - 0.45) };
  if (transition === "soft") return { prev: Math.max(0, 1 - t * 2.8), cur: Math.max(0, (t - 0.34) / 0.66) };
  return { prev: Math.max(0, 1 - t * 2.4), cur: Math.max(0, t * 2.4 - 1.35) };
}

function mixJoinFrames(
  previous: SimulatorView,
  current: SimulatorView,
  arrived: number,
  sameColor: boolean,
  transition: string,
): SimulatorView {
  const levels = joinLevels(arrived, sameColor, transition);
  const useNew = levels.cur >= levels.prev;
  const pick = <T extends { id: string }>(newer: T[], older: T[]): Array<{ item: T; level: number }> => {
    const chosen: Array<{ item: T; level: number }> = [];
    for (const item of newer) {
      const prior = older.find((row) => row.id === item.id);
      const source = useNew ? item : (prior ?? item);
      const level = useNew ? levels.cur : prior ? levels.prev : levels.cur;
      if (level <= 0.02) continue;
      chosen.push({ item: source, level });
    }
    return chosen;
  };
  const pars = pick(current.pars, previous.pars).map(({ item, level }) => ({
    ...item,
    intensity: clamp01(item.intensity * level),
  }));
  const bars = pick(current.bars, previous.bars).map(({ item, level }) => ({
    ...item,
    dimmer: clamp01(item.dimmer * level),
    segments: item.segments.map((value) => clamp01(value * level)),
  }));
  const beams = pick(current.beams, previous.beams).map(({ item, level }) => ({
    ...item,
    dimmer: clamp01(item.dimmer * level),
  }));
  return {
    pars,
    bars,
    beams,
    faces: [],
    nonzero_channels: pars.length + bars.length + beams.length,
    blackout_visual: false,
  };
}

export function buildPresetPreviewClip(
  episodes: PreviewEpisode[],
  layout: StageLayout,
  opts: { presetId: string; seconds?: number; fps?: number; startS?: number },
): {
  preset_id: string;
  fps: number;
  seconds: number;
  start_s: number;
  transport: "none";
  playback: "sim";
  safe_mock_preview: true;
  frames: SimulatorView[];
} {
  const fps = Math.max(8, Math.min(15, Math.round(opts.fps ?? 12)));
  const seconds = Math.max(2, Math.min(12, Number(opts.seconds) || 8));
  const source = episodes.length ? episodes : [{ palette: "deep_red", effect: "breathe", duration_s: 18 }];
  let cursor = 0;
  const timed = source.map((episode) => {
    const duration = Math.max(0.5, Number(episode.duration_s) || 18);
    const start = cursor;
    cursor += duration;
    return { episode, start, duration };
  });
  const cycle = Math.max(0.5, cursor);
  const startS = Math.max(0, Math.min(Number(opts.startS) || 0, cycle));
  const total = Math.max(1, Math.round(seconds * fps));
  const frames: SimulatorView[] = [];
  for (let i = 0; i < total; i += 1) {
    const showT = (((startS + i / fps) % cycle) + cycle) % cycle;
    const index = timed.findIndex((item) => showT >= item.start && showT < item.start + item.duration);
    const row = timed[index >= 0 ? index : timed.length - 1]!;
    const localT = showT - row.start;
    const style = savedStyle(row.episode, opts.presetId);
    const plain = previewFrameAt(row.episode, layout, localT, { mixStyle: style });
    const window = transitionWindow(String(row.episode.transition || ""), row.duration);
    if (!style || localT >= window || localT < 0) {
      frames.push(plain);
      continue;
    }
    const prev = timed[(index + timed.length - 1) % timed.length]!;
    const previous = previewFrameAt(prev.episode, layout, Math.max(0, prev.duration - 1e-4), {
      mixStyle: savedStyle(prev.episode, opts.presetId),
    });
    const blend = transitionBlend(String(row.episode.transition || ""), localT, row.duration);
    frames.push(
      mixJoinFrames(
        previous,
        plain,
        1 - blend,
        String(prev.episode.palette || "") === String(row.episode.palette || ""),
        String(row.episode.transition || ""),
      ),
    );
  }
  return {
    preset_id: opts.presetId,
    fps,
    seconds,
    start_s: startS,
    transport: "none",
    playback: "sim",
    safe_mock_preview: true,
    frames,
  };
}
