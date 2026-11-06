import { defineComponent } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { previewCacheKey, useAutoPreview } from "./useAutoPreview";
import { fetchAutoPreview } from "../api/auto";
import type { AutoPreviewClip, AutoPreviewQuery, SimulatorView } from "../vite-env";

vi.mock("../api/auto", () => ({
  fetchAutoPreview: vi.fn(),
  fetchAutoCatalog: vi.fn(),
}));

function frame(segments: number[], r = 1): SimulatorView {
  return {
    pars: [],
    bars: [
      {
        id: "bar_1",
        label: "bar_1",
        kind: "bar",
        side: "left",
        ring: "",
        dimmer: 1,
        r,
        g: 0.2,
        b: 0,
        segments,
        segment_r: segments.map((value) => value * r),
        segment_g: segments.map(() => 0),
        segment_b: segments.map(() => 0),
      },
    ],
    beams: [],
    faces: [],
    nonzero_channels: 4,
    blackout_visual: false,
  };
}

function clip(partial: Partial<AutoPreviewClip> & { look: string }): AutoPreviewClip {
  return {
    cycle_beats: 4,
    palette: "orange",
    intensity: "full",
    bpm: 120,
    seconds: 2,
    transport: "none",
    playback: "renderer",
    safe_mock_preview: true,
    compatible: true,
    canonical: true,
    frames: [frame([1, 0, 0, 0, 0, 0, 0, 0]), frame([0, 0, 0, 0, 0, 0, 0, 1])],
    ...partial,
  };
}

function harness() {
  let api!: ReturnType<typeof useAutoPreview>;
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useAutoPreview();
        return () => null;
      },
    }),
  );
  return { api, wrapper };
}

const queryA: AutoPreviewQuery = {
  look: "vertical_wave",
  cycleBeats: 4,
  palette: "orange",
  intensity: "full",
  bpm: 120,
};
const queryB: AutoPreviewQuery = { ...queryA, look: "hit_bars" };

