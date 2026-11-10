/**
 * Per-installation Durable Object: hibernatable device WebSocket + admin proxies.
 * Cloudflare sends allowlisted commands only вЂ” never Art-Net frames.
 */
import { appUpdateCommandId, planAppDelivery } from "../app/nudge";
import { getAppAssignment, reportAppUpdateResult } from "../app/releases";
import { ackCommand, getCommand, logCommand } from "../db/commands";
import { getAssignment } from "../scenes/releases";
import { touchHeartbeat } from "../db/installations";
import type { Env } from "../env";
import { nowIso, uuid } from "../env";
import {
  ALLOWED_COMMAND_TYPES,
  DESK_SERVICE_COMMANDS,
  CALIBRATION_COMMAND_TYPES,
  CALIBRATION_LEASE_MS,
  DEFAULT_COMMAND_TTL_MS,
  HOLD_LEASE_MS,
  MOMENTARY_PRESS_TYPES,
  MOMENTARY_RELEASE_TYPES,
  PROTOCOL_VERSION,
  RECONNECT_GUIDANCE,
  isAllowedCommandType,
  type AllowedCommandType,
  type CommandEnvelope,
} from "../protocol/types";

const APP_UPDATE_COMMAND_TYPES: ReadonlySet<string> = new Set([
  "check_app_update",
  "stage_app_update",
  "apply_app_update",
  "rollback_app_update",
  "restart_runtime",
]);
interface Attachment {
  role: "device" | "admin";
  installationId: string;
  sessionId: string;
  /** Admin cloud source, e.g. cloud:web:<sessionId> */
  source?: string;
}
interface PendingCommand {
  command_id: string;
  type: string;
  created_at: number;
  ttl_ms: number;
  momentary_press: boolean;
  /** Desired assignment sync вЂ” redeliver until applied ACK; accepted must not block. */
  desired_sync: boolean;
  payload: Record<string, unknown>;
}
interface ActiveHold {
  hold_id: string;
  source: string;
  effect: string;
  lease_until: number;
  adminSessionId: string;
  /** Command that armed this hold — a failed executor ACK drops it. */
  command_id?: string;
}
interface KeyboardLease {
  source: string;
  code: string;
  profileId: string;
  leaseUntil: number;
  adminSessionId: string;
  installationId: string;
}
interface StoredMeta {
  pending?: PendingCommand[];
  holds?: ActiveHold[];
  /** Keyboard leases survive hibernation so expiry can keyup. They are not replayed as keydown. */
  keyboardLeases?: KeyboardLease[];
  installationId?: string;
  /** Last device heartbeat payload — replayed on admin_hello (not invented). */
  lastHeartbeat?: Record<string, unknown>;
  lastHeartbeatAt?: string;
}
export class DeviceDO implements DurableObject {
  private state: DurableObjectState;
  private env: Env;
  private sessions: Map<WebSocket, Attachment> = new Map();
  private deviceSocket: WebSocket | null = null;
  private deviceSessionId: string | null = null;
  private installationId: string | null = null;
  private pending: Map<string, PendingCommand> = new Map();
  private holds: Map<string, ActiveHold> = new Map();
  /** In-memory only. A restart must not replay a key that was already down. */
  private keyboardLeases: Map<string, KeyboardLease> = new Map();
  /** In-memory only. One calibration owner; a restart must not resume a beam test. */
  private calibrationOwner: string | null = null;
  private calibrationLeaseUntil = 0;
  private lastHeartbeat: Record<string, unknown> | null = null;
  private lastHeartbeatAt: string | null = null;
  /** One stage/apply push per desired build while this socket stays up. */
  private nudgedStageBuild: string | null = null;
  private nudgedApplyBuild: string | null = null;
  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
    this.state.blockConcurrencyWhile(async () => {
      const stored = await this.state.storage.get<StoredMeta>("meta");
      if (stored?.pending) {
        for (const p of stored.pending) {
          this.pending.set(p.command_id, {
            ...p,
            desired_sync: Boolean(p.desired_sync),
            momentary_press: Boolean(p.momentary_press),
          });
        }
      }
      if (stored?.holds) {
        for (const h of stored.holds) {
          this.holds.set(this.holdKey(h.adminSessionId, h.hold_id), h);
        }
      }
      if (stored?.keyboardLeases) {
        for (const lease of stored.keyboardLeases) {
          const key = `${lease.adminSessionId}:${lease.code}`;
          this.keyboardLeases.set(key, lease);
        }
      }
      if (stored?.installationId) this.installationId = stored.installationId;
      if (stored?.lastHeartbeat && typeof stored.lastHeartbeat === "object") {
        this.lastHeartbeat = stored.lastHeartbeat;
        this.lastHeartbeatAt = stored.lastHeartbeatAt ?? null;
      }
      for (const ws of this.state.getWebSockets()) {
        const att = ws.deserializeAttachment() as Attachment | null;
        if (!att) {
          ws.close(1008, "missing attachment");
          continue;
        }
        this.sessions.set(ws, att);
        if (att.role === "device") {
          this.deviceSocket = ws;
          this.deviceSessionId = att.sessionId;
          this.installationId = att.installationId;
        }
      }
      // Restore alarm for nearest hold lease / pending TTL after hibernation.
      await this.scheduleAlarm();
    });
  }
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path === "/connect/device" && request.headers.get("Upgrade") === "websocket") {
      return this.acceptDevice(request);
    }
    if (path === "/connect/admin" && request.headers.get("Upgrade") === "websocket") {
      return this.acceptAdmin(request);
    }
    if (path === "/command" && request.method === "POST") {
      return this.handleHttpCommand(request);
    }
    if (path === "/update-result" && request.method === "POST") {
      // REST path finalized command_log; clear pending map to converge state.
      const body = (await request.json()) as {
        command_id?: string;
        event_type?: string;
      };
      const commandId = String(body.command_id ?? "");
      if (commandId && this.pending.has(commandId)) {
        this.pending.delete(commandId);
        await this.persistMeta();
        await this.scheduleAlarm();
      }
      return Response.json({ ok: true, cleared: Boolean(commandId) });
    }
    if (path === "/status") {
      return Response.json({
        online: Boolean(this.deviceSocket),
        device_connected: Boolean(this.deviceSocket),
        device_session_id: this.deviceSessionId,
        pending_commands: this.pending.size,
        last_state: this.lastHeartbeat,
        active_holds: [...this.holds.values()],
        reconnect: RECONNECT_GUIDANCE,
      });
    }
    return new Response("not found", { status: 404 });
  }
  private acceptDevice(request: Request): Response {
    const installationId = request.headers.get("x-installation-id");
    if (!installationId) return new Response("missing installation", { status: 400 });
    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    const sessionId = uuid();
    if (this.deviceSocket) {
      try {
        this.deviceSocket.close(4000, "superseded");
      } catch {
        /* ignore */
      }
      this.sessions.delete(this.deviceSocket);
    }
    this.state.acceptWebSocket(server);
    const att: Attachment = { role: "device", installationId, sessionId };
    server.serializeAttachment(att);
    this.sessions.set(server, att);
    this.deviceSocket = server;
    this.deviceSessionId = sessionId;
    this.installationId = installationId;
    // Do NOT replay momentary presses after reconnect вЂ” drop them.
    for (const [id, cmd] of [...this.pending.entries()]) {
      if (cmd.momentary_press) {
        this.pending.delete(id);
      }
    }
    void this.persistMeta();
    // Desired scene + app state lives in D1; push on reconnect until applied ACK.
    void this.pushDesiredAssignment(installationId);
    void this.deliverCurrentAppAssignment(installationId);
    server.send(
      JSON.stringify({
        protocol_version: PROTOCOL_VERSION,
        message_id: uuid(),
        type: "session_hello",
        device_id: installationId,
        sent_at: nowIso(),
        payload: {
          session_id: sessionId,
          reconnect: RECONNECT_GUIDANCE,
          allowed_commands: ALLOWED_COMMAND_TYPES,
        },
      }),
    );
    return new Response(null, { status: 101, webSocket: client });
  }
  private acceptAdmin(request: Request): Response {
    const installationId = request.headers.get("x-installation-id");
    // Prefer caller-provided per-tab source (cloud:web:<sessionId>).
    const source = request.headers.get("x-admin-source") ?? `cloud:web:${uuid()}`;
    if (!installationId) return new Response("missing installation", { status: 400 });
    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    const sessionId = uuid();
    this.state.acceptWebSocket(server);
    const att: Attachment = {
      role: "admin",
      installationId,
      sessionId,
      source,
    };
    server.serializeAttachment(att);
    this.sessions.set(server, att);
    const deviceOnline = Boolean(this.deviceSocket);
    server.send(
      JSON.stringify({
        protocol_version: PROTOCOL_VERSION,
        message_id: uuid(),
        type: "admin_hello",
        device_id: installationId,
        sent_at: nowIso(),
        payload: {
          session_id: sessionId,
          source,
          device_online: deviceOnline,
          online: deviceOnline,
          // Authoritative last device snapshot only — never invent armed/blackout.
          last_state: this.lastHeartbeat
            ? {
                ...this.lastHeartbeat,
                online: deviceOnline,
                received_at: this.lastHeartbeatAt,
              }
            : null,
        },
      }),
    );
    return new Response(null, { status: 101, webSocket: client });
  }
  private async handleHttpCommand(request: Request): Promise<Response> {
    const body = (await request.json()) as {
      type: string;
      installation_id: string;
      payload?: Record<string, unknown>;
      actor?: string;
      source?: string;
    };
    if (!isAllowedCommandType(body.type)) {
      return Response.json({ error: { code: "not_allowlisted", message: body.type } }, { status: 400 });
    }
    const result = await this.dispatchCommand({
      type: body.type,
      installationId: body.installation_id,
      payload: body.payload ?? {},
      source: body.source ?? (body.actor ? `cloud:${body.actor}` : "cloud:api"),
      adminSessionId: null,
    });
    return Response.json(result, { status: result.ok ? 200 : 409 });
  }
  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    const att = this.sessions.get(ws) ?? (ws.deserializeAttachment() as Attachment | null);
    if (!att) return;
    const text = typeof message === "string" ? message : new TextDecoder().decode(message);
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(text) as Record<string, unknown>;
    } catch {
      return;
    }
    if (att.role === "device") {
      await this.onDeviceMessage(att, msg);
      return;
    }
    if (att.role === "admin") {
      await this.onAdminMessage(ws, att, msg);
    }
  }
  async webSocketClose(ws: WebSocket, _code: number, _reason: string, _wasClean: boolean): Promise<void> {
    const att = this.sessions.get(ws) ?? (ws.deserializeAttachment() as Attachment | null);
    this.sessions.delete(ws);
    if (!att) return;
    if (att.role === "device" && this.deviceSessionId === att.sessionId) {
      this.deviceSocket = null;
      this.deviceSessionId = null;
      // Device session dropped — tell admins (admin WSS close must NOT mean this).
      this.broadcastAdmins({
        protocol_version: PROTOCOL_VERSION,
        message_id: uuid(),
        type: "device_offline",
        device_id: att.installationId,
        sent_at: nowIso(),
        payload: { online: false, reason: "device_websocket_closed" },
      });
    }
    if (att.role === "admin") {
      const source = att.source ?? `cloud:web:${att.sessionId}`;
      await this.releaseSourceHolds(source, att.sessionId);
      await this.endCalibrationIfOwner(source, att.installationId);
    }
  }
  async webSocketError(ws: WebSocket, _error: unknown): Promise<void> {
    const att = this.sessions.get(ws) ?? (ws.deserializeAttachment() as Attachment | null);
    this.sessions.delete(ws);
    if (att?.role === "admin") {
      const source = att.source ?? `cloud:web:${att.sessionId}`;
      await this.releaseSourceHolds(source, att.sessionId);
      await this.endCalibrationIfOwner(source, att.installationId);
    }
    if (att?.role === "device" && this.deviceSessionId === att.sessionId) {
      this.deviceSocket = null;
      this.deviceSessionId = null;
    }
  }
  async alarm(): Promise<void> {
    const now = Date.now();
    const installationId =
      this.installationId ??
      [...this.sessions.values()].find((a) => a.role === "device")?.installationId ??
      "";
    for (const [id, hold] of [...this.holds.entries()]) {
      if (hold.lease_until <= now) {
        this.holds.delete(id);
        // Lease expiry: effect-specific release for THIS hold only (not release_source).
        const releaseType = releaseTypeForEffect(hold.effect);
        await this.dispatchCommand({
          type: releaseType,
          installationId,
          payload: {
            source: hold.source,
            hold_id: hold.hold_id,
            effect: hold.effect,
            reason: "lease_expired",
          },
          source: hold.source,
          adminSessionId: hold.adminSessionId,
          forceType: releaseType,
        });
      }
    }
    if (this.calibrationOwner && this.calibrationLeaseUntil > 0 && this.calibrationLeaseUntil <= now) {
      const owner = this.calibrationOwner;
      this.calibrationOwner = null;
      this.calibrationLeaseUntil = 0;
      await this.dispatchCommand({
        type: "calibration_end",
        installationId,
        payload: { reason: "lease_expired" },
        source: owner,
        adminSessionId: null,
      });
    }
    for (const [id, lease] of [...this.keyboardLeases.entries()]) {
      if (lease.leaseUntil > now) continue;
      this.keyboardLeases.delete(id);
      await this.dispatchCommand({
        type: "keyboard",
        installationId: lease.installationId || installationId,
        payload: {
          code: lease.code,
          type: "keyup",
          repeat: false,
          profile_id: lease.profileId,
          reason: "lease_expired",
        },
        source: lease.source,
        adminSessionId: lease.adminSessionId,
      });
    }
    for (const [id, cmd] of [...this.pending.entries()]) {
      if (!this.usesTransportTtl(cmd.type)) continue;
      if (now - cmd.created_at >= cmd.ttl_ms) {
        this.pending.delete(id);
        // Stale transient can fail; desired sync stays redeliverable via D1 on reconnect.
        await ackCommand(this.env, id, "failed", "ttl_expired", "Command TTL expired", {
          installationId: this.installationId ?? undefined,
          type: cmd.type,
        });
      }
    }
    await this.persistMeta();
    await this.scheduleAlarm();
  }
  private async onDeviceMessage(att: Attachment, msg: Record<string, unknown>): Promise<void> {
    const type = String(msg.type ?? "");
    if (type === "heartbeat") {
      const payload = (msg.payload ?? {}) as Record<string, unknown>;
      await touchHeartbeat(this.env, att.installationId, {
        uptime_s: num(payload.uptime_s),
        armed: bool(payload.armed),
        blackout: bool(payload.blackout),
        transport: str(payload.transport),
        udp_active: bool(payload.udp_active),
        current_scene_id: str(payload.current_scene_id),
        current_release_version: num(payload.current_release_version),
        current_content_hash: str(payload.current_content_hash),
        last_synced_hash: str(payload.last_synced_hash),
        last_error: payload.last_error === null ? null : str(payload.last_error),
        app_version: str(payload.app_version),
        build_id: str(payload.build_id),
        arch: str(payload.arch),
        install_mode: str(payload.install_mode),
        updater_protocol_version: num(payload.updater_protocol_version),
      });
      this.lastHeartbeat = { ...payload };
      this.lastHeartbeatAt = nowIso();
      await this.persistMeta();
      this.broadcastAdmins({
        protocol_version: PROTOCOL_VERSION,
        message_id: uuid(),
        type: "device_heartbeat",
        device_id: att.installationId,
        sent_at: nowIso(),
        payload: {
          ...payload,
          online: true,
          app_update_status: payload.app_update_status ?? null,
          staged_app_version: payload.staged_app_version ?? null,
          staged_build_id: payload.staged_build_id ?? null,
        },
      });
      void this.nudgeAppUpdate(att.installationId, payload);
      return;
    }
    if (type === "preview") {
      const payload = (msg.payload ?? {}) as Record<string, unknown>;
      this.broadcastAdmins({
        protocol_version: PROTOCOL_VERSION,
        message_id: uuid(),
        type: "device_preview",
        device_id: att.installationId,
        sent_at: nowIso(),
        payload: {
          stage: payload.stage ?? null,
          program: payload.program ?? null,
          look: payload.look ?? null,
          captured_at: payload.captured_at ?? null,
        },
      });
      return;
    }
    if (type === "update_result") {
      // Device WSS: terminal apply/fail/rollback with original command_id.
      const payload = (msg.payload ?? {}) as Record<string, unknown>;
      const commandId = String(payload.command_id ?? "");
      const statusRaw = String(payload.status ?? "");
      const eventType =
        statusRaw === "failed" || statusRaw === "unsupported"
          ? "failed"
          : statusRaw === "rolled_back"
            ? "rolled_back"
            : statusRaw === "staged"
              ? "staged"
              : statusRaw === "cancelled"
                ? "cancelled"
                : statusRaw === "applied"
                  ? "applied"
                  : null;
      if (eventType === null) {
        this.broadcastAdmins(msg);
        return;
      }
      const detail =
        payload.detail && typeof payload.detail === "object"
          ? (payload.detail as Record<string, unknown>)
          : { ...payload };
      const result = await reportAppUpdateResult(this.env, {
        installationId: att.installationId,
        eventType,
        commandId: commandId || null,
        detail,
      });
      if (result.terminal && commandId) {
        this.pending.delete(commandId);
        await this.persistMeta();
        await this.scheduleAlarm();
      }
      this.broadcastAdmins({
        ...msg,
        payload: { ...payload, ...result },
      });
      return;
    }
    if (type === "ack") {
      const payload = (msg.payload ?? {}) as Record<string, unknown>;
      const commandId = String(payload.command_id ?? "");
      const status = String(payload.status ?? "");
      if (!status) {
        this.broadcastAdmins({
          ...msg,
          payload: { ...payload, status: "unknown" },
        });
        return;
      }
      if (commandId) {
        const existing = await getCommand(this.env, commandId);
        const pending = this.pending.get(commandId);
        // ACK scoped to this installation (+ type when known) — cannot ACK another install's command.
        if (
          existing &&
          existing.installation_id === att.installationId &&
          (!pending || pending.type === existing.type)
        ) {
          const desiredSync =
            pending?.desired_sync === true ||
            existing.type === "sync_scene_release" ||
            APP_UPDATE_COMMAND_TYPES.has(existing.type);
          // accepted must NOT finalize/remove pending for desired sync / app updates
          // (device may still report update_result after deferred apply).
          if (status === "accepted" && desiredSync) {
            await ackCommand(
              this.env,
              commandId,
              "accepted",
              str(payload.error_code) ?? null,
              str(payload.error_message) ?? null,
              { installationId: att.installationId, type: existing.type },
            );
          } else {
            this.pending.delete(commandId);
            if (status === "failed" || status === "rejected") {
              this.dropHoldsForCommand(commandId);
            }
            await ackCommand(
              this.env,
              commandId,
              status,
              str(payload.error_code) ?? null,
              str(payload.error_message) ?? null,
              { installationId: att.installationId, type: existing.type },
            );
            await this.persistMeta();
          }
        }
      }
      this.broadcastAdmins(msg);
      return;
    }
    if (type === "state") {
      this.broadcastAdmins(msg);
    }
  }
  private async onAdminMessage(
    ws: WebSocket,
    att: Attachment,
    msg: Record<string, unknown>,
  ): Promise<void> {
    const type = String(msg.type ?? "");
    if (type === "hold_keepalive") {
      const body = (msg.payload as Record<string, unknown> | undefined) ?? {};
      const holdId = String(body.hold_id ?? "");
      const code = String(body.code ?? "");
      const holdKey = holdId ? this.holdKey(att.sessionId, holdId) : "";
      const hold = holdKey ? this.holds.get(holdKey) : undefined;
      if (hold && hold.adminSessionId === att.sessionId) {
        hold.lease_until = Date.now() + HOLD_LEASE_MS;
        this.holds.set(holdKey, hold);
      }
      if (code) {
        const leaseKey = `${att.sessionId}:${code}`;
        const lease = this.keyboardLeases.get(leaseKey);
        const profile = String(body.profile_id ?? lease?.profileId ?? "");
        if (lease && lease.adminSessionId === att.sessionId && (!profile || profile === lease.profileId)) {
          lease.leaseUntil = Date.now() + HOLD_LEASE_MS;
          this.keyboardLeases.set(leaseKey, lease);
        }
      }
      await this.persistMeta();
      await this.scheduleAlarm();
      if (this.deviceSocket) {
        const source = att.source ?? `cloud:web:${att.sessionId}`;
        await this.dispatchCommand({
          type: "hold_keepalive",
          installationId: att.installationId,
          payload: {
            hold_id: holdId || undefined,
            code: code || undefined,
            profile_id: body.profile_id,
            source,
          },
          source,
          adminSessionId: att.sessionId,
        });
      }
      return;
    }
    if (!isAllowedCommandType(type)) {
      ws.send(
        JSON.stringify({
          type: "error",
          payload: { code: "not_allowlisted", message: type },
        }),
      );
      return;
    }
    const source = att.source ?? `cloud:web:${att.sessionId}`;
    const incoming = (msg.payload as Record<string, unknown> | undefined) ?? {};
    const incomingCommandId = typeof incoming.command_id === "string" ? incoming.command_id : "";
    if (source.startsWith("cloud:desk:") && DESK_SERVICE_COMMANDS.has(type)) {
      ws.send(
        JSON.stringify({
          type: "error",
          payload: { code: "desk_forbidden", message: type, command_id: incomingCommandId },
        }),
      );
      return;
    }
    const payload = { ...((msg.payload as Record<string, unknown>) ?? {}) };
    // The socket owns the source. A tab cannot name another tab's source.
    payload.source = source;
    delete payload.source_id;
    if (type === "keyboard") {
      const code = String(payload.code ?? "");
      const edge = String(payload.type ?? "");
      const leaseKey = `${att.sessionId}:${code}`;
      if (edge === "keyup") {
        this.keyboardLeases.delete(leaseKey);
      } else if (edge === "keydown" && payload.repeat !== true && code) {
        this.keyboardLeases.set(leaseKey, {
          source,
          code,
          profileId: String(payload.profile_id ?? "qwerty"),
          leaseUntil: Date.now() + HOLD_LEASE_MS,
          adminSessionId: att.sessionId,
          installationId: att.installationId,
        });
        await this.scheduleAlarm();
      }
    }
    let holdId: string | null = null;
    if (MOMENTARY_PRESS_TYPES.has(type)) {
      const requested = String(payload.hold_id ?? "");
      holdId = requested || uuid();
      payload.hold = { source, hold_id: holdId, lease_ms: HOLD_LEASE_MS };
    }
    if (MOMENTARY_RELEASE_TYPES.has(type)) {
      // Client release MUST send hold_id; delete that specific hold only
      // (H release must not clear V of the same tab, and vice versa).
      const releaseHoldId = String(payload.hold_id ?? "");
      if (!releaseHoldId) {
        ws.send(
          JSON.stringify({
            type: "error",
            payload: { code: "hold_id_required", message: "Release requires hold_id" },
          }),
        );
        return;
      }
      const releaseKey = this.holdKey(att.sessionId, releaseHoldId);
      const hold = this.holds.get(releaseKey);
      if (!hold || hold.adminSessionId !== att.sessionId) {
        ws.send(
          JSON.stringify({
            type: "error",
            payload: { code: "hold_not_found", message: "Unknown hold_id for this session" },
          }),
        );
        return;
      }
      const expectedEffect = type.replace(/_release$/, "");
      if (hold.effect !== expectedEffect) {
        ws.send(
          JSON.stringify({
            type: "error",
            payload: {
              code: "hold_effect_mismatch",
              message: `hold effect ${hold.effect} != release ${expectedEffect}`,
            },
          }),
        );
        return;
      }
      this.holds.delete(releaseKey);
      payload.source = hold.source;
      payload.hold_id = releaseHoldId;
      await this.persistMeta();
      await this.scheduleAlarm();
    }
    const result = await this.dispatchCommand({
      type,
      installationId: att.installationId,
      payload,
      source,
      adminSessionId: att.sessionId,
    });
    if (holdId && MOMENTARY_PRESS_TYPES.has(type)) {
      if (result.ok) {
        const effect = type.replace(/_press$/, "");
        this.holds.set(this.holdKey(att.sessionId, holdId), {
          hold_id: holdId,
          source,
          effect,
          lease_until: Date.now() + HOLD_LEASE_MS,
          adminSessionId: att.sessionId,
          command_id: result.command_id,
        });
        await this.persistMeta();
        await this.scheduleAlarm();
      } else {
        this.holds.delete(this.holdKey(att.sessionId, holdId));
        await this.persistMeta();
      }
    }
    ws.send(
      JSON.stringify({
        type: "command_result",
        payload: {
          ...result,
          ...(holdId && result.ok
            ? { hold_id: holdId, hold: payload.hold, lease_ms: HOLD_LEASE_MS }
            : {}),
        },
      }),
    );
  }
  private async dispatchCommand(opts: {
    type: AllowedCommandType | string;
    installationId: string;
    payload: Record<string, unknown>;
    source: string;
    adminSessionId: string | null;
    forceType?: AllowedCommandType;
    desiredSync?: boolean;
  }): Promise<{
    ok: boolean;
    command_id: string;
    status: string;
    idempotent_replay?: boolean;
    error?: string;
  }> {
    const type = (opts.forceType ?? opts.type) as AllowedCommandType;
    if (!isAllowedCommandType(type)) {
      return { ok: false, command_id: "", status: "failed", error: "not_allowlisted" };
    }
    if (CALIBRATION_COMMAND_TYPES.has(type)) {
      const busy =
        this.calibrationOwner != null &&
        this.calibrationOwner !== opts.source &&
        this.calibrationLeaseUntil > Date.now();
      if (busy) {
        return { ok: false, command_id: "", status: "failed", error: "calibration_busy" };
      }
      if (type === "calibration_end") {
        this.calibrationOwner = null;
        this.calibrationLeaseUntil = 0;
      } else {
        this.calibrationOwner = opts.source;
        this.calibrationLeaseUntil = Date.now() + CALIBRATION_LEASE_MS;
      }
    }
    const commandId =
      typeof opts.payload.command_id === "string" && opts.payload.command_id
        ? opts.payload.command_id
        : uuid();
    const desiredSync =
      opts.desiredSync === true ||
      (type === "sync_scene_release" && commandId.startsWith("desired:")) ||
      (APP_UPDATE_COMMAND_TYPES.has(type) && commandId.startsWith("desired-app"));
    const existing = await getCommand(this.env, commandId);
    if (existing) {
      // Applied в†’ permanent idempotent success (no re-send).
      if (existing.status === "applied") {
        return {
          ok: true,
          command_id: commandId,
          status: existing.status,
          idempotent_replay: true,
        };
      }
      // Momentary / ordinary: accepted or failed blocks re-issue of same command_id.
      // Desired sync: accepted must NOT permanently block re-send until applied ACK.
      if (!desiredSync) {
        return {
          ok: true,
          command_id: commandId,
          status: existing.status,
          idempotent_replay: true,
        };
      }
      // Desired sync still pending (accepted) or previously failed в†’ fall through to redeliver.
    }
    const ttl = Number(opts.payload.ttl_ms ?? DEFAULT_COMMAND_TTL_MS);
    const envelope: CommandEnvelope = {
      protocol_version: PROTOCOL_VERSION,
      message_id: uuid(),
      type,
      device_id:
        opts.installationId ||
        this.installationId ||
        (this.sessions.values().next().value?.installationId ?? ""),
      sent_at: nowIso(),
      payload: {
        ...opts.payload,
        command_id: commandId,
        idempotent: true,
        ttl_ms: ttl,
        source: opts.source,
      },
    };
    // Offline: transient commands fail immediately вЂ” no fake pending that never sends.
    // Desired scene state is tracked in D1 separately (picked up on reconnect).
    if (!this.deviceSocket) {
      if (!existing) {
        await logCommand(this.env, {
          command_id: commandId,
          installation_id: envelope.device_id,
          type,
          payload: envelope.payload,
          status: "failed",
          error_code: "device_offline",
          error_message: "Device offline",
        });
      }
      return { ok: false, command_id: commandId, status: "failed", error: "device_offline" };
    }
    if (!existing) {
      await logCommand(this.env, {
        command_id: commandId,
        installation_id: envelope.device_id,
        type,
        payload: envelope.payload,
        status: "accepted",
      });
    } else if (existing.status === "failed" && desiredSync) {
      // Reset to accepted so reconnect redelivery is tracked until applied.
      await ackCommand(this.env, commandId, "accepted", null, null, {
        installationId: envelope.device_id,
        type,
      });
    }
    this.pending.set(commandId, {
      command_id: commandId,
      type,
      created_at: Date.now(),
      ttl_ms: ttl,
      momentary_press:
        MOMENTARY_PRESS_TYPES.has(type) ||
        type === "keyboard" ||
        type === "hold_keepalive" ||
        CALIBRATION_COMMAND_TYPES.has(type),
      desired_sync: desiredSync,
      payload: envelope.payload,
    });
    await this.persistMeta();
    await this.scheduleAlarm();
    try {
      this.deviceSocket.send(JSON.stringify(envelope));
    } catch {
      this.pending.delete(commandId);
      this.dropHoldsForCommand(commandId);
      await ackCommand(this.env, commandId, "failed", "send_failed", "Send failed", {
        installationId: envelope.device_id,
        type,
      });
      await this.persistMeta();
      return { ok: false, command_id: commandId, status: "failed", error: "send_failed" };
    }
    return { ok: true, command_id: commandId, status: "accepted" };
  }
  private async endCalibrationIfOwner(source: string, installationId: string): Promise<void> {
    if (this.calibrationOwner !== source) return;
    this.calibrationOwner = null;
    this.calibrationLeaseUntil = 0;
    await this.dispatchCommand({
      type: "calibration_end",
      installationId,
      payload: { reason: "owner_lost" },
      source,
      adminSessionId: null,
    });
  }
  /** Disconnect/error: release_source clears ALL holds for that admin source. */
  private async releaseSourceHolds(source: string, adminSessionId: string): Promise<void> {
    const toRelease = [...this.holds.values()].filter(
      (h) => h.adminSessionId === adminSessionId || h.source === source,
    );
    for (const hold of toRelease) {
      this.holds.delete(this.holdKey(hold.adminSessionId, hold.hold_id));
    }
    for (const [id, lease] of [...this.keyboardLeases.entries()]) {
      if (lease.adminSessionId === adminSessionId || lease.source === source) {
        this.keyboardLeases.delete(id);
      }
    }
    await this.persistMeta();
    if (this.deviceSocket) {
      await this.dispatchCommand({
        type: "release_source",
        installationId: this.installationId ?? "",
        payload: { source, hold_ids: toRelease.map((h) => h.hold_id) },
        source,
        adminSessionId,
      });
    }
    await this.scheduleAlarm();
  }
  private async pushDesiredAssignment(installationId: string): Promise<void> {
    const assignment = await getAssignment(this.env, installationId);
    if (!assignment || !this.deviceSocket) return;
    const commandId = `desired:${installationId}:${assignment.scene_id}:${assignment.desired_version}:${assignment.updated_at}`;
    await this.dispatchCommand({
      type: "sync_scene_release",
      installationId,
      payload: {
        command_id: commandId,
        scene_id: assignment.scene_id,
        version: assignment.desired_version,
        track_latest: Boolean(assignment.track_latest),
      },
      source: "cloud:assignment",
      adminSessionId: null,
      desiredSync: true,
    });
  }
  /**
   * Reconnect and heartbeat deliver only the assignment that is current in D1.
   * A cancelled or replaced build is dropped and is not turned into apply.
   */
  private async deliverCurrentAppAssignment(installationId: string): Promise<void> {
    const assignment = await getAppAssignment(this.env, installationId);
    this.dropReplacedAppCommands(
      assignment?.desired_build_id ?? null,
      assignment?.desired_version ?? null,
    );
    await this.persistMeta();
    await this.pushDesiredAppAssignment(installationId);
  }

  private dropReplacedAppCommands(
    desiredBuild: string | null,
    desiredVersion: string | null,
  ): void {
    for (const [id, cmd] of [...this.pending.entries()]) {
      if (!APP_UPDATE_COMMAND_TYPES.has(cmd.type) || cmd.type === "restart_runtime") continue;
      const build = str(cmd.payload?.build_id);
      const version = str(cmd.payload?.version);
      const current =
        Boolean(desiredBuild) &&
        build === desiredBuild &&
        (!version || !desiredVersion || version === desiredVersion);
      if (!current) this.pending.delete(id);
    }
  }

  /** While the socket stays open, offer check or stage. Never apply from a heartbeat. */
  private async nudgeAppUpdate(
    installationId: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const assignment = await getAppAssignment(this.env, installationId);
    this.dropReplacedAppCommands(
      assignment?.desired_build_id ?? null,
      assignment?.desired_version ?? null,
    );
    if (!assignment?.desired_version || !assignment.desired_build_id) return;
    const desired = assignment.desired_build_id;
    const autoStage = Boolean(assignment.auto_update);
    const commandId = appUpdateCommandId(
      autoStage ? "stage" : "check",
      installationId,
      assignment.desired_version,
      desired,
      assignment.updated_at,
    );
    const existing = await getCommand(this.env, commandId);
    const plan = planAppDelivery({
      installationId,
      desiredVersion: assignment.desired_version,
      desiredBuildId: desired,
      updatedAt: assignment.updated_at,
      autoStage,
      runningBuildId: str(payload.build_id),
      stagedBuildId: str(payload.staged_build_id),
      stagedVersion: str(payload.staged_app_version),
      armed: bool(payload.armed) ?? null,
      commandStatus: existing?.status ?? null,
    });
    if (plan.action === "none") return;
    if (this.nudgedStageBuild === desired && existing?.status !== "failed") return;
    this.nudgedStageBuild = desired;
    await this.pushDesiredAppAssignment(installationId);
  }

  /** Push desired APPLICATION assignment. Check and stage use different command ids. */
  private async pushDesiredAppAssignment(installationId: string): Promise<void> {
    const assignment = await getAppAssignment(this.env, installationId);
    if (!assignment || !this.deviceSocket) return;
    if (!assignment.desired_version || !assignment.desired_build_id) return;
    const autoStage = Boolean(assignment.auto_update);
    const kind = autoStage ? "stage" : "check";
    const commandId = appUpdateCommandId(
      kind,
      installationId,
      assignment.desired_version,
      assignment.desired_build_id,
      assignment.updated_at,
    );
    const type: AllowedCommandType = autoStage ? "stage_app_update" : "check_app_update";
    await this.dispatchCommand({
      type,
      installationId,
      payload: {
        command_id: commandId,
        version: assignment.desired_version,
        build_id: assignment.desired_build_id,
        channel: assignment.channel,
        track_latest: Boolean(assignment.track_latest),
        auto_stage: autoStage,
      },
      source: "cloud:app_assignment",
      adminSessionId: null,
      desiredSync: true,
    });
  }
  private broadcastAdmins(msg: unknown): void {
    const data = JSON.stringify(msg);
    for (const [ws, att] of this.sessions) {
      if (att.role === "admin") {
        try {
          ws.send(data);
        } catch {
          /* ignore */
        }
      }
    }
  }
  private holdKey(adminSessionId: string | null | undefined, holdId: string): string {
    return `${adminSessionId ?? ""}::${holdId}`;
  }
  private dropHoldsForCommand(commandId: string): void {
    if (!commandId) return;
    for (const [id, hold] of [...this.holds.entries()]) {
      if (hold.command_id === commandId) this.holds.delete(id);
    }
  }
  private async persistMeta(): Promise<void> {
    await this.state.storage.put("meta", {
      pending: [...this.pending.values()],
      holds: [...this.holds.values()],
      keyboardLeases: [...this.keyboardLeases.values()],
      installationId: this.installationId ?? undefined,
      lastHeartbeat: this.lastHeartbeat ?? undefined,
      lastHeartbeatAt: this.lastHeartbeatAt ?? undefined,
    } satisfies StoredMeta);
  }
  /** A staged apply waits for update_result. Its socket TTL is not a timer. */
  private usesTransportTtl(type: string): boolean {
    return !APP_UPDATE_COMMAND_TYPES.has(type);
  }
  /** Schedule alarm only when holds or a transport TTL exist. No perpetual timer. */
  private async scheduleAlarm(): Promise<void> {
    const deadlines: number[] = [];
    for (const hold of this.holds.values()) deadlines.push(hold.lease_until);
    for (const lease of this.keyboardLeases.values()) deadlines.push(lease.leaseUntil);
    if (this.calibrationLeaseUntil > 0) deadlines.push(this.calibrationLeaseUntil);
    for (const cmd of this.pending.values()) {
      if (!this.usesTransportTtl(cmd.type)) continue;
      deadlines.push(cmd.created_at + cmd.ttl_ms);
    }
    if (deadlines.length === 0) {
      await this.state.storage.deleteAlarm();
      return;
    }
    const next = Math.min(...deadlines);
    await this.state.storage.setAlarm(next);
  }
}
function releaseTypeForEffect(effect: string): AllowedCommandType {
  switch (effect) {
    case "strobe":
      return "strobe_release";
    case "sweep":
      return "sweep_release";
    case "vertical_sweep":
      return "vertical_sweep_release";
    default:
      return "release_source";
  }
}
function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}
function bool(v: unknown): boolean | undefined {
  return typeof v === "boolean" ? v : undefined;
}
function str(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}
