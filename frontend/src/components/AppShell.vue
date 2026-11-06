<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { isCloudAdminBuild } from "../cloud/mode";
import SceneSelector from "./control/SceneSelector.vue";
import ToastStack from "./ToastStack.vue";
import type { ConnectionStatus } from "../composables/useAppState";
import { useTheme } from "../composables/useTheme";
import { loadInputProfile, saveInputProfile, type InputProfileId } from "../lib/inputProfile";
import type { SceneInfo } from "../vite-env";

const props = defineProps<{
  connection: ConnectionStatus;
  transport: string;
  preferredTransport?: string;
  armed: boolean;
  udpActive?: boolean;
  frameSum?: number;
  nonzeroChannels?: number;
  outputError: string | null;
  blackout?: boolean;
  sceneId?: string;
  sceneName?: string;
  scenes?: SceneInfo[];
  sceneSwitching?: boolean;
  locationName?: string;
  locations?: { id: string; name: string }[];
  locationId?: string | null;
  syncLabel?: string | null;
  showLogout?: boolean;
}>();

const emit = defineEmits<{
  "switch-scene": [sceneId: string];
  "switch-location": [locationId: string];
  logout: [];
}>();

const locationChoices = computed(() => {
  if (props.locations?.length) return props.locations;
  if (props.locationName) {
    return [{ id: props.locationId || "current", name: props.locationName }];
  }
  return [];
});

const { isLight, setTheme } = useTheme();

const SIDEBAR_KEY = "orng-sidebar-collapsed";
const collapsed = ref(false);

const cloudDesk = isCloudAdminBuild();

const links = [
  { to: "/", label: "Пульт", glyph: "◉" },
  { to: "/presets", label: "Пресети", glyph: "▤" },
  { to: "/setup", label: "Калібрування", glyph: "⚙" },
  { to: "/channels", label: "Канали", glyph: "⑂" },
  ...(cloudDesk ? [] : [{ to: "/cloud", label: "Привʼязка", glyph: "☁" }]),
];

const menuOpen = ref(false);
const burgerBtn = ref<HTMLButtonElement | null>(null);
const menuEl = ref<HTMLElement | null>(null);
const deskRole = ref<"local" | "raspberry">("raspberry");
const discoverNote = ref("");

onMounted(async () => {
  try {
    const body = (await (await fetch("/api/desk")).json()) as { role?: string };
    if (body.role === "local" || body.role === "raspberry") deskRole.value = body.role;
  } catch {
    /* the saved choice stays; a failed read does not switch output */
  }
});

async function recheckOutput() {
  discoverNote.value = "";
  const response = await fetch("/api/output/discover", { method: "POST" });
  const body = (await response.json()) as {
    detail?: string;
    nodes?: string[];
    matches_saved?: boolean;
    needs_choice?: boolean;
    saved_target_ip?: string | null;
  };
  if (!response.ok) {
    discoverNote.value = body.detail || "Перевірку не виконано";
    return;
  }
  const nodes = body.nodes ?? [];
  if (body.matches_saved && body.saved_target_ip) {
    discoverNote.value = `Збережене підключення ${body.saved_target_ip} відповідає.`;
    return;
  }
  if (nodes.length === 0) {
    discoverNote.value = body.saved_target_ip
      ? `Збережено ${body.saved_target_ip}. Зараз контролер не відповів.`
      : "Контролер не знайдено. Підключення задається один раз у налаштуваннях локації.";
    return;
  }
  if (body.needs_choice) {
    discoverNote.value = `Кілька контролерів: ${nodes.join(", ")}. Оберіть збережене підключення, автоматично нікого не вибрано.`;
    return;
  }
  discoverNote.value = `Знайдено ${nodes[0]}. Його ще не збережено як перевірене підключення.`;
}

