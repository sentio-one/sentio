// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const accessMocks = vi.hoisted(() => ({
  listInvites: vi.fn(),
  listGrants: vi.fn(),
  createInvite: vi.fn(),
  revokeInvite: vi.fn(),
  revokeGrant: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({ getProfiles: vi.fn() }));

vi.mock("@/lib/api-access", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-access")>()),
  accessApi: accessMocks,
}));
vi.mock("@/lib/api", () => ({ api: apiMocks, fetchJSON: vi.fn() }));
vi.mock("@/contexts/usePageHeader", () => ({ usePageHeader: () => ({ setEnd: vi.fn(), setTitle: vi.fn() }) }));

import { ApiError } from "@/lib/api-error";
// Imported once, up front: loading the UI library takes longer than a test is allowed to, so it must not happen inside one.
import AccessPage from "./AccessPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SUB_ALICE = 'auth:v1:["self-hosted","https://sso.acme.example","u-alice"]';
const NOW = Math.floor(Date.now() / 1000);

const invite = (over: Record<string, unknown> = {}) => ({
  id: "inv1", profile: "alpha", created_by: SUB_ALICE, created_at: NOW - 60, expires_at: NOW + 3 * 86400 + 600,
  max_uses: 1, uses: 0, email: null, revoked_at: null, status: "active", ...over,
});
const grant = (over: Record<string, unknown> = {}) => ({
  subject: SUB_ALICE, profile: "alpha", invite_id: "inv1", granted_at: NOW - 30, ...over,
});

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderPage() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<AccessPage />));
  await flush();
}

const $ = (selector: string) => container.querySelector(selector) as HTMLElement | null;
const $$ = (selector: string) => [...container.querySelectorAll(selector)] as HTMLElement[];
const byText = (text: string, tag = "button") => $$(tag).find((el) => el.textContent?.trim() === text) ?? null;

async function click(el: Element | null) {
  if (!el) throw new Error("element not rendered");
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await flush();
}

