import { createCommandId, postCommand } from "../../api/client";
import { postInputAction } from "../../api/input";
import { activateScene } from "../../api/scenes";
import type { ControlTransport } from "../transport";

/** Existing local FastAPI runtime — default mode. */
export function createLocalRuntimeTransport(): ControlTransport {
  return {
    kind: "local",
    async blackout(enabled: boolean) {
      await postCommand("blackout", {
        enabled,
        client_command_id: createCommandId("blackout"),
      });
    },
    async arm() {
      await postCommand("arm", { client_command_id: createCommandId("arm") });
    },
    async disarm() {
      await postCommand("disarm", { client_command_id: createCommandId("disarm") });
    },
    async selectPreset(presetId: string) {
      await postCommand("select_preset", {
        preset_id: presetId,
        client_command_id: createCommandId("select-preset"),
      });
    },
    async selectPad(slot: number) {
      await postInputAction({
        action: "select_pad_slot",
        pad_slot: slot,
      });
    },
    async strobe(edge) {
      await postInputAction({
        action: edge === "press" ? "strobe_press" : "strobe_release",
      });
    },
    async sweep(edge, mode = "horizontal") {
      const action =
        mode === "vertical"
          ? edge === "press"
            ? "vertical_sweep_press"
            : "vertical_sweep_release"
          : edge === "press"
            ? "sweep_press"
            : "sweep_release";
      await postInputAction({ action });
    },
    async faceToggle() {
      await postInputAction({ action: "face_toggle" });
    },
    async episodeNudge(delta: number) {
      await postInputAction({
        action: delta >= 0 ? "episode_next" : "episode_prev",
      });
    },
    async speed() {
      // Local ControlView uses absolute preview/live-fx speed APIs.
      // Cloud admin uses the nudge-based `speed` command instead.
    },
    async activateScene(sceneId: string) {
      await activateScene(sceneId);
    },
  };
}
