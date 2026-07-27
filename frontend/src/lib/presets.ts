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

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60)
    .toString()
    .padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}
