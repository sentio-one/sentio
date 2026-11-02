# Task 12 verification and acceptance

Recorded 2026-11-02. This report separates code review and automated evidence from live acceptance. It does not claim a push, PR, deployment, merge, or user acceptance.

## Revision and review

- Branch: `feat/shared-whatsapp-granted-user`
- Current main base: `315e8949ee91a2f4308ae2be848c7970e9d8468a`
- Runtime/test head: `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`
- Latest reviewed head: `61b34c8ed23b3732a224d8926cdd723748e4e314`; the only change after the runtime/test head corrects a pre-existing test fixture shebang for lint portability.
- Whole-branch source review through `61b34c8ed2`: no Critical, Important or Minor findings. Review did not run tests or establish live behavior. Full review notes are in the local ignored Task 12 scratch directory, not a portable artifact.
- Fork decisions D18-D20 are recorded in `FORK_DECISIONS.md`.

## Automated verification

| Check | Result | Evidence / limit |
|---|---|---|
| Native Windows focused feature batch | **15 files; 228 passed, 0 failed, 20 POSIX skips; exit 0** | `D:/Projects/sentio-task12-verification/focused-rebased.log` and `.exit`; includes native junction coverage and WhatsApp/granted-user feature tests. |
| Native Windows junction behavior | **2 passed, 0 failed; exit 0** before rebase | `D:/Projects/sentio-task12-verification/windows-junctions.log` and `.exit`; the post-rebase focused batch also includes these cases. |
| Windows full `python scripts/check` | **12 checks OK; exit 0; zero blocking/advisory health findings against main `315e8949ee91`** | `D:/Projects/sentio-task12-verification/lint-final.log` and `.exit`, at `61b34c8ed2`. |
| Linux broad suite at `e89baeb2b4` | **Pending final receipt** | Initial run noted `test_api_admission_off_loop` at 0.54s against a 0.5s threshold. Isolated rerun and current-main comparison are pending. Do not call the broad run green or classify the timing as a regression until those results are recorded. |
| Linux `scripts/check` | **Pending receipt** | Coordinator to add command, revision, counts and exit status after completion. |
| Push / PR / CI | **Pending** | Coordinator owns final commit/push and PR creation. Add actual pushed head, PR URL and CI status only after receipts are available. No merge requested. |

Historical broad results and known main/base failures are in `D:/Projects/sentio-linux-verification-20261102.md`; they are not a substitute for the pending refreshed Linux results. The Windows focused and lint receipts above do not establish live readiness.

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