async function type(el: HTMLElement | null, value: string) {
  if (!el) throw new Error("field not rendered");
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const field = (label: string) => $(`input[aria-label="${label}"]`);

beforeEach(() => {
  for (const fn of [...Object.values(accessMocks), ...Object.values(apiMocks)]) fn.mockReset();
  accessMocks.listInvites.mockResolvedValue({ invites: [invite()] });
  accessMocks.listGrants.mockResolvedValue({ grants: [grant()] });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [{ name: "default" }, { name: "alpha" }, { name: "beta" }] });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("AccessPage", () => {
  it("lists invites and grants with who, what and how long", async () => {
    await renderPage();

    const [row] = $$('[data-testid="invite-row"]');
    expect(row.textContent).toContain("alpha");
    expect(row.textContent).toContain("active");
    expect(row.textContent).toContain("0 / 1");
    expect(row.textContent).toMatch(/in 3 days/);
    const [grantRow] = $$('[data-testid="grant-row"]');
    expect(grantRow.textContent).toContain("u-alice");
    expect(grantRow.textContent).toContain("sso.acme.example");
    expect(grantRow.textContent).toContain("alpha");
  });

  it("says so plainly when there is nothing yet", async () => {
    accessMocks.listInvites.mockResolvedValue({ invites: [] });
    accessMocks.listGrants.mockResolvedValue({ grants: [] });
    await renderPage();

    expect($$('[data-testid="invite-row"]')).toHaveLength(0);
    expect(container.textContent).toMatch(/No invites yet/);
    expect(container.textContent).toMatch(/No one has been granted access/);
  });

  it("shows a non-admin an admins-only notice and no controls", async () => {
    accessMocks.listInvites.mockRejectedValue(new ApiError("no", { status: 403, body: '{"detail":"admin_required"}', url: "/x" }));
    await renderPage();

    expect(container.textContent).toMatch(/Only administrators/i);
    expect(byText("Create invite")).toBeNull();
  });

  it("explains a gateway with no access policy instead of showing a broken page", async () => {
    accessMocks.listInvites.mockRejectedValue(
      new ApiError("conflict", { status: 409, body: '{"detail":"access_policy_not_configured"}', url: "/x" }),
    );
    await renderPage();

    expect(container.textContent).toMatch(/access policy is not configured/i);
    expect(byText("Create invite")).toBeNull();
  });

  it("creates an invite from the form and shows the link exactly once", async () => {
    accessMocks.createInvite.mockResolvedValue({
      invite_id: "inv2", profile: "beta", expires_at: NOW + 86400, link: "hermes://connect?url=https%3A%2F%2Fgw.acme.example&invite=SECRETTOKEN",
    });
    await renderPage();

    await type(field("Profile"), "beta");
    await type(field("Valid for (hours)"), "48");
    await type(field("Maximum uses"), "3");
    await type(field("Restrict to email (optional)"), "bob@acme.example");
    await click(byText("Create invite"));

    expect(accessMocks.createInvite).toHaveBeenCalledWith({ profile: "beta", ttl_hours: 48, max_uses: 3, email: "bob@acme.example" });
    const shown = $('[data-testid="new-invite-link"]') as HTMLInputElement;
    expect(shown.value).toContain("SECRETTOKEN");
    expect(container.textContent).toMatch(/shown only once/i);
    expect(accessMocks.listInvites.mock.calls.length).toBeGreaterThanOrEqual(2); // refreshed after creating

    await click(byText("Done"));
    expect($('[data-testid="new-invite-link"]')).toBeNull();
    expect(container.textContent).not.toContain("SECRETTOKEN");
  });

  it("leaves the email out of the request when it is blank and needs a profile to create", async () => {
    accessMocks.createInvite.mockResolvedValue({ invite_id: "i", profile: "alpha", expires_at: NOW + 1, link: "hermes://connect?x" });
    await renderPage();

    expect((byText("Create invite") as HTMLButtonElement).disabled).toBe(true);
    await type(field("Profile"), "alpha");
    expect((byText("Create invite") as HTMLButtonElement).disabled).toBe(false);
    await click(byText("Create invite"));

    expect(accessMocks.createInvite.mock.calls[0][0]).toEqual({ profile: "alpha", ttl_hours: 168, max_uses: 1 });
  });

  it("offers the profiles that exist as suggestions", async () => {
    await renderPage();

    const options = $$("datalist option").map((o) => o.getAttribute("value"));
    expect(options).toEqual(["default", "alpha", "beta"]);
  });

  it("reports a refused create in words and keeps what was typed", async () => {
    accessMocks.createInvite.mockRejectedValue(new ApiError("bad", { status: 400, body: '{"detail":"unknown_profile"}', url: "/x" }));
    await renderPage();
    await type(field("Profile"), "ghost");
    await click(byText("Create invite"));

    expect(container.textContent).toMatch(/does not exist/i);
    expect((field("Profile") as HTMLInputElement).value).toBe("ghost");
    expect($('[data-testid="new-invite-link"]')).toBeNull();
  });

  it("asks before revoking an invite, and can also take back the access it created", async () => {
    accessMocks.revokeInvite.mockResolvedValue({ revoked: true });
    await renderPage();

    await click($('[aria-label="Revoke invite inv1"]'));
    expect(accessMocks.revokeInvite).not.toHaveBeenCalled();
    await click(byText("Cancel"));
    expect(accessMocks.revokeInvite).not.toHaveBeenCalled();

    await click($('[aria-label="Revoke invite inv1"]'));
    await click(byText("Confirm revoke"));
    expect(accessMocks.revokeInvite).toHaveBeenLastCalledWith("inv1", false);

    await click($('[aria-label="Revoke invite inv1"]'));
    await click($('input[aria-label="Also remove access this invite created"]'));
    await click(byText("Confirm revoke"));
    expect(accessMocks.revokeInvite).toHaveBeenLastCalledWith("inv1", true);
  });

  it("does not offer to revoke an invite that is already revoked", async () => {
    accessMocks.listInvites.mockResolvedValue({ invites: [invite({ status: "revoked", revoked_at: NOW })] });
    await renderPage();

    expect($('[aria-label="Revoke invite inv1"]')).toBeNull();
    expect($('[data-testid="invite-row"]')?.textContent).toContain("revoked");
  });

  it("asks before taking a person's access away, then refreshes", async () => {
    accessMocks.revokeGrant.mockResolvedValue({ revoked: true });
    await renderPage();
    const before = accessMocks.listGrants.mock.calls.length;

    await click($('[aria-label="Revoke access alpha for u-alice"]'));
    expect(accessMocks.revokeGrant).not.toHaveBeenCalled();
    await click(byText("Confirm revoke"));

    expect(accessMocks.revokeGrant).toHaveBeenCalledWith(SUB_ALICE, "alpha");
    expect(accessMocks.listGrants.mock.calls.length).toBeGreaterThan(before);
  });

  it("shows a failed revoke instead of pretending it worked", async () => {
    accessMocks.revokeGrant.mockRejectedValue(new ApiError("The server could not find what the dashboard asked for.", { status: 404, body: "{}", url: "/x" }));
    await renderPage();
    await click($('[aria-label="Revoke access alpha for u-alice"]'));
    await click(byText("Confirm revoke"));

    expect(container.textContent).toMatch(/could not find/i);
    expect($$('[data-testid="grant-row"]')).toHaveLength(1);
  });
});
