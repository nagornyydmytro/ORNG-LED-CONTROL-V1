<script setup lang="ts">
import { onMounted, ref } from "vue";
import DeskApp from "./DeskApp.vue";
import LanLogin from "./desk/LanLogin.vue";
import { probeLanSession, type LanPhase } from "./desk/lanSession";

const phase = ref<LanPhase>("checking");

onMounted(async () => {
  phase.value = await probeLanSession();
});

function entered() {
  window.location.assign("/");
}
</script>

<template>
  <p
    v-if="phase === 'checking'"
    class="lan-login"
  >
    Перевірка локального пульта…
  </p>
  <LanLogin
    v-else-if="phase === 'login'"
    @entered="entered"
  />
  <DeskApp
    v-else
    :operator-desk="phase === 'open'"
  />
</template>
