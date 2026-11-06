/**
 * Heartbeat delivery for an application assignment.
 *
 * check, stage, and apply are different commands. A successful check ACK is
 * not a staged package, and a disarmed room is not permission to apply.
 * This planner never returns apply: the operator asks for that explicitly.
 */

export type AppDeliveryPlan =
  | { action: "none" }
  | {
      action: "check" | "stage";
      commandId: string;
      version: string;
      buildId: string;
    };

export function appUpdateCommandId(
  kind: "check" | "stage",
  installationId: string,
  version: string,
  buildId: string,
  updatedAt: string,
): string {
  return `desired-app-${kind}:${installationId}:${version}:${buildId}:${updatedAt}`;
}

export function planAppDelivery(input: {
  installationId: string;
  desiredVersion?: string | null;
  desiredBuildId?: string | null;
  updatedAt: string;
  autoStage: boolean;
  runningBuildId?: string | null;
  stagedBuildId?: string | null;
  stagedVersion?: string | null;
  /** Present so callers can pass heartbeat posture. It does not authorize apply. */
  armed?: boolean | null;
  commandStatus?: string | null;
}): AppDeliveryPlan {
  const version = input.desiredVersion ?? "";
  const buildId = input.desiredBuildId ?? "";
  if (!version || !buildId) return { action: "none" };
  if (input.runningBuildId && input.runningBuildId === buildId) return { action: "none" };
  const stagedHere =
    input.stagedBuildId === buildId && input.stagedVersion === version;
  if (stagedHere) return { action: "none" };
  if (input.commandStatus === "accepted") return { action: "none" };
  const kind = input.autoStage ? "stage" : "check";
  return {
    action: kind,
    commandId: appUpdateCommandId(kind, input.installationId, version, buildId, input.updatedAt),
    version,
    buildId,
  };
}
