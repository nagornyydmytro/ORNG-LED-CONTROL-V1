import type { InjectionKey } from "vue";
import type { useAppState } from "./useAppState";

export type AppStateApi = ReturnType<typeof useAppState>;
export const APP_STATE_KEY: InjectionKey<AppStateApi> = Symbol("app-state");
