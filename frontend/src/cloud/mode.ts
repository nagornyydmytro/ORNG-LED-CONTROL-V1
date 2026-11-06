/** True when Vite built with `--mode cloud` (admin SPA for light.orng.wtf). */
export function isCloudAdminBuild(): boolean {
  return import.meta.env.VITE_ORNG_CLOUD_ADMIN === "true";
}

/** Local FastAPI UI may expose a DEV-only approve helper when this is set. */
export function isLocalCloudDevApproveEnabled(): boolean {
  if (isCloudAdminBuild()) return false;
  return import.meta.env.VITE_ORNG_CLOUD_DEV_APPROVE === "true";
}

export function defaultAdminBase(): string {
  if (isCloudAdminBuild()) {
    return typeof window !== "undefined" ? window.location.origin : "";
  }
  try {
    return localStorage.getItem("orng-cloud-admin") || "http://127.0.0.1:8787";
  } catch {
    return "http://127.0.0.1:8787";
  }
}

export function defaultAccessEmail(): string {
  try {
    return localStorage.getItem("orng-cloud-access-email") || "dev@localhost";
  } catch {
    return "dev@localhost";
  }
}
