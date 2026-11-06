<script setup lang="ts">
import { ref } from "vue";
import { isCloudAdminBuild } from "../cloud/mode";
import CloudAdminView from "./CloudAdminView.vue";
import ServiceConsoleView from "./ServiceConsoleView.vue";

const cloud = isCloudAdminBuild();
const pane = ref<"service" | "releases">("service");
</script>

<template>
  <section
    v-if="cloud"
    class="advanced-page"
  >
    <div
      class="pick-row"
      role="tablist"
      aria-label="Сервіс"
    >
      <button
        type="button"
        class="action-btn"
        :class="{ active: pane === 'service' }"
        :aria-pressed="pane === 'service'"
        @click="pane = 'service'"
      >
        Локації
      </button>
      <button
        type="button"
        class="action-btn"
        :class="{ active: pane === 'releases' }"
        :aria-pressed="pane === 'releases'"
        @click="pane = 'releases'"
      >
        Релізи
      </button>
    </div>
    <ServiceConsoleView v-if="pane === 'service'" />
    <CloudAdminView v-else />
  </section>
  <p
    v-else
    class="banner"
  >
    Сервісні дії доступні лише в захищеній хмарній сесії.
  </p>
</template>
