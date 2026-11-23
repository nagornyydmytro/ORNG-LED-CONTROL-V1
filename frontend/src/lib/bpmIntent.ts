import { ref } from "vue";
import {
  asPeriod,
  bpmChoiceKey,
  normalizeGroups,
  paletteFromApi,
  readBpmChoice,
  writeBpmChoice,
  type BpmChoice,
} from "./bpmPalettes";

export type BpmField =
  | "enabled"
  | "scope"
  | "mode"
  | "palette"
  | "cast"
  | "groups"
  | "excluded"
  | "character"
  | "intensity"
  | "period"
  | "parRhythm"
  | "accent";

export type BpmSyncPatch = {
  enabled?: boolean;
  mode?: BpmChoice["mode"];
  scope?: BpmChoice["scope"];
  palette?: string;
  cast?: BpmChoice["cast"];
  groups?: BpmChoice["groups"];
  excluded?: string[];
  character?: BpmChoice["character"];
  intensity?: BpmChoice["intensity"];
  period?: BpmChoice["period"] | string | number;
  parRhythm?: boolean;
  accent?: number;
  /** Executor generation. Absent means the picture cannot be ordered. */
  revision?: number;
  /** Player process id. A different id is not newer by itself. */
  session?: string;
  /** Player process generation. A smaller epoch is an old session, even if its revision is larger. */
  epoch?: number;
};

export type BpmCommand = {
  enabled: boolean;
  mode: BpmChoice["mode"];
  scope: BpmChoice["scope"];
  palette: BpmChoice["palette"];
  cast: BpmChoice["cast"];
  groups: BpmChoice["groups"];
  excluded: string[];
  character: BpmChoice["character"];
  intensity: BpmChoice["intensity"];
  period: BpmChoice["period"];
  parRhythm: boolean;
  accent: number;
};

type FieldMark = {
  rev: string;
  pending: boolean;
  error: string | null;
  basis: number;
  value: unknown;
  /** Executor command that owns this press. Survives reload for addressable settle. */
  commandId?: string | null;
  intentMs?: number;
  /** Full bpm-sync body as sent for this press. Needed for idempotent replay. */
  body?: BpmCommand;
};

type IntentRecord = {
  seen: number;
  session: string | null;
  epoch: number | null;
  confirmed: BpmCommand | null;
  /** Full desired picture, including fields the user has not touched. */
  choice: BpmCommand | null;
  fields: Partial<Record<BpmField, FieldMark>>;
};

export const bpmIntentTick = ref(0);

const memory = new Map<string, IntentRecord>();
let lastIntentMs = 0;

