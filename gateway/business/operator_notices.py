"""Plain-language notices for operators: what happened to a draft, never the raw result object."""
from __future__ import annotations

from gateway.business.models import BusinessConflict, BusinessDenied, Scope

_REFUSALS = {
    "stale_revision": "the customer wrote again, so this draft is out of date. A fresh draft will follow",
    "digest_mismatch": "the draft changed after this card was made. Use the newest card",
    "unbound_digest": "this card no longer matches a draft",
    "revision_not_found": "that draft no longer exists",
    "permission_denied": "you are not allowed to approve this draft",
}


def contact_label(store, scope: Scope) -> str:
    """The customer's number for a notice; the canonical contact id is internal and means nothing to a person."""
    with store._connect() as db:
        rows = db.execute(
            "SELECT alias FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? "
            "AND contact_id=? AND held=0 AND alias LIKE 'phone:%' ORDER BY alias",
            (scope.runtime_profile, scope.transport_profile, scope.account_id, scope.contact_id)).fetchall()
    return f"+{rows[0]['alias'].removeprefix('phone:')}" if rows else "the customer"


def draft_text(store, scope: Scope, revision_id: str) -> str | None:
    """The draft a card was about; a refused approval may name a draft that no longer exists."""
    try:
        return store.get_revision(scope, revision_id).candidate.text
    except (BusinessDenied, BusinessConflict):
        return None


def approval_text(result: dict, *, label: str, draft: str | None) -> str:
    code = result.get("error")
    if code or result.get("approval_count") == 0 or result.get("ok") is False:
        reason = _REFUSALS.get(str(code), f"{code or 'it was refused'}")
        return f"⚠️ Not approved for {label}: {reason}. Nothing was sent."
    quoted = f":\n\n{draft}" if draft else "."
    return f"✅ Approved for {label}. It goes out as soon as the sending rules allow{quoted}"
