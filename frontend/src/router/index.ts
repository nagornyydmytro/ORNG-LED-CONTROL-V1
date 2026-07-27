import { createRouter, createWebHistory } from "vue-router";
import ControlView from "../views/ControlView.vue";
import PresetsView from "../views/PresetsView.vue";
import SetupView from "../views/SetupView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "control", component: ControlView, meta: { title: "Пульт" } },
    { path: "/presets", name: "presets", component: PresetsView, meta: { title: "Пресети" } },
    { path: "/setup", name: "setup", component: SetupView, meta: { title: "Налаштування" } },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
