import { beforeEach, describe, expect, it } from "vitest";
import {
  beginBpmChange,
  bindBpmCommandId,
  bpmDesk,
  confirmBpmFromTelemetry,
  finishBpmChange,
  listPendingBpmCommands,
  resetBpmIntent,
  settlePendingBpmCommands,
} from "./bpmIntent";

beforeEach(() => {
  localStorage.clear();
  resetBpmIntent();
});

describe("BPM intent races", () => {
  it("keeps on-off-on when the first reply arrives last", () => {
    const first = beginBpmChange("loc:a", { enabled: true }, { enabled: false, revision: 1, session: "s", epoch: 2 });
    const second = beginBpmChange("loc:a", { enabled: false });
    const third = beginBpmChange("loc:a", { enabled: true });
    expect(bpmDesk("loc:a").enabled).toBe(true);
    expect(first.intentMs).toBeLessThan(second.intentMs);
    expect(second.intentMs).toBeLessThan(third.intentMs);
    finishBpmChange("loc:a", first.fieldRevs, true, undefined, 2, 2);
    finishBpmChange("loc:a", second.fieldRevs, true, undefined, 3, 2);
    expect(bpmDesk("loc:a").enabled).toBe(true);
    expect(first.fieldRevs.enabled).not.toBe(third.fieldRevs.enabled);
  });

  it("ignores a late picture from an older player session", () => {
    confirmBpmFromTelemetry("loc:a", {
      enabled: false,
      scope: "auto",
      palette: "blue",
      revision: 1,
      session: "new-player",
      epoch: 4,
    });
    confirmBpmFromTelemetry("loc:a", {
      enabled: true,
      scope: "preset",
      palette: "red",
      revision: 80,
      session: "old-player",
      epoch: 3,
    });
    const desk = bpmDesk("loc:a");
    expect(desk.enabled).toBe(false);
    expect(desk.choice.palette).toBe("blue");
    expect(desk.choice.scope).toBe("auto");
  });

  it("does not let an older matching picture finish the newest press", () => {
    beginBpmChange("loc:a", { enabled: true }, { enabled: false, revision: 0, session: "A", epoch: 2 });
    beginBpmChange("loc:a", { enabled: false });
    beginBpmChange("loc:a", { enabled: true });
    confirmBpmFromTelemetry("loc:a", { enabled: true, revision: 1, session: "A", epoch: 2 });
    confirmBpmFromTelemetry("loc:a", { enabled: false, revision: 2, session: "A", epoch: 2 });
    const desk = bpmDesk("loc:a");
    expect(desk.enabled).toBe(true);
    expect(desk.pending).toBe(true);
  });

  it("does not let one tab's reply clear the other tab's choice when counters would match", () => {
    const tabA = beginBpmChange("loc:a", { palette: "red" }, { enabled: true, scope: "auto", palette: "orange", revision: 2, session: "s", epoch: 1 });
    resetBpmIntent();
    const tabB = beginBpmChange("loc:a", { palette: "blue" });
    finishBpmChange("loc:a", tabA.fieldRevs, true, undefined, 3, 1);
    expect(bpmDesk("loc:a").choice.palette).toBe("blue");
    expect(tabA.fieldRevs.palette).not.toBe(tabB.fieldRevs.palette);
  });

  it("finishes an older field ack after a newer sibling field advanced seen", () => {
    const palette = beginBpmChange(
      "loc:a",
      { palette: "red" },
      { enabled: true, scope: "auto", palette: "orange", intensity: "full", revision: 0, session: "A", epoch: 2 },
    );
    const intensity = beginBpmChange("loc:a", { intensity: "soft" });
    confirmBpmFromTelemetry("loc:a", {
      enabled: true,
      scope: "auto",
      palette: "red",
      intensity: "soft",
      revision: 2,
      session: "A",
      epoch: 2,
    });
    finishBpmChange("loc:a", intensity.fieldRevs, true, undefined, 2, 2);
    confirmBpmFromTelemetry("loc:a", {
      enabled: true,
      scope: "auto",
      palette: "red",
      intensity: "full",
      revision: 1,
      session: "A",
      epoch: 2,
    });
    finishBpmChange("loc:a", palette.fieldRevs, true, undefined, 1, 2);
    const desk = bpmDesk("loc:a");
    expect(desk.choice.palette).toBe("red");
    expect(desk.choice.intensity).toBe("soft");
    expect(desk.pending).toBe(false);
    confirmBpmFromTelemetry("loc:a", {
      enabled: true,
      scope: "auto",
      palette: "blue",
      intensity: "soft",
      revision: 3,
      session: "A",
      epoch: 2,
    });
    expect(bpmDesk("loc:a").choice.palette).toBe("blue");
  });

  it("settles a pending press by command_id after the in-memory callback is gone", async () => {
    const press = beginBpmChange(
      "loc:a",
      { palette: "red" },
      { enabled: true, scope: "auto", palette: "orange", revision: 1, session: "A", epoch: 2 },
    );
    bindBpmCommandId("loc:a", press.fieldRevs, "cmd-red");
    expect(listPendingBpmCommands("loc:a")).toHaveLength(1);
    resetBpmIntent();
    expect(bpmDesk("loc:a").choice.palette).toBe("red");
    expect(bpmDesk("loc:a").pending).toBe(true);
    await settlePendingBpmCommands("loc:a", async (command) => {
      expect(command.commandId).toBe("cmd-red");
      return {
        ok: true,
        revision: 4,
        epoch: 2,
        snapshot: {
          enabled: true,
          scope: "auto",
          palette: "red",
          intensity: "full",
          revision: 4,
          session: "A",
          epoch: 2,
        },
      };
    });
    const desk = bpmDesk("loc:a");
    expect(desk.pending).toBe(false);
    expect(desk.choice.palette).toBe("red");
  });
});
