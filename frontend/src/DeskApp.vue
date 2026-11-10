<script setup lang="ts">
import { computed, onMounted, provide } from "vue";
import AppShell from "./components/AppShell.vue";
import { APP_STATE_KEY } from "./composables/appStateKey";
import { useAppState } from "./composables/useAppState";
import { provideToasts } from "./composables/useToasts";
import { createLocalScenePort, pullCanonicalScene } from "./runtime/localScenePort";
import {
  installScenePort,
  locationStatusLabel,
  sceneSyncPhase,
  useLocalSceneBackend,
} from "./runtime/scenePort";

const props = defineProps<{
  operatorDesk?: boolean;
}>();

useLocalSceneBackend();
installScenePort(createLocalScenePort());

const toasts = provideToasts();
const appState = useAppState(toasts);
provide(APP_STATE_KEY, appState);

const connection = computed(() => appState.connection.value);
const transport = computed(() => appState.output.value?.transport ?? "mock");
const preferredTransport = computed(
  () => appState.output.value?.preferred_transport ?? "mock",
);
const armed = computed(() => appState.output.value?.armed ?? false);
const udpActive = computed(() => appState.output.value?.udp_active ?? false);
const frameSum = computed(() => appState.output.value?.frame_sum ?? 0);
const nonzeroChannels = computed(
  () => appState.output.value?.nonzero_channels ?? 0,
);
const outputError = computed(() => appState.output.value?.last_error ?? null);
const blackout = computed(() => appState.engine.value?.blackout ?? false);
const sceneId = computed(() => appState.sceneId.value);
const sceneName = computed(() => appState.sceneName.value);
const scenes = computed(() => appState.availableScenes.value);
const sceneSwitching = computed(() => appState.sceneSwitching.value);

function onSwitchScene(nextId: string) {
  void appState.switchScene(nextId);
}

const syncLabel = computed(() => locationStatusLabel(sceneSyncPhase.value));

onMounted(() => {
  void pullCanonicalScene();
});
</script>

<template>
  <AppShell
    :connection="connection"
    :transport="transport"
    :preferred-transport="preferredTransport"
    :armed="armed"
    :udp-active="udpActive"
    :frame-sum="frameSum"
    :nonzero-channels="nonzeroChannels"
    :output-error="outputError"
    :blackout="blackout"
    :scene-id="sceneId"
    :scene-name="sceneName"
    :scenes="scenes"
    :scene-switching="sceneSwitching"
    :sync-label="syncLabel"
    :operator-desk="props.operatorDesk === true"
    @switch-scene="onSwitchScene"
  />
</template>
