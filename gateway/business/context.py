"""Complete, scope-owned customer context for composition and review."""

from __future__ import annotations

import uuid

from gateway.business.models import ContextSnapshot, EpisodeRef, Scope


def _unavailable_inputs(events) -> tuple[str, ...]:
    # A voice note without a transcript is missing input, never an empty customer message.
    return tuple(f"voice_transcript:{event.id}" for event in events if event.kind == "voice" and not event.text)


def build_context(store, scope: Scope, episode: EpisodeRef, *, policy, lesson_version: int) -> ContextSnapshot:
    """Snapshot the conversation as it actually happened, plus the required policy bundle.

    Only recorded events appear: a held or unsent draft is not evidence of anything said. Required
    but unavailable facts, policies or transcripts are explicit ``missing`` entries.
    """
    events = store.list_events(scope)
    from gateway.business.store_jobs import context_contributions
    plugin_facts, plugin_actions = context_contributions(store, scope)
    confirmed = ()
    if hasattr(store, "confirmed_lesson_texts"):
        confirmed = store.confirmed_lesson_texts(scope, policy.id)
    snapshot = ContextSnapshot(
        id=str(uuid.uuid4()), scope=scope, episode=episode, policy_version=policy.version,
        lesson_version=lesson_version, events=events, facts=policy.facts + plugin_facts, actions=policy.actions + plugin_actions,
        rules=policy.rules, review_criteria=policy.criteria, confirmed_lessons=confirmed,
        missing=tuple(policy.missing) + _unavailable_inputs(events),
    )
    store.save_snapshot(snapshot)
    return snapshot
