# Task 8 implementation report

Base: 29c6e587d1. Implemented the active-profile discovery fix in the existing open GET /api/profiles/active handler.

## Contract

- Only request.state.access_allowed_profiles = None is unrestricted.
- Non-empty grants return active/current equal to an explicitly granted selector, otherwise the alphabetically first grant.
- Empty grants return HTTP 403 (profile_scope_mismatch); no host profile reads occur on this branch.
- Admin and policy-ungated callers retain the sticky active / running current distinction.
- Existing route classification remains open; authentication and grant stamping still run through the existing middleware.

## Evidence

Used the canonical interpreter C:/Users/Givemore/Desktop/Sentio Langley/sentio/.venv/Scripts/python.exe via HERMES_PYTHON and Git Bash scripts/run_tests.sh.

Red: tests/hermes_cli/test_granted_user_active_profile.py on the unchanged base implementation: 9 failed, 3 passed. Failures showed the host work/default pair instead of granted profiles, and 200 instead of 403 for empty grants. See task-8-red.log.

Green: scripts/run_tests.sh tests/hermes_cli/test_granted_user_active_profile.py tests/hermes_cli/test_dashboard_rest_access.py -v: 2 files, 41 tests passed, 0 failed. See task-8-green.log.

Health: canonical Python scripts/check --only health --base 29c6e587d1: 2 files, 0 blocking, 0 advisory. See task-8-health.log. git diff --check passed.

The new tests exercise both a directly stamped handler and real gated_auth_middleware with signed sessions and real access-policy grant resolution. Cases include a sole grant, explicit permitted selector, ungranted selector fallback, deterministic alphabetical fallback, empty grants, admin and policy-disabled callers. No source-reading tests or new dependencies.

## Scope and limitations

This repairs discovery metadata only. Earlier dashboard evidence identified a host-default discovery response despite a sole sales grant while the actual TUI session already ran sales. The independent model identity probe received provider 401, so this task makes no model-identity claim. No live provider call or full-suite/release certification was performed.

The brief's empty-set truthiness fallback was deliberately superseded by the binding R5 preflight requirement. No changes were made to access middleware classification or profile authorization policy.
