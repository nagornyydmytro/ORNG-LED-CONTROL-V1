import { onBeforeUnmount, ref } from "vue";
import { fetchPreviewClip } from "../api/client";
import type { SimulatorView } from "../vite-env";

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
  /** Position inside the clip, in show seconds — drives the preview clock. */
  const timeS = ref(0);
  const startS = ref(0);

  let frames: SimulatorView[] = [];
  let index = 0;
  let timer: number | null = null;

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function view(): SimulatorView | null {
    return frames.length ? frames[index % frames.length] : null;
  }

  async function load(id: string, seconds = 12, fps = 15, fromS = 0) {
    stop();
    presetId.value = id;
    loading.value = true;
    error.value = null;
    startS.value = fromS;
    try {
      const clip = await fetchPreviewClip(id, seconds, fps, fromS);
      frames = clip.frames;
      frameCount.value = frames.length;
      index = 0;
      timeS.value = fromS;
      timer = window.setInterval(() => {
        index = (index + 1) % Math.max(1, frames.length);
        timeS.value = fromS + index / clip.fps;
      }, 1000 / clip.fps);
    } catch (err) {
      frames = [];
      frameCount.value = 0;
      error.value = err instanceof Error ? err.message : "Не вдалося завантажити перегляд";
    } finally {
      loading.value = false;
    }
  }

  function clear() {
    stop();
    frames = [];
    frameCount.value = 0;
    presetId.value = null;
  }

  onBeforeUnmount(stop);

  return { presetId, loading, error, frameCount, timeS, startS, view, load, clear };
}
