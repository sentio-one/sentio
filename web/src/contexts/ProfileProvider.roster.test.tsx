// @vitest-environment jsdom
// A granted non-admin's roster lists only their own profiles (private fork, access policy). The dashboard opens on
// `default`, which they cannot reach, so it must move them onto a profile they can, not leave a page of 403s.

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

const roster = vi.hoisted(() => ({ names: [] as string[] }));

vi.mock("@/lib/api", () => ({
  api: {
    getProfiles: () => Promise.resolve({ profiles: roster.names.map((name) => ({ name })) }),
    getActiveProfile: () => Promise.resolve({ current: "default", active: "default" }),
  },
  setManagementProfile: () => {},
}));

import { ProfileProvider } from "./ProfileProvider";
import { useProfileScope } from "./useProfileScope";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function Probe() {
  const { profile } = useProfileScope();
  return <i data-profile={profile} />;
}

async function openOn(entry: string, names: string[]) {
  roster.names = names;
  window.__HERMES_DASHBOARD_PROFILE__ = "default"; // what the server injects for the main profile's dashboard
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={[entry]}>
        <ProfileProvider>
          <Probe />
        </ProfileProvider>
      </MemoryRouter>,
    ),
  );
  return container.querySelector("i")!.dataset.profile;
}

afterEach(async () => {
  delete window.__HERMES_DASHBOARD_PROFILE__;
  await act(async () => root?.unmount());
  container?.remove();
});

describe("ProfileProvider and the caller's roster", () => {
  it("moves a non-admin off `default` onto the profile they were granted", async () => {
    expect(await openOn("/chat", ["sentio-sales"])).toBe("sentio-sales");
  });

  it("moves them even when the URL names a profile they cannot reach", async () => {
    expect(await openOn("/chat?profile=default", ["sentio-sales"])).toBe("sentio-sales");
  });

  it("leaves an admin, whose roster lists every profile, where they were", async () => {
    expect(await openOn("/chat", ["default", "sentio-sales"])).toBe("default");
  });
});
