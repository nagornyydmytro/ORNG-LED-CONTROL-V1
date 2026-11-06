import { createApp } from "vue";
import { isCloudAdminBuild } from "./cloud/mode";
import { bootTheme } from "./composables/theme";
import "./styles.css";

bootTheme();

async function boot() {
  if (isCloudAdminBuild()) {
    const [{ default: CloudAdminApp }, { router }] = await Promise.all([
      import("./CloudAdminApp.vue"),
      import("./router"),
    ]);
    createApp(CloudAdminApp).use(router).mount("#app");
    return;
  }
  const [{ default: App }, { router }] = await Promise.all([
    import("./App.vue"),
    import("./router"),
  ]);
  createApp(App).use(router).mount("#app");
}

void boot();
