import type { Env } from "../../src/env";
import type { D1Migration } from "@cloudflare/vitest-plugin";

declare module "cloudflare:workers" {
  interface Env extends import("../../src/env").Env {
    TEST_MIGRATIONS: D1Migration[];
  }
}

declare module "cloudflare:test" {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface ProvidedEnv extends Env {
    TEST_MIGRATIONS: D1Migration[];
  }
}
