<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from "vue";
import {
  DEFAULT_CHANNEL_PALETTE,
  FALLBACK_CHANNEL_ROLES,
  PALETTE_KEYS,
  fetchChannelRoles,
  fetchPatch,
  fetchProfiles,
  saveProfile,
  setupPost,
  type ChannelRoleOption,
} from "../api/setup";
import { APP_STATE_KEY } from "../composables/appStateKey";

const appState = inject(APP_STATE_KEY, null);

const FIXTURE_TYPES = [
  { id: "par", label: "PAR", kinds: ["par", "face_par"] as const, footprint: 7 },
  { id: "bar", label: "LED Bar", kinds: ["bar"] as const, footprint: 15 },
  { id: "beam", label: "Beam Head", kinds: ["beam"] as const, footprint: 13 },
] as const;

type FixtureTypeId = (typeof FIXTURE_TYPES)[number]["id"];

interface ChannelEdit {
  local: number;
  role: string;
  label: string;
  fixed_value: number | null;
  segment_index: number | null;
  palette: Record<string, number> | null;
  control_values: Record<string, number> | null;
  controlOpen: boolean;
  testValue: number;
  paletteOpen: boolean;
}

const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);

const selectedType = ref<FixtureTypeId | "">("");
const selectedFixtureId = ref("");
const selectedProfileId = ref("");

const patch = ref<Record<string, unknown> | null>(null);
const profiles = ref<Record<string, Record<string, unknown>>>({});
const roleOptions = ref<ChannelRoleOption[]>([...FALLBACK_CHANNEL_ROLES]);
const channels = ref<ChannelEdit[]>([]);

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

const selectedFixture = computed(
  () => fixturesOfType.value.find((fx) => String(fx.id) === selectedFixtureId.value) ?? null,
);

const selectedProfile = computed(() => {
  if (!selectedProfileId.value) return null;
  return profiles.value[selectedProfileId.value] ?? null;
});

const roleLabelMap = computed(() => {
  const map = new Map<string, string>();
  for (const opt of roleOptions.value) {
    map.set(opt.role, opt.label);
  }
  return map;
});

function clampByte(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(255, Math.round(value)));
}

function emptyChannel(local: number): ChannelEdit {
  return {
    local,
    role: "unused",
    label: "Не призначено",
    fixed_value: null,
    segment_index: null,
    palette: null,
    control_values: null,
    controlOpen: false,
    testValue: 0,
    paletteOpen: false,
  };
}

function channelFromProfile(raw: Record<string, unknown>, local: number): ChannelEdit {
  const paletteRaw = raw.palette;
  let palette: Record<string, number> | null = null;
  if (paletteRaw && typeof paletteRaw === "object") {
    palette = { ...DEFAULT_CHANNEL_PALETTE };
    for (const key of PALETTE_KEYS) {
      const v = (paletteRaw as Record<string, unknown>)[key];
      if (typeof v === "number") palette[key] = clampByte(v);
    }
  }
  const controlRaw = raw.control_values;
  let control_values: Record<string, number> | null = null;
  if (controlRaw && typeof controlRaw === "object") {
    control_values = {};
    for (const [key, value] of Object.entries(controlRaw as Record<string, unknown>)) {
      if (typeof value === "number") control_values[key] = clampByte(value);
    }
  }
  return {
    local,
    role: String(raw.role ?? "unused"),
    label: raw.label != null ? String(raw.label) : "",
    fixed_value: raw.fixed_value == null ? null : clampByte(Number(raw.fixed_value)),
    segment_index: raw.segment_index == null ? null : Number(raw.segment_index),
    palette,
    control_values,
    controlOpen: false,
    testValue: 0,
    paletteOpen: false,
  };
}

function ensurePalette(ch: ChannelEdit): Record<string, number> {
  if (!ch.palette) {
    ch.palette = { ...DEFAULT_CHANNEL_PALETTE };
  }
  return ch.palette;
}

function needsPalette(role: string, hasPalette: boolean): boolean {
  return (
    role === "whole_color" ||
    role === "color" ||
    role === "segment_color" ||
    hasPalette
  );
}

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

function needsControlValues(role: string): boolean {
  return role in CONTROL_KEYS_BY_ROLE;
}

function ensureControlValues(ch: ChannelEdit): Record<string, number> {
  const keys = CONTROL_KEYS_BY_ROLE[ch.role] ?? [];
  if (!ch.control_values) ch.control_values = {};
  for (const key of keys) {
    if (ch.control_values[key] == null) {
      ch.control_values[key] =
        key === "open" || key === "max" ? 255 : key === "neutral" ? 128 : key === "min" ? 32 : 0;
    }
  }
  return ch.control_values;
}

