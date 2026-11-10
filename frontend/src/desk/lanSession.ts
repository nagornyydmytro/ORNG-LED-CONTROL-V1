/** Same-origin check for the venue LAN desk. Absent on the loopback API and on the cloud build. */

export type LanPhase = "checking" | "loopback" | "login" | "open";

export async function probeLanSession(): Promise<Exclude<LanPhase, "checking">> {
  try {
    const response = await fetch("/api/lan/session");
    if (response.status === 404) return "loopback";
    if (!response.ok) return "loopback";
    const body = (await response.json()) as { lan_desk?: boolean; authenticated?: boolean };
    if (body.lan_desk !== true) return "loopback";
    return body.authenticated ? "open" : "login";
  } catch {
    return "loopback";
  }
}

export async function loginLanDesk(password: string): Promise<void> {
  const response = await fetch("/api/lan/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    let detail = "Вхід не виконано";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* the status is enough */
    }
    throw new Error(detail);
  }
}
