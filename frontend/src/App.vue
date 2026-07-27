<script setup lang="ts">
import { onMounted, ref } from "vue";
import type { HealthResponse } from "./vite-env";

const health = ref<HealthResponse | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    health.value = (await response.json()) as HealthResponse;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Невідома помилка";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="shell">
    <header class="hero">
      <p class="brand">
        ORNG LED CONTROL
      </p>
      <h1>Локальний світловий пульт</h1>
      <p class="lead">
        Scaffold HOME PLAN. Повний пульт, пресети та симулятор з’являться на
        наступних етапах.
      </p>
    </header>

    <section
      class="status"
      aria-live="polite"
    >
      <h2>Стан backend</h2>
      <p v-if="loading">
        Перевірка…
      </p>
      <p
        v-else-if="error"
        class="error"
      >
        Помилка: {{ error }}
      </p>
      <ul
        v-else-if="health"
        class="facts"
      >
        <li>
          <span>Статус</span>
          {{ health.status }}
        </li>
        <li>
          <span>Версія</span>
          {{ health.version }}
        </li>
        <li>
          <span>Transport</span>
          {{ health.transport }}
        </li>
        <li>
          <span>Art-Net armed</span>
          {{ health.output_armed ? "так" : "ні" }}
        </li>
      </ul>
    </section>
  </div>
</template>
