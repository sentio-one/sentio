/** Admin API for per-profile invites and grants (`/api/access/*`, gateway/access_grants.py). */
import { fetchJSON } from "@/lib/api";
import { ApiError } from "@/lib/api-error";

export type InviteStatus = "active" | "used_up" | "expired" | "revoked";

export interface AccessInvite {
  id: string;
  profile: string;
  created_by: string;
  created_at: number;
  expires_at: number;
  max_uses: number;
  uses: number;
  email: string | null;
  revoked_at: number | null;
  status: InviteStatus;
}

export interface AccessGrant {
  subject: string;
  profile: string;
  invite_id: string | null;
  granted_at: number;
}

export interface InviteRequest {
  profile: string;
  ttl_hours: number;
  max_uses: number;
  email?: string;
}

/** The only response that carries the link; the gateway never returns the token again. */
export interface CreatedInvite {
  invite_id: string;
  profile: string;
  expires_at: number;
  link: string;
}

export const accessApi = {
  listInvites: () => fetchJSON<{ invites: AccessInvite[] }>("/api/access/invites"),
  listGrants: () => fetchJSON<{ grants: AccessGrant[] }>("/api/access/grants"),
  createInvite: (body: InviteRequest) =>
    fetchJSON<CreatedInvite>("/api/access/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  revokeInvite: (id: string, revokeGrants: boolean) =>
    fetchJSON<{ revoked: boolean }>(
      `/api/access/invites/${encodeURIComponent(id)}${revokeGrants ? "?revoke_grants=true" : ""}`,
      { method: "DELETE" },
    ),
  revokeGrant: (subject: string, profile: string) =>
    fetchJSON<{ revoked: boolean }>(
      `/api/access/grants?${new URLSearchParams({ subject, profile }).toString()}`,
      { method: "DELETE" },
    ),
};

export type AccessProblem = "forbidden" | "not_configured" | "other";

function detailCode(error: ApiError): string {
  try {
    const parsed: unknown = JSON.parse(error.body);
    const detail = parsed && typeof parsed === "object" ? (parsed as { detail?: unknown }).detail : null;
    return typeof detail === "string" ? detail : "";
  } catch {
    return "";
  }
}

/** Why the page cannot be used: not an administrator, or the gateway has no access policy at all. */
export function accessProblem(error: unknown): AccessProblem {
  if (!(error instanceof ApiError)) return "other";
  if (error.status === 403) return "forbidden";
  if (error.status === 409 && detailCode(error) === "access_policy_not_configured") return "not_configured";
  return "other";
}

const CREATE_ERRORS: Record<string, string> = {
  unknown_profile: "That profile does not exist on this server.",
  invalid_profile: "That is not a valid profile name.",
};

export function createErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Could not create the invite.";
  return CREATE_ERRORS[detailCode(error)] ?? error.message;
}

/** `auth:v1:["provider","issuer","sub"]` is how the gateway names a person; anything else is shown as it is. */
export function describeSubject(subject: string): { sub: string; issuerHost: string; provider: string } {
  const prefix = "auth:v1:";
  if (subject.startsWith(prefix)) {
    try {
      const parts: unknown = JSON.parse(subject.slice(prefix.length));
      if (Array.isArray(parts) && parts.length === 3 && parts.every((p) => typeof p === "string")) {
        const [provider, issuer, sub] = parts as string[];
        let issuerHost = "";
        try {
          issuerHost = new URL(issuer).host;
        } catch {
          issuerHost = issuer;
        }
        return { sub, issuerHost, provider };
      }
    } catch {
      // fall through: show the raw subject
    }
  }
  return { sub: subject, issuerHost: "", provider: "" };
}

export function inviteExpiryLabel(expiresAt: number, now: number = Math.floor(Date.now() / 1000)): string {
  const left = expiresAt - now;
  if (left <= 0) return "expired";
  const days = Math.floor(left / 86400);
  if (days >= 1) return `in ${days} day${days === 1 ? "" : "s"}`;
  const hours = Math.floor(left / 3600);
  if (hours >= 1) return `in ${hours} hour${hours === 1 ? "" : "s"}`;
  return "in under an hour";
}
