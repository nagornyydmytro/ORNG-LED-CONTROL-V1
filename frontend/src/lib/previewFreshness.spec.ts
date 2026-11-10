import { describe, expect, it } from "vitest";
import { PREVIEW_REPORT_MS, REMOTE_PREVIEW_LIVE_MS, acceptPreview } from "./previewFreshness";

describe("device preview freshness", () => {
  it("dates an old snapshot from its device age, not from arrival", () => {
    const stamp = acceptPreview(
      null,
      { session: "render-1", sequence: 4, age_ms: 5000 },
      20_000,
    );
    expect(stamp).toEqual({ session: "render-1", sequence: 4, at: 15_000 });
    expect(20_000 - (stamp?.at ?? 20_000)).toBeGreaterThan(750);
  });

  it("does not make a repeated snapshot younger", () => {
    const first = acceptPreview(
      null,
      { session: "render-1", sequence: 4, age_ms: 20 },
      10_000,
    );
    const again = acceptPreview(
      first,
      { session: "render-1", sequence: 4, age_ms: 5020 },
      15_000,
    );
    expect(again?.at).toBe(9980);
    expect(15_000 - (again?.at ?? 15_000)).toBeGreaterThan(750);
  });

  it("accepts the next snapshot from the same renderer", () => {
    const held = acceptPreview(
      null,
      { session: "render-1", sequence: 4, age_ms: 4000 },
      10_000,
    );
    const next = acceptPreview(
      held,
      { session: "render-1", sequence: 5, age_ms: 12 },
      10_000,
    );
    expect(next?.at).toBe(9988);
  });

  it("stays controllable across one report and one missed report", () => {
    expect(PREVIEW_REPORT_MS).toBe(5_000);
    expect(REMOTE_PREVIEW_LIVE_MS).toBeGreaterThan(PREVIEW_REPORT_MS);
    expect(REMOTE_PREVIEW_LIVE_MS).toBeGreaterThanOrEqual(PREVIEW_REPORT_MS * 2);
    const stamp = acceptPreview(null, { session: "render-1", sequence: 8, age_ms: 100 }, 20_000);
    expect(stamp).not.toBeNull();
    const age = 20_000 - (stamp?.at ?? 20_000);
    expect(age).toBeLessThan(REMOTE_PREVIEW_LIVE_MS);
    expect(PREVIEW_REPORT_MS + 100).toBeLessThan(REMOTE_PREVIEW_LIVE_MS);
  });

  it("rejects a packet that has no verifiable age", () => {
    expect(acceptPreview(null, { session: "render-1", sequence: 1 }, 10_000)).toBeNull();
    expect(
      acceptPreview(null, { captured_at: 123.4, sequence: 1, age_ms: 10 }, 10_000),
    ).toBeNull();
  });
});
