<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";
import ToastStack from "./ToastStack.vue";
import type { ConnectionStatus } from "../composables/useAppState";

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

const links = [
  { to: "/", label: "Пульт", glyph: "◉" },
  { to: "/presets", label: "Пресети", glyph: "▤" },
  { to: "/setup", label: "Налаштування", glyph: "⚙" },
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
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="sidebar__brand">
        <span class="sidebar__mark">ORNG</span>
        <span class="sidebar__sub">LED CONTROL</span>
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
        >
          <span
            class="nav-link__glyph"
            aria-hidden="true"
          >{{ link.glyph }}</span>
          <span class="nav-link__label">{{ link.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar__foot">
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
            :title="'frame_sum / nonzero'"
          >Σ{{ frameSum ?? 0 }}/{{ nonzeroChannels ?? 0 }}</span>
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
