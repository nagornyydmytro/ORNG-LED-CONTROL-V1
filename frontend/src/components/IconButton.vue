<script setup lang="ts">
defineProps<{
  icon: "prev" | "next" | "restart" | "play" | "pause" | "expand" | "collapse" | "up" | "down";
  label: string;
}>();
</script>

<template>
  <button
    type="button"
    class="icon-btn"
    :aria-label="label"
    :title="label"
  >
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      :class="{ 'icon-btn__fill': icon === 'play' || icon === 'pause' }"
    >
      <path
        v-if="icon === 'prev'"
        d="M15 6 9 12l6 6"
      />
      <path
        v-else-if="icon === 'next'"
        d="M9 6l6 6-6 6"
      />
      <path
        v-else-if="icon === 'up'"
        d="M6 14l6-6 6 6"
      />
      <path
        v-else-if="icon === 'down'"
        d="M6 10l6 6 6-6"
      />
      <path
        v-else-if="icon === 'play'"
        d="M8 5.5v13l11-6.5-11-6.5z"
      />
      <path
        v-else-if="icon === 'pause'"
        d="M8 5.5h3.2v13H8zM12.8 5.5H16v13h-3.2z"
      />
      <path
        v-else-if="icon === 'restart'"
        d="M19 12a7 7 0 1 1-2-4.9M19 4.5V9h-4.5"
      />
      <path
        v-else-if="icon === 'expand'"
        d="M9 4.5H4.5V9M15 4.5h4.5V9M9 19.5H4.5V15M15 19.5h4.5V15"
      />
      <path
        v-else
        d="M4.5 9V4.5H9M19.5 9V4.5H15M4.5 15v4.5H9M19.5 15v4.5H15"
      />
    </svg>
  </button>
</template>
