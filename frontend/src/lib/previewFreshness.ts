export type PreviewStamp = {
  session: string;
  sequence: number;
  at: number;
};

/**
 * Remote desk stays live across one effect report and one missed report.
 * A stopped renderer still ages out: a repeated sequence does not move `at`.
 */
/** One report every 5s, plus one missed report, plus a little slack. */
export const PREVIEW_REPORT_MS = 5_000;
export const REMOTE_PREVIEW_LIVE_MS = PREVIEW_REPORT_MS * 2 + 2_000;

/**
 * Birth time of a device snapshot on the browser clock.
 *
 * ``age_ms`` is measured on the device when the packet is built. The browser
 * subtracts that age from its own clock. It never subtracts the Pi monotonic
 * clock. A second delivery of the same session and sequence keeps the earlier
 * birth, so a repeat cannot make the picture young again.
 */
export function acceptPreview(
  previous: PreviewStamp | null,
  payload: { session?: unknown; sequence?: unknown; age_ms?: unknown },
  now = Date.now(),
): PreviewStamp | null {
  const session = typeof payload.session === "string" ? payload.session : "";
  const sequence =
    typeof payload.sequence === "number" && Number.isFinite(payload.sequence)
      ? payload.sequence
      : null;
  const age =
    typeof payload.age_ms === "number" && Number.isFinite(payload.age_ms)
      ? Math.max(0, payload.age_ms)
      : null;
  if (!session || sequence === null || age === null) return null;
  const born = now - age;
  if (previous && previous.session === session && previous.sequence === sequence) {
    return { session, sequence, at: Math.min(previous.at, born) };
  }
  return { session, sequence, at: born };
}
