import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../cloud/mode", () => ({
  isCloudAdminBuild: () => true,
  defaultAdminBase: () => "https://light.orng.wtf",
  defaultAccessEmail: () => "",
}));

describe("adminCloud CSRF client", () => {
  beforeEach(() => {
    vi.resetModules();
    document.cookie = "orng_admin_csrf=; Max-Age=0; path=/";
  });

  it("attaches CSRF header on mutating device commands", async () => {
    document.cookie = "orng_admin_csrf=csrf-token-abc; path=/";
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, command_id: "c1" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { adminDeviceCommand } = await import("./adminCloud");
    await adminDeviceCommand("7e4457ad-test", "blackout", { enabled: true });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers as HeadersInit);
    expect(headers.get("x-csrf-token")).toBe("csrf-token-abc");
    expect(init?.credentials).toBe("include");
    expect(String(init?.method).toUpperCase()).toBe("POST");
  });

  it("maps missing CSRF 403 to session login error", async () => {
    document.cookie = "orng_admin_csrf=stale; path=/";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: { code: "csrf_failed", message: "CSRF token missing or invalid" },
          }),
          { status: 403, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    const { adminDeviceCommand, AdminAuthError } = await import("./adminCloud");
    await expect(
      adminDeviceCommand("7e4457ad-test", "select_preset", { preset_id: "A01" }),
    ).rejects.toBeInstanceOf(AdminAuthError);
  });

  it("503 is a service error and does not end a live session", async () => {
    const session = await import("../cloud/adminSession");
    session.markAdminAuthed("ops");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("busy", { status: 503 })),
    );
    const { adminMe } = await import("./adminCloud");
    await expect(adminMe()).rejects.toThrow("Сервіс тимчасово недоступний");
    expect(session.adminAuthed.value).toBe(true);
    session.resetAdminSessionForTests();
  });

  it("login does not require prior CSRF header", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, username: "ops" }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "set-cookie": "orng_admin_csrf=fresh; Path=/; Secure",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { adminLogin } = await import("./adminCloud");
    await adminLogin("ops", "secret");

    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers as HeadersInit);
    expect(headers.get("x-csrf-token")).toBeNull();
    expect(init?.credentials).toBe("include");
  });
});
