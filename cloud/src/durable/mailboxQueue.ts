import { DEFAULT_COMMAND_TTL_MS, isEphemeralCommand } from "../protocol/types";

export type MailboxCommand = {
  command_id: string;
  type: string;
  payload: Record<string, unknown>;
  enqueued_at: number;
  ttl_ms: number;
};

export function isExpired(cmd: MailboxCommand, now = Date.now()): boolean {
  const ttl = Number.isFinite(cmd.ttl_ms) && cmd.ttl_ms > 0 ? cmd.ttl_ms : DEFAULT_COMMAND_TTL_MS;
  const start = Number.isFinite(cmd.enqueued_at) ? cmd.enqueued_at : 0;
  return now >= start + ttl;
}

export function rememberCommand(
  queue: MailboxCommand[],
  cmd: MailboxCommand,
): MailboxCommand[] | null {
  if (isEphemeralCommand(cmd.type)) return null;
  if (queue.some((item) => item.command_id === cmd.command_id)) return queue;
  return [...queue, cmd].slice(-20);
}

/** Drop what must never be delivered. Sent commands stay until the device acks them. */
export function withoutAckedOrExpired(
  queue: MailboxCommand[],
  acked: ReadonlySet<string>,
  now = Date.now(),
): MailboxCommand[] {
  return queue.filter(
    (cmd) => !acked.has(cmd.command_id) && !isExpired(cmd, now) && !isEphemeralCommand(cmd.type),
  );
}

/**
 * Try to hand each command to the device. A throw keeps that command and every
 * command after it. A successful send also keeps the command until an ack:
 * the queue is the confirmation, not the attempt.
 */
export function deliverMailbox(
  queue: MailboxCommand[],
  send: (cmd: MailboxCommand) => void,
  now = Date.now(),
): MailboxCommand[] {
  const pending = withoutAckedOrExpired(queue, new Set(), now);
  const kept: MailboxCommand[] = [];
  let failed = false;
  for (const cmd of pending) {
    if (!failed) {
      try {
        send(cmd);
      } catch {
        failed = true;
      }
    }
    kept.push(cmd);
  }
  return kept;
}
