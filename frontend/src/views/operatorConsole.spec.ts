import { describe, expect, it } from "vitest";
import type { AdminDevice } from "../api/adminCloud";

function operatorLocations(devices: AdminDevice[]): AdminDevice[] {
  return devices.filter((d) => !d.revoked && d.online);
}

function autoSelectId(devices: AdminDevice[], remembered: string | null): string | null {
  const online = operatorLocations(devices).map((d) => d.id);
  if (online.length === 1) return online[0]!;
  if (remembered && online.includes(remembered)) return remembered;
  return null;
}

describe("operator location selection", () => {
  const online: AdminDevice = {
    id: "7e4457ad-c47e-4aaf-beba-87f83a1c92d0",
    name: "ORNG BAR",
    platform: "raspberry",
    online: true,
    revoked: false,
    current_scene_id: "orng-bar",
    current_release_version: 1,
    armed: false,
    blackout: true,
    last_seen_at: null,
  };
  const stale: AdminDevice = {
    id: "00000000-stale-offline",
    name: "ORNG BAR",
    platform: "raspberry",
    online: false,
    revoked: false,
    current_scene_id: "orng-bar",
    current_release_version: 1,
    armed: false,
    blackout: true,
    last_seen_at: null,
  };

  it("hides offline/stale from operator locations", () => {
    expect(operatorLocations([online, stale])).toEqual([online]);
  });

  it("auto-selects the single online location", () => {
    expect(autoSelectId([online, stale], null)).toBe(online.id);
  });

  it("blocks offline commands conceptually", () => {
    const locations = operatorLocations([stale]);
    expect(locations).toHaveLength(0);
  });
});
