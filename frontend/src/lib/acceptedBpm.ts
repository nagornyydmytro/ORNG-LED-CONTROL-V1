import { BPM_SECTIONS } from "./bpmPalettes";
import type { BpmSyncState } from "../vite-env";

/** Confirmed follower tempo only. Not a raw estimate, preview BPM, or a hold leftover. */
export function acceptedBpmNumber(bpm: BpmSyncState | null | undefined): number | null {
  if (!bpm?.enabled) return null;
  if (bpm.locked !== true || bpm.sync !== "locked") return null;
  if (typeof bpm.bpm !== "number" || !Number.isFinite(bpm.bpm)) return null;
  return bpm.bpm;
}

export function acceptedBpmValue(bpm: BpmSyncState | null | undefined): string {
  const value = acceptedBpmNumber(bpm);
  return value === null ? "—" : `${Math.round(value)} BPM`;
}

export function bpmDeskReason(bpm: BpmSyncState | null | undefined): string {
  if (!bpm?.enabled) return "";
  if (acceptedBpmNumber(bpm) !== null) {
    if (bpm.scope === "auto") return BPM_SECTIONS[bpm.section ?? ""] || "";
    return "";
  }
  if (bpm.sync === "no_input") return "Підключіть звук";
  if (bpm.sync === "waiting") return "Чекаємо музику";
  if (bpm.sync === "searching") return "Шукаємо ритм";
  if (bpm.sync === "hold") return "Утримання";
  if (bpm.sync === "fallback") return "Без сітки";
  const device = typeof bpm.device === "string" && bpm.device.length > 0;
  const unplugged =
    bpm.capture === "no_device" || (bpm.capture === "unavailable" && !device);
  if (unplugged) return "Підключіть звук";
  if (bpm.signal === true) return "Шукаємо ритм";
  return "Чекаємо музику";
}
