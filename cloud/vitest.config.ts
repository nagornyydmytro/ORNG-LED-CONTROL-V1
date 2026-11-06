import path from "node:path";
import { fileURLToPath } from "node:url";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

const root = path.dirname(fileURLToPath(import.meta.url));

/**
 * Vitest 4 projects:
 * - unit: Node + memoryEnv (fast)
 * - workers: @cloudflare/vitest-plugin (workerd DO + WebSocket)
 *
 * DO WebSockets need shared storage: `npm test` uses --maxWorkers=1 --no-isolate.
 */
export default defineConfig({
  test: {
    globals: true,
    testTimeout: 90_000,
    projects: [
      {
        test: {
          name: "unit",
          environment: "node",
          include: ["tests/**/*.test.ts"],
          exclude: ["tests/workers/**"],
          testTimeout: 30_000,
        },
      },
      {
        plugins: [
          cloudflareTest(async () => {
            const migrations = await readD1Migrations(path.join(root, "migrations"));
            return {
              wrangler: { configPath: "./wrangler.jsonc" },
              miniflare: {
                bindings: {
                  DEV_BYPASS_ACCESS: "1",
                  DEV_ALLOW_TEST_APP_KEYS: "1",
                  DEV_ALLOW_QUERY_TOKEN: "",
                  TEST_MIGRATIONS: migrations,
                },
              },
            };
          }),
        ],
        test: {
          name: "workers",
          include: ["tests/workers/**/*.test.ts"],
          setupFiles: ["./tests/workers/apply-migrations.ts"],
          testTimeout: 90_000,
          hookTimeout: 60_000,
          fileParallelism: false,
        },
      },
    ],
  },
});
