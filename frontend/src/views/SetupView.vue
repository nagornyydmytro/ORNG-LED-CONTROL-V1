<script setup lang="ts">
const steps = [
  "Mock / Art-Net",
  "Мережа, IP, Universe, FPS",
  "Список приладів",
  "DMX patch",
  "Fixture profile",
  "Raw DMX tester",
  "Розташування",
  "Калібрація Bars / Beam",
  "Тест групи / Blackout",
  "Зведення готовності",
];
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1>Налаштування</h1>
      <p>
        Покроковий майстер з’явиться на етапі L009. Зараз показано структуру
        з канону; апаратні пункти залишаються
        <strong>Не перевірено на обладнанні</strong>.
      </p>
    </header>

    <ol class="setup-list">
      <li
        v-for="(step, index) in steps"
        :key="step"
      >
        <span class="num">{{ index + 1 }}</span>
        <div>
          <strong>{{ step }}</strong>
          <p>Не перевірено на обладнанні</p>
        </div>
      </li>
    </ol>
  </section>
</template>
