<script setup lang="ts">
defineProps<{ label?: string }>();
</script>

<template>
  <span class="hw-badge">{{ label ?? "Не перевірено на обладнанні" }}</span>
</template>
