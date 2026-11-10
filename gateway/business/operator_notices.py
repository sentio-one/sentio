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


_SAID_MAX_CHARS = 300
_SAID_MAX_MESSAGES = 3


def customer_said(store, scope: Scope) -> list[str]:
    """What the customer wrote since we last spoke, so a reviewer who cannot open the chat can judge the draft."""
    said: list[str] = []
    for event in reversed(store.list_events(scope)):
        if event.actor != "customer":
            break
        if event.text:
            said.append(event.text)
    return said[::-1][-_SAID_MAX_MESSAGES:]


def review_card_text(*, code: str, label: str, said: list[str], draft: str) -> str:
    lead = "".join(f'They said: "{text[:_SAID_MAX_CHARS]}"\n' for text in said)
    lead = f"{lead}\n" if lead else ""
    return (f"🕓 Draft {code} for {label}\n\n{lead}Draft reply:\n{draft}\n\n"
            f"React 👍 to send or 👎 to discard, or reply: edit {code} your text")


def _reason(code) -> str:
    return _REFUSALS.get(str(code), str(code or "it was refused"))


def rejection_text(result: dict, *, label: str) -> str:
    if result.get("ok") is False or result.get("error"):
        return f"⚠️ Could not discard the draft for {label}: {_reason(result.get('error'))}. Nothing was sent."
    return f"🗑 Discarded the draft for {label}. Nothing was sent."


def edit_text(result: dict, *, label: str, text: str) -> str:
    if result.get("state") == "attention":
        checks = (result.get("revision") or {}).get("checks") or {}
        reasons = "; ".join(str(i.get("reason") or i.get("code")) for i in checks.get("issues", ())) or "the content checks"
        return (f"⚠️ Your edit for {label} did not pass the content checks: {reasons}. "
                "Nothing was sent. Send the edit again with changes.")
    if result.get("ok") is False or result.get("error") or result.get("approval_count") == 0:
        return f"⚠️ Your edit was not approved for {label}: {_reason(result.get('error'))}. Nothing was sent."
    return f"✏️ Your version is approved for {label}. It goes out as soon as the sending rules allow:\n\n{text}"


def silence_text(*, label: str, said: list[str], reason: str) -> str:
    quoted = "".join(f'They said: "{text[:_SAID_MAX_CHARS]}"\n' for text in said)
    return f"🤫 No reply drafted for {label}.\n{quoted}Why: {reason}\nNothing was sent. Answer from your phone if one is needed."
