<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  FALLBACK_CHANNEL_ROLES,
  PALETTE_KEYS,
  fetchChannelRoles,
  fetchPatch,
  fetchProfiles,
  saveProfile,
  type ChannelRoleOption,
} from "../api/setup";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { publishLocationDraft, savedLocationMessage, sceneSyncDetail, sceneSyncPhase } from "../runtime/scenePort";
import {
  channelDraftFrom,
  commitProfile,
  type ChannelDraft,
  type ChannelIssue,
} from "./channelCommit";

const FIXTURE_TYPES = [
  { id: "par", label: "PAR", kinds: ["par", "face_par"] as const },
  { id: "bar", label: "LED Bar", kinds: ["bar"] as const },
  { id: "beam", label: "Beam Head", kinds: ["beam"] as const },
] as const;

type FixtureTypeId = (typeof FIXTURE_TYPES)[number]["id"];

interface ChannelEdit extends ChannelDraft {
  controlOpen: boolean;
  paletteOpen: boolean;
  fieldError: Record<string, string>;
}

const CHANNEL_STASH = "orng-unsaved-channels:";
const appState = inject(APP_STATE_KEY, null);

const loading = ref(true);
const saving = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const dirty = ref(false);

const selectedType = ref<FixtureTypeId | "">("");
const selectedFixtureId = ref("");
const selectedProfileId = ref("");
const formProfileId = ref("");
const baselineJson = ref("");

const patch = ref<Record<string, unknown> | null>(null);
const profiles = ref<Record<string, Record<string, unknown>>>({});
const roleOptions = ref<ChannelRoleOption[]>([...FALLBACK_CHANNEL_ROLES]);
const channels = ref<ChannelEdit[]>([]);

const sceneKey = computed(() => String(appState?.state?.value?.scene_id || "local"));

const fixtures = computed(() => {
  const list = patch.value?.fixtures;
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
});

const typeMeta = computed(() => FIXTURE_TYPES.find((t) => t.id === selectedType.value) ?? null);

const fixturesOfType = computed(() => {
  const meta = typeMeta.value;
  if (!meta) return [] as Record<string, unknown>[];
  const kinds = new Set<string>(meta.kinds);
  return fixtures.value.filter((fx) => kinds.has(String(fx.kind ?? "")));
});

const selectedProfile = computed(() => {
  if (!selectedProfileId.value) return null;
  return profiles.value[selectedProfileId.value] ?? null;
});

const sharedFixtures = computed(() => {
  if (!selectedProfileId.value) return [] as Record<string, unknown>[];
  return fixtures.value.filter((fx) => String(fx.profile_id ?? "") === selectedProfileId.value);
});

const sharedFixtureNames = computed(() =>
  sharedFixtures.value.map((fx) => String(fx.label ?? fx.id)).join(", "),
);

const CONTROL_KEYS_BY_ROLE: Record<string, string[]> = {
  shutter: ["closed", "open"],
  strobe: ["closed", "open", "min", "max"],
  program: ["off"],
  effect_speed: ["off", "min", "max"],
  direction_mode: ["off", "neutral"],
  movement_speed: ["neutral"],
  color: ["off", "open"],
  whole_color: ["off"],
};

function needsPalette(role: string): boolean {
  return role === "whole_color" || role === "color" || role === "segment_color";
}

function needsControlValues(role: string): boolean {
  return role in CONTROL_KEYS_BY_ROLE;
}

function paletteKeys(ch: ChannelEdit): string[] {
  const extra = Object.keys(ch.paletteRaw).filter(
    (key) => !(PALETTE_KEYS as readonly string[]).includes(key),
  );
  return [...PALETTE_KEYS, ...extra];
}

function controlKeys(ch: ChannelEdit): string[] {
  const known = CONTROL_KEYS_BY_ROLE[ch.role] ?? [];
  const extra = Object.keys(ch.controlRaw).filter((key) => !known.includes(key));
  return [...known, ...extra];
}

function showPalette(ch: ChannelEdit): boolean {
  return needsPalette(ch.role) || Object.keys(ch.paletteRaw).length > 0;
}

