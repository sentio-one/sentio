# Native WhatsApp Business Operations: Shadow Mode (Plan 01)

## Overview

This runbook documents operational procedures, evaluation metrics, and safety invariants for **Shadow Mode** of the Native WhatsApp Training Wheels messaging architecture.

Shadow mode is strictly observation-only:
- The legacy agent interaction loop runs as usual and continues its normal dispatch.
- Business admissions, conversation provenance, facts, and actions are durably recorded in SQLite.
- An auxiliary model analyzes decisions (`compose`, `no_reply`, `escalate`, `failed`) and drafts customer candidates using dedicated, tool-free auxiliary calls.
- Deterministic checks and advisory reviews run against draft candidates.
- **Under NO circumstances does shadow mode dispatch messages to customers natively.** Outbound socket calls remain 0.
- Shadow mode does **not** certify an account or agent for autonomous or unsupervised sending.

---

## Configuration

In `~/.hermes/config.yaml` (profile-aware):

```yaml
platforms:
  whatsapp:
    extra:
      business:
        mode: shadow
        required_policies:
          - sales_ops
        operators:
          - "27600000001@s.whatsapp.net"
        rewrite_attempts: 2
        approval_ttl_seconds: 86400
        queue_ttl_seconds: 604800
        governor:
          timezone: Africa/Johannesburg
          min_gap_seconds: 15
          reply_min_gap_seconds: 5
          jitter_seconds: 3
          hard_max_per_day: 100
          failure_count: 5
          failure_window_seconds: 300
          windows:
            reply:
              - weekdays: [0, 1, 2, 3, 4, 5, 6]
                start: "07:00"
                end: "21:00"
            followup:
              - weekdays: [0, 1, 2, 3, 4]
                start: "08:00"
                end: "17:00"
            cold:
              - weekdays: [0, 1, 2, 3, 4]
                start: "09:00"
                end: "16:00"
          caps:
            reply:
              per_hour: 30
              per_day: 100
            followup:
              per_hour: 15
              per_day: 50
            cold:
              per_hour: 10
              per_day: 30
```

---

## Operational Architecture

1. **Ingress Observation:**
   - Every inbound WhatsApp event is recorded in the profile's `state.db` before any plugin or routing hook can drop or modify it.
   - Provenance (voice transcript vs raw text, reaction emoji and target, owner vs customer sender) is captured in `ContextEvent`.

2. **Audience Separation:**
   - Legacy working agent text remains internal.
   - Auxiliary composer (`business_compose`) receives verified facts and completed actions from the conversation snapshot.
   - Customer-facing candidates are drafted without model tools or credential exposure.

3. **Check & Review Pipeline:**
   - Candidate undergoes deterministic policy checks (`business_check` hook).
   - If checks pass, candidate undergoes advisory review (`business_review` aux call).
   - If the candidate requires correction, up to 2 rewrites are attempted.
   - If the rewrite budget is exhausted or checks fail, an operator task is registered in `business_tasks` and the receipt state is set to `attention` or `blocked`.

4. **Failure & Silence Handling:**
   - Working turn runtime failure bypasses composer and shadow decision immediately, recording a failure verdict and creating an operator task.
   - Intentional silence produces a `no_reply` receipt with no draft revisions created.
   - Silence, failure, and escalation states are preserved without empty-response normalization.

---

## Shadow Evaluation Metrics

Offline evaluation (`tests/gateway/business/test_drafting_eval.py`) verifies the shadow pipeline against sanitized historical drafting cases:

- **Quality:** Assesses whether candidates respect policy rules, avoid internal state leaks, and incorporate verified facts.
- **Coverage:** Percentage of inbound customer turns that yield a structured decision (`held`, `attention`, `no_reply`, `escalate`).
- **Cost:** Number of auxiliary LLM tokens and API calls per customer turn (composer + reviewer).
- **Latency:** End-to-end time to snapshot context, compose candidate, check, and review.

---

## Safety Invariants

- **No Native Dispatch:** `socket_calls` must be 0 for shadow operations.
- **Supervised Latch:** Supervised live sending is disabled until Plan 02 and Plan 03 cutover readiness is certified.
- **Fail-Closed:** Missing policy, reviewer failure, or CAS version mismatch records an attention task and halts drafting.
