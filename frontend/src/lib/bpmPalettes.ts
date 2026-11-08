import { ref } from "vue";

export const BPM_PRESET_MODES = [
  { id: "smooth", label: "Плавний" },
  { id: "bar", label: "Такт" },
  { id: "beat", label: "Біт" },
] as const;

/** Discrete wheel slots. Bars cannot guarantee a colour they do not have. */
export const BPM_PALETTES = [
  { id: "orange", label: "Помаранчевий", kind: "mono", swatches: ["#ff6b00"], note: "" },
  { id: "red", label: "Червоний", kind: "mono", swatches: ["#ff1405"], note: "" },
  { id: "blue", label: "Синій", kind: "mono", swatches: ["#1e47ff"], note: "" },
  { id: "violet", label: "Фіолетовий", kind: "mono", swatches: ["#c71eff"], note: "Колесо бара — purple." },
  { id: "amber", label: "Бурштиновий", kind: "warm", swatches: ["#ffe61f"], note: "" },
  { id: "white", label: "Білий", kind: "mono", swatches: ["#fff5e6"], note: "" },
  { id: "ember", label: "Жар", kind: "dual", swatches: ["#ff6b00", "#ff1405"], note: "Бари помаранчеві, PAR червоні." },
  { id: "sunset", label: "Захід", kind: "warm", swatches: ["#ff6b00", "#ffe61f"], note: "Ліво помаранч, право бурштин." },
  { id: "ice", label: "Лід", kind: "cool", swatches: ["#1e47ff", "#00f2f2"], note: "Бари сині, голови ціан." },
  { id: "night", label: "Ніч", kind: "cool", swatches: ["#1e47ff", "#fff5e6"], note: "" },
  { id: "contrast", label: "Контраст", kind: "contrast", swatches: ["#ff6b00", "#1e47ff"], note: "Тепло / холод по сцені." },
  { id: "forest", label: "Ліс", kind: "dual", swatches: ["#1eff2e", "#ffe61f"], note: "" },
  { id: "carnival", label: "Карнавал", kind: "multi", swatches: ["#ff6b00", "#c71eff", "#00f2f2"], note: "" },
  { id: "traffic", label: "Світлофор", kind: "multi", swatches: ["#ff1405", "#ffe61f", "#1eff2e"], note: "" },
] as const;

export const BPM_LEGACY_PALETTES = [
  { id: "orange", label: "Помаранчевий" },
  { id: "red", label: "Червоний" },
  { id: "blue", label: "Синій" },
  { id: "violet", label: "Фіолетовий" },
] as const;

export const BPM_CASTS = [
  { id: "all", label: "Усі" },
  { id: "bars", label: "Бари" },
  { id: "heads", label: "Голови" },
] as const;

export const BPM_GROUPS = [
  { id: "beam", label: "Голови" },
  { id: "par", label: "PAR" },
  { id: "bar", label: "Бари" },
] as const;

export type BpmGroupId = (typeof BPM_GROUPS)[number]["id"];

export function groupsFromCast(cast: BpmCastId | string | null | undefined): BpmGroupId[] {
  if (cast === "bars") return ["bar"];
  if (cast === "heads") return ["par", "beam"];
  return ["par", "bar", "beam"];
}

export function normalizeGroups(value: unknown, cast?: BpmCastId | string | null): BpmGroupId[] {
  if (!Array.isArray(value)) return groupsFromCast(cast);
  const found: BpmGroupId[] = [];
  for (const item of BPM_GROUPS) {
    if (value.includes(item.id)) found.push(item.id);
  }
  return found;
}

export const BPM_CHARACTERS = [
  { id: "smooth", label: "Плавний" },
  { id: "rhythmic", label: "Ритмічний" },
  { id: "impact", label: "Ударний" },
] as const;

export const BPM_INTENSITIES = [
  { id: "soft", label: "М'яка" },
  { id: "normal", label: "Норма" },
  { id: "full", label: "Повна" },
] as const;

export const BPM_PERIODS = [
  { id: "auto", label: "Авто" },
  { id: 0.5, label: "½" },
  { id: 1, label: "1" },
  { id: 2, label: "2" },
  { id: 4, label: "4" },
  { id: 8, label: "8" },
] as const;

export type BpmPaletteId = (typeof BPM_PALETTES)[number]["id"];
export type BpmCastId = (typeof BPM_CASTS)[number]["id"];
export type BpmCharacterId = (typeof BPM_CHARACTERS)[number]["id"];
export type BpmIntensityId = (typeof BPM_INTENSITIES)[number]["id"];
export type BpmPeriodId = (typeof BPM_PERIODS)[number]["id"];

export const PERIODS_FOR_CHARACTER: Record<BpmCharacterId, readonly BpmPeriodId[]> = {
  smooth: ["auto", 2, 4, 8],
  rhythmic: ["auto", 0.5, 1, 2, 4, 8],
  impact: ["auto", 0.5, 1, 2, 4],
};

export const BPM_SECTIONS: Record<string, string> = {
  groove: "Рівний ритм",
  break: "Спад",
  riser: "Наростання",
  drop: "Дроп",
};

const LEGACY_CAST = new Set(["bars", "heads"]);