function showControl(ch: ChannelEdit): boolean {
  return needsControlValues(ch.role) || Object.keys(ch.controlRaw).length > 0;
}

function profileChannelCount(profile: Record<string, unknown> | null): number {
  const rawList = Array.isArray(profile?.channels)
    ? (profile.channels as Record<string, unknown>[])
    : [];
  const locals = rawList
    .map((raw) => Number(raw.local))
    .filter((local) => Number.isFinite(local) && local >= 1);
  if (locals.length === 0) return rawList.length;
  return Math.max(rawList.length, ...locals);
}

function asEdit(draft: ChannelDraft): ChannelEdit {
  return { ...draft, controlOpen: false, paletteOpen: false, fieldError: {} };
}

function loadChannelsFromProfile(profileId: string) {
  const profile = profiles.value[profileId];
  const rawList = Array.isArray(profile?.channels)
    ? (profile.channels as Record<string, unknown>[])
    : [];
  const byLocal = new Map<number, Record<string, unknown>>();
  for (const raw of rawList) {
    const local = Number(raw.local);
    if (Number.isFinite(local) && local >= 1) byLocal.set(local, raw);
  }
  const count = profileChannelCount(profile ?? null);
  const next: ChannelEdit[] = [];
  for (let local = 1; local <= count; local += 1) {
    next.push(asEdit(channelDraftFrom(byLocal.get(local), local)));
  }
  channels.value = next;
}

function showProfile(profileId: string) {
  selectedProfileId.value = profileId;
  formProfileId.value = profileId;
  loadChannelsFromProfile(profileId);
  baselineJson.value = JSON.stringify(profiles.value[profileId] ?? null);
  dirty.value = false;
}

function pickProfileForType(typeId: FixtureTypeId): string {
  const meta = FIXTURE_TYPES.find((t) => t.id === typeId);
  if (!meta) return "";
  const kinds = new Set<string>(meta.kinds);
  const matches = Object.values(profiles.value).filter((p) => kinds.has(String(p.kind ?? "")));
  const preferred = matches.find((p) => String(p.kind) === typeId) ?? matches[0];
  return preferred ? String(preferred.id) : "";
}

function setStatus(ok: string | null, err: string | null = null) {
  message.value = ok;
  error.value = err;
}

function markDirty() {
  dirty.value = true;
  message.value = null;
}

function onRoleChange(ch: ChannelEdit, role: string) {
  ch.role = role;
  ch.fieldError = {};
  markDirty();
}

function setRaw(ch: ChannelEdit, key: "fixedRaw" | "segmentRaw", value: string) {
  ch[key] = value;
  ch.fieldError = {};
  markDirty();
}

function setBag(ch: ChannelEdit, bag: "paletteRaw" | "controlRaw", key: string, value: string) {
  ch[bag] = { ...ch[bag], [key]: value };
  ch.fieldError = {};
  markDirty();
}

function applyIssues(issues: ChannelIssue[]) {
  const byLocal = new Map(issues.map((issue) => [issue.local, issue]));
  for (const ch of channels.value) {
    const issue = byLocal.get(ch.local);
    ch.fieldError = issue ? { [issue.field]: issue.message } : {};
  }
}

function selectType(typeId: FixtureTypeId) {
  if (typeId === selectedType.value) return;
  if (dirty.value) {
    setStatus(null, "Є незбережені зміни профілю. Спершу збережіть їх або поверніть збережене.");
    return;
  }
  selectedType.value = typeId;
  selectedFixtureId.value = "";
  const profileId = pickProfileForType(typeId);
  if (profileId) showProfile(profileId);
  else {
    selectedProfileId.value = "";
    formProfileId.value = "";
    channels.value = [];
  }
}

function selectFixture(id: string) {
  if (id === selectedFixtureId.value) return;
  const fx = fixturesOfType.value.find((item) => String(item.id) === id);
  const profileId = String(fx?.profile_id ?? "");
  if (dirty.value && profileId !== formProfileId.value) {
    setStatus(null, "Є незбережені зміни профілю. Спершу збережіть їх або поверніть збережене.");
    return;
  }
  selectedFixtureId.value = id;
  if (profileId && profileId !== formProfileId.value && profiles.value[profileId]) {
    showProfile(profileId);
  }
}

