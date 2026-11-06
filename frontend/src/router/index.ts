import { createRouter, createWebHistory } from "vue-router";
import AdvancedView from "../views/AdvancedView.vue";
import CloudView from "../views/CloudView.vue";
import AutoDeskPreview from "../views/AutoDeskPreview.vue";
import ChannelsView from "../views/ChannelsView.vue";
import ControlView from "../views/ControlView.vue";
import NotFoundView from "../views/NotFoundView.vue";
import PresetsView from "../views/PresetsView.vue";
import SetupView from "../views/SetupView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "control", component: ControlView, meta: { title: "Пульт" } },
    {
      path: "/_preview/auto-desk",
      name: "auto-desk-preview",
      component: AutoDeskPreview,
      meta: { title: "Авто пульт" },
    },
    { path: "/presets", name: "presets", component: PresetsView, meta: { title: "Пресети" } },
    { path: "/setup", name: "setup", component: SetupView, meta: { title: "Налаштування" } },
    {
      path: "/channels",
      name: "channels",
      component: ChannelsView,
      meta: { title: "Налаштування каналів" },
    },
    {
      path: "/cloud",
      name: "cloud",
      component: CloudView,
      meta: { title: "Привʼязка" },
    },
    {
      path: "/advanced",
      name: "advanced",
      component: AdvancedView,
      meta: { title: "Сервіс" },
    },
    { path: "/settings", redirect: "/setup" },
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: NotFoundView,
      meta: { title: "Сторінку не знайдено" },
    },
  ],
});

router.afterEach(() => {
  window.dispatchEvent(new CustomEvent("orng-release-holds"));
});
