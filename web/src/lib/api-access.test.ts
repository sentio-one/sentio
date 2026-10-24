import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchJSON = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchJSON }));

import { ApiError } from "@/lib/api-error";
import { accessApi, accessProblem, describeSubject, inviteExpiryLabel, createErrorMessage } from "@/lib/api-access";

const apiError = (status: number, body: unknown) =>
  new ApiError("human sentence", { status, body: JSON.stringify(body), url: "/api/access/invites" });

beforeEach(() => fetchJSON.mockReset());

describe("accessApi", () => {
  it("lists invites and grants from the admin routes", async () => {
    fetchJSON.mockResolvedValue({ invites: [], grants: [] });
    await accessApi.listInvites();
    await accessApi.listGrants();
    expect(fetchJSON.mock.calls.map((c) => c[0])).toEqual(["/api/access/invites", "/api/access/grants"]);
  });

  it("creates an invite with a JSON body and only the fields that were set", async () => {
    fetchJSON.mockResolvedValue({ invite_id: "i1", link: "hermes://connect?x" });
    await accessApi.createInvite({ profile: "alpha", ttl_hours: 24, max_uses: 3 });
    const [url, init] = fetchJSON.mock.calls[0];
    expect(url).toBe("/api/access/invites");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ profile: "alpha", ttl_hours: 24, max_uses: 3 });
  });

  it("revokes an invite, optionally with the access it created", async () => {
    fetchJSON.mockResolvedValue({ revoked: true });
    await accessApi.revokeInvite("abc 1", false);
    await accessApi.revokeInvite("abc 1", true);
    expect(fetchJSON.mock.calls.map((c) => [c[0], c[1].method])).toEqual([
      ["/api/access/invites/abc%201", "DELETE"],
      ["/api/access/invites/abc%201?revoke_grants=true", "DELETE"],
    ]);
  });

  it("revokes a grant by subject and profile, encoding both", async () => {
    fetchJSON.mockResolvedValue({ revoked: true });
    await accessApi.revokeGrant('auth:v1:["p","https://i.example","s&t"]', "alpha");
    const url = new URL(fetchJSON.mock.calls[0][0], "https://x.example");
    expect(url.pathname).toBe("/api/access/grants");
    expect(url.searchParams.get("subject")).toBe('auth:v1:["p","https://i.example","s&t"]');
    expect(url.searchParams.get("profile")).toBe("alpha");
    expect(fetchJSON.mock.calls[0][1].method).toBe("DELETE");
  });
});

describe("accessProblem", () => {
  it("tells a non-admin from a gateway with no access policy", () => {
    expect(accessProblem(apiError(403, { detail: "admin_required" }))).toBe("forbidden");
    expect(accessProblem(apiError(409, { detail: "access_policy_not_configured" }))).toBe("not_configured");
  });

  it("does not mistake other failures for either", () => {
    expect(accessProblem(apiError(409, { detail: "something_else" }))).toBe("other");
    expect(accessProblem(apiError(500, {}))).toBe("other");
    expect(accessProblem(new Error("network"))).toBe("other");
    expect(accessProblem(null)).toBe("other");
  });
});

describe("createErrorMessage", () => {
  it("explains the codes an admin can actually act on", () => {
    expect(createErrorMessage(apiError(400, { detail: "unknown_profile" }))).toMatch(/does not exist/i);
    expect(createErrorMessage(apiError(400, { detail: "invalid_profile" }))).toMatch(/not a valid profile name/i);
  });

  it("falls back to the humanised message, and never to a raw object", () => {
    expect(createErrorMessage(apiError(400, { detail: "email must look like an address" }))).toBe("human sentence");
    expect(createErrorMessage("boom")).toBe("Could not create the invite.");
  });
});

describe("describeSubject", () => {
  it("splits the namespaced subject into the person, their issuer and provider", () => {
    expect(describeSubject('auth:v1:["self-hosted","https://sso.acme.example/realms/x","u-alice"]')).toEqual({
      sub: "u-alice",
      issuerHost: "sso.acme.example",
      provider: "self-hosted",
    });
  });

  it("shows anything it cannot parse as-is rather than hiding it", () => {
    expect(describeSubject("uid:1000")).toEqual({ sub: "uid:1000", issuerHost: "", provider: "" });
    expect(describeSubject('auth:v1:["only-one"]')).toEqual({ sub: 'auth:v1:["only-one"]', issuerHost: "", provider: "" });
    expect(describeSubject("")).toEqual({ sub: "", issuerHost: "", provider: "" });
  });
});

describe("inviteExpiryLabel", () => {
  const now = 1_800_000_000;
  it("reads as time left, then as expired", () => {
    expect(inviteExpiryLabel(now + 3 * 86400 + 100, now)).toBe("in 3 days");
    expect(inviteExpiryLabel(now + 5 * 3600 + 100, now)).toBe("in 5 hours");
    expect(inviteExpiryLabel(now + 30 * 60, now)).toBe("in under an hour");
    expect(inviteExpiryLabel(now - 5, now)).toBe("expired");
  });
});
