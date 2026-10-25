"""Measured drafting latency and explicitly paired reviewer cohorts."""

import math


def measured_evidence(store, scope):
    unknown = {"p95_draft_latency_ms": None, "reviewer_agreement_rate": None, "review_groups": []}
    if store is None:
        return unknown
    key = (scope.runtime_profile, scope.transport_profile, scope.account_id)
    with store._connect() as db:
        latencies = db.execute(
            "SELECT elapsed_ms FROM business_draft_measurements WHERE runtime_profile=? AND transport_profile=? AND account_id=? ORDER BY elapsed_ms", key,
        ).fetchall()
        groups = db.execute(
            "SELECT policy_version,model_version,message_class,COUNT(*) AS paired,SUM(agreed) AS agreed "
            "FROM business_reviews WHERE runtime_profile=? AND transport_profile=? AND account_id=? "
            "AND unavailable=0 AND unpaired=0 GROUP BY policy_version,model_version,message_class", key,
        ).fetchall()
    return {
        "p95_draft_latency_ms": latencies[math.ceil(len(latencies) * .95) - 1][0] if latencies else None,
        "reviewer_agreement_rate": groups[0]["agreed"] / groups[0]["paired"] if len(groups) == 1 else None,
        "review_groups": [dict(row) for row in groups],
    }


def record_draft_measurement(store, scope, revision_id, elapsed_ms):
    if not revision_id:
        return
    with store._connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO business_draft_measurements(runtime_profile,transport_profile,account_id,revision_id,elapsed_ms) VALUES(?,?,?,?,?)",
            (scope.runtime_profile, scope.transport_profile, scope.account_id, revision_id, elapsed_ms),
        )
