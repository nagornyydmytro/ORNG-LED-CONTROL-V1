import { createCommandId } from "./client";
import { getWebSessionSourceId } from "../lib/sessionSourceId";
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
  sourceId: string = getWebSessionSourceId(),
): Promise<InputDispatchResponse> {
  return parseJson(
    await fetch("/api/input/button", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        button_id: buttonId,
        edge,
        source,
        source_id: sourceId,
        client_command_id: createCommandId(`input-btn-${buttonId}`),
      }),
    }),
  );
}

export async function postInputKeyboard(payload: {
  code: string;
  type: "keydown" | "keyup";
  repeat?: boolean;
  source_id?: string;
  profile_id?: "qwerty" | "ch57x";
}): Promise<InputDispatchResponse> {
  const { source_id = getWebSessionSourceId(), ...rest } = payload;
  return parseJson(
    await fetch("/api/input/keyboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...rest,
        source_id,
        client_command_id: createCommandId("input-key"),
      }),
    }),
  );
}

export async function postInputAction(payload: {
  action: string;
  edge?: "press" | "release" | "pulse";
  source?: "ui" | "keyboard" | "mock";
  pad_slot?: number;
  preset_id?: string;
  source_id?: string;
}): Promise<InputDispatchResponse> {
  const {
    source_id = getWebSessionSourceId(),
    source = "ui",
    ...rest
  } = payload;
  return parseJson(
    await fetch("/api/input/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...rest,
        source,
        source_id,
        client_command_id: createCommandId(`input-action-${rest.action}`),
      }),
    }),
  );
}

/** Physical pad buttons 1–9 map to home pad slots (configured ids), not fixed P01–P09. */
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
};

export const BUTTON_FACE = 11;
export const BUTTON_WHITE_HIT = 12;
export const BUTTON_STROBE = 13;
export const BUTTON_BLACKOUT = 14;
export const BUTTON_BRIGHTNESS_DOWN = 15;
export const BUTTON_BRIGHTNESS_UP = 16;
