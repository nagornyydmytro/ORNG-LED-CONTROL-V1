import { describe, expect, it, vi } from "vitest";
import { PRESET_BUTTONS, BUTTON_STROBE, BUTTON_FACE } from "../../api/input";

describe("input pad contract constants", () => {
  it("maps P01–P10 to buttons 1–10", () => {
    for (let i = 1; i <= 10; i += 1) {
      const id = `P${String(i).padStart(2, "0")}`;
      expect(PRESET_BUTTONS[id]).toBe(i);
    }
  });

  it("reserves strobe and face button ids from canon", () => {
    expect(BUTTON_STROBE).toBe(13);
    expect(BUTTON_FACE).toBe(11);
  });
});

describe("keyboard adapter payload shape", () => {
  it("builds keyboard posts without throwing", async () => {
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
    await postInputKeyboard({ code: "KeyS", type: "keydown", repeat: false });
    expect(fetchMock).toHaveBeenCalled();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body)).code).toBe("KeyS");
  });
});
