<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import {
  fetchAppConfig,
  fetchLayout,
  fetchPatch,
  fetchProfiles,
  fetchReadiness,
  saveAppConfig,
  saveLayout,
  savePatch,
  saveProfile,
  setupPost,
  validatePatch,
} from "../api/setup";
import { postCommand } from "../api/client";
import HardwareBadge from "../components/setup/HardwareBadge.vue";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { APP_STATE_KEY } from "../composables/appStateKey";

const appState = inject(APP_STATE_KEY, null);

const STEPS = [
  "Mock / Art-Net",
  "Мережа, IP, Universe, FPS",
  "Список приладів",
  "DMX patch",
  "Fixture profile",
  "Raw DMX tester",
  "Розташування",
  "Калібрація Bars / Beam",
  "Тест групи / Blackout",
  "Зведення готовності",
] as const;

const step = ref(0);
const loading = ref(true);
const saving = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const patchErrors = ref<string[]>([]);
const readiness = ref<Record<string, unknown> | null>(null);

const app = ref<Record<string, unknown> | null>(null);
const patch = ref<Record<string, unknown> | null>(null);
const profiles = ref<Record<string, Record<string, unknown>>>({});
const layout = ref<Record<string, unknown> | null>(null);

const selectedProfileId = ref("par_7ch_provisional");
const rawChannel = ref(1);
const rawValue = ref(0);
const rawActive = ref(false);
const rawNonzero = ref(0);

const artnet = computed(() => {
  const cfg = app.value?.artnet as Record<string, unknown> | undefined;
  return cfg ?? {};
});

const fixtures = computed(() => {
  const list = patch.value?.fixtures;
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
});

const selectedProfile = computed(() => profiles.value[selectedProfileId.value] ?? null);

const calibration = reactive({
  barInvertNotes: "Фізична орієнтація сегментів Bars — PENDING HARDWARE",
  beamNotes: "Pan/tilt home, invert і робочі межі — PENDING HARDWARE",
});

function setStatus(ok: string | null, err: string | null = null) {
  message.value = ok;
  error.value = err;
}

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    const [appCfg, patchCfg, profileMap, layoutCfg, ready] = await Promise.all([
      fetchAppConfig(),
      fetchPatch(),
      fetchProfiles(),
      fetchLayout(),
      fetchReadiness(),
    ]);
    app.value = appCfg;
    patch.value = patchCfg;
    profiles.value = profileMap;
    layout.value = layoutCfg;
    readiness.value = ready;
    const ids = Object.keys(profileMap);
    if (ids.length && !profileMap[selectedProfileId.value]) {
      selectedProfileId.value = ids[0];
    }
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка завантаження");
  } finally {
    loading.value = false;
  }
}

async function persistApp() {
  if (!app.value) return;
  saving.value = true;
  try {
    await saveAppConfig(app.value);
    setStatus("App config збережено (Mock output, без arm)");
    readiness.value = await fetchReadiness();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження app");
  } finally {
    saving.value = false;
  }
}

async function runValidatePatch() {
  if (!patch.value) return;
  const result = await validatePatch(patch.value);
  patchErrors.value = result.errors;
  if (result.ok) setStatus("Patch валідний (перетини не знайдені)");
  else setStatus(null, "Patch має помилки — збереження заблоковано");
}

async function persistPatch() {
  if (!patch.value) return;
  saving.value = true;
  try {
    const result = await validatePatch(patch.value);
    patchErrors.value = result.errors;
    if (!result.ok) {
      setStatus(null, "Виправте помилки patch до збереження");
      return;
    }
    await savePatch(patch.value);
    setStatus("Patch збережено");
    readiness.value = await fetchReadiness();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження patch");
  } finally {
    saving.value = false;
  }
}

async function persistProfile() {
  const profile = selectedProfile.value;
  if (!profile) return;
  saving.value = true;
  try {
    await saveProfile(String(profile.id), profile);
    setStatus(`Профіль ${String(profile.id)} збережено (hardware_verified=false)`);
    readiness.value = await fetchReadiness();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження профілю");
  } finally {
    saving.value = false;
  }
}

