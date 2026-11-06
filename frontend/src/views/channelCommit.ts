import { parseBoundedInt } from "../runtime/sceneValidate";

export interface ChannelDraft {
  local: number;
  role: string;
  label: string;
  fixedRaw: string;
  segmentRaw: string;
  paletteRaw: Record<string, string>;
  controlRaw: Record<string, string>;
  source: Record<string, unknown>;
}

export interface ChannelIssue {
  local: number;
  field: string;
  message: string;
}

function textOf(value: unknown): string {
  return value == null ? "" : String(value);
}

export function channelDraftFrom(raw: Record<string, unknown> | undefined, local: number): ChannelDraft {
  const paletteRaw: Record<string, string> = {};
  if (raw?.palette && typeof raw.palette === "object") {
    for (const [key, value] of Object.entries(raw.palette as Record<string, unknown>)) {
      paletteRaw[key] = textOf(value);
    }
  }
  const controlRaw: Record<string, string> = {};
  if (raw?.control_values && typeof raw.control_values === "object") {
    for (const [key, value] of Object.entries(raw.control_values as Record<string, unknown>)) {
      controlRaw[key] = textOf(value);
    }
  }
  return {
    local,
    role: String(raw?.role ?? "unused"),
    label: raw?.label != null ? String(raw.label) : "",
    fixedRaw: textOf(raw?.fixed_value),
    segmentRaw: textOf(raw?.segment_index),
    paletteRaw,
    controlRaw,
    source: raw ? { ...raw } : {},
  };
}

function untouchedGap(draft: ChannelDraft): boolean {
  return (
    Object.keys(draft.source).length === 0 &&
    draft.role === "unused" &&
    draft.fixedRaw.trim() === "" &&
    draft.segmentRaw.trim() === "" &&
    Object.keys(draft.paletteRaw).length === 0 &&
    Object.keys(draft.controlRaw).length === 0
  );
}

function mergeBag(
  draft: ChannelDraft,
  sourceKey: "palette" | "control_values",
  raw: Record<string, string>,
  label: string,
): { ok: true; value: Record<string, number> | undefined } | { ok: false; issue: ChannelIssue } {
  const sourceBag = draft.source[sourceKey];
  const base: Record<string, number> = {};
  if (sourceBag && typeof sourceBag === "object") {
    for (const [key, value] of Object.entries(sourceBag as Record<string, unknown>)) {
      if (typeof value === "number") base[key] = value;
    }
  }
  const keys = new Set([...Object.keys(base), ...Object.keys(raw)]);
  for (const key of keys) {
    const text = (raw[key] ?? "").trim();
    const sourceHad = sourceBag && typeof sourceBag === "object" && key in (sourceBag as object);
    if (text === "" && !sourceHad) continue;
    const parsed = parseBoundedInt(text, 0, 255);
    if (parsed == null) {
      return {
        ok: false,
        issue: {
          local: draft.local,
          field: `${sourceKey}:${key}`,
          message: `Канал ${draft.local}: ${label} ${key} має бути цілим 0..255`,
        },
      };
    }
    base[key] = parsed;
  }
  return { ok: true, value: Object.keys(base).length ? base : undefined };
}

export function commitChannel(
  draft: ChannelDraft,
  segmentCount: number | null,
): { ok: true; entry: Record<string, unknown> } | { ok: false; issue: ChannelIssue } {
  const entry: Record<string, unknown> = {
    ...draft.source,
    local: draft.local,
    role: draft.role,
    label: draft.label === "" && draft.source.label == null ? null : draft.label,
  };
  const needsSegment = draft.role === "segment" || draft.role === "segment_color";
  if (draft.segmentRaw.trim() !== "") {
    const parsed = parseBoundedInt(draft.segmentRaw, 1, 8);
    if (parsed == null) {
      return {
        ok: false,
        issue: {
          local: draft.local,
          field: "segment_index",
          message: `Канал ${draft.local}: індекс сегмента має бути цілим 1..8`,
        },
      };
    }
    if (segmentCount != null && segmentCount > 0 && parsed > segmentCount) {
      return {
        ok: false,
        issue: {
          local: draft.local,
          field: "segment_index",
          message: `Канал ${draft.local}: індекс сегмента поза 1..${segmentCount}`,
        },
      };
    }
    entry.segment_index = parsed;
  } else if (needsSegment && typeof draft.source.segment_index !== "number") {
    return {
      ok: false,
      issue: {
        local: draft.local,
        field: "segment_index",
        message: `Канал ${draft.local}: індекс сегмента має бути цілим 1..8`,
      },
    };
  }
  if (draft.role === "fixed") {
    const raw = draft.fixedRaw.trim();
    if (raw === "" && typeof draft.source.fixed_value === "number") {
      entry.fixed_value = draft.source.fixed_value;
    } else {
      const parsed = parseBoundedInt(raw, 0, 255);
      if (parsed == null) {
        return {
          ok: false,
          issue: {
            local: draft.local,
            field: "fixed",
            message: `Канал ${draft.local}: фіксоване значення має бути цілим 0..255`,
          },
        };
      }
      entry.fixed_value = parsed;
    }
  }
  const palette = mergeBag(draft, "palette", draft.paletteRaw, "палітра");
  if (!palette.ok) return palette;
  if (palette.value) entry.palette = palette.value;
  const control = mergeBag(draft, "control_values", draft.controlRaw, "значення");
  if (!control.ok) return control;
  if (control.value) entry.control_values = control.value;
  return { ok: true, entry };
}

export function commitProfile(
  profile: Record<string, unknown>,
  drafts: ChannelDraft[],
): { ok: true; profile: Record<string, unknown> } | { ok: false; issues: ChannelIssue[] } {
  const segmentCount = typeof profile.segment_count === "number" && Number.isInteger(profile.segment_count)
    ? profile.segment_count
    : null;
  const issues: ChannelIssue[] = [];
  const channels: Record<string, unknown>[] = [];
  for (const draft of drafts) {
    if (untouchedGap(draft)) continue;
    const result = commitChannel(draft, segmentCount);
    if (!result.ok) issues.push(result.issue);
    else channels.push(result.entry);
  }
  if (issues.length) return { ok: false, issues };
  return { ok: true, profile: { ...profile, channels } };
}