/** Location and scene stay different keys, even when the strings match. */
export function bpmChoiceKey(scope: {
  locationId?: string | null;
  sceneId?: string | null;
}): string {
  const locationId = scope.locationId?.trim();
  if (locationId) return `loc:${locationId}`;
  const sceneId = scope.sceneId?.trim();
  if (sceneId) return `scene:${sceneId}`;
  return "local";
}

function storageKey(choiceKey: string | null | undefined): string {
  return `orng-bpm-choice:${choiceKey || "local"}`;
}

export type BpmChoice = {
  scope: "preset" | "auto";
  mode: "smooth" | "bar" | "beat";
  palette: BpmPaletteId;
  cast: BpmCastId;
  groups: BpmGroupId[];
  excluded: string[];
  character: BpmCharacterId;
  intensity: BpmIntensityId;
  period: BpmPeriodId;
  parRhythm: boolean;
  accent: number;
};

export function asPeriod(value: unknown, character?: BpmCharacterId): BpmPeriodId {
  if (value === "auto" || value == null || value === "") return "auto";
  const number = Number(value);
  const match = BPM_PERIODS.find((item) => item.id !== "auto" && Number(item.id) === number);
  const period = match ? match.id : "auto";
  if (!character) return period;
  return PERIODS_FOR_CHARACTER[character].includes(period) ? period : "auto";
}

function isBpmPalette(value: unknown): value is BpmPaletteId {
  return BPM_PALETTES.some((item) => item.id === value);
}

/** A palette from the location API. Legacy cast names are not colours. */
export function paletteFromApi(value: unknown, fallback: unknown = "orange"): BpmPaletteId {
  if (isBpmPalette(value)) return value;
  if (value === "heads") return "white";
  if (isBpmPalette(fallback)) return fallback;
  return "orange";
}

function splitLegacyPalette(raw: unknown): { palette: BpmPaletteId; cast: BpmCastId } {
  if (raw === "heads") return { palette: "white", cast: "heads" };
  if (typeof raw === "string" && LEGACY_CAST.has(raw)) {
    return { palette: "orange", cast: raw as BpmCastId };
  }
  const palette = BPM_PALETTES.some((item) => item.id === raw) ? (raw as BpmPaletteId) : "orange";
  return { palette, cast: "all" };
}

export function hasDeskFlag(
  capabilities: string[] | null | undefined,
  flag: string,
): boolean {
  return Array.isArray(capabilities) && capabilities.includes(flag);
}

export function hasBpmSync(capabilities: string[] | null | undefined): boolean {
  if (!Array.isArray(capabilities) || capabilities.length === 0) return true;
  return capabilities.includes("bpm_sync");
}

export function hasAutoDirector(capabilities: string[] | null | undefined): boolean {
  return hasDeskFlag(capabilities, "auto_director");
}

/** True only while a confident musical lock owns the speed controls.
 * An older runtime omits ``locked``; the switch itself then holds the controls.
 */
export function bpmControlsLocked(
  bpm: { enabled?: boolean; locked?: boolean } | null | undefined,
): boolean {
  if (!bpm?.enabled) return false;
  if (typeof bpm.locked === "boolean") return bpm.locked;
  return true;
}

export function readBpmChoice(choiceKey?: string | null): BpmChoice {
  const fallback: BpmChoice = {
    scope: "preset",
    mode: "smooth",
    palette: "orange",
    cast: "all",
    groups: ["par", "bar", "beam"],
    excluded: [],
    character: "rhythmic",
    intensity: "full",
    period: "auto",
    parRhythm: false,
    accent: 0,
  };
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey(choiceKey)) || "") as Partial<BpmChoice> & {
      palette?: string;
    };
    const mode = BPM_PRESET_MODES.some((item) => item.id === raw.mode) ? raw.mode : fallback.mode;
    const split = splitLegacyPalette(raw.palette);
    const cast = BPM_CASTS.some((item) => item.id === raw.cast) ? raw.cast : split.cast;
    const character = BPM_CHARACTERS.some((item) => item.id === raw.character)
      ? raw.character
      : fallback.character;
    const intensity = BPM_INTENSITIES.some((item) => item.id === raw.intensity)
      ? raw.intensity
      : fallback.intensity;
    return {
      scope: raw.scope === "auto" ? "auto" : "preset",
      mode: mode ?? fallback.mode,
      palette: split.palette,
      cast: cast ?? fallback.cast,
      groups: normalizeGroups(raw.groups, cast ?? fallback.cast),
      excluded: Array.isArray(raw.excluded)
        ? raw.excluded.filter((item): item is string => typeof item === "string")
        : [],
      character: character ?? fallback.character,
      intensity: intensity ?? fallback.intensity,
      period: asPeriod(raw.period, character ?? fallback.character),
      parRhythm: raw.parRhythm === true,
      accent: typeof raw.accent === "number" && raw.accent > 0 ? Math.min(1, raw.accent) : 0,
    };
  } catch {
    return fallback;
  }
}

export const bpmChoiceRevision = ref(0);

export function writeBpmChoice(choice: BpmChoice, choiceKey?: string | null) {
  try {
    localStorage.setItem(storageKey(choiceKey), JSON.stringify(choice));
  } catch {
    /* the highlight still stays for this visit */
  }
  bpmChoiceRevision.value += 1;
}
