import { describe, expect, it } from "vitest";
import {
  commandDue,
  fresherDesk,
  pollQueuedCommands,
  readDeskSnapshot,
  replayAgeMs,
  stampDeskSnapshot,
} from "../src/db/deskLink";
import { isDurableVolumeLimit } from "../src/auth/device";

describe("desk snapshot", () => {
  it("reads the current effect and leaves other capability keys alone", () => {
    const raw = JSON.stringify({
      arch: "aarch64",
      desk: {
        look: "bars",
        program: "beat",
        preset_id: "A01",
        sequence: 4,
        capabilities: ["bpm_sync", "auto_director"],
      },
    });
    expect(readDeskSnapshot(raw)).toMatchObject({
      look: "bars",
      program: "beat",
      preset_id: "A01",
      sequence: 4,
      capabilities: ["bpm_sync", "auto_director"],
    });
    expect(readDeskSnapshot("{}")).toBeNull();
    expect(readDeskSnapshot("not-json")).toBeNull();
  });

  it("prefers the mailbox snapshot when it is newer than the stored desk", () => {
    const stored = { look: "old", updated_at: "2026-11-10T10:00:00.000Z", capabilities: ["bpm_sync"] };
    const mailbox = {
      look: "bars",
      updated_at: "2026-11-10T12:00:00.000Z",
      capabilities: ["bpm_sync", "auto_director"],
    };
    expect(fresherDesk(stored, mailbox)?.look).toBe("bars");
    expect(fresherDesk(mailbox, stored)?.look).toBe("bars");
    expect(fresherDesk(stored, null)?.look).toBe("old");
  });

  it("does not treat an expired command as due, and recognises the durable object cap", () => {
    const created = "2026-11-10T12:00:00.000Z";
    const start = Date.parse(created);
    expect(commandDue(created, 30_000, start + 1_000)).toBe(true);
    expect(commandDue(created, 30_000, start + 30_000)).toBe(false);
    expect(commandDue("not-a-time", 30_000, start)).toBe(false);
    expect(isDurableVolumeLimit(new Error("Exceeded allowed volume of requests in Durable Objects free tier."))).toBe(
      true,
    );
    expect(isDurableVolumeLimit(new Error("exceeded D1 daily row read limit"))).toBe(false);
  });

  it("returns a command that arrives while the hall is waiting, without waiting out the whole window", async () => {
    let clock = 0;
    let reads = 0;
    const slept: number[] = [];
    const found = await pollQueuedCommands(
      async () => {
        reads += 1;
        return reads < 3 ? [] : [{ command_id: "press", type: "select_preset", payload: {} }];
      },
      2_000,
      250,
      async (ms) => {
        slept.push(ms);
        clock += ms;
      },
      () => clock,
    );
    expect(found.map((item) => item.command_id)).toEqual(["press"]);
    expect(slept).toEqual([250, 250]);
    expect(clock).toBeLessThan(2_000);
  });
});

describe("stored preview age", () => {
  const born = "2026-11-11T00:00:00.000Z";

  it("keeps the birth of the same session and sequence", () => {
    const previous = { session: "show", sequence: 7, age_ms: 800, updated_at: born, look: "bars" };
    const again = stampDeskSnapshot(previous, { ...previous, look: "bars" }, Date.parse(born) + 9_000);
    expect(again.updated_at).toBe(born);
    expect(again.age_ms).toBe(800);
    const changed = stampDeskSnapshot(previous, { ...previous, sequence: 8 }, Date.parse(born) + 9_000);
    expect(changed.updated_at).not.toBe(born);
  });

  it("adds the time since the snapshot was stored", () => {
    const desk = { session: "show", sequence: 7, age_ms: 800, updated_at: born };
    const readAt = Date.parse(born) + 12_000;
    expect(replayAgeMs(desk, readAt)).toBe(12_800);
    expect(replayAgeMs(desk, readAt)).toBe(replayAgeMs(desk, readAt));
    expect(replayAgeMs({ session: "show", sequence: 7 }, readAt)).toBeNull();
  });
});