async function onDeskRole(role: "local" | "raspberry") {
  const previous = deskRole.value;
  try {
    const response = await fetch("/api/desk/role", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    if (!response.ok) {
      deskRole.value = previous;
      discoverNote.value = "Роль не змінено";
      return;
    }
    deskRole.value = role;
  } catch {
    deskRole.value = previous;
    discoverNote.value = "Роль не змінено";
  }
}
const inputProfile = ref<InputProfileId>(loadInputProfile());

function onInputProfile(id: InputProfileId) {
  inputProfile.value = id;
  saveInputProfile(id);
  window.dispatchEvent(new CustomEvent("orng-input-profile", { detail: id }));
}

function toggleTheme() {
  setTheme(isLight.value ? "dark" : "light");
}

function toggleSidebar() {
  collapsed.value = !collapsed.value;
}

function menuFocusables(): HTMLElement[] {
  const root = menuEl.value;
  if (!root) return [];
  return [...root.querySelectorAll<HTMLElement>("a, button, input, select, textarea")].filter(
    (el) => !el.hasAttribute("disabled"),
  );
}

function closeMenu() {
  if (!menuOpen.value) return;
  menuOpen.value = false;
  void nextTick(() => burgerBtn.value?.focus());
}

function toggleMenu() {
  if (menuOpen.value) closeMenu();
  else menuOpen.value = true;
}

function onMenuKey(event: KeyboardEvent) {
  if (!menuOpen.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeMenu();
    return;
  }
  if (event.key !== "Tab") return;
  const items = menuFocusables();
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

watch(menuOpen, (open) => {
  if (!open) return;
  void nextTick(() => menuFocusables()[0]?.focus());
});

onMounted(() => {
  try {
    collapsed.value = sessionStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    collapsed.value = false;
  }
  window.addEventListener("keydown", onMenuKey);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onMenuKey);
});

watch(collapsed, (value) => {
  try {
    sessionStorage.setItem(SIDEBAR_KEY, value ? "1" : "0");
  } catch {
    // ignore quota / private mode
  }
});
</script>

<template>
  <div
    class="shell"
    :class="{ 'shell--nav-collapsed': collapsed }"
  >
    <aside
      class="sidebar"
      :class="{ 'sidebar--collapsed': collapsed }"
      :aria-expanded="!collapsed"
    >
      <div class="sidebar__top">
        <div class="sidebar__brand">
          <img
            class="sidebar__logo"
            src="/logo.svg"
            alt=""
            width="28"
            height="28"
          >
          <div
            v-if="!collapsed"
            class="sidebar__titles"
          >
            <span class="sidebar__mark">ORNG</span>
            <span class="sidebar__sub">LED CONTROL</span>
          </div>
        </div>
        <button
          type="button"
          class="sidebar__toggle"
          :aria-label="collapsed ? 'Розкрити меню' : 'Сховати меню'"
          :title="collapsed ? 'Розкрити меню' : 'Сховати меню'"
          @click="toggleSidebar"
        >
          {{ collapsed ? "»" : "«" }}
        </button>
      </div>

      <nav
        class="sidebar__nav"
        aria-label="Основна навігація"
      >
        <RouterLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          class="nav-link"
          :title="link.label"
        >
          <span
            class="nav-link__glyph"
            aria-hidden="true"
          >{{ link.glyph }}</span>
          <span
            v-if="!collapsed"
            class="nav-link__label"
          >{{ link.label }}</span>
        </RouterLink>
      </nav>

      <div
        v-if="locationChoices.length && !collapsed"
        class="sidebar__locations"
        role="group"
        aria-label="Локація"
      >
        <button
          v-for="loc in locationChoices"
          :key="loc.id"
          type="button"
          class="action-btn"
          :class="{ active: loc.id === locationId }"
          :aria-pressed="loc.id === locationId"
          @click="emit('switch-location', loc.id)"
        >
          {{ loc.name }}
        </button>
      </div>

      <div class="sidebar__foot">
        <div
          class="theme-row"
          role="group"
          aria-label="Тема"
        >
          <span
            v-if="!collapsed"
            class="theme-row__label"
          >Світла</span>
          <button
            type="button"
            class="theme-slider"
            role="switch"
            :class="{ 'is-dark': !isLight }"
            :aria-checked="!isLight"
            aria-label="Перемкнути тему"
            @click="toggleTheme"
          >
            <span class="theme-slider__thumb" />
          </button>
          <span
            v-if="!collapsed"
            class="theme-row__label"
          >Темна</span>
        </div>
        <button
          v-if="showLogout"
          type="button"
          class="action-btn sidebar__logout"
          @click="emit('logout')"
        >
          Вийти
        </button>
      </div>

    </aside>

    <div class="shell__body">
      <header class="topbar">
        <div class="topbar__brand">
          <img
            class="sidebar__logo"
            src="/logo.svg"
            alt=""
            width="28"
            height="28"
          >
          <span class="topbar__mark">ORNG</span>
        </div>
        <div class="topbar__state">
          <span
            v-if="connection !== 'online'"
            class="status-dot"
            :data-status="connection"
            aria-hidden="true"
          />
          <SceneSelector
            v-if="(scenes?.length ?? 0) > 1"
            :scene-id="sceneId ?? 'orng-bar'"
            :scene-name="sceneName ?? 'ORNG BAR'"
            :scenes="scenes ?? []"
            :disabled="connection === 'offline'"
            :busy="sceneSwitching"
            @switch="emit('switch-scene', $event)"
          />
          <span
            v-if="syncLabel"
            class="topbar__sync"
          >{{ syncLabel }}</span>
        </div>
        <button
          ref="burgerBtn"
          type="button"
          class="topbar__burger"
          aria-label="Меню"
          :aria-expanded="menuOpen"
          @click="toggleMenu"
        >
          <span class="topbar__burger-line" />
          <span class="topbar__burger-line" />
          <span class="topbar__burger-line" />
        </button>
      </header>
      <button
        v-if="menuOpen"
        type="button"
        class="shell-menu__backdrop"
        aria-label="Закрити меню"
        @click="closeMenu"
      />
      <Transition name="shell-sheet">
      <div
        v-if="menuOpen"
        ref="menuEl"
        class="shell-menu"
        role="dialog"
        aria-label="Меню"
      >
        <div class="shell-menu__handle" aria-hidden="true" />
        <div
          v-if="locationChoices.length"
          class="shell-menu__locations"
          role="group"
          aria-label="Локація"
        >
          <button
            v-for="loc in locationChoices"
            :key="loc.id"
            type="button"
            class="action-btn"
            :class="{ active: loc.id === locationId }"
            :aria-pressed="loc.id === locationId"
            @click="emit('switch-location', loc.id); closeMenu()"
          >
            {{ loc.name }}
          </button>
        </div>
        <nav class="shell-menu__nav">
          <RouterLink
            v-for="link in links"
            :key="link.to"
            :to="link.to"
            class="nav-link"
            @click="closeMenu"
          >
            {{ link.label }}
          </RouterLink>
        </nav>
        <div
          v-if="!cloudDesk"
          class="choice-group"
          role="group"
          aria-label="Виконавець"
        >
          <span class="choice-group__label">Виконавець</span>
          <button
            type="button"
            class="action-btn"
            :class="{ active: deskRole === 'raspberry' }"
            :aria-pressed="deskRole === 'raspberry'"
            @click="onDeskRole('raspberry')"
          >
            Через Raspberry
          </button>
          <button
            type="button"
            class="action-btn"
            :class="{ active: deskRole === 'local' }"
            :aria-pressed="deskRole === 'local'"
            @click="onDeskRole('local')"
          >
            З цього комп'ютера
          </button>
          <button
            v-if="deskRole === 'local'"
            type="button"
            class="action-btn"
            @click="recheckOutput"
          >
            Перевірити контролер
          </button>
          <p
            v-if="discoverNote"
            class="shell-menu__note"
          >
            {{ discoverNote }}
          </p>
        </div>
        <div
          class="choice-group"
          role="group"
          aria-label="Керування з цього екрана"
        >
          <span class="choice-group__label">Керування з цього екрана</span>
          <button
            type="button"
            class="action-btn"
            :class="{ active: inputProfile === 'qwerty' }"
            :aria-pressed="inputProfile === 'qwerty'"
            @click="onInputProfile('qwerty')"
          >
            Клавіатура
          </button>
          <button
            type="button"
            class="action-btn"
            :class="{ active: inputProfile === 'ch57x' }"
            :aria-pressed="inputProfile === 'ch57x'"
            @click="onInputProfile('ch57x')"
          >
            Макропад
          </button>
          <p class="shell-menu__note">
            Медіаклавіші працюють лише якщо ця система віддає їх браузеру. OTG-пад на телефоні сам собою не вважається підтриманим.
          </p>
        </div>
        <div
          class="theme-row"
          role="group"
          aria-label="Тема"
        >
          <span class="theme-row__label">Світла</span>
          <button
            type="button"
            class="theme-slider"
            role="switch"
            :class="{ 'is-dark': !isLight }"
            :aria-checked="!isLight"
            aria-label="Перемкнути тему"
            @click="toggleTheme"
          >
            <span class="theme-slider__thumb" />
          </button>
          <span class="theme-row__label">Темна</span>
        </div>
        <button
          v-if="showLogout"
          type="button"
          class="action-btn shell-menu__logout"
          @click="emit('logout'); closeMenu()"
        >
          Вийти
        </button>
      </div>
      </Transition>

      <p
        v-if="outputError"
        class="banner banner--error"
        role="alert"
      >
        Помилка виводу: {{ outputError }}
      </p>

      <main class="main">
        <RouterView :key="locationId || sceneId || 'desk'" />
      </main>
    </div>

    <ToastStack />
  </div>
</template>
