<script setup lang="ts">
import { ref } from "vue";
import { loginLanDesk } from "./lanSession";

const emit = defineEmits<{ entered: [] }>();

const password = ref("");
const error = ref("");
const pending = ref(false);

async function submit() {
  error.value = "";
  pending.value = true;
  try {
    await loginLanDesk(password.value);
    password.value = "";
    emit("entered");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Вхід не виконано";
  } finally {
    pending.value = false;
  }
}
</script>

<template>
  <main class="lan-login">
    <form
      class="lan-login__card"
      @submit.prevent="submit"
    >
      <h1>Локальний пульт зали</h1>
      <p>
        Ця сторінка відкривається в мережі майданчика і працює без інтернету.
        Хмарний вхід на light.orng.wtf лишається окремо: сайт сам сюди не переходить.
      </p>
      <label>
        Пароль оператора
        <input
          v-model="password"
          type="password"
          name="password"
          autocomplete="current-password"
          required
        >
      </label>
      <p
        v-if="error"
        class="lan-login__error"
        role="alert"
      >
        {{ error }}
      </p>
      <button
        type="submit"
        :disabled="pending"
      >
        Увійти
      </button>
    </form>
  </main>
</template>
