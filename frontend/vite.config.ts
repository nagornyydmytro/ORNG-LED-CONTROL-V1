import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

function resolveAppVersion(): string {
  const fromEnv = process.env.VITE_ORNG_APP_VERSION?.trim();
  if (fromEnv) return fromEnv;
  try {
    return readFileSync(resolve(__dirname, "../VERSION"), "utf8").trim() || "0.0.0";
  } catch {
    return "0.0.0";
  }
}

export default defineConfig(({ mode }) => {
  const isCloud = mode === "cloud";
  const appVersion = resolveAppVersion();

  return {
    plugins: [
      vue(),
      isCloud
        ? {
            name: "orng-copy-auto-preview-pack",
            closeBundle() {
              const scenes = resolve(__dirname, "../cloud/src/scenes");
              const destDir = resolve(__dirname, "../cloud/admin-dist");
              const clipsSrc = join(scenes, "autoPreviewClips");
              if (!existsSync(clipsSrc)) return;
              mkdirSync(destDir, { recursive: true });
              const clipsDest = join(destDir, "autoPreviewClips");
              mkdirSync(clipsDest, { recursive: true });
              for (const name of readdirSync(clipsSrc)) {
                if (!name.endsWith(".json.gz")) continue;
                copyFileSync(join(clipsSrc, name), join(clipsDest, name));
              }
              const meta = join(scenes, "autoPreviewPack.meta.json");
              if (existsSync(meta)) copyFileSync(meta, join(destDir, "autoPreviewPack.meta.json"));
            },
          }
        : null,
    ].filter(Boolean),
    define: {
      // Compile-time flag for cloud admin SPA (light.orng.wtf / admin-dist).
      "import.meta.env.VITE_ORNG_CLOUD_ADMIN": JSON.stringify(isCloud ? "true" : "false"),
      "import.meta.env.VITE_ORNG_APP_VERSION": JSON.stringify(appVersion),
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: process.env.ORNG_DEV_API || "http://127.0.0.1:8000",
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
    },
    build: {
      outDir: isCloud ? "../cloud/admin-dist" : "dist",
      emptyOutDir: true,
    },
    test: {
      environment: "jsdom",
      globals: true,
      include: ["src/**/*.spec.ts"],
      setupFiles: ["src/test/setup.ts"],
    },
  };
});