function fieldToken(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

function nextIntentMs(): number {
  const now = Date.now();
  const intent = now > lastIntentMs ? now : lastIntentMs + 1;
  lastIntentMs = intent;
  return intent;
}

const CHOICE_FIELDS: Array<Exclude<BpmField, "enabled">> = [
  "scope",
  "mode",
  "palette",
  "cast",
  "groups",
  "excluded",
  "character",
  "intensity",
  "period",
  "parRhythm",
  "accent",
];

const ALL_FIELDS: BpmField[] = ["enabled", ...CHOICE_FIELDS];

function intentStorageKey(choiceKey: string): string {
  return `orng-bpm-intent:${choiceKey}`;
}

function blank(): IntentRecord {
  return { seen: -1, session: null, epoch: null, confirmed: null, choice: null, fields: {} };
}

function load(choiceKey: string): IntentRecord | null {
  const cached = memory.get(choiceKey);
  if (cached) return cached;
  try {
    const raw = JSON.parse(localStorage.getItem(intentStorageKey(choiceKey)) || "") as IntentRecord;
    if (raw && typeof raw.seen === "number" && raw.fields && typeof (raw as { enabled?: unknown }).enabled !== "boolean") {
      const record: IntentRecord = {
        seen: raw.seen,
        session: raw.session ?? null,
        epoch: typeof raw.epoch === "number" ? raw.epoch : null,
        confirmed: raw.confirmed ?? null,
        choice: raw.choice ?? raw.confirmed ?? null,
        fields: raw.fields,
      };
      memory.set(choiceKey, record);
      return record;
    }
    if (raw && typeof (raw as { enabled?: unknown }).enabled === "boolean") {
      const legacy = raw as unknown as { enabled: boolean; choice: BpmChoice; fields?: IntentRecord["fields"] };
      const record: IntentRecord = {
        seen: -1,
        session: null,
        epoch: null,
        confirmed: null,
        choice: null,
        fields: {},
      };
      for (const field of ALL_FIELDS) {
        const mark = legacy.fields?.[field];
        if (!mark?.pending) continue;
        record.fields[field] = {
          rev: String(mark.rev),
          pending: true,
          error: mark.error ?? null,
          basis: -1,
          value: field === "enabled" ? legacy.enabled : legacy.choice[field],
        };
      }
      memory.set(choiceKey, record);
      return record;
    }
  } catch {
    /* a damaged record falls back to the executor picture */
  }
  return null;
}

function persist(choiceKey: string, record: IntentRecord) {
  memory.set(choiceKey, record);
  const visible = record.choice ?? overlay(record.confirmed ?? commandFromChoice(readBpmChoice(choiceKey), false), record);
  writeBpmChoice(
    {
      scope: visible.scope,
      mode: visible.mode,
      palette: visible.palette,
      cast: visible.cast,
      groups: visible.groups,
      excluded: visible.excluded,
      character: visible.character,
      intensity: visible.intensity,
      period: visible.period,
      parRhythm: visible.parRhythm,
      accent: visible.accent,
    },
    choiceKey,
  );
  try {
    localStorage.setItem(
      intentStorageKey(choiceKey),
      JSON.stringify({
        seen: record.seen,
        session: record.session,
        epoch: record.epoch,
        confirmed: record.confirmed,
        choice: record.choice,
        fields: record.fields,
      }),
    );
  } catch {
    /* the in-memory choice still paints this visit */
  }
  bpmIntentTick.value += 1;
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (!event.key?.startsWith("orng-bpm-intent:")) return;
    memory.delete(event.key.slice("orng-bpm-intent:".length));
    bpmIntentTick.value += 1;
  });
}

export function choiceKeyFor(scope: { locationId?: string | null; sceneId?: string | null }): string {
  return bpmChoiceKey(scope);
}

function hasStoredIntent(choiceKey: string): boolean {
  return memory.has(choiceKey) || Boolean(localStorage.getItem(intentStorageKey(choiceKey)));
}

function commandFromChoice(choice: BpmChoice, enabled: boolean): BpmCommand {
  return {
    enabled,
    mode: choice.mode,
    scope: choice.scope,
    palette: choice.palette,
    cast: choice.cast,
    groups: [...choice.groups],
    excluded: [...choice.excluded],
    character: choice.character,
    intensity: choice.intensity,
    period: choice.period,
    parRhythm: choice.parRhythm,
    accent: choice.accent,
  };
}

function pictureFrom(raw: Partial<BpmSyncPatch> | null | undefined, fallback: BpmCommand): BpmCommand {
  const next = commandFromChoice(
    {
      scope: fallback.scope,
      mode: fallback.mode,
      palette: fallback.palette,
      cast: fallback.cast,
      groups: [...fallback.groups],
      excluded: [...fallback.excluded],
      character: fallback.character,
      intensity: fallback.intensity,
      period: fallback.period,
      parRhythm: fallback.parRhythm,
      accent: fallback.accent,
    },
    fallback.enabled,
  );
  if (!raw) return next;
  if (typeof raw.enabled === "boolean") next.enabled = raw.enabled;
  if (raw.scope === "auto" || raw.scope === "preset") next.scope = raw.scope;
  if (raw.mode) next.mode = raw.mode;
  if (raw.palette) next.palette = paletteFromApi(raw.palette, next.palette);
  if (raw.cast) next.cast = raw.cast;
  if (raw.groups) next.groups = normalizeGroups(raw.groups, next.cast);
  if (raw.excluded) next.excluded = [...raw.excluded];
  if (raw.character) next.character = raw.character;
  if (raw.intensity) next.intensity = raw.intensity;
  if (raw.period != null) next.period = asPeriod(raw.period, next.character);
  if (typeof raw.parRhythm === "boolean") next.parRhythm = raw.parRhythm;
  if (typeof raw.accent === "number") next.accent = raw.accent;
  return next;
}

