/** Start of each episode, in show seconds. Unequal lengths stay on their own boundaries. */
export function episodeStarts(durations: number[]): number[] {
  const starts: number[] = [];
  let cursor = 0;
  for (const raw of durations) {
    starts.push(cursor);
    const duration = Number(raw);
    cursor += Number.isFinite(duration) ? Math.max(0, duration) : 0;
  }
  return starts;
}

export function episodeStartS(durations: number[], episodeIndex: number): number {
  const starts = episodeStarts(durations);
  if (episodeIndex < 0 || episodeIndex >= starts.length) return 0;
  return starts[episodeIndex] ?? 0;
}

export function episodeIndexAt(durations: number[], timeS: number): number {
  const starts = episodeStarts(durations);
  if (!starts.length) return 0;
  const time = Math.max(0, timeS);
  for (let index = 0; index < starts.length; index += 1) {
    const end = index + 1 < starts.length ? starts[index + 1]! : Number.POSITIVE_INFINITY;
    if (time < end) return index;
  }
  return starts.length - 1;
}
