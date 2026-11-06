/** Compile-time app version from repo VERSION / VITE_ORNG_APP_VERSION. */
export function orngAppVersion(): string {
  return import.meta.env.VITE_ORNG_APP_VERSION || "0.0.0";
}