function fieldValue(command: BpmCommand, field: BpmField): unknown {
  if (field === "enabled") return command.enabled;
  return command[field];
}

function writeField(command: BpmCommand, field: BpmField, value: unknown) {
  if (field === "enabled") {
    command.enabled = value === true;
    return;
  }
  if (field === "groups" || field === "excluded") {
    command[field] = Array.isArray(value) ? [...value] : command[field];
    return;
  }
  (command as unknown as Record<string, unknown>)[field] = value;
}

function overlay(base: BpmCommand, record: IntentRecord): BpmCommand {
  const next = pictureFrom(null, base);
  for (const field of ALL_FIELDS) {
    const mark = record.fields[field];
    if (!mark?.pending && !mark?.error) continue;
    writeField(next, field, mark.value);
  }
  return next;
}

function revisionOf(picture: Partial<BpmSyncPatch> | null | undefined): number | null {
  const revision = picture?.revision;
  return typeof revision === "number" && Number.isFinite(revision) ? revision : null;
}

function epochOf(picture: Partial<BpmSyncPatch> | null | undefined): number | null {
  const epoch = picture?.epoch;
  return typeof epoch === "number" && Number.isFinite(epoch) ? epoch : null;
}

function ensure(choiceKey: string): IntentRecord {
  return load(choiceKey) ?? blank();
}

/** Adopt a genuinely newer executor picture. A matching value does not finish a different press. */
export function confirmBpmFromTelemetry(
  choiceKey: string,
  snapshot: Partial<BpmSyncPatch> | null | undefined,
) {
  if (!snapshot) return;
  const revision = revisionOf(snapshot);
  const epoch = epochOf(snapshot);
  const session = typeof snapshot.session === "string" && snapshot.session ? snapshot.session : null;
  if (revision === null && !session && epoch === null) return;
  const existing = load(choiceKey);
  const record = existing ?? blank();
  if (epoch !== null && record.epoch !== null && epoch < record.epoch) return;
  const newerProcess = epoch !== null && (record.epoch === null || epoch > record.epoch);
  const sessionChanged = Boolean(session && record.session && session !== record.session);
  if (!newerProcess && sessionChanged) return;
  if (!newerProcess && (revision === null || revision <= record.seen)) return;
  const fallback = record.choice ?? record.confirmed ?? commandFromChoice(readBpmChoice(choiceKey), false);
  const reported = pictureFrom(snapshot, fallback);
  if (!record.confirmed) record.confirmed = pictureFrom(null, fallback);
  if (!record.choice) record.choice = pictureFrom(null, record.confirmed);
  for (const field of ALL_FIELDS) {
    const mark = record.fields[field];
    const value = fieldValue(reported, field);
    if (mark?.pending) {
      // The executor picture may be recorded, but only finishBpmChange for this
      // press's own rev may drop the pending mark. An older ON ack matches a
      // later ON press and must not confirm it.
      if (!record.confirmed) record.confirmed = pictureFrom(null, fallback);
      writeField(record.confirmed, field, value);
      continue;
    }
    writeField(record.confirmed, field, value);
    writeField(record.choice, field, value);
  }
  if (newerProcess) record.seen = revision ?? -1;
  else if (revision !== null) record.seen = revision;
  if (session) record.session = session;
  if (epoch !== null) record.epoch = epoch;
  persist(choiceKey, record);
}