async function persistLayout() {
  if (!layout.value) return;
  saving.value = true;
  try {
    await saveLayout(layout.value);
    setStatus("Layout збережено");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження layout");
  } finally {
    saving.value = false;
  }
}

async function enterRaw() {
  const ack = await setupPost("raw-tester/enter");
  rawActive.value = Boolean(ack.state.raw_tester?.active);
  rawNonzero.value = Number(ack.state.raw_tester?.nonzero_channels ?? 0);
  setStatus("Raw tester активний (починає з нулів, Mock only)");
}

async function exitRaw() {
  const ack = await setupPost("raw-tester/exit");
  rawActive.value = Boolean(ack.state.raw_tester?.active);
  rawNonzero.value = 0;
  setStatus("Raw tester вимкнено, тестові значення скинуто в 0");
}

async function rawBlackout() {
  const ack = await setupPost("raw-tester/blackout");
  rawActive.value = Boolean(ack.state.raw_tester?.active);
  rawNonzero.value = Number(ack.state.raw_tester?.nonzero_channels ?? 0);
  setStatus("Raw Blackout");
}

async function setRawChannel() {
  const ack = await setupPost("raw-tester/set", {
    channel: rawChannel.value,
    value: rawValue.value,
  });
  rawActive.value = Boolean(ack.state.raw_tester?.active);
  rawNonzero.value = Number(ack.state.raw_tester?.nonzero_channels ?? 0);
}

async function identifyFixture(id: string) {
  const ack = await setupPost("identify-fixture", { fixture_id: id, level: 200 });
  rawActive.value = Boolean(ack.state.raw_tester?.active);
  rawNonzero.value = Number(ack.state.raw_tester?.nonzero_channels ?? 0);
  setStatus(`Identify ${id}`);
}

async function identifyGroup(group: string) {
  const ack = await setupPost("identify-group", { group, level: 180 });
  rawActive.value = Boolean(ack.state.raw_tester?.active);
  rawNonzero.value = Number(ack.state.raw_tester?.nonzero_channels ?? 0);
  setStatus(`Identify group ${group}`);
}

async function toggleBlackout(enabled: boolean) {
  await postCommand("blackout", { enabled });
  setStatus(enabled ? "Blackout увімкнено" : "Blackout вимкнено");
}

function updateFixtureAddress(index: number, value: string) {
  const list = fixtures.value;
  if (!list[index]) return;
  list[index].start_address = Number(value);
  patch.value = { ...patch.value!, fixtures: list };
  void runValidatePatch();
}

function updateProfileChannelLocal(index: number, field: string, value: string) {
  const profile = selectedProfile.value;
  if (!profile) return;
  const channels = [...((profile.channels as Record<string, unknown>[]) ?? [])];
  channels[index] = { ...channels[index], [field]: field === "local" ? Number(value) : value };
  profiles.value = {
    ...profiles.value,
    [selectedProfileId.value]: { ...profile, channels },
  };
}

function updateSpatial(fixtureId: string, field: string, value: boolean) {
  const list = fixtures.value.map((fx) => {
    if (fx.id !== fixtureId) return fx;
    const spatial = { ...(fx.spatial as Record<string, unknown>), [field]: value };
    return { ...fx, spatial };
  });
  patch.value = { ...patch.value!, fixtures: list };
}

watch(step, async (next, prev) => {
  if (prev === 5 && next !== 5 && rawActive.value) {
    await exitRaw();
  }
  if (next === 9) {
    readiness.value = await fetchReadiness();
  }
});

onMounted(() => {
  void loadAll();
});

onUnmounted(() => {
  if (rawActive.value) {
    void setupPost("raw-tester/exit").catch(() => undefined);
  }
});
</script>

