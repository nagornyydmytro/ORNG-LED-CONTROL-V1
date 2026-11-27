import { onBeforeUnmount, ref } from "vue";
import { fetchPreviewClip } from "../api/client";
import { takeScenePort } from "../runtime/scenePort";
import type { SimulatorView } from "../vite-env";

export type PreviewClipLoadOptions = {
  /** Catalog browsing loops the window. Desk follow must not. Default true. */
  loop?: boolean;
  /** Drive frames from wall clock after load. Desk prefers follow() instead. Default true. */
  freeRun?: boolean;
};

/**
 * Plays a backend-rendered preview clip locally.
 *
 * The clip is produced by the real show renderer on a throwaway engine, so it
 * is a faithful preview — and it never touches the transport, never arms
 * Art-Net and never changes the live output frame.
 */
export function usePreviewClip() {
  const presetId = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const frameCount = ref(0);
  const paletteSample = ref(false);
  /** Position inside the clip, in show seconds — drives the preview clock. */
  const timeS = ref(0);
  const startS = ref(0);

  let frames: SimulatorView[] = [];
  let index = 0;
  let timer: number | null = null;
  let clipFps = 15;
  let clipSeconds = 0;
  let loopClip = true;

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function view(): SimulatorView | null {
    return frames.length ? frames[index % frames.length] : null;
  }

  function frameAt(showTimeS: number): number | null {
    if (!frames.length || clipFps <= 0 || clipSeconds <= 0) return null;
    const elapsed = showTimeS - startS.value;
    if (elapsed < -0.05 || elapsed >= clipSeconds) return null;
    return Math.min(frames.length - 1, Math.max(0, Math.floor(elapsed * clipFps)));
  }

  function armFreeRun(fromShowS: number) {
    stop();
    if (!frames.length || clipFps <= 0) return;
    const wall0 = performance.now();
    const show0 = fromShowS;
    timer = window.setInterval(() => {
      const advanced = (performance.now() - wall0) / 1000;
      const show = show0 + advanced;
      const elapsed = show - startS.value;
      if (elapsed >= clipSeconds) {
        if (loopClip) {
          index = 0;
          timeS.value = startS.value;
          armFreeRun(startS.value);
          return;
        }
        index = frames.length - 1;
        timeS.value = startS.value + clipSeconds;
        stop();
        return;
      }
      index = Math.min(frames.length - 1, Math.max(0, Math.floor(elapsed * clipFps)));
      timeS.value = show;
    }, 1000 / clipFps);
  }

  /**
   * Snap the loaded window to a live show clock. Returns false when the time
   * is outside the buffered window so the caller can reload.
   */
  function follow(showTimeS: number, freeRun = true): boolean {
    const at = frameAt(showTimeS);
    if (at == null) return false;
    const drift = Math.abs(showTimeS - timeS.value);
    index = at;
    timeS.value = showTimeS;
    if (!freeRun) {
      stop();
      return true;
    }
    // Re-anchor only on a jump so dense heartbeats do not stutter the clock.
    if (timer == null || drift > 0.2) armFreeRun(showTimeS);
    return true;
  }

  async function load(
    id: string,
    seconds = 12,
    fps = 15,
    fromS = 0,
    opts: PreviewClipLoadOptions = {},
  ) {
    stop();
    presetId.value = id;
    loading.value = true;
    error.value = null;
    startS.value = fromS;
    loopClip = opts.loop !== false;
    try {
      const port = takeScenePort();
      const clip = port?.previewClip
        ? await port.previewClip(id, seconds, fps, fromS)
        : await fetchPreviewClip(id, seconds, fps, fromS);
      paletteSample.value = clip.playback === "palette";
      frames = clip.frames;
      frameCount.value = frames.length;
      clipFps = Math.max(1, Number(clip.fps) || fps || 15);
      clipSeconds = Math.max(0, Number(clip.seconds) || seconds || 0);
      if (!clipSeconds && frames.length) clipSeconds = frames.length / clipFps;
      index = 0;
      timeS.value = fromS;
      if (opts.freeRun !== false) armFreeRun(fromS);
    } catch (err) {
      frames = [];
      frameCount.value = 0;
      clipSeconds = 0;
      error.value = err instanceof Error ? err.message : "Не вдалося завантажити перегляд";
    } finally {
      loading.value = false;
    }
  }

  function clear() {
    stop();
    frames = [];
    frameCount.value = 0;
    clipSeconds = 0;
    presetId.value = null;
  }

  onBeforeUnmount(stop);

  return {
    presetId,
    loading,
    error,
    frameCount,
    paletteSample,
    timeS,
    startS,
    view,
    load,
    follow,
    clear,
  };
}
