# Task 12 verification and acceptance

Recorded 2026-11-02. This report separates code review and automated evidence from live acceptance. PR #36 is open at pushed head `38bc40eee56efa4493bcb20d91743138c9d8996e`; all PR checks pass (TypeScript, Python, Dokploy validation, sandbox, Aikido checks, and deep review). No deployment, merge, or user acceptance is claimed.

## Revision and review

- Branch: `feat/shared-whatsapp-granted-user`
- Current main base: `315e8949ee91a2f4308ae2be848c7970e9d8468a`
- Runtime/test head: `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`
- Latest reviewed source head: `61b34c8ed23b3732a224d8926cdd723748e4e314`; the only change after the runtime/test head corrects a pre-existing test fixture shebang for lint portability. Current pushed docs head: `38bc40eee56efa4493bcb20d91743138c9d8996e`.
- Whole-branch source review through `61b34c8ed2`: no Critical, Important or Minor findings. Review did not run tests or establish live behavior. Full review notes are in the local ignored Task 12 scratch directory, not a portable artifact.
- Fork decisions D18-D20 are recorded in `FORK_DECISIONS.md`.

## Automated verification

| Check | Result | Evidence / limit |
|---|---|---|
| Native Windows focused feature batch | **15 files; 228 passed, 0 failed, 20 POSIX skips; exit 0** | `D:/Projects/sentio-task12-verification/focused-rebased.log` and `.exit`; includes native junction coverage and WhatsApp/granted-user feature tests. |
| Native Windows junction behavior | **2 passed, 0 failed; exit 0** before rebase | `D:/Projects/sentio-task12-verification/windows-junctions.log` and `.exit`; the post-rebase focused batch also includes these cases. |
| Windows full `python scripts/check` | **12 checks OK; exit 0; zero blocking/advisory health findings against main `315e8949ee91`** | `D:/Projects/sentio-task12-verification/lint-final.log` and `.exit`, at `61b34c8ed2`. |
| Linux current-main comparison | **All broad-run failures reproduced on base** | At current main `315e8949ee91a2f4308ae2be848c7970e9d8468a`, `test_session_client_env.py` had 2 failed/11 passed; `test_gateway_service.py` had 1 failure; `test_update_recovery_closure.py` had 8 failures; `test_update_post_commit_safety.py` had 1 failure. The three-file comparison reported 10 failures, with client-env independently compared. Host-local receipt on `thuto-lms`: `/home/deploy/sentio-tests/20261102-shared-whatsapp/receipts/task12/baseline-final`. The feature's final 12 failures are therefore all reproduced on main. |
| Linux broad suite at `e89baeb2b4` | **Complete; 12 failures all reproduced on current main** | `scripts/run_tests.sh tests/gateway/ tests/hermes_cli/`: 3,396 files, 30,161 passed, 12 failed, 471 skipped; exit 1; 8 workers; 2,131.8s. Host-local receipt on `thuto-lms`: `/home/deploy/sentio-tests/20261102-shared-whatsapp/receipts/task12/broad-final`. Failure groups are listed above. The earlier interrupted 16-worker run's timing-threshold failures did not recur in this complete 8-worker run and are not final failures. |
| Linux `scripts/check` | **Passed; exit 0** | At `e89baeb2b418f0f4bc8fe545c43c65d8306180fc` compared with current main `315e8949ee91a2f4308ae2be848c7970e9d8468a`. Host-local Linux lint receipt is in `/home/deploy/sentio-tests/20261102-shared-whatsapp/receipts/task12/` on `thuto-lms`. |
| Push / PR / CI | **PR #36 open; all checks pass** | Pushed head `38bc40eee56efa4493bcb20d91743138c9d8996e`. Passing checks include TypeScript, Python, Dokploy validation, sandbox, Aikido checks, and deep review. No merge requested. |

The full broad run is not all-green: it exits 1, but every reported failure is reproduced on clean current main. The initial interrupted 16-worker run's timing failures did not recur in the complete 8-worker run. Windows and Linux automated checks do not establish live readiness.

## User desktop and phone acceptance

Perform after the coordinator confirms the pushed revision and PR are ready. Use the intended desktop/gateway environment and record date, revision, pass/fail, and sanitized evidence for each item in the spec's Verification section. Do not record phone numbers, account emails, tokens, or credentials.

### Desktop and granted profile

- [ ] Confirm the running desktop/backend is the intended revision.
- [ ] Sign in with the sales-only OIDC account; active profile and profile pill show `sentio-sales`.
- [ ] Workspace list, read, edit, upload, and delete work only inside the explicitly granted profile's `workspace/`. Confirm other profiles and paths outside that workspace remain inaccessible.
- [ ] Confirm host Git and media APIs remain denied to this account (including `/api/git/status` and `/api/media`).
- [ ] Confirm a customized existing `SOUL.md` remains untouched. In a fresh profile/session, confirm the seeded Sentio SOUL text and record the actual model identity answer separately from prompt inspection.
- [ ] Inspect native desktop and gateway logs/session metadata to confirm the runtime profile. A model identity result requires provider authorization and a successful response.

### WhatsApp shared number

- [ ] Configure `gateway.owners`, provision the second phone with `hermes whatsapp add-user`, then restart the gateway after route changes.
- [ ] Owner phone receives a response on `default`.
- [ ] Routed second phone receives a response running on its own sandboxed profile.
- [ ] Third ordinarily allowlisted but unrouted phone receives no response and does not fall through to `default`.
- [ ] Missing or parked route targets fail closed; no fallback to `default` occurs.
- [ ] Confirm actual sandbox execution and inspect gateway/session logs. Fake transport tests alone do not prove phone delivery or runtime execution.

Record acceptance outcomes here when performed:

| Date / revision | Check | Pass / fail | Sanitized evidence or notes |
|---|---|---|---|
| Pending | Desktop and phone acceptance | Pending | User to complete after PR is ready. |

## Known limitations to retain

- The upstream `DEFAULT_AGENT_IDENTITY` and Hermes helper text (`HERMES_AGENT_HELP_GUIDANCE` and/or `HERMES_AGENT_HELP_GUIDANCE_NO_SKILLS`) remain unchanged by scope. Earlier prompt-builder inspection found this text alongside the Sentio SOUL. The prior live provider request failed with HTTP 401; no successful identity answer is evidenced yet.
- The Docker sandbox `/workspace` is ephemeral and has no host bind mount. Files uploaded to the host profile workspace are not visible inside the sandbox runtime. Treat this as an existing scope boundary, not a promised file-transfer path.
- The earlier dashboard baseline showed `sentio-sales` runtime metadata behind the `default` transport/active label. Native desktop behavior and gateway logs were unavailable during that inspection; retain it as historical pre-change evidence, not proof of the current build.
- Optional destructive profile deletion cannot reconstruct contents after a partially destructive underlying deletion failure; ordinary `remove-user` keeps the profile.
