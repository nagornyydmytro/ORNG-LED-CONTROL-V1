export const PREVIEW_SAMPLES_PER_BEAT = 12;
export const PREVIEW_MIN_FRAMES = 16;
export const PREVIEW_MAX_FRAMES = 72;

export function clampPreviewBpm(bpm: number): number {
  return Math.max(40, Math.min(220, Number(bpm) || 120));
}

export function previewDurationS(cycleBeats: number, bpm: number): number {
  return Math.max(0.05, Number(cycleBeats) * 60 / clampPreviewBpm(bpm));
}

export function phaseFrameCount(cycleBeats: number): number {
  return Math.max(
    PREVIEW_MIN_FRAMES,
    Math.min(PREVIEW_MAX_FRAMES, Math.round(Number(cycleBeats) * PREVIEW_SAMPLES_PER_BEAT)),
  );
}
