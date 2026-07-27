<script setup lang="ts">
defineProps<{
  active: boolean;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  toggle: [];
}>();
</script>

<template>
  <button
    type="button"
    class="blackout-btn"
    :class="{ active }"
    :aria-pressed="active"
    :disabled="disabled"
    @click="emit('toggle')"
  >
    BLACKOUT
  </button>
</template>