function formStamp(): string {
  return JSON.stringify(channels.value.map((ch) => ({
    local: ch.local,
    role: ch.role,
    label: ch.label,
    fixedRaw: ch.fixedRaw,
    segmentRaw: ch.segmentRaw,
    paletteRaw: ch.paletteRaw,
    controlRaw: ch.controlRaw,
  })));
}

function writeStash(key: string) {
  if (!dirty.value || !formProfileId.value) return;
  sessionStorage.setItem(CHANNEL_STASH + key, JSON.stringify({
    type: selectedType.value,
    fixtureId: selectedFixtureId.value,
    profileId: formProfileId.value,
    channels: channels.value,
  }));
}

function readStash(key: string): {
  type: FixtureTypeId | "";
  fixtureId: string;
  profileId: string;
  channels: ChannelEdit[];
} | null {
  const raw = sessionStorage.getItem(CHANNEL_STASH + key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as {
      type?: FixtureTypeId | "";
      fixtureId?: string;
      profileId?: string;
      channels?: ChannelEdit[];
    };
    if (!parsed.profileId || !Array.isArray(parsed.channels)) return null;
    return {
      type: parsed.type ?? "",
      fixtureId: parsed.fixtureId ?? "",
      profileId: parsed.profileId,
      channels: parsed.channels,
    };
  } catch {
    return null;
  }
}

function clearStash(key: string) {
  sessionStorage.removeItem(CHANNEL_STASH + key);
}

async function saveMapping() {
  const profile = selectedProfile.value;
  const profileId = selectedProfileId.value;
  if (!profile || !profileId || saving.value) {
    if (!profile || !profileId) setStatus(null, "Немає профілю для збереження");
    return;
  }
  saving.value = true;
  try {
    const prepared = commitProfile(profile, channels.value);
    if (!prepared.ok) {
      applyIssues(prepared.issues);
      setStatus(null, prepared.issues[0]?.message ?? "Виправте помилки до збереження");
      return;
    }
    const remoteProfiles = await fetchProfiles();
    const remote = remoteProfiles[profileId];
    if (JSON.stringify(remote ?? null) !== baselineJson.value) {
      setStatus(null, "З’явилась новіша сцена. Правки на сторінці не замінені.");
      return;
    }
    const writing = commitProfile(profile, channels.value);
    if (!writing.ok) {
      applyIssues(writing.issues);
      setStatus(null, writing.issues[0]?.message ?? "Виправте помилки до збереження");
      return;
    }
    const stamp = formStamp();
    await saveProfile(profileId, writing.profile);
    profiles.value = { ...profiles.value, [profileId]: writing.profile };
    baselineJson.value = JSON.stringify(writing.profile);
    await publishLocationDraft();
    if (formStamp() !== stamp) {
      const newer = commitProfile(profiles.value[profileId] ?? writing.profile, channels.value);
      if (!newer.ok) {
        applyIssues(newer.issues);
        dirty.value = true;
        setStatus(null, "Опубліковано попередні правки. Новіші ще мають помилку.");
        return;
      }
      await saveProfile(profileId, newer.profile);
      profiles.value = { ...profiles.value, [profileId]: newer.profile };
      dirty.value = true;
      writeStash(sceneKey.value);
      setStatus("Опубліковано попередні правки. Новіші лишились у чернетці.");
      return;
    }
    if (sceneSyncPhase.value === "conflict" || sceneSyncPhase.value === "failed") {
      dirty.value = true;
      setStatus(null, sceneSyncDetail.value || "Публікація не вдалася");
      return;
    }
    dirty.value = false;
    clearStash(sceneKey.value);
    applyIssues([]);
    loadChannelsFromProfile(profileId);
    setStatus(savedLocationMessage());
  } catch (err) {
    dirty.value = true;
    const failed = sceneSyncPhase.value === "conflict" || sceneSyncPhase.value === "failed";
    setStatus(null, failed
      ? (sceneSyncDetail.value || "Публікація не вдалася")
      : (err instanceof Error ? err.message : "Не вдалося зберегти мапінг"));
  } finally {
    saving.value = false;
  }
}

