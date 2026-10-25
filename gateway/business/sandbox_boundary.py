"""The same-user control socket cannot tell an operator from the agent's own tools.

Approval integrity therefore rests on the owning profile's agent tools running outside
the gateway host (docs/private-fork/SANDBOX_PLAN.md). A profile whose terminal backend is
``local`` shares the gateway's uid, socket and credentials.
"""

from __future__ import annotations

from gateway.business.models import BusinessDenied

# Operations that only reduce or inspect sending; every other operation can authorize it.
SOCKET_SAFE_OPERATIONS = frozenset({"queue", "show", "tasks", "status", "readiness", "digest", "pause", "reject"})


def agent_tools_sandboxed(service, runtime_profile: str) -> bool:
    """True only when the profile's resolved terminal policy runs tools off the host; unresolvable is False."""
    from gateway.business.control import profile_home
    from tools.terminal_scope import TerminalPolicyUnavailable, build_profile_terminal_scope
    try:
        scope = build_profile_terminal_scope(profile_home(service, runtime_profile))
    except (TerminalPolicyUnavailable, BusinessDenied):
        return False
    return scope.get("TERMINAL_ENV", "local") != "local"


def socket_operation_refused(service, runtime_profile: str, operation: str) -> bool:
    """Refuse authorizing socket operations for a supervised profile whose agent shares the host."""
    action = operation.removeprefix("operator.").removeprefix("business.")
    if action in SOCKET_SAFE_OPERATIONS:
        return False
    with service.store._connect() as db:
        supervised = db.execute(
            "SELECT 1 FROM business_account_modes WHERE runtime_profile=? AND mode='supervised' LIMIT 1",
            (runtime_profile,)).fetchone()
    return bool(supervised) and not agent_tools_sandboxed(service, runtime_profile)
