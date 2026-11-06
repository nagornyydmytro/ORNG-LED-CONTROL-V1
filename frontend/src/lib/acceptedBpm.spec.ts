import { describe, expect, it } from "vitest";
import { acceptedBpmNumber, acceptedBpmValue, bpmDeskReason } from "./acceptedBpm";
import type { BpmSyncState } from "../vite-env";

function row(partial: Partial<BpmSyncState>): BpmSyncState {
  return {
    enabled: true,
    scope: "preset",
    mode: "smooth",
    bpm: null,
    locked: false,
    device: "Mock USB",
    capture: "listening",
    sync: "searching",
    ...partial,
  };
}

describe("accepted desk BPM", () => {
  it("shows a rounded confirmed tempo in Preset and Auto", () => {
    const preset = row({ bpm: 124.4, locked: true, sync: "locked", scope: "preset" });
    const auto = row({
      bpm: 124.4,
      locked: true,
      sync: "locked",
      scope: "auto",
      section: "groove",
    });
    expect(acceptedBpmValue(preset)).toBe("124 BPM");
    expect(acceptedBpmValue(auto)).toBe("124 BPM");
    expect(bpmDeskReason(preset)).toBe("");
    expect(bpmDeskReason(auto)).toBe("Рівний ритм");
  });

  it("does not treat hold, raw leftover, or unlocked estimate as current tempo", () => {
    expect(acceptedBpmNumber(row({ bpm: 124, locked: false, sync: "hold" }))).toBeNull();
    expect(acceptedBpmValue(row({ bpm: 124, locked: false, sync: "hold" }))).toBe("—");
    expect(bpmDeskReason(row({ bpm: 124, locked: false, sync: "hold" }))).toBe("Утримання");
    expect(acceptedBpmNumber(row({ bpm: 179.7, locked: false, sync: "searching" }))).toBeNull();
    expect(bpmDeskReason(row({ bpm: null, locked: false, sync: "searching" }))).toBe(
      "Шукаємо ритм",
    );
  });
});
