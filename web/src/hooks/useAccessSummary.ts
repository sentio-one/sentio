import { useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";
import { canManageAccess } from "@/lib/access-nav";
import type { AccessSummary } from "@/lib/access-nav";

/** The signed-in user's standing under the access policy. Starts hidden and stays hidden if it cannot tell. */
export function useAccessSummary(): { loaded: boolean; canManageAccess: boolean } {
  const [state, setState] = useState({ loaded: false, canManageAccess: false });

  useEffect(() => {
    let cancelled = false;
    fetchJSON<{ access?: AccessSummary }>("/api/auth/me")
      .then((me) => !cancelled && setState({ loaded: true, canManageAccess: canManageAccess(me?.access) }))
      .catch(() => !cancelled && setState({ loaded: true, canManageAccess: false }));
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
