import { describe, expect, it } from "vitest";
import { channelDraftFrom, commitProfile } from "./channelCommit";

const channel = {
  local: 1,
  role: "whole_color",
  label: "Color",
  notes: "keep me",
  curve: "linear",
  segment_index: null,
  fixed_value: null,
  palette: { off: 0, red: 16, lime: 9 },
  control_values: { open: 10, custom: 3 },
};

const profile = {
  id: "par_7ch_provisional",
  notes: "profile note",
  hardware_verified: false,
  segment_count: 0,
  channels: [channel],
};

describe("commitProfile", () => {
  it("keeps notes and extra palette values when nothing was edited", () => {
    const draft = channelDraftFrom(channel, 1);
    const result = commitProfile(profile, [draft]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.profile.notes).toBe("profile note");
    expect(result.profile.hardware_verified).toBe(false);
    expect(result.profile.channels).toEqual([channel]);
  });

  it("keeps compatible values when the role changes and does not invent 0", () => {
    const draft = channelDraftFrom(channel, 1);
    draft.role = "dimmer";
    const result = commitProfile(profile, [draft]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.profile.channels).toEqual([{ ...channel, role: "dimmer" }]);
  });

  it("reports a bad fixed value and does not rewrite it to 0", () => {
    const draft = channelDraftFrom({ ...channel, role: "fixed", fixed_value: null }, 1);
    draft.role = "fixed";
    draft.fixedRaw = "nope";
    const result = commitProfile(profile, [draft]);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.issues[0]?.message).toContain("цілим 0..255");
    expect(draft.fixedRaw).toBe("nope");
  });
});
