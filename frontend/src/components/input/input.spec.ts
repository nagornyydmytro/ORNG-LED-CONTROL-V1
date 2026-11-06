import { beforeEach, describe, expect, it, vi } from "vitest";
import { PRESET_BUTTONS, BUTTON_STROBE, BUTTON_FACE } from "../../api/input";
import {
  getWebSessionSourceId,
  resetWebSessionSourceIdForTests,
} from "../../lib/sessionSourceId";

describe("input pad contract constants", () => {
  it("maps legacy P01–P09 labels to pad slot buttons 1–9", () => {
    for (let i = 1; i <= 9; i += 1) {
      const id = `P${String(i).padStart(2, "0")}`;
      expect(PRESET_BUTTONS[id]).toBe(i);
    }
    expect(PRESET_BUTTONS.P10).toBeUndefined();
  });

  it("reserves strobe and face button ids from canon", () => {
    expect(BUTTON_STROBE).toBe(13);
    expect(BUTTON_FACE).toBe(11);
  });
});

describe("web session source_id", () => {
  beforeEach(() => {
    resetWebSessionSourceIdForTests();
    sessionStorage.clear();
  });

  it("creates a stable web:<session-id> once per tab", () => {
    const a = getWebSessionSourceId();
    const b = getWebSessionSourceId();
    expect(a).toMatch(/^web:[0-9a-f]+$/i);
    expect(b).toBe(a);
    expect(sessionStorage.getItem("orng-web-session-id")).toBe(a);
  });

  it("reuses sessionStorage across module cache resets", () => {
    const first = getWebSessionSourceId();
    resetWebSessionSourceIdForTests();
    const second = getWebSessionSourceId();
    expect(second).toBe(first);
  });
});

describe("keyboard adapter payload shape", () => {
  beforeEach(() => {
    resetWebSessionSourceIdForTests();
    sessionStorage.clear();
  });

  it("builds keyboard posts with stable source_id", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        accepted: true,
        idempotent_replay: false,
        state: { engine: { strobe_held: true }, output: { last_error: null } },
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { postInputKeyboard } = await import("../../api/input");
    await postInputKeyboard({ code: "KeyZ", type: "keydown", repeat: false });
    expect(fetchMock).toHaveBeenCalled();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(init.body)) as {
      code: string;
      source_id: string;
    };
    expect(body.code).toBe("KeyZ");
    expect(body.source_id).toMatch(/^web:/);
  });

  it("builds button posts with source_id", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        accepted: true,
        idempotent_replay: false,
        state: { engine: {}, output: { last_error: null } },
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { postInputButton } = await import("../../api/input");
    await postInputButton(13, "press", "ui");
    const body = JSON.parse(
      String((fetchMock.mock.calls[0][1] as RequestInit).body),
    ) as { button_id: number; source_id: string; edge: string };
    expect(body.button_id).toBe(13);
    expect(body.edge).toBe("press");
    expect(body.source_id).toMatch(/^web:/);
  });

  it("builds sweep action posts through /api/input/action with source_id", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        accepted: true,
        idempotent_replay: false,
        state: { engine: { sweep_active: true }, output: { last_error: null } },
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { postInputAction } = await import("../../api/input");
    await postInputAction({ action: "sweep_press", edge: "press" });
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/input/action");
    const body = JSON.parse(
      String((fetchMock.mock.calls[0][1] as RequestInit).body),
    ) as { action: string; source_id: string; edge: string };
    expect(body.action).toBe("sweep_press");
    expect(body.edge).toBe("press");
    expect(body.source_id).toMatch(/^web:/);
  });

  it("overlapping H/V sweep releases send matching actions regardless of order", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as { action: string };
      calls.push(body.action);
      return {
        ok: true,
        json: async () => ({
          ok: true,
          accepted: true,
          idempotent_replay: false,
          state: { engine: {}, output: { last_error: null } },
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { postInputAction } = await import("../../api/input");
    // Simulate the fixed ControlView contract: mode-specific releases.
    await postInputAction({ action: "sweep_press", edge: "press" });
    await postInputAction({ action: "vertical_sweep_press", edge: "press" });
    await postInputAction({ action: "sweep_release", edge: "release" });
    await postInputAction({ action: "vertical_sweep_release", edge: "release" });
    expect(calls).toEqual([
      "sweep_press",
      "vertical_sweep_press",
      "sweep_release",
      "vertical_sweep_release",
    ]);
  });
});