describe("useAutoPreview", () => {
  beforeEach(() => {
    vi.mocked(fetchAutoPreview).mockReset();
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 1),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  it("plays cached composition A at the live BPM after visiting B", async () => {
    vi.mocked(fetchAutoPreview).mockImplementation(async (query: AutoPreviewQuery) =>
      clip({ look: query.look, seconds: 2, bpm: 120 }),
    );
    const { api, wrapper } = harness();
    await api.load(queryA, "loc:scene:rev1", "scene");
    expect(api.seconds.value).toBe(2);
    api.setBpm(60);
    expect(api.seconds.value).toBe(4);
    await api.load({ ...queryB, bpm: 60 }, "loc:scene:rev1", "scene");
    await api.load({ ...queryA, bpm: 60 }, "loc:scene:rev1", "scene");
    expect(fetchAutoPreview).toHaveBeenCalledTimes(2);
    expect(api.look.value).toBe("vertical_wave");
    expect(api.seconds.value).toBe(4);
    wrapper.unmount();
  });

  it("applies the current BPM when a late response arrives", async () => {
    let release: (value: AutoPreviewClip) => void = () => undefined;
    vi.mocked(fetchAutoPreview).mockImplementationOnce(
      () =>
        new Promise<AutoPreviewClip>((resolve) => {
          release = resolve;
        }),
    );
    const { api, wrapper } = harness();
    const pending = api.load(queryA, "loc:scene:rev1", "scene");
    api.setBpm(60);
    release(clip({ look: "vertical_wave", seconds: 2, bpm: 120 }));
    await pending;
    expect(api.seconds.value).toBe(4);
    wrapper.unmount();
  });

  it("clears the old preview and ignores pending work when invalidated", async () => {
    let release: (value: AutoPreviewClip) => void = () => undefined;
    vi.mocked(fetchAutoPreview)
      .mockResolvedValueOnce(clip({ look: "vertical_wave" }))
      .mockImplementationOnce(
        () =>
          new Promise<AutoPreviewClip>((resolve) => {
            release = resolve;
          }),
      );
    const { api, wrapper } = harness();
    await api.load(queryA, "loc:scene:rev1", "scene");
    expect(api.view()).not.toBeNull();
    expect(api.canonical.value).toBe(true);
    const pending = api.load(queryB, "loc:scene:rev1", "scene");
    api.invalidate();
    expect(api.view()).toBeNull();
    expect(api.canonical.value).toBe(false);
    expect(api.playing.value).toBe(false);
    release(clip({ look: "hit_bars" }));
    await pending;
    await flushPromises();
    expect(api.view()).toBeNull();
    expect(api.look.value).toBe("hit_bars");
    wrapper.unmount();
  });

  it("keys the cache by location and scene revision, not only scene_id", async () => {
    vi.mocked(fetchAutoPreview).mockResolvedValue(clip({ look: "vertical_wave" }));
    const { api, wrapper } = harness();
    await api.load(queryA, "loc-a:scene:rev1", "scene");
    await api.load(queryA, "loc-a:scene:rev1", "scene");
    expect(fetchAutoPreview).toHaveBeenCalledTimes(1);
    await api.load(queryA, "loc-a:scene:rev2", "scene");
    expect(fetchAutoPreview).toHaveBeenCalledTimes(2);
    await api.load(queryA, "loc-b:scene:rev2", "scene");
    expect(fetchAutoPreview).toHaveBeenCalledTimes(3);
    expect(previewCacheKey(queryA, "loc-a:scene:rev1")).not.toBe(
      previewCacheKey(queryA, "loc-a:scene:rev2"),
    );
    wrapper.unmount();
  });

  it("asks for phase samples of the cycle instead of 24 frames", async () => {
    vi.mocked(fetchAutoPreview).mockResolvedValue(
      clip({ look: "vertical_wave", cycle_beats: 8, seconds: 4 }),
    );
    const { api, wrapper } = harness();
    await api.load({ ...queryA, cycleBeats: 8 }, "loc:scene:rev1", "scene");
    expect(fetchAutoPreview).toHaveBeenCalledWith(
      expect.objectContaining({ look: "vertical_wave", cycleBeats: 8, frames: 72 }),
    );
    wrapper.unmount();
  });

  it("picks the nearest canonical frame and does not invent RGB between DMX segments", async () => {
    const first = frame([1, 0, 0, 0, 0, 0, 0, 0], 1);
    const last = frame([0, 0, 0, 0, 0, 0, 0, 1], 0.2);
    vi.mocked(fetchAutoPreview).mockResolvedValue(
      clip({ look: "vertical_wave", frames: [first, last] }),
    );
    const { api, wrapper } = harness();
    await api.load(queryA, "loc:scene:rev1", "scene");
    api.pause();
    api.beat.value = 0;
    expect(api.view()?.bars[0]?.segments).toEqual(first.bars[0]?.segments);
    expect(api.view()?.bars[0]?.r).toBe(1);
    api.beat.value = 1;
    expect(api.view()?.bars[0]?.segments).toEqual(first.bars[0]?.segments);
    api.beat.value = 2;
    expect(api.view()?.bars[0]?.segments).toEqual(last.bars[0]?.segments);
    expect(api.view()?.bars[0]?.r).toBe(0.2);
    expect(api.view()?.bars[0]?.r).not.toBe(0.6);
    wrapper.unmount();
  });

  it("does not treat an empty clip as a successful renderer preview", async () => {
    vi.mocked(fetchAutoPreview).mockResolvedValue(
      clip({ look: "vertical_wave", canonical: true, frames: [] }),
    );
    const { api, wrapper } = harness();
    await api.load(queryA, "loc:scene:rev1", "scene");
    expect(api.canonical.value).toBe(false);
    expect(api.view()).toBeNull();
    expect(api.playing.value).toBe(false);
    expect(api.reason.value ?? "").toContain("Кадрів немає.");
    wrapper.unmount();
  });
});
