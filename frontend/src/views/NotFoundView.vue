<script setup lang="ts">
import { useRouter } from "vue-router";

const router = useRouter();

function home() {
  void router.push("/");
}
</script>

<template>
  <section class="ui-page">
    <p
      class="banner banner--warn"
      role="alert"
    >
      Сторінку не знайдено.
    </p>
    <button
      type="button"
      class="btn btn--primary"
      @click="home"
    >
      На пульт
    </button>
  </section>
</template>