function loadChannelsFromProfile(profileId: string, footprint: number) {
  const profile = profiles.value[profileId];
  const rawList = Array.isArray(profile?.channels)
    ? (profile!.channels as Record<string, unknown>[])
    : [];
  const byLocal = new Map<number, Record<string, unknown>>();
  for (const raw of rawList) {
    const local = Number(raw.local);
    if (Number.isFinite(local) && local >= 1) byLocal.set(local, raw);
  }
  const next: ChannelEdit[] = [];
  for (let local = 1; local <= footprint; local += 1) {
    const raw = byLocal.get(local);
    next.push(raw ? channelFromProfile(raw, local) : emptyChannel(local));
  }
  channels.value = next;
}

function pickProfileForType(typeId: FixtureTypeId): string {
  const meta = FIXTURE_TYPES.find((t) => t.id === typeId);
  if (!meta) return "";
  const kinds = new Set<string>(meta.kinds);
  const matches = Object.values(profiles.value).filter((p) => {
    const kind = String(p.kind ?? "");
    const footprint = Number(p.footprint ?? 0);
    return kinds.has(kind) && footprint === meta.footprint;
  });
  if (matches.length === 0) {
    const loose = Object.values(profiles.value).filter((p) => kinds.has(String(p.kind ?? "")));
    return loose[0] ? String(loose[0].id) : "";
  }
  // Prefer rear PAR over face_par when type is PAR.
  const preferred = matches.find((p) => String(p.kind) === typeId) ?? matches[0];
  return preferred ? String(preferred.id) : "";
}

function setStatus(ok: string | null, err: string | null = null) {
  message.value = ok;
  error.value = err;
}

function globalDmx(local: number): number | null {
  const fx = selectedFixture.value;
  if (!fx) return null;
  const start = Number(fx.start_address);
  if (!Number.isFinite(start) || start < 1) return null;
  return start + local - 1;
}

function assignmentLabel(ch: ChannelEdit): string {
  const roleLabel = roleLabelMap.value.get(ch.role) ?? ch.role;
  if (ch.label && ch.label.trim()) return `${roleLabel} · ${ch.label}`;
  return roleLabel;
}

async function refreshAfterTest() {
  await appState?.refreshRest?.();
}

async function beginTesting() {
  if (!selectedFixtureId.value) {
    setStatus(null, "Спочатку виберіть фізичний прилад");
    return;
  }
  try {
    await setupPost("fixture-channel-test/begin", { fixture_id: selectedFixtureId.value });
    testing.value = true;
    for (const ch of channels.value) ch.testValue = 0;
    await refreshAfterTest();
    setStatus("Тестування каналів розпочато");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося почати тестування");
  }
}

async function endTesting() {
  try {
    await setupPost("fixture-channel-test/end", {});
    testing.value = false;
    for (const ch of channels.value) ch.testValue = 0;
    await refreshAfterTest();
    setStatus("Тестування завершено");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося завершити тестування");
  }
}

async function resetAllTestValues() {
  if (!selectedFixtureId.value) return;
  try {
    if (testing.value) {
      await setupPost("fixture-channel-test/reset", { fixture_id: selectedFixtureId.value });
      await refreshAfterTest();
    }
    for (const ch of channels.value) ch.testValue = 0;
    setStatus("Усі тестові значення скинуто в 0");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося скинути значення");
  }
}

async function resetOneChannel(ch: ChannelEdit) {
  ch.testValue = 0;
  if (!testing.value || !selectedFixtureId.value) return;
  try {
    await setupPost("fixture-channel-test/set", {
      fixture_id: selectedFixtureId.value,
      local: ch.local,
      value: 0,
    });
    await refreshAfterTest();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося скинути канал");
  }
}

async function onTestValueChange(ch: ChannelEdit, raw: number) {
  ch.testValue = clampByte(raw);
  if (!testing.value || !selectedFixtureId.value) return;
  try {
    await setupPost("fixture-channel-test/set", {
      fixture_id: selectedFixtureId.value,
      local: ch.local,
      value: ch.testValue,
    });
    await refreshAfterTest();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося встановити канал");
  }
}

function onRoleChange(ch: ChannelEdit, role: string) {
  ch.role = role;
  if (role === "fixed" && ch.fixed_value == null) {
    ch.fixed_value = 0;
  }
  if (role !== "fixed") {
    ch.fixed_value = null;
  }
  if (needsPalette(role, Boolean(ch.palette))) {
    ensurePalette(ch);
  } else if (role !== "whole_color" && role !== "color" && role !== "segment_color") {
    ch.palette = null;
    ch.paletteOpen = false;
  }
  if (needsControlValues(role)) {
    ensureControlValues(ch);
  } else {
    ch.control_values = null;
    ch.controlOpen = false;
  }
  if (role === "segment" || role === "segment_color") {
    if (ch.segment_index == null) ch.segment_index = 1;
  }
}

