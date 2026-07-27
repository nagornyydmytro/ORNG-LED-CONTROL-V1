import { createCommandId } from "./client";
import type { AppState } from "../vite-env";

export interface InputDispatchResponse {
  ok: boolean;
  accepted: boolean;
  reason?: string | null;
  idempotent_replay: boolean;
  state: AppState;
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function postInputButton(
  buttonId: number,
  edge: "press" | "release" | "pulse" = "pulse",
  source: "ui" | "keyboard" | "mock" = "ui",
): Promise<InputDispatchResponse> {
  return parseJson(
    await fetch("/api/input/button", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        button_id: buttonId,
        edge,
        source,
        client_command_id: createCommandId(`input-btn-${buttonId}`),
      }),
    }),
  );
}

export async function postInputKeyboard(payload: {
  code: string;
  type: "keydown" | "keyup";
  repeat?: boolean;
}): Promise<InputDispatchResponse> {
  return parseJson(
    await fetch("/api/input/keyboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        client_command_id: createCommandId("input-key"),
      }),
    }),
  );
}

/** Canon §9 pad: presets 1–10, face, white hit, strobe, blackout, brightness ± */
export const PRESET_BUTTONS: Record<string, number> = {
  P01: 1,
  P02: 2,
  P03: 3,
  P04: 4,
  P05: 5,
  P06: 6,
  P07: 7,
  P08: 8,
  P09: 9,
  P10: 10,
};

export const BUTTON_FACE = 11;
export const BUTTON_WHITE_HIT = 12;
export const BUTTON_STROBE = 13;
export const BUTTON_BLACKOUT = 14;
export const BUTTON_BRIGHTNESS_DOWN = 15;
export const BUTTON_BRIGHTNESS_UP = 16;
