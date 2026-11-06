import { describe, expect, it } from "vitest";
import { appUpdateCommandId, planAppDelivery } from "../src/app/nudge";

const base = {
  installationId: "inst-1",
  desiredVersion: "0.2.0",
  desiredBuildId: "build-b",
  updatedAt: "t1",
  autoStage: false,
  runningBuildId: "build-a",
  stagedBuildId: "build-a",
  stagedVersion: "0.1.0",
  armed: false,
  commandStatus: "applied",
};

describe("app delivery plan", () => {
  it("does not treat a successful check ACK or a disarmed room as apply", () => {
    const plan = planAppDelivery(base);
    expect(plan.action).not.toBe("apply");
    expect(plan.action).toBe("check");
    if (plan.action === "check") {
      expect(plan.commandId.startsWith("desired-app-check:")).toBe(true);
      expect(plan.buildId).toBe("build-b");
    }
  });

  it("uses a different id for stage than for check", () => {
    expect(appUpdateCommandId("check", "inst-1", "0.2.0", "build-b", "t1")).not.toBe(
      appUpdateCommandId("stage", "inst-1", "0.2.0", "build-b", "t1"),
    );
  });

  it("stage-only stops once the desired build is actually staged", () => {
    const plan = planAppDelivery({
      ...base,
      autoStage: true,
      stagedBuildId: "build-b",
      stagedVersion: "0.2.0",
      armed: false,
      commandStatus: "applied",
    });
    expect(plan).toEqual({ action: "none" });
  });

  it("a cleared or replaced assignment does not revive a command", () => {
    expect(
      planAppDelivery({ ...base, desiredVersion: null, desiredBuildId: null }).action,
    ).toBe("none");
    const replaced = planAppDelivery({
      ...base,
      desiredVersion: "0.3.0",
      desiredBuildId: "build-c",
      commandStatus: "applied",
    });
    expect(replaced.action).toBe("check");
    if (replaced.action === "check") expect(replaced.buildId).toBe("build-c");
  });
});
