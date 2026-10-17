// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchJSON = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchJSON }));

import { canManageAccess, withoutAccessNav } from "@/lib/access-nav";
import { useAccessSummary } from "@/hooks/useAccessSummary";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const items = [{ path: "/sessions" }, { path: "/access" }, { path: "/config" }];

describe("canManageAccess", () => {
  it("is true only for an administrator on a server that has an access policy", () => {
    expect(canManageAccess({ configured: true, admin: true, profiles: [] })).toBe(true);
    expect(canManageAccess({ configured: true, admin: false, profiles: ["alpha"] })).toBe(false);
    expect(canManageAccess({ configured: false, admin: true, profiles: [] })).toBe(false);
  });

  it("fails closed when the summary is missing or malformed", () => {
    expect(canManageAccess(null)).toBe(false);
    expect(canManageAccess(undefined)).toBe(false);
    expect(canManageAccess({} as never)).toBe(false);
    expect(canManageAccess({ configured: "yes", admin: 1 } as never)).toBe(false);
  });
});

describe("withoutAccessNav", () => {
  it("drops only the Access entry, keeping everything else in order", () => {
    expect(withoutAccessNav(items, false).map((i) => i.path)).toEqual(["/sessions", "/config"]);
  });

  it("keeps it for someone allowed to manage access, and does not mutate its input", () => {
    expect(withoutAccessNav(items, true)).toEqual(items);
    withoutAccessNav(items, false);
    expect(items).toHaveLength(3);
  });
});

describe("useAccessSummary", () => {
  let container: HTMLDivElement;

  function Probe() {
    const state = useAccessSummary();
    return createElement("pre", { "data-testid": "state" }, JSON.stringify(state));
  }

  async function mount() {
    container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => root.render(createElement(Probe)));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    return root;
  }

  const state = () => JSON.parse(container.querySelector("pre")!.textContent!);

  // Braces matter: a hook that returns a function is treated as its own cleanup, and mockReset() returns the mock.
  beforeEach(() => {
    fetchJSON.mockReset();
  });
  afterEach(() => {
    container?.remove();
  });

  it("reads the caller's standing from /api/auth/me", async () => {
    fetchJSON.mockResolvedValue({ user_id: "u", access: { configured: true, admin: true, profiles: [] } });
    await mount();

    expect(fetchJSON.mock.calls[0][0]).toBe("/api/auth/me");
    expect(state()).toEqual({ loaded: true, canManageAccess: true });
  });

  it("hides the entry for a non-admin", async () => {
    fetchJSON.mockResolvedValue({ access: { configured: true, admin: false, profiles: ["alpha"] } });
    await mount();

    expect(state()).toEqual({ loaded: true, canManageAccess: false });
  });

  it("hides it, rather than showing it, when the request fails or an older server sends no summary", async () => {
    fetchJSON.mockRejectedValueOnce(new Error("401"));
    await mount();
    expect(state()).toEqual({ loaded: true, canManageAccess: false });

    container.remove();
    fetchJSON.mockResolvedValueOnce({ user_id: "u" });
    await mount();
    expect(state()).toEqual({ loaded: true, canManageAccess: false });
  });

  it("starts hidden while it is still asking", async () => {
    fetchJSON.mockReturnValue(new Promise(() => {}));
    container = document.createElement("div");
    document.body.append(container);
    await act(async () => createRoot(container).render(createElement(Probe)));

    expect(state()).toEqual({ loaded: false, canManageAccess: false });
  });
});