async function saveMapping() {
  const profile = selectedProfile.value;
  const profileId = selectedProfileId.value;
  if (!profile || !profileId) {
    setStatus(null, "Немає профілю для збереження");
    return;
  }
  saving.value = true;
  try {
    const nextChannels = channels.value.map((ch) => {
      const entry: Record<string, unknown> = {
        local: ch.local,
        role: ch.role,
        label: ch.label || null,
      };
      if (ch.role === "fixed") {
        entry.fixed_value = clampByte(ch.fixed_value ?? 0);
      }
      if (ch.segment_index != null && (ch.role === "segment" || ch.role === "segment_color")) {
        entry.segment_index = ch.segment_index;
      }
      if (needsPalette(ch.role, Boolean(ch.palette)) && ch.palette) {
        const palette: Record<string, number> = {};
        for (const key of PALETTE_KEYS) {
          palette[key] = clampByte(ch.palette[key] ?? DEFAULT_CHANNEL_PALETTE[key]);
        }
        entry.palette = palette;
      }
      if (needsControlValues(ch.role) && ch.control_values) {
        const control: Record<string, number> = {};
        for (const key of CONTROL_KEYS_BY_ROLE[ch.role] ?? []) {
          if (ch.control_values[key] != null) control[key] = clampByte(ch.control_values[key]);
        }
        entry.control_values = control;
      }
      return entry;
    });
    const nextProfile = {
      ...profile,
      channels: nextChannels,
      hardware_verified: false,
    };
    await saveProfile(profileId, nextProfile);
    profiles.value = { ...profiles.value, [profileId]: nextProfile };
    setStatus(`Мапінг профілю ${profileId} збережено`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося зберегти мапінг");
  } finally {
    saving.value = false;
  }
}

watch(selectedType, (typeId) => {
  selectedFixtureId.value = "";
  if (!typeId) {
    selectedProfileId.value = "";
    channels.value = [];
    return;
  }
  const meta = FIXTURE_TYPES.find((t) => t.id === typeId);
  if (!meta) return;
  const profileId = pickProfileForType(typeId);
  selectedProfileId.value = profileId;
  if (profileId) {
    loadChannelsFromProfile(profileId, meta.footprint);
  } else {
    channels.value = Array.from({ length: meta.footprint }, (_, i) => emptyChannel(i + 1));
  }
});

watch(selectedFixtureId, (fixtureId) => {
  const meta = typeMeta.value;
  if (!meta || !fixtureId) return;
  const fx = fixturesOfType.value.find((f) => String(f.id) === fixtureId);
  if (!fx) return;
  const profileId = String(fx.profile_id ?? "");
  if (profileId && profiles.value[profileId]) {
    selectedProfileId.value = profileId;
    loadChannelsFromProfile(profileId, meta.footprint);
  }
});

onMounted(async () => {
  loading.value = true;
  try {
    const [patchCfg, profileMap, roles] = await Promise.all([
      fetchPatch(),
      fetchProfiles(),
      fetchChannelRoles(),
    ]);
    patch.value = patchCfg;
    profiles.value = profileMap;
    roleOptions.value = roles;
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося завантажити конфіг");
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="channels-view setup-view">
    <header class="card__head">
      <h1>Налаштування каналів</h1>
      <p class="card__sub">
        Призначення локальних DMX-каналів за типом приладу та живе тестування на
        фізичному фікстурі.
      </p>
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
    </p>

    <section
      class="card"
      aria-label="Тип приладу"
    >
      <label class="field">
        <span>Тип приладу</span>
        <select v-model="selectedType">
          <option value="">
            — оберіть —
          </option>
          <option
            v-for="t in FIXTURE_TYPES"
            :key="t.id"
            :value="t.id"
          >
            {{ t.label }} ({{ t.footprint }} ch)
          </option>
        </select>
      </label>

      <label
        v-if="selectedType"
        class="field"
      >
        <span>Фізичний прилад</span>
        <select v-model="selectedFixtureId">
          <option value="">
            — оберіть —
          </option>
          <option
            v-for="fx in fixturesOfType"
            :key="String(fx.id)"
            :value="String(fx.id)"
          >
            {{ fx.label ?? fx.id }} · {{ fx.id }} · addr {{ fx.start_address }}
          </option>
        </select>
      </label>

      <p
        v-if="selectedProfileId"
        class="hint"
      >
        Профіль: <strong>{{ selectedProfileId }}</strong>
        <template v-if="selectedFixture">
          · start {{ selectedFixture.start_address }}
        </template>
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
          :disabled="!selectedFixtureId || testing"
          @click="beginTesting"
        >
          Почати тестування
        </button>
        <button
          type="button"
          class="action-btn"
          :disabled="!selectedFixtureId"
          @click="resetAllTestValues"
        >
          Скинути всі значення в 0
        </button>
        <button
          type="button"
          class="action-btn"
          :disabled="!testing"
          @click="endTesting"
        >
          Завершити тестування
        </button>
        <button
          type="button"
          class="action-btn"
          :disabled="saving || !selectedProfileId"
          @click="saveMapping"
        >
          Зберегти мапінг
        </button>
      </div>

      <p
        v-if="selectedType === 'beam'"
        class="banner banner--warn"
        role="status"
      >
        Beam Head: невідомий канал може керувати рухом, програмою або reset. Після старту
        тестування рухайте кожен ползунок лише вручну.
      </p>

      <p
        v-if="testing"
        class="hint"
      >
        Тестування активне — можна одночасно тримати кілька ненульових ползунків (наприклад
        спочатку dimmer, потім колір). Значення <strong>не</strong> зберігаються в мапінг
        до «Зберегти мапінг».
      </p>

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
            <strong class="channel-row__ch">CH {{ ch.local }}</strong>
            <span
              v-if="globalDmx(ch.local) != null"
              class="channel-row__map"
            >
              → DMX {{ globalDmx(ch.local) }}
            </span>
            <span class="channel-row__assign">{{ assignmentLabel(ch) }}</span>
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

            <label class="field">
              <span>Тест 0–255</span>
              <input
                type="range"
                min="0"
                max="255"
                :value="ch.testValue"
                @input="onTestValueChange(ch, Number(($event.target as HTMLInputElement).value))"
              >
            </label>

            <label class="field field--inline">
              <span>Значення</span>
              <input
                type="number"
                min="0"
                max="255"
                :value="ch.testValue"
                @change="onTestValueChange(ch, Number(($event.target as HTMLInputElement).value))"
              >
            </label>

            <button
              type="button"
              class="action-btn"
              title="Скинути тестове значення в 0"
              @click="resetOneChannel(ch)"
            >
              0
            </button>
          </div>

          <div
            v-if="ch.role === 'fixed'"
            class="channel-row__extra"
          >
            <label class="field field--inline">
              <span>Фіксоване значення</span>
              <input
                v-model.number="ch.fixed_value"
                type="number"
                min="0"
                max="255"
              >
            </label>
          </div>

          <div
            v-if="ch.role === 'segment' || ch.role === 'segment_color'"
            class="channel-row__extra"
          >
            <label class="field field--inline">
              <span>Індекс сегмента</span>
              <input
                v-model.number="ch.segment_index"
                type="number"
                min="1"
                max="8"
              >
            </label>
          </div>

          <div
            v-if="needsControlValues(ch.role)"
            class="channel-row__palette"
          >
            <button
              type="button"
              class="action-btn"
              @click="ch.controlOpen = !ch.controlOpen; ensureControlValues(ch)"
            >
              {{ ch.controlOpen ? "Сховати робочі значення" : "Робочі значення функції" }}
            </button>
            <div
              v-if="ch.controlOpen && ch.control_values"
              class="palette-grid"
            >
              <label
                v-for="key in CONTROL_KEYS_BY_ROLE[ch.role] ?? []"
                :key="key"
                class="field field--inline"
              >
                <span>{{ key }}</span>
                <input
                  v-model.number="ch.control_values[key]"
                  type="number"
                  min="0"
                  max="255"
                >
              </label>
            </div>
          </div>

          <div
            v-if="needsPalette(ch.role, Boolean(ch.palette))"
            class="channel-row__palette"
          >
            <button
              type="button"
              class="action-btn"
              @click="ch.paletteOpen = !ch.paletteOpen; ensurePalette(ch)"
            >
              {{ ch.paletteOpen ? "Сховати палітру" : "Палітра кольорів" }}
            </button>
            <div
              v-if="ch.paletteOpen && ch.palette"
              class="palette-grid"
            >
              <label
                v-for="key in PALETTE_KEYS"
                :key="key"
                class="field field--inline"
              >
                <span>{{ key }}</span>
                <input
                  v-model.number="ch.palette[key]"
                  type="number"
                  min="0"
                  max="255"
                >
              </label>
            </div>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
