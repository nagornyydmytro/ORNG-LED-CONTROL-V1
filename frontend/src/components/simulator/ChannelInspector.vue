<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{
  /** Non-reactive accessor so the inspector never re-renders the whole app. */
  frame: () => number[];
  nonzero: number;
}>();

/** Deliberately slow: 512 cells at 30 fps is what used to freeze the UI. */
const REFRESH_MS = 250;

const open = ref(false);
const values = ref<number[]>([]);
let timer: number | null = null;

function sample() {
  values.value = props.frame().slice();
}

function stop() {
  if (timer !== null) {
    window.clearInterval(timer);
    timer = null;
  }
}

watch(open, (isOpen) => {
  stop();
  if (!isOpen) return;
  sample();
  timer = window.setInterval(sample, REFRESH_MS);
});

onBeforeUnmount(stop);
</script>

<template>
  <div class="inspector">
    <button
      type="button"
      class="inspector__toggle"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span>Інспектор каналів (512)</span>
      <span class="inspector__count">ненульових {{ nonzero }}</span>
      <span aria-hidden="true">{{ open ? "▲" : "▼" }}</span>
    </button>
    <div
      v-if="open"
      class="inspector__grid"
      data-testid="channel-inspector"
    >
      <div
        v-for="(value, index) in values"
        :key="index"
        class="ch"
        :class="{ lit: value > 0 }"
        :title="`Ch ${index + 1}: ${value}`"
      >
        <span class="n">{{ index + 1 }}</span>
        <span class="v">{{ value }}</span>
      </div>
    </div>
    <p
      v-if="open"
      class="inspector__note"
    >
      Оновлення 4 рази на секунду, щоб не гальмувати сцену.
    </p>
  </div>
</template>
