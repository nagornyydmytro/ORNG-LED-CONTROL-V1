<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { RouterLink, RouterView } from "vue-router";
import ToastStack from "./ToastStack.vue";
import type { ConnectionStatus } from "../composables/useAppState";
import { useTheme } from "../composables/useTheme";

defineProps<{
  connection: ConnectionStatus;
  transport: string;
  preferredTransport?: string;
  armed: boolean;
  udpActive?: boolean;
  frameSum?: number;
  nonzeroChannels?: number;
  outputError: string | null;
  blackout?: boolean;
}>();

const { isLight, setTheme } = useTheme();

const SIDEBAR_KEY = "orng-sidebar-collapsed";
const collapsed = ref(false);

const links = [
  { to: "/", label: "Пульт", glyph: "◉" },
  { to: "/presets", label: "Пресети", glyph: "▤" },
  { to: "/setup", label: "Налаштування", glyph: "⚙" },
  { to: "/channels", label: "Налаштування каналів", glyph: "⑂" },
];

function connectionLabel(status: ConnectionStatus): string {
  switch (status) {
    case "online":
      return "Онлайн";
    case "connecting":
      return "Підключення…";
    case "reconnecting":
      return "Перепідключення…";
    case "offline":
      return "Офлайн";
  }
}

function onThemeToggle(event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  setTheme(checked ? "light" : "dark");
}

function toggleSidebar() {
  collapsed.value = !collapsed.value;
}

onMounted(() => {
  try {
    collapsed.value = sessionStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    collapsed.value = false;
  }
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

      <div class="sidebar__theme">
        <label class="theme-switch">
          <span
            v-if="!collapsed"
            class="theme-switch__label"
          >
            <span class="theme-switch__title">Тема</span>
            <span class="theme-switch__hint">{{ isLight ? "Світла · glass" : "Темна" }}</span>
          </span>
          <span class="theme-switch__track">
            <input
              class="theme-switch__input"
              type="checkbox"
              role="switch"
              :checked="isLight"
              :aria-checked="isLight"
              aria-label="Перемкнути світлу liquid-glass тему"
              @change="onThemeToggle"
            >
            <span
              class="theme-switch__thumb"
              aria-hidden="true"
            />
          </span>
        </label>
      </div>

      <div
        v-if="!collapsed"
        class="sidebar__foot"
      >
        <p class="sidebar__note">
          Локальний пульт · офлайн-режим
        </p>
      </div>
    </aside>

    <div class="shell__body">
      <header class="topbar">
        <div class="topbar__state">
          <span
            class="status-dot"
            :data-status="connection"
            aria-hidden="true"
          />
          <span class="topbar__conn">{{ connectionLabel(connection) }}</span>
        </div>
        <div class="topbar__tags">
          <span
            class="badge badge--muted"
            :title="'Бажаний transport з YAML'"
          >YAML {{ (preferredTransport ?? "mock").toUpperCase() }}</span>
          <span
            class="badge"
            :class="transport === 'artnet' ? 'badge--warn' : 'badge--muted'"
            :title="'Фактичний runtime transport'"
          >RT {{ transport.toUpperCase() }}</span>
          <span
            class="badge"
            :class="udpActive ? 'badge--warn' : 'badge--ok'"
          >UDP {{ udpActive ? "ON" : "OFF" }}</span>
          <span
            v-if="armed"
            class="badge badge--warn"
          >armed</span>
          <span
            v-else
            class="badge badge--ok"
          >disarmed</span>
          <span
            v-if="blackout"
            class="badge badge--danger"
          >BLACKOUT</span>
          <span
            class="badge badge--muted"
            :title="'Фактичний wire-кадр: frame_sum / nonzero'"
          >wire Σ{{ frameSum ?? 0 }}/{{ nonzeroChannels ?? 0 }}</span>
        </div>
      </header>

      <p
        v-if="outputError"
        class="banner banner--error"
        role="alert"
      >
        Помилка виводу: {{ outputError }}
      </p>

      <main class="main">
        <RouterView />
      </main>
    </div>

    <ToastStack />
  </div>
</template>