function restoreSaved() {
  clearStash(sceneKey.value);
  error.value = null;
  message.value = null;
  if (formProfileId.value && profiles.value[formProfileId.value]) {
    showProfile(formProfileId.value);
  } else {
    dirty.value = false;
  }
}

async function reload(options?: { keepForm?: boolean }) {
  if (options?.keepForm && dirty.value) {
    try {
      const remoteProfiles = await fetchProfiles();
      const remote = formProfileId.value ? remoteProfiles[formProfileId.value] : null;
      if (formProfileId.value && JSON.stringify(remote ?? null) !== baselineJson.value) {
        error.value = "З’явилась новіша сцена. Несохранені правки лишились на сторінці.";
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Не вдалося завантажити конфіг";
    }
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    const [patchCfg, profileMap, roles] = await Promise.all([
      fetchPatch(),
      fetchProfiles(),
      fetchChannelRoles(),
    ]);
    patch.value = patchCfg;
    profiles.value = profileMap;
    roleOptions.value = roles;
    const stashed = readStash(sceneKey.value);
    if (stashed && profileMap[stashed.profileId]) {
      selectedType.value = stashed.type;
      selectedFixtureId.value = stashed.fixtureId;
      selectedProfileId.value = stashed.profileId;
      formProfileId.value = stashed.profileId;
      channels.value = stashed.channels;
      baselineJson.value = JSON.stringify(profileMap[stashed.profileId] ?? null);
      dirty.value = true;
    }
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося завантажити конфіг");
  } finally {
    loading.value = false;
  }
}

watch(sceneKey, async (next, prev) => {
  if (!prev || next === prev) return;
  if (dirty.value) writeStash(prev);
  dirty.value = false;
  channels.value = [];
  selectedType.value = "";
  selectedFixtureId.value = "";
  selectedProfileId.value = "";
  formProfileId.value = "";
  await reload();
});

onMounted(() => {
  void reload();
});

onBeforeUnmount(() => {
  if (dirty.value) writeStash(sceneKey.value);
});
</script>

<template>
  <div class="channels-view setup-view">
    <header class="page-title">
      <h1>Канали</h1>
    </header>

    <p
      v-if="loading"
      class="banner"
    >
      Завантаження…
    </p>

    <p
      v-if="message"
      class="banner banner--ok"
      role="status"
    >
      {{ message }}
    </p>
    <p
      v-if="error"
      class="banner banner--error"
      role="alert"
    >
      {{ error }}
      <button
        type="button"
        class="btn btn--sm"
        style="margin-left: 12px"
        @click="reload({ keepForm: true })"
      >
        Спробувати знову
      </button>
    </p>

    <section
      class="card"
      aria-label="Тип приладу"
    >
      <div
        class="pick-row"
        role="group"
        aria-label="Тип приладу"
      >
        <button
          v-for="t in FIXTURE_TYPES"
          :key="t.id"
          type="button"
          class="action-btn"
          :class="{ active: selectedType === t.id }"
          :aria-pressed="selectedType === t.id"
          @click="selectType(t.id)"
        >
          {{ t.label }}
        </button>
      </div>

      <div
        v-if="selectedType && fixturesOfType.length"
        class="pick-row"
        role="group"
        aria-label="Прилад"
      >
        <button
          v-for="fx in fixturesOfType"
          :key="String(fx.id)"
          type="button"
          class="action-btn"
          :class="{ active: selectedFixtureId === String(fx.id) }"
          :aria-pressed="selectedFixtureId === String(fx.id)"
          @click="selectFixture(String(fx.id))"
        >
          {{ fx.label ?? fx.id }}
        </button>
      </div>
      <p
        v-if="selectedProfileId && sharedFixtures.length"
        class="hint"
      >
        Профіль {{ selectedProfileId }} змінить: {{ sharedFixtureNames }}
      </p>
    </section>

    <section
      v-if="channels.length"
      class="card"
      aria-label="Канали"
    >
      <div class="row-actions channels-actions">
        <button
          type="button"
          class="action-btn"
          :disabled="saving || !selectedProfileId"
          @click="saveMapping"
        >
          Зберегти мапінг
        </button>
        <button
          v-if="dirty"
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="restoreSaved"
        >
          Повернути збережене
        </button>
      </div>

      <div
        class="channel-list"
        data-testid="channel-list"
      >
        <article
          v-for="ch in channels"
          :key="ch.local"
          class="channel-row"
          :data-local="ch.local"
        >
          <div class="channel-row__head">
            <strong class="channel-row__ch">{{ ch.local }}</strong>
          </div>

          <div class="channel-row__controls">
            <label class="field">
              <span>Функція каналу</span>
              <select
                :value="ch.role"
                @change="onRoleChange(ch, ($event.target as HTMLSelectElement).value)"
              >
                <option
                  v-for="opt in roleOptions"
                  :key="opt.role"
                  :value="opt.role"
                >
                  {{ opt.label }}
                </option>
              </select>
            </label>
          </div>

          <div
            v-if="ch.role === 'fixed'"
            class="channel-row__extra"
          >
            <label class="field field--inline">
              <span>Фіксоване значення</span>
              <input
                type="text"
                inputmode="numeric"
                :value="ch.fixedRaw"
                @input="setRaw(ch, 'fixedRaw', ($event.target as HTMLInputElement).value)"
              >
            </label>
            <p
              v-if="ch.fieldError.fixed"
              class="hint"
              role="alert"
            >
              {{ ch.fieldError.fixed }}
            </p>
          </div>

          <div
            v-if="ch.role === 'segment' || ch.role === 'segment_color'"
            class="channel-row__extra"
          >
            <label class="field field--inline">
              <span>Індекс сегмента</span>
              <input
                type="text"
                inputmode="numeric"
                :value="ch.segmentRaw"
                @input="setRaw(ch, 'segmentRaw', ($event.target as HTMLInputElement).value)"
              >
            </label>
            <p
              v-if="ch.fieldError.segment_index"
              class="hint"
              role="alert"
            >
              {{ ch.fieldError.segment_index }}
            </p>
          </div>

          <div
            v-if="showControl(ch)"
            class="channel-row__palette"
          >
            <button
              type="button"
              class="action-btn"
              @click="ch.controlOpen = !ch.controlOpen"
            >
              {{ ch.controlOpen ? "Сховати робочі значення" : "Робочі значення функції" }}
            </button>
            <div
              v-if="ch.controlOpen"
              class="palette-grid"
            >
              <label
                v-for="key in controlKeys(ch)"
                :key="key"
                class="field field--inline"
              >
                <span>{{ key }}</span>
                <input
                  type="text"
                  inputmode="numeric"
                  :value="ch.controlRaw[key] ?? ''"
                  @input="setBag(ch, 'controlRaw', key, ($event.target as HTMLInputElement).value)"
                >
                <p
                  v-if="ch.fieldError[`control_values:${key}`]"
                  class="hint"
                  role="alert"
                >
                  {{ ch.fieldError[`control_values:${key}`] }}
                </p>
              </label>
            </div>
          </div>

          <div
            v-if="showPalette(ch)"
            class="channel-row__palette"
          >
            <button
              type="button"
              class="action-btn"
              @click="ch.paletteOpen = !ch.paletteOpen"
            >
              {{ ch.paletteOpen ? "Сховати палітру" : "Палітра кольорів" }}
            </button>
            <div
              v-if="ch.paletteOpen"
              class="palette-grid"
            >
              <label
                v-for="key in paletteKeys(ch)"
                :key="key"
                class="field field--inline"
              >
                <span>{{ key }}</span>
                <input
                  type="text"
                  inputmode="numeric"
                  :value="ch.paletteRaw[key] ?? ''"
                  @input="setBag(ch, 'paletteRaw', key, ($event.target as HTMLInputElement).value)"
                >
                <p
                  v-if="ch.fieldError[`palette:${key}`]"
                  class="hint"
                  role="alert"
                >
                  {{ ch.fieldError[`palette:${key}`] }}
                </p>
              </label>
            </div>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
