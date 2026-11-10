/** The effect that is playing. The hall does not send a fixture picture. */

const EFFECT_FIELDS = ["program", "look", "preset_id", "session", "sequence", "age_ms"] as const;

export function previewRelayPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of EFFECT_FIELDS) {
    if (payload[key] !== undefined) out[key] = payload[key];
  }
  return out;
}
