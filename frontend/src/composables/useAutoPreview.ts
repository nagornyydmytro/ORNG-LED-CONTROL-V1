import { onBeforeUnmount, ref } from "vue";
import { fetchAutoPreview, type AutoPreviewQuery } from "../api/auto";
import { clampPreviewBpm, phaseFrameCount, previewDurationS } from "../lib/autoPreview";
import type { AutoPreviewClip, SimulatorView } from "../vite-env";

const CACHE_LIMIT = 4;

export function previewCacheKey(
  query: Pick<AutoPreviewQuery, "look" | "cycleBeats" | "palette" | "intensity">,
  sceneKey: string,
): string {
  return [sceneKey, query.look, query.cycleBeats, query.palette, query.intensity].join("|");
}

export function useAutoPreview() {
  const loading = ref(false);
  const error = ref<string | null>(null);
  const compatible = ref(true);
  const reason = ref<string | null>(null);
  const look = ref<string | null>(null);
  const cycleBeats = ref(4);
  const seconds = ref(2);
  const beat = ref(0);
  const playing = ref(false);
  const note = ref<string | null>(null);
  const sceneId = ref<string | null>(null);
  const canonical = ref(false);

  let frames: SimulatorView[] = [];
  let token = 0;
  let raf = 0;
  let startedAt = 0;
  let elapsedS = 0;
  let bpm = 120;
  const cache = new Map<string, AutoPreviewClip>();

  function view(): SimulatorView | null {
    if (!frames.length) return null;
    const span = Math.max(0.25, cycleBeats.value);
    const phase = ((beat.value / span) % 1 + 1) % 1;
    const index = Math.min(frames.length - 1, Math.floor(phase * frames.length + 1e-9) % frames.length);
    return frames[index] ?? null;
  }

  function stopClock() {
    if (raf) {
      window.cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  function tick() {
    if (!playing.value || !frames.length) return;
    const duration = Math.max(0.05, seconds.value);
    const now = performance.now();
    const passed = elapsedS + (now - startedAt) / 1000;
    const wrapped = ((passed % duration) + duration) % duration;
    beat.value = (wrapped / duration) * cycleBeats.value;
    raf = window.requestAnimationFrame(tick);
  }

  function play() {
    if (!frames.length || !compatible.value || !canonical.value) return;
    if (playing.value) return;
    playing.value = true;
    startedAt = performance.now();
    stopClock();
    raf = window.requestAnimationFrame(tick);
  }

  function pause() {
    if (!playing.value) return;
    elapsedS += (performance.now() - startedAt) / 1000;
    playing.value = false;
    stopClock();
  }

  function restart() {
    elapsedS = 0;
    beat.value = 0;
    startedAt = performance.now();
    if (frames.length && compatible.value && canonical.value) {
      playing.value = true;
      stopClock();
      raf = window.requestAnimationFrame(tick);
    }
  }

  function syncDuration() {
    const duration = previewDurationS(cycleBeats.value, bpm);
    const phase = cycleBeats.value > 0 ? beat.value / cycleBeats.value : 0;
    seconds.value = duration;
    elapsedS = phase * duration;
    startedAt = performance.now();
  }

  function setBpm(next: number) {
    bpm = clampPreviewBpm(next);
    syncDuration();
  }

  function remember(key: string, clip: AutoPreviewClip) {
    cache.set(key, clip);
    if (cache.size <= CACHE_LIMIT) return;
    const first = cache.keys().next().value;
    if (typeof first === "string") cache.delete(first);
  }

  function apply(clip: AutoPreviewClip, query: AutoPreviewQuery) {
    compatible.value = clip.compatible !== false;
    reason.value = clip.reason ?? null;
    note.value = clip.note ?? null;
    cycleBeats.value = Number(clip.cycle_beats) || query.cycleBeats;
    seconds.value = previewDurationS(cycleBeats.value, bpm);
    const hasFrames = Array.isArray(clip.frames) && clip.frames.length > 0;
    canonical.value = clip.canonical === true && hasFrames;
    frames = canonical.value ? clip.frames : [];
    if (!canonical.value && hasFrames === false && compatible.value && !reason.value) {
      reason.value = "Кадрів немає.";
    }
    elapsedS = 0;
    beat.value = 0;
    startedAt = performance.now();
    if (frames.length && canonical.value) play();
    else {
      playing.value = false;
      stopClock();
    }
  }

  async function load(query: AutoPreviewQuery, sceneKey: string, nextSceneId: string | null) {
    const mine = ++token;
    const key = previewCacheKey(query, sceneKey);
    loading.value = true;
    error.value = null;
    look.value = query.look;
    cycleBeats.value = query.cycleBeats;
    sceneId.value = nextSceneId;
    bpm = clampPreviewBpm(query.bpm);
    seconds.value = previewDurationS(query.cycleBeats, bpm);
    const cached = cache.get(key);
    if (cached) {
      if (mine !== token) return;
      apply(cached, query);
      loading.value = false;
      return;
    }
    try {
      const clip = await fetchAutoPreview({
        ...query,
        frames: query.frames ?? phaseFrameCount(query.cycleBeats),
      });
      if (mine !== token) return;
      remember(key, clip);
      apply(clip, query);
    } catch (err) {
      if (mine !== token) return;
      frames = [];
      canonical.value = false;
      compatible.value = false;
      playing.value = false;
      stopClock();
      error.value = err instanceof Error ? err.message : "Не вдалося побудувати прев'ю";
    } finally {
      if (mine === token) loading.value = false;
    }
  }

  function invalidate() {
    token += 1;
    stopClock();
    playing.value = false;
    frames = [];
    canonical.value = false;
    beat.value = 0;
    elapsedS = 0;
    loading.value = false;
    error.value = null;
  }

  function clear() {
    invalidate();
    cache.clear();
  }

  onBeforeUnmount(clear);

  return {
    loading,
    error,
    compatible,
    reason,
    look,
    cycleBeats,
    seconds,
    beat,
    playing,
    note,
    sceneId,
    canonical,
    view,
    load,
    play,
    pause,
    restart,
    setBpm,
    invalidate,
    clear,
  };
}
