import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import { bootTheme } from "./composables/theme";
import "./styles.css";

bootTheme();

createApp(App).use(router).mount("#app");
