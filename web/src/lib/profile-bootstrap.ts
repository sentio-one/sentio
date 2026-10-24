declare global {
  interface Window {
    __HERMES_DASHBOARD_PROFILE__?: string;
    __HERMES_INITIAL_PROFILE__?: string;
  }
}

export function dashboardInitialProfile(): string {
  if (typeof window === "undefined") return "";
  return window.__HERMES_INITIAL_PROFILE__ ?? "";
}

/**
 * The profile this backend process itself serves, injected by the server and
 * empty when it cannot be named unambiguously (custom HERMES_HOME).
 *
 * It is the LAST fallback for the management scope: without it the dashboard
 * sends no `?profile=` at all, which a multi-profile host now refuses (400) on
 * every destructive route. It is never a guess — the backend only emits a name
 * that provably resolves back to its own home, so it targets exactly the home
 * an unnamed request used to reach.
 */
export function dashboardServingProfile(): string {
  if (typeof window === "undefined") return "";
  return window.__HERMES_DASHBOARD_PROFILE__ ?? "";
}

export function initialProfileScope(
  searchParams: URLSearchParams,
  bootstrapProfile = dashboardInitialProfile(),
  servingProfile = dashboardServingProfile(),
): string {
  const urlProfile = searchParams.get("profile");
  if (urlProfile !== null) return urlProfile;
  return bootstrapProfile || servingProfile;
}

export function shouldAdoptActiveProfile(
  urlProfile: string | null,
  bootstrapProfile: string,
  currentProfile: string,
  activeProfile: string,
): boolean {
  // Only the machine (default-hosted) dashboard aligns to the sticky active
  // profile. A dashboard hosted by a named profile (`hermes -p demo serve`)
  // serves THAT agent: adopting the machine-global active_profile would
  // silently retarget its banner, chat TUI env and model to another profile.
  return (
    urlProfile === null &&
    !bootstrapProfile &&
    currentProfile === "default" &&
    activeProfile !== currentProfile
  );
}

/**
 * The profile to switch to when the scope the dashboard opened on is not one the caller's roster lists, or null to stay.
 * The roster is filtered to what the caller may reach, so a granted non-admin opening on `default` lands on a profile of
 * their own instead of a page of 403s. An empty roster, or a scope that names no profile (never guessed, see
 * `dashboardServingProfile`), changes nothing.
 */
export function profileToAdopt(roster: string[], profile: string): string | null {
  return profile && roster.length > 0 && !roster.includes(profile) ? roster[0] : null;
}
