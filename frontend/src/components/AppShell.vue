<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";
import ToastStack from "./ToastStack.vue";
import type { ConnectionStatus } from "../composables/useAppState";

defineProps<{
  connection: ConnectionStatus;
  transport: string;
  armed: boolean;
  outputError: string | null;
}>();

const links = [
  { to: "/", label: "Пульт" },
  { to: "/presets", label: "Пресети" },
  { to: "/setup", label: "Налаштування" },
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
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-block">
        <p class="brand">
          ORNG LED CONTROL
        </p>
        <p class="brand-sub">
          Локальний світловий пульт
        </p>
      </div>
      <div
        class="conn"
        :data-status="connection"
        aria-live="polite"
      >
        <span class="dot" />
        <span>{{ connectionLabel(connection) }}</span>
        <span class="sep">·</span>
        <span>{{ transport.toUpperCase() }}</span>
        <span
          v-if="armed"
          class="armed"
        >УВІМКНЕНО</span>
        <span
          v-else
          class="disarmed"
        >вимкнено</span>
      </div>
    </header>

    <p
      v-if="outputError"
      class="output-error"
      role="alert"
    >
      Помилка виводу: {{ outputError }}
    </p>

    <nav
      class="nav"
      aria-label="Основна навігація"
    >
      <RouterLink
        v-for="link in links"
        :key="link.to"
        :to="link.to"
        class="nav-link"
      >
        {{ link.label }}
      </RouterLink>
    </nav>

    <main class="main">
      <RouterView />
    </main>

    <ToastStack />
  </div>
</template>