/** Apply a user edit before any network wait. Untouched fields keep the newest executor picture. */
export function beginBpmChange(
  choiceKey: string,
  patch: BpmSyncPatch,
  seed?: Partial<BpmSyncPatch>,
): {
  rev: string;
  fieldRevs: Partial<Record<BpmField, string>>;
  command: BpmCommand;
  intentMs: number;
} {
  if (seed) confirmBpmFromTelemetry(choiceKey, seed);
  const record = ensure(choiceKey);
  const fallback = commandFromChoice(readBpmChoice(choiceKey), seed?.enabled === true);
  const base = overlay(record.choice ?? record.confirmed ?? pictureFrom(seed, fallback), record);
  const fieldRevs: Partial<Record<BpmField, string>> = {};
  let rev = "";
  const touch = (field: BpmField, value: unknown) => {
    rev = fieldToken();
    fieldRevs[field] = rev;
    record.fields[field] = { rev, pending: true, error: null, basis: record.seen, value };
  };
  if (patch.enabled !== undefined) {
    base.enabled = patch.enabled;
    touch("enabled", patch.enabled);
  }
  if (patch.scope !== undefined) {
    base.scope = patch.scope;
    touch("scope", patch.scope);
  }
  if (patch.mode !== undefined) {
    base.mode = patch.mode;
    touch("mode", patch.mode);
  }
  if (patch.palette !== undefined) {
    base.palette = paletteFromApi(patch.palette, base.palette);
    touch("palette", base.palette);
  }
  if (patch.cast !== undefined) {
    base.cast = patch.cast;
    touch("cast", patch.cast);
  }
  if (patch.groups !== undefined) {
    base.groups = normalizeGroups(patch.groups, base.cast);
    touch("groups", [...base.groups]);
  }
  if (patch.excluded !== undefined) {
    base.excluded = [...patch.excluded];
    touch("excluded", [...base.excluded]);
  }
  if (patch.character !== undefined) {
    base.character = patch.character;
    touch("character", patch.character);
  }
  if (patch.intensity !== undefined) {
    base.intensity = patch.intensity;
    touch("intensity", patch.intensity);
  }
  if (patch.period !== undefined) {
    base.period = asPeriod(patch.period, base.character);
    touch("period", base.period);
  }
  if (patch.parRhythm !== undefined) {
    base.parRhythm = patch.parRhythm;
    touch("parRhythm", patch.parRhythm);
  }
  if (patch.accent !== undefined) {
    base.accent = patch.accent;
    touch("accent", patch.accent);
  }
  const intentMs = nextIntentMs();
  const body = pictureFrom(null, base);
  for (const field of Object.keys(fieldRevs) as BpmField[]) {
    const mark = record.fields[field];
    if (!mark || mark.rev !== fieldRevs[field]) continue;
    mark.intentMs = intentMs;
    mark.body = body;
  }
  record.choice = body;
  persist(choiceKey, record);
  return { rev, fieldRevs, command: base, intentMs };
}

/** Attach the wire command_id after it is minted. Reload settle uses this id. */
export function bindBpmCommandId(
  choiceKey: string,
  fieldRevs: Partial<Record<BpmField, string>>,
  commandId: string,
) {
  const record = load(choiceKey);
  if (!record || !commandId) return;
  let changed = false;
  for (const [field, rev] of Object.entries(fieldRevs) as Array<[BpmField, string]>) {
    const mark = record.fields[field];
    if (!mark || mark.rev !== rev) continue;
    mark.commandId = commandId;
    changed = true;
  }
  if (changed) persist(choiceKey, record);
}

export type PendingBpmCommand = {
  commandId: string;
  fieldRevs: Partial<Record<BpmField, string>>;
  body: BpmCommand;
  intentMs: number;
};

/** Pending presses that still have a wire command_id after reload. */
export function listPendingBpmCommands(choiceKey: string): PendingBpmCommand[] {
  const record = load(choiceKey);
  if (!record) return [];
  const byId = new Map<string, PendingBpmCommand>();
  for (const [field, mark] of Object.entries(record.fields) as Array<[BpmField, FieldMark]>) {
    if (!mark?.pending || !mark.commandId || !mark.body) continue;
    let entry = byId.get(mark.commandId);
    if (!entry) {
      entry = {
        commandId: mark.commandId,
        fieldRevs: {},
        body: mark.body,
        intentMs: typeof mark.intentMs === "number" ? mark.intentMs : 0,
      };
      byId.set(mark.commandId, entry);
    }
    entry.fieldRevs[field] = mark.rev;
  }
  return [...byId.values()];
}

/**
 * Ask the wire for each stored command_id. Idempotent replay confirms without
 * re-applying; a lost ACK can complete. Call after reload when callbacks are gone.
 */
