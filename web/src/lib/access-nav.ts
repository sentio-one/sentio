/** Whether the dashboard should offer the Access page (invites and grants), from `/api/auth/me`'s `access` block. */

export interface AccessSummary {
  configured: boolean;
  admin: boolean;
  profiles: string[];
}

/** Only an administrator on a server that has an access policy; anything missing or malformed means no. */
export function canManageAccess(summary: AccessSummary | null | undefined): boolean {
  return summary?.configured === true && summary.admin === true;
}

/** The nav without the Access entry unless the caller may manage access. Never mutates its input. */
export function withoutAccessNav<T extends { path: string }>(items: T[], allowed: boolean): T[] {
  return allowed ? items : items.filter((item) => item.path !== "/access");
}