<template>
  <section
    class="page setup-wizard"
    aria-label="Майстер налаштування"
  >
    <header class="page-head">
      <h1>Налаштування</h1>
      <p>
        Покроковий Mock-майстер. Апаратні пункти залишаються
        <HardwareBadge />.
      </p>
    </header>

    <p
      v-if="loading"
      class="banner"
    >
      Завантаження конфігурації…
    </p>
    <p
      v-else-if="error"
      class="banner warn"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="message"
      class="banner"
      role="status"
    >
      {{ message }}
    </p>

    <nav
      class="wizard-steps"
      aria-label="Кроки майстра"
    >
      <button
        v-for="(label, index) in STEPS"
        :key="label"
        type="button"
        class="wizard-step"
        :class="{ active: step === index }"
        :aria-current="step === index ? 'step' : undefined"
        @click="step = index"
      >
        <span class="num">{{ index + 1 }}</span>
        <span class="label">{{ label }}</span>
      </button>
    </nav>

    <div
      v-if="!loading && app && patch"
      class="wizard-panel"
    >
      <!-- 1 Transport -->
      <div v-show="step === 0">
        <h2>1. Mock / Art-Net</h2>
        <HardwareBadge />
        <label class="field">
          Бажаний transport (YAML)
          <select v-model="app.transport">
            <option value="mock">Mock</option>
            <option value="artnet">Art-Net (конфіг лише, без arm)</option>
          </select>
        </label>
        <p class="hint">
          Runtime залишається на Mock і не вмикає реальну мережу. Art-Net output
          за замовчуванням вимкнений.
        </p>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistApp"
        >
          Зберегти
        </button>
      </div>

      <!-- 2 Network -->
      <div v-show="step === 1">
        <h2>2. Мережа, IP, Universe, FPS</h2>
        <HardwareBadge />
        <label class="field">
          Target IP
          <input
            :value="String(artnet.target_ip ?? '')"
            placeholder="напр. 10.0.0.50"
            @input="
              (app.artnet as Record<string, unknown>).target_ip =
                ($event.target as HTMLInputElement).value || null
            "
          >
        </label>
        <label class="field">
          Universe
          <input
            type="number"
            min="0"
            max="15"
            :value="Number(artnet.universe ?? 0)"
            @input="(app.artnet as Record<string, unknown>).universe = Number(($event.target as HTMLInputElement).value)"
          >
        </label>
        <label class="field">
          FPS
          <input
            type="number"
            min="1"
            max="60"
            :value="Number(artnet.fps ?? 30)"
            @input="(app.artnet as Record<string, unknown>).fps = Number(($event.target as HTMLInputElement).value)"
          >
        </label>
        <label class="field">
          UDP port
          <input
            type="number"
            :value="Number(artnet.udp_port ?? 6454)"
            @input="(app.artnet as Record<string, unknown>).udp_port = Number(($event.target as HTMLInputElement).value)"
          >
        </label>
        <p class="hint">
          Discovery контролера не виконується. hardware_verified завжди false на HOME.
        </p>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistApp"
        >
          Зберегти мережеві поля
        </button>
      </div>

      <!-- 3 Fixture list -->
      <div v-show="step === 2">
        <h2>3. Список приладів</h2>
        <ul class="fixture-list">
          <li
            v-for="fx in fixtures"
            :key="String(fx.id)"
          >
            <strong>{{ fx.label }}</strong>
            <span>{{ fx.id }} · {{ fx.kind }} · {{ fx.profile_id }}</span>
            <HardwareBadge />
          </li>
        </ul>
      </div>

      <!-- 4 Patch -->
      <div v-show="step === 3">
        <h2>4. DMX patch</h2>
        <p class="hint">
          Помилки перетинів видно до збереження.
        </p>
        <div class="table">
          <div
            v-for="(fx, index) in fixtures"
            :key="String(fx.id)"
            class="table-row"
          >
            <span>{{ fx.label }}</span>
            <label>
              Start
              <input
                type="number"
                min="1"
                max="512"
                :value="Number(fx.start_address)"
                @input="updateFixtureAddress(index, ($event.target as HTMLInputElement).value)"
              >
            </label>
          </div>
        </div>
        <ul
          v-if="patchErrors.length"
          class="errors"
          role="alert"
        >
          <li
            v-for="err in patchErrors"
            :key="err"
          >
            {{ err }}
          </li>
        </ul>
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            @click="runValidatePatch"
          >
            Перевірити
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="saving || patchErrors.length > 0"
            @click="persistPatch"
          >
            Зберегти patch
          </button>
        </div>
      </div>

      <!-- 5 Profiles -->
      <div v-show="step === 4">
        <h2>5. Fixture profile</h2>
        <HardwareBadge />
        <label class="field">
          Профіль
          <select v-model="selectedProfileId">
            <option
              v-for="(prof, id) in profiles"
              :key="id"
              :value="id"
            >
              {{ prof.label }} ({{ id }})
            </option>
          </select>
        </label>
        <div
          v-if="selectedProfile"
          class="table"
        >
          <div
            v-for="(ch, index) in (selectedProfile.channels as Record<string, unknown>[])"
            :key="index"
            class="table-row"
          >
            <label>
              Local
              <input
                type="number"
                min="1"
                :value="Number(ch.local)"
                @input="updateProfileChannelLocal(index, 'local', ($event.target as HTMLInputElement).value)"
              >
            </label>
            <label>
              Role
              <input
                :value="String(ch.role)"
                @input="updateProfileChannelLocal(index, 'role', ($event.target as HTMLInputElement).value)"
              >
            </label>
          </div>
        </div>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistProfile"
        >
          Зберегти профіль
        </button>
      </div>

      <!-- 6 Raw tester -->
      <div v-show="step === 5">
        <h2>6. Raw DMX tester</h2>
        <p class="hint">
          Починає з нулів, Mock only, Blackout миттєвий, вихід скидає значення.
          Art-Net не вмикається.
        </p>
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            :disabled="rawActive"
            @click="enterRaw"
          >
            Увійти
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="!rawActive"
            @click="exitRaw"
          >
            Вийти (zero)
          </button>
          <button
            type="button"
            class="blackout-btn"
            @click="rawBlackout"
          >
            BLACKOUT
          </button>
        </div>
        <div class="raw-edit">
          <label class="field">
            Глобальний канал
            <input
              v-model.number="rawChannel"
              type="number"
              min="1"
              max="512"
            >
          </label>
          <label class="field">
            Значення
            <input
              v-model.number="rawValue"
              type="number"
              min="0"
              max="255"
            >
          </label>
          <button
            type="button"
            class="action-btn"
            :disabled="!rawActive"
            @click="setRawChannel"
          >
            Встановити
          </button>
        </div>
        <p>
          Статус: {{ rawActive ? "активний" : "вимкнений" }} · nonzero
          {{ rawNonzero }}
        </p>
      </div>

      <!-- 7 Layout -->
      <div v-show="step === 6">
        <h2>7. Розташування</h2>
        <HardwareBadge />
        <label class="field">
          Опис
          <textarea
            v-if="layout"
            v-model="(layout.description as string)"
            rows="3"
          />
        </label>
        <p class="hint">
          viewer_facing = true (з боку глядача). Фізична орієнтація — PENDING HARDWARE.
        </p>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistLayout"
        >
          Зберегти layout
        </button>
      </div>

      <!-- 8 Calibration -->
      <div v-show="step === 7">
        <h2>8. Калібрація Bars / Beam</h2>
        <HardwareBadge />
        <p class="hint">
          {{ calibration.barInvertNotes }}
        </p>
        <p class="hint">
          {{ calibration.beamNotes }}
        </p>
        <div
          v-for="fx in fixtures.filter((f) => f.kind === 'bar' || f.kind === 'beam')"
          :key="String(fx.id)"
          class="table-row"
        >
          <strong>{{ fx.label }}</strong>
          <label
            v-if="fx.kind === 'bar'"
            class="check"
          >
            <input
              type="checkbox"
              :checked="Boolean((fx.spatial as Record<string, unknown>).invert_segments)"
              @change="updateSpatial(String(fx.id), 'invert_segments', ($event.target as HTMLInputElement).checked)"
            >
            invert_segments (draft)
          </label>
          <template v-else>
            <label class="check">
              <input
                type="checkbox"
                :checked="Boolean((fx.spatial as Record<string, unknown>).pan_invert)"
                @change="updateSpatial(String(fx.id), 'pan_invert', ($event.target as HTMLInputElement).checked)"
              >
              pan_invert (draft)
            </label>
            <label class="check">
              <input
                type="checkbox"
                :checked="Boolean((fx.spatial as Record<string, unknown>).tilt_invert)"
                @change="updateSpatial(String(fx.id), 'tilt_invert', ($event.target as HTMLInputElement).checked)"
              >
              tilt_invert (draft)
            </label>
          </template>
        </div>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistPatch"
        >
          Зберегти draft spatial flags
        </button>
      </div>

      <!-- 9 Tests -->
      <div v-show="step === 8">
        <h2>9. Тест приладу / групи / Blackout</h2>
        <HardwareBadge />
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            @click="identifyFixture('par_1')"
          >
            Тест PAR 1
          </button>
          <button
            type="button"
            class="action-btn"
            @click="identifyGroup('par')"
          >
            Група PAR
          </button>
          <button
            type="button"
            class="action-btn"
            @click="identifyGroup('bar')"
          >
            Група Bar
          </button>
          <button
            type="button"
            class="blackout-btn"
            @click="toggleBlackout(true)"
          >
            Blackout увімк.
          </button>
          <button
            type="button"
            class="action-btn"
            @click="toggleBlackout(false)"
          >
            Blackout вимк.
          </button>
          <button
            type="button"
            class="action-btn"
            @click="exitRaw"
          >
            Скинути raw / нулі
          </button>
        </div>
      </div>

      <!-- 10 Readiness -->
      <div v-show="step === 9">
        <h2>10. Зведення готовності</h2>
        <HardwareBadge />
        <ul
          v-if="readiness"
          class="ready-list"
        >
          <li>Бажаний транспорт: {{ readiness.transport_preferred }}</li>
          <li>Робочий транспорт: {{ readiness.runtime_transport }}</li>
          <li>Вивід увімкнено: {{ readiness.output_armed }}</li>
          <li>Мережа Art-Net: {{ readiness.artnet_network_enabled }}</li>
          <li>Patch OK: {{ readiness.patch_ok }}</li>
          <li>Приладів: {{ readiness.fixture_count }}</li>
          <li>
            Art-Net:
            <HardwareBadge :label="String(readiness.artnet_badge)" />
          </li>
        </ul>
        <p class="hint">
          Mock-проходження майстра не підтверджує апаратну готовність.
        </p>
      </div>
    </div>

    <StageSimulator
      v-if="appState"
      :simulator="appState.state.value?.simulator ?? null"
      :frame="appState.state.value?.frame ?? []"
      :engine-preset-id="appState.engine.value?.preset_id ?? '—'"
      :preset-time-s="appState.engine.value?.preset_time_s ?? 0"
      :episode-index="appState.engine.value?.episode_index ?? 0"
      :episode-count="appState.engine.value?.episode_count ?? 10"
      :cycle-duration-s="appState.engine.value?.cycle_duration_s ?? 180"
      :blackout="appState.engine.value?.blackout ?? false"
      :strobe-held="appState.engine.value?.strobe_held ?? false"
      :white-hit-active="appState.engine.value?.white_hit_active ?? false"
      :face-on="appState.engine.value?.face_on ?? false"
      :preview-speed="appState.state.value?.preview_speed ?? 1"
      :disabled="appState.connection.value === 'offline'"
      compact
      @update:preview-speed="appState.setPreviewSpeed"
    />

    <footer class="wizard-nav">
      <button
        type="button"
        class="action-btn"
        :disabled="step === 0"
        @click="step -= 1"
      >
        Назад
      </button>
      <span>{{ step + 1 }} / {{ STEPS.length }}</span>
      <button
        type="button"
        class="action-btn"
        :disabled="step >= STEPS.length - 1"
        @click="step += 1"
      >
        Далі
      </button>
    </footer>
  </section>
</template>