export async function settlePendingBpmCommands(
  choiceKey: string,
  settle: (command: PendingBpmCommand) => Promise<{
    ok: boolean;
    error?: string;
    revision?: number;
    epoch?: number;
    snapshot?: Partial<BpmSyncPatch> | null;
  }>,
): Promise<void> {
  for (const command of listPendingBpmCommands(choiceKey)) {
    const still = listPendingBpmCommands(choiceKey).find((row) => row.commandId === command.commandId);
    if (!still) continue;
    try {
      const result = await settle(command);
      if (result.snapshot) confirmBpmFromTelemetry(choiceKey, result.snapshot);
      finishBpmChange(
        choiceKey,
        still.fieldRevs,
        result.ok,
        result.error,
        result.revision,
        result.epoch,
      );
    } catch (err) {
      finishBpmChange(
        choiceKey,
        still.fieldRevs,
        false,
        err instanceof Error ? err.message : "Не підтверджено",
      );
    }
  }
}

/** A late result only settles the fields that still wear its revision. */
export function finishBpmChange(
  choiceKey: string,
  fieldRevs: Partial<Record<BpmField, string>>,
  ok: boolean,
  error?: string,
  revision?: number,
  epoch?: number,
) {
  const record = load(choiceKey);
  if (!record) return;
  const staleEpoch = typeof epoch === "number" && record.epoch !== null && epoch < record.epoch;
  const newerProcess = typeof epoch === "number" && (record.epoch === null || epoch > record.epoch);
  let changed = false;
  for (const [field, rev] of Object.entries(fieldRevs) as Array<[BpmField, string]>) {
    const mark = record.fields[field];
    if (!mark || mark.rev !== rev) continue;
    if (!ok) {
      mark.pending = true;
      mark.error = error || "Не підтверджено";
      changed = true;
      continue;
    }
    // Addressable success is not blocked by a newer sibling field's revision.
    // Only an older player process is ignored.
    if (staleEpoch) continue;
    if (!record.confirmed) record.confirmed = commandFromChoice(readBpmChoice(choiceKey), false);
    if (!record.choice) record.choice = pictureFrom(null, record.confirmed);
    const fresherConfirm = record.seen > mark.basis;
    if (!fresherConfirm) writeField(record.confirmed, field, mark.value);
    writeField(
      record.choice,
      field,
      fresherConfirm ? fieldValue(record.confirmed, field) : mark.value,
    );
    delete record.fields[field];
    changed = true;
    if (newerProcess && typeof epoch === "number") record.epoch = epoch;
    if (typeof revision === "number" && (newerProcess || revision > record.seen)) record.seen = revision;
  }
  if (!changed) return;
  persist(choiceKey, record);
}

export type BpmDeskView = {
  enabled: boolean;
  choice: BpmChoice;
  pending: boolean;
  error: string | null;
  owned: boolean;
};

export function bpmDesk(
  choiceKey: string,
  telemetry?: Partial<BpmSyncPatch> | null,
): BpmDeskView {
  void bpmIntentTick.value;
  const fallback = commandFromChoice(readBpmChoice(choiceKey), telemetry?.enabled === true);
  const record = hasStoredIntent(choiceKey) ? load(choiceKey) : null;
  const base = record?.choice ?? record?.confirmed ?? pictureFrom(telemetry, fallback);
  const shown = record ? overlay(base, record) : pictureFrom(telemetry, fallback);
  const marks = record ? Object.values(record.fields) : [];
  return {
    enabled: shown.enabled,
    choice: {
      scope: shown.scope,
      mode: shown.mode,
      palette: shown.palette,
      cast: shown.cast,
      groups: [...shown.groups],
      excluded: [...shown.excluded],
      character: shown.character,
      intensity: shown.intensity,
      period: shown.period,
      parRhythm: shown.parRhythm,
      accent: shown.accent,
    },
    pending: marks.some((mark) => mark?.pending),
    error: marks.find((mark) => mark?.error)?.error ?? null,
    owned: marks.some((mark) => mark?.pending || mark?.error),
  };
}

/** Drop an in-memory desk. Tests use this between cases. */
export function resetBpmIntent() {
  memory.clear();
  lastIntentMs = 0;
  bpmIntentTick.value += 1;
}
