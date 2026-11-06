export const PRESET_CATALOG = [
  { id: "A01", label: "Static 1" },
  { id: "A02", label: "Static 2" },
  { id: "A03", label: "Static 3" },
  { id: "P01", label: "Look 1" },
  { id: "P02", label: "Look 2" },
  { id: "P03", label: "Look 3" },
  { id: "P04", label: "Look 4" },
  { id: "P05", label: "Look 5" },
  { id: "P06", label: "Look 6" },
  { id: "P07", label: "Look 7" },
  { id: "P08", label: "Look 8" },
  { id: "P09", label: "Look 9" },
  { id: "P10", label: "Look 10" },
] as const;

export type PresetId = (typeof PRESET_CATALOG)[number]["id"];

/** Mirror of backend PALETTE_RGBW — used only to preview a preset's real look. */
export const PALETTE_CSS: Record<string, string> = {
  warm_orange: "rgb(255, 89, 13)",
  deep_red: "rgb(255, 20, 5)",
  amber: "rgb(255, 140, 20)",
  white_warm: "rgb(255, 226, 187)",
  violet_orange: "rgb(217, 51, 191)",
  cool_blue: "rgb(38, 89, 255)",
  mint: "rgb(51, 242, 166)",
  magenta: "rgb(255, 26, 140)",
  rose: "rgb(255, 36, 26)",
  deep_blue: "rgb(13, 46, 255)",
  violet: "rgb(140, 20, 255)",
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
