/** Operator-facing update phases. Check, download, stage, restart, and applied stay distinct. */

export type UpdateBadge = "available" | "downloaded" | "waiting" | "applied" | "error";

export type UpdateBadges = Record<UpdateBadge, boolean>;

const WAITING_STATES = new Set([
  "waiting_for_safe_state",
  "applying",
  "restarting",
  "health_check",
  "rolling_back",
  "waiting",
]);

const BUSY_STATES = new Set([
  "checking",
  "downloading",
  "verifying",
  "applying",
  "restarting",
  "health_check",
  "rolling_back",
]);

export function updateBadges(input: {
  updateState?: string | null;
  updatePhase?: string | null;
  lastError?: string | null;
  runningBuild?: string | null;
  desiredBuild?: string | null;
  stagedBuild?: string | null;
}): UpdateBadges {
  const state = input.updateState ?? "";
  const phase = input.updatePhase ?? "";
  const error = Boolean(input.lastError) || state === "failed" || phase === "error";
  const stagedIsDesired =
    Boolean(input.stagedBuild) &&
    (!input.desiredBuild || input.stagedBuild === input.desiredBuild);
  const downloaded =
    !error &&
    stagedIsDesired &&
    (state === "staged" || phase === "downloaded" || input.stagedBuild === input.desiredBuild);
  const waiting = !error && (WAITING_STATES.has(state) || phase === "waiting" || phase === "restarting");
  const applied =
    !error &&
    !waiting &&
    (phase === "applied" ||
      state === "healthy" ||
      state === "rolled_back" ||
      Boolean(input.runningBuild && input.desiredBuild && input.runningBuild === input.desiredBuild));
  const available =
    !error &&
    !applied &&
    Boolean(input.desiredBuild) &&
    input.desiredBuild !== input.runningBuild &&
    !downloaded;
  return { available, downloaded, waiting, applied, error };
}

export function appUpdateButtonState(input: {
  hasInstallation: boolean;
  installMode?: string | null;
  selfUpdateAllowed?: boolean | null;
  devicePlatform?: string | null;
  deviceArch?: string | null;
  releasePlatform?: string | null;
  releaseArch?: string | null;
  updateState?: string | null;
  stagedBuild?: string | null;
  targetBuild?: string | null;
  busy?: boolean;
}): {
  check: boolean;
  stage: boolean;
  apply: boolean;
  cancel: boolean;
  rollback: boolean;
  reason: string | null;
} {
  const mode = (input.installMode ?? "").toLowerCase();
  const managed =
    input.selfUpdateAllowed !== false && mode !== "developer" && mode !== "dev";
  const platformOk =
    !input.releasePlatform ||
    !input.devicePlatform ||
    (input.releasePlatform === input.devicePlatform &&
      (!input.releaseArch || !input.deviceArch || input.releaseArch === input.deviceArch));
  const state = input.updateState ?? "";
  const busyOp = BUSY_STATES.has(state);
  const stagedForTarget = Boolean(
    input.stagedBuild && input.targetBuild && input.stagedBuild === input.targetBuild,
  );
  if (!input.hasInstallation) {
    return {
      check: false,
      stage: false,
      apply: false,
      cancel: false,
      rollback: false,
      reason: "Оберіть установку",
    };
  }
  if (!managed) {
    return {
      check: !input.busy,
      stage: false,
      apply: false,
      cancel: false,
      rollback: false,
      reason: "Цей режим встановлення не оновлює сам себе",
    };
  }
  if (!platformOk) {
    return {
      check: !input.busy,
      stage: false,
      apply: false,
      cancel: !input.busy && (state === "staged" || state === "downloading" || state === "verifying"),
      rollback: false,
      reason: "Платформа або архітектура релізу не збігається з установкою",
    };
  }
  return {
    check: !input.busy,
    stage: !input.busy && !busyOp,
    apply:
      !input.busy &&
      !busyOp &&
      stagedForTarget &&
      (state === "staged" || state === "waiting_for_safe_state"),
    cancel:
      !input.busy &&
      (state === "downloading" ||
        state === "verifying" ||
        state === "staged" ||
        state === "waiting_for_safe_state"),
    rollback: !input.busy && !busyOp,
    reason: null,
  };
}

export function applyConfirmText(target: {
  installationId: string;
  version: string;
  buildId: string;
}): string {
  return (
    `Перезапуск ${target.installationId}: ${target.version || "—"} / ${target.buildId || "—"}. ` +
    "Blackout і Disarm перед заміною цього релізу. Продовжити?"
  );
}

export function selectionUnchanged(
  capturedId: string,
  currentId: string | null | undefined,
): boolean {
  return Boolean(capturedId) && currentId === capturedId;
}
