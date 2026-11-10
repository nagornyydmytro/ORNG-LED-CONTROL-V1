import { describe, expect, it } from "vitest";
import { deliverMailbox, rememberCommand, withoutAckedOrExpired, type MailboxCommand } from "../src/durable/mailboxQueue";
import { isEphemeralCommand } from "../src/protocol/types";

function cmd(partial: Partial<MailboxCommand> & Pick<MailboxCommand, "command_id" | "type">): MailboxCommand {
  return {
    payload: {},
    enqueued_at: Date.now(),
    ttl_ms: 30_000,
    ...partial,
  };
}

describe("mailbox queue", () => {
  it("refuses to store a short press, a key, or a calibration step", () => {
    expect(isEphemeralCommand("strobe_press")).toBe(true);
    expect(isEphemeralCommand("keyboard")).toBe(true);
    expect(isEphemeralCommand("calibration_set")).toBe(true);
    expect(isEphemeralCommand("blackout")).toBe(false);
    const queue = [cmd({ command_id: "keep", type: "blackout" })];
    expect(rememberCommand(queue, cmd({ command_id: "press", type: "strobe_press" }))).toBeNull();
    expect(queue).toHaveLength(1);
  });

  it("drops a command whose own ttl has passed and does not give it a new life", () => {
    const stale = cmd({ command_id: "old", type: "blackout", enqueued_at: 0, ttl_ms: 1_000 });
    const live = cmd({ command_id: "new", type: "select_preset", enqueued_at: 5_000, ttl_ms: 30_000 });
    expect(withoutAckedOrExpired([stale, live], new Set(), 5_000).map((item) => item.command_id)).toEqual([
      "new",
    ]);
  });

  it("keeps a command when the send fails and until the device acks it", () => {
    const queue = [
      cmd({ command_id: "a", type: "blackout" }),
      cmd({ command_id: "b", type: "select_preset" }),
    ];
    const sent: string[] = [];
    const afterFailure = deliverMailbox(queue, (item) => {
      sent.push(item.command_id);
      if (item.command_id === "a") throw new Error("send failed");
    });
    expect(sent).toEqual(["a"]);
    expect(afterFailure.map((item) => item.command_id)).toEqual(["a", "b"]);
    const afterAck = withoutAckedOrExpired(afterFailure, new Set(["a"]));
    expect(afterAck.map((item) => item.command_id)).toEqual(["b"]);
  });
});