<script setup lang="ts">
import { inject } from "vue";
import { PRESET_CATALOG } from "../lib/presets";
import { APP_STATE_KEY } from "../composables/appStateKey";

const api = inject(APP_STATE_KEY);
if (!api) {
  throw new Error("App state is not provided");
}

const { availableIds, selectPreset, engine, connection } = api;
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1>Пресети</h1>
      <p>
        Повний редактор з’явиться на етапі L010. Зараз можна запускати
        доступні серверні пресети.
      </p>
    </header>

    <div class="preset-cards">
      <article
        v-for="preset in PRESET_CATALOG"
        :key="preset.id"
        class="preset-card"
        :class="{
          active: engine?.preset_id === preset.id,
          locked: !availableIds.has(preset.id),
        }"
      >
        <h2>{{ preset.id }} · {{ preset.label }}</h2>
        <p v-if="!availableIds.has(preset.id)">
          Ще не завантажено на сервер (очікує L011).
        </p>
        <p v-else>
          Готовий до запуску.
        </p>
        <button
          type="button"
          class="action-btn"
          :disabled="connection === 'offline' || !availableIds.has(preset.id)"
          @click="selectPreset(preset.id)"
        >
          Запуск
        </button>
      </article>
    </div>
  </section>
</template>
