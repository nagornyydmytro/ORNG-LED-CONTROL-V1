import { computed, inject, provide, ref, type ComputedRef, type InjectionKey } from "vue";

export interface ToastItem {
  id: number;
  message: string;
  tone: "info" | "error" | "success";
}

interface ToastApi {
  toasts: ComputedRef<ToastItem[]>;
  push: (message: string, tone?: ToastItem["tone"]) => void;
  dismiss: (id: number) => void;
}

const TOAST_KEY: InjectionKey<ToastApi> = Symbol("toasts");

export function createToastStore(): ToastApi {
  const items = ref<ToastItem[]>([]);
  let nextId = 1;

  function dismiss(id: number) {
    items.value = items.value.filter((item) => item.id !== id);
  }

  function push(message: string, tone: ToastItem["tone"] = "info") {
    const id = nextId++;
    items.value = [...items.value, { id, message, tone }];
    window.setTimeout(() => dismiss(id), 3200);
  }

  return {
    toasts: computed(() => items.value),
    push,
    dismiss,
  };
}

export function provideToasts(): ToastApi {
  const api = createToastStore();
  provide(TOAST_KEY, api);
  return api;
}

export function useToasts(): ToastApi {
  const api = inject(TOAST_KEY);
  if (!api) {
    throw new Error("Toast store is not provided");
  }
  return api;
}
