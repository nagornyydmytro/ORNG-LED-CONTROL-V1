import { describe, expect, it } from "vitest";
import { episodeIndexAt, episodeStartS } from "./episodeClock";

describe("episode boundaries", () => {
  const durations = [4, 10, 2];

  it("does not use the average length as the episode start", () => {
    const average = durations.reduce((sum, item) => sum + item, 0) / durations.length;
    expect(Math.floor(4 / average)).toBe(0);
    expect(Math.floor(13.9 / average)).toBe(2);
    expect(episodeIndexAt(durations, 4)).toBe(1);
    expect(episodeStartS(durations, 1)).toBe(4);
    expect(episodeIndexAt(durations, 13.9)).toBe(1);
    expect(episodeIndexAt(durations, 14)).toBe(2);
    expect(episodeStartS(durations, 2)).toBe(14);
  });
});
