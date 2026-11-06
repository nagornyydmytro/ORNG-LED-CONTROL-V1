import type { Env } from "../env";

export function deviceStub(env: Env, installationId: string): DurableObjectStub {
  const id = env.DEVICE_DO.idFromName(installationId);
  return env.DEVICE_DO.get(id);
}

export async function forwardDeviceWs(
  env: Env,
  installationId: string,
  request: Request,
): Promise<Response> {
  const stub = deviceStub(env, installationId);
  const headers = new Headers(request.headers);
  headers.set("x-installation-id", installationId);
  // Preserve Upgrade / Connection for hibernation WebSocket accept.
  return stub.fetch(
    new Request("https://device-do/connect/device", {
      method: request.method,
      headers,
    }),
  );
}

export async function forwardAdminWs(
  env: Env,
  installationId: string,
  request: Request,
  source: string,
): Promise<Response> {
  const stub = deviceStub(env, installationId);
  const headers = new Headers(request.headers);
  headers.set("x-installation-id", installationId);
  headers.set("x-admin-source", source);
  return stub.fetch(
    new Request("https://device-do/connect/admin", {
      method: request.method,
      headers,
    }),
  );
}
