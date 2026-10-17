import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Trash2 } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import {
  accessApi,
  accessProblem,
  createErrorMessage,
  describeSubject,
  inviteExpiryLabel,
} from "@/lib/api-access";
import type { AccessGrant, AccessInvite, CreatedInvite } from "@/lib/api-access";

type Load = "loading" | "ready" | "forbidden" | "not_configured" | "error";

type Confirm =
  | { kind: "invite"; id: string; profile: string; alsoGrants: boolean }
  | { kind: "grant"; subject: string; profile: string; label: string };

const FIELD =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-primary";

function when(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString();
}

function wholeNumber(text: string, low: number, high: number): number | null {
  const n = Number(text);
  return Number.isInteger(n) && n >= low && n <= high ? n : null;
}

export default function AccessPage() {
  const [load, setLoad] = useState<Load>("loading");
  const [loadError, setLoadError] = useState("");
  const [invites, setInvites] = useState<AccessInvite[]>([]);
  const [grants, setGrants] = useState<AccessGrant[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [form, setForm] = useState({ profile: "", ttl: "168", uses: "1", email: "" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [actionError, setActionError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [inviteList, grantList] = await Promise.all([accessApi.listInvites(), accessApi.listGrants()]);
      setInvites(inviteList.invites);
      setGrants(grantList.grants);
      setLoad("ready");
    } catch (error) {
      const problem = accessProblem(error);
      setLoadError(errorMessage(error));
      setLoad(problem === "other" ? "error" : problem);
    }
  }, []);

  useEffect(() => {
    void refresh();
    api
      .getProfiles()
      .then((res) => setProfiles((res.profiles ?? []).map((p: { name: string }) => p.name)))
      .catch(() => setProfiles([]));
  }, [refresh]);

  const ttl = wholeNumber(form.ttl, 1, 720);
  const uses = wholeNumber(form.uses, 1, 1000);
  const canCreate = Boolean(form.profile.trim()) && ttl !== null && uses !== null && !creating;

  const create = async () => {
    if (!canCreate || ttl === null || uses === null) return;
    setCreating(true);
    setCreateError("");
    try {
      const email = form.email.trim();
      const result = await accessApi.createInvite({
        profile: form.profile.trim(),
        ttl_hours: ttl,
        max_uses: uses,
        ...(email ? { email } : {}),
      });
      setCreated(result);
      setCopied(false);
      setForm((f) => ({ ...f, profile: "", email: "" }));
      await refresh();
    } catch (error) {
      setCreateError(createErrorMessage(error));
    } finally {
      setCreating(false);
    }
  };

  const copyLink = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const confirmRevoke = async () => {
    if (!confirm) return;
    setRevoking(true);
    setActionError("");
    try {
      if (confirm.kind === "invite") await accessApi.revokeInvite(confirm.id, confirm.alsoGrants);
      else await accessApi.revokeGrant(confirm.subject, confirm.profile);
      setConfirm(null);
      await refresh();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setRevoking(false);
    }
  };

  if (load === "loading") {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  if (load === "forbidden") {
    return <p className="py-12 text-sm text-muted-foreground">Only administrators can manage invites and access.</p>;
  }

  if (load === "not_configured") {
    return (
      <p className="py-12 text-sm text-muted-foreground">
        This server's access policy is not configured, so there are no per-profile invites. Configure it (see the
        deployment guide), then reload this page.
      </p>
    );
  }

  if (load === "error") {
    return (
      <div className="flex flex-col items-start gap-3 py-12 text-sm">
        <p className="text-destructive">{loadError || "Could not load invites and access."}</p>
        <Button size="sm" onClick={() => void refresh()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Invite someone to a profile</h2>
        <p className="text-sm text-muted-foreground">
          Creates a link that opens the Hermes desktop app, signs the person in with your company login and gives
          them access to this one profile.
        </p>
        <div className="grid gap-3 sm:grid-cols-4">
          <label className="flex flex-col gap-1 text-xs font-medium">
            Profile
            <input
              aria-label="Profile"
              className={FIELD}
              list="access-profiles"
              value={form.profile}
              onChange={(e) => setForm((f) => ({ ...f, profile: e.target.value }))}
            />
            <datalist id="access-profiles">
              {profiles.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium">
            Valid for (hours)
            <input
              aria-label="Valid for (hours)"
              className={FIELD}
              type="number"
              min={1}
              max={720}
              value={form.ttl}
              onChange={(e) => setForm((f) => ({ ...f, ttl: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium">
            Maximum uses
            <input
              aria-label="Maximum uses"
              className={FIELD}
              type="number"
              min={1}
              max={1000}
              value={form.uses}
              onChange={(e) => setForm((f) => ({ ...f, uses: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium">
            Restrict to email (optional)
            <input
              aria-label="Restrict to email (optional)"
              className={FIELD}
              type="email"
              value={form.email}
              onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
            />
          </label>
        </div>
        <div className="flex items-center gap-3">
          <Button size="sm" onClick={() => void create()} disabled={!canCreate}>
            Create invite
          </Button>
          {createError ? <span className="text-sm text-destructive">{createError}</span> : null}
        </div>

        {created ? (
          <div className="flex flex-col gap-2 rounded-md border border-border p-4">
            <p className="text-sm font-medium">Invite for {created.profile} is ready.</p>
            <p className="text-xs text-muted-foreground">
              This link is shown only once and is not stored. Copy it now and send it to the person.
            </p>
            <div className="flex gap-2">
              <input
                data-testid="new-invite-link"
                readOnly
                className={FIELD}
                value={created.link}
                onFocus={(e) => e.currentTarget.select()}
              />
              <Button size="sm" onClick={() => void copyLink()} prefix={copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}>
                {copied ? "Copied" : "Copy link"}
              </Button>
              <Button size="sm" onClick={() => setCreated(null)}>
                Done
              </Button>
            </div>
          </div>
        ) : null}
      </section>

      {confirm ? (
        <section className="flex flex-col gap-3 rounded-md border border-destructive/50 p-4" role="alertdialog">
          <p className="text-sm font-medium">
            {confirm.kind === "invite"
              ? `Revoke the invite for ${confirm.profile}?`
              : `Take away ${confirm.label}'s access to ${confirm.profile}?`}
          </p>
          {confirm.kind === "invite" ? (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                aria-label="Also remove access this invite created"
                checked={confirm.alsoGrants}
                onChange={(e) => setConfirm({ ...confirm, alsoGrants: e.target.checked })}
              />
              Also remove access this invite created
            </label>
          ) : null}
          {actionError ? <p className="text-sm text-destructive">{actionError}</p> : null}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void confirmRevoke()} disabled={revoking}>
              Confirm revoke
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setConfirm(null);
                setActionError("");
              }}
            >
              Cancel
            </Button>
          </div>
        </section>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Invites</h2>
        {invites.length === 0 ? (
          <p className="text-sm text-muted-foreground">No invites yet.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1">Profile</th>
                <th>Status</th>
                <th>Uses</th>
                <th>Expires</th>
                <th>Email</th>
                <th>Created by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {invites.map((inv) => (
                <tr key={inv.id} data-testid="invite-row" className="border-t border-border">
                  <td className="py-2 font-medium">{inv.profile}</td>
                  <td>
                    <Badge>{inv.status.replace("_", " ")}</Badge>
                  </td>
                  <td>
                    {inv.uses} / {inv.max_uses}
                  </td>
                  <td title={when(inv.expires_at)}>{inviteExpiryLabel(inv.expires_at)}</td>
                  <td>{inv.email ?? ""}</td>
                  <td title={inv.created_by}>{describeSubject(inv.created_by).sub}</td>
                  <td className="text-right">
                    {inv.status === "revoked" ? null : (
                      <Button
                        size="sm"
                        aria-label={`Revoke invite ${inv.id}`}
                        prefix={<Trash2 className="h-4 w-4" />}
                        onClick={() => setConfirm({ kind: "invite", id: inv.id, profile: inv.profile, alsoGrants: false })}
                      >
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">People with access from invites</h2>
        {grants.length === 0 ? (
          <p className="text-sm text-muted-foreground">No one has been granted access through an invite yet.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1">Person</th>
                <th>Sign-in</th>
                <th>Profile</th>
                <th>Since</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {grants.map((g) => {
                const who = describeSubject(g.subject);
                return (
                  <tr key={`${g.subject}|${g.profile}`} data-testid="grant-row" className="border-t border-border">
                    <td className="py-2 font-medium" title={g.subject}>
                      {who.sub}
                    </td>
                    <td className="text-muted-foreground">{[who.issuerHost, who.provider].filter(Boolean).join(" · ")}</td>
                    <td>{g.profile}</td>
                    <td>{when(g.granted_at)}</td>
                    <td className="text-right">
                      <Button
                        size="sm"
                        aria-label={`Revoke access ${g.profile} for ${who.sub}`}
                        prefix={<Trash2 className="h-4 w-4" />}
                        onClick={() => setConfirm({ kind: "grant", subject: g.subject, profile: g.profile, label: who.sub })}
                      >
                        Revoke
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="text-xs text-muted-foreground">
          Access that comes from your company login groups is managed there, not here.
        </p>
      </section>
    </div>
  );
}
