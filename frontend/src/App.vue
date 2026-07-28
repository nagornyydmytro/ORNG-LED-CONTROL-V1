<script setup lang="ts">
import { computed, provide } from "vue";
import AppShell from "./components/AppShell.vue";
import { APP_STATE_KEY } from "./composables/appStateKey";
import { useAppState } from "./composables/useAppState";
import { provideToasts } from "./composables/useToasts";

const toasts = provideToasts();
const appState = useAppState(toasts);
provide(APP_STATE_KEY, appState);

const connection = computed(() => appState.connection.value);
const transport = computed(() => appState.output.value?.transport ?? "mock");
const armed = computed(() => appState.output.value?.armed ?? false);
const outputError = computed(() => appState.output.value?.last_error ?? null);
</script>

<template>
  <AppShell
    :connection="connection"
    :transport="transport"
    :armed="armed"
    :output-error="outputError"
  />
</template>
