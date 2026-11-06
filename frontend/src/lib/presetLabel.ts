/** Short English pad names. Stored scene labels stay unchanged. */
export function presetButtonLabel(id: string, fallback?: string): string {
  const staticMatch = /^A0*(\d+)$/.exec(id);
  if (staticMatch) return `Static ${Number(staticMatch[1])}`;
  const lookMatch = /^P0*(\d+)$/.exec(id);
  if (lookMatch) return `Look ${Number(lookMatch[1])}`;
  if (id === "NONE") return "NONE";
  const text = (fallback || id).trim();
  return text.length > 16 ? id : text;
}
