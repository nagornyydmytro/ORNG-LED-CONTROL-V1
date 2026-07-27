<script setup lang="ts">
import { useToasts } from "../composables/useToasts";

const { toasts, dismiss } = useToasts();
</script>

<template>
  <div
    class="toasts"
    aria-live="polite"
    aria-relevant="additions"
  >
    <button
      v-for="toast in toasts"
      :key="toast.id"
      type="button"
      class="toast"
      :data-tone="toast.tone"
      @click="dismiss(toast.id)"
    >
      {{ toast.message }}
    </button>
  </div>
</template>
