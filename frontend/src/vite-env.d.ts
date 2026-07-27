/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<object, object, unknown>;
  export default component;
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  transport: string;
  output_armed: boolean;
  frontend_dist_present: boolean;
}
