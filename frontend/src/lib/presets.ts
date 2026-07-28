export const PRESET_CATALOG = [
  { id: "P01", label: "Дуже плавний" },
  { id: "P02", label: "Атмосферний" },
  { id: "P03", label: "М'яка динаміка" },
  { id: "P04", label: "Помірний" },
  { id: "P05", label: "Універсальний" },
  { id: "P06", label: "Ритмічний" },
  { id: "P07", label: "Активний" },
  { id: "P08", label: "Енергійний" },
  { id: "P09", label: "Жорсткий" },
  { id: "P10", label: "Максимальний" },
] as const;

export type PresetId = (typeof PRESET_CATALOG)[number]["id"];

/** Mirror of backend PALETTE_RGBW — used only to preview a preset's real look. */
export const PALETTE_CSS: Record<string, string> = {
  warm_orange: "rgb(255, 89, 13)",
  deep_red: "rgb(255, 13, 5)",
  amber: "rgb(255, 140, 20)",
  white_warm: "rgb(255, 226, 187)",
  violet_orange: "rgb(217, 51, 191)",
  cool_blue: "rgb(38, 89, 255)",
  mint: "rgb(51, 242, 166)",
  magenta: "rgb(255, 26, 140)",
};

export function paletteCss(name: string): string {
  return PALETTE_CSS[name] ?? "rgb(120, 124, 136)";
}

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60)
    .toString()
    .padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}
