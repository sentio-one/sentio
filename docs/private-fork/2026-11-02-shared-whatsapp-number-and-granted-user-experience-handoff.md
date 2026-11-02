# Shared WhatsApp number and granted-user experience: portable handoff

Recorded 2026-11-02. Branch `feat/shared-whatsapp-granted-user` is rebased onto current `origin/main` `315e8949ee91a2f4308ae2be848c7970e9d8468a`. Runtime/test head `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`; lint-only fixture correction at `61b34c8ed23b3732a224d8926cdd723748e4e314`. Tasks 1-11 implementation and scoped reviews are complete; Task 7 remains a partial dashboard diagnosis. Task 12 whole-branch review, native Windows checks, Linux lint, and final Linux broad run are complete. The final broad run had 12 failures; all were reproduced on clean current main. PR #36 is open at pushed head `38bc40eee56efa4493bcb20d91743138c9d8996e`; all PR checks pass. No deployment, merge or live acceptance is claimed. See the [Task 12 verification and acceptance handoff](2026-11-02-shared-whatsapp-number-and-granted-user-experience-task12-verification.md) for receipts and the user checklist.

Read the [spec](2026-11-02-shared-whatsapp-number-and-granted-user-experience.md), especially its corrections, and [tracked plan](2026-11-02-shared-whatsapp-number-and-granted-user-experience-plan.md). The spec/preflight corrections override illustrative plan code. The local `.superpowers/sdd/2026-11-02-shared-whatsapp-number-and-granted-user-experience-plan/` directory contains ignored briefs, reports and raw logs; it will **not** follow a clone. Material results and limits are carried here instead. No raw scratch artifacts should be force-added.

## Commit map and scoped reviews

| Task | Commits | Result |
|---|---|---|
| Spec / plan | `132461b518`, `edb23a2825` | Original requirements and corrected implementation plan |
| 1 baseline | `3dab08b435` | Real routing resolver with fake transport; review passed |
| 2 owners gate | `16e8a75569`, `9aa9345b5b` | Negative default-profile admission gate; gateway-defaults extraction fixes FILE_LINES ratchet; both reviewed |
| 3 user service | `2523ef33d3`, `71bff3642c` | Shared add/list/remove with serialized writes/rollback. Initial review required collision, stale allowlist and same-profile fixes; scoped re-review passed |
| 4 CLI | `bc3e627a25` | Thin noninteractive add/list/remove commands; review passed |
| 5 REST | `6b27950b15` | Admin-only shared-number endpoints, real app middleware tests; review passed |
| 6 docs | `77f9c28801` | User guide and fork decision D17; review passed |
| 7 diagnosis | `29c6e587d1` | Existing dashboard session baseline recorded; review passed within stated limits |
| 8 active discovery | `3d4ada8886`, `82bdf4cba3` | Grant-aware discovery; reviewed. Second commit removes mistakenly tracked scratch evidence only |
| 9 managed files | `18cbb862c9` | Explicit selector, grant workspace jail, dangling/missing-profile guards; review passed |
| 10 fs | `d3fa231700`, `b5235cae0f` | Seven routes jailed before SSH selection; initial review found staging symlink bypass, fixed with exclusive temporary file and re-reviewed successfully |
| 11 SOUL | `7dd9d45ebf` | Exact Sentio template, preserved old Hermes + ASCII legacy seeds, existing upgrade path; review approved |
| Test lint | `6de7cf5bc3` | Explicit `check=False` on existing child-process regression, preserving captured return-code assertion |

All these task commits use the requested Claude coauthor trailer. The original handoff/plan status commit follows them. Subsequent rebase and Task 12 verification are documented separately below and in the Task 12 report.

## Focused test receipts

All Python tests used `scripts/run_tests.sh`, never bare pytest. New feature regressions were recorded red before production edits; Task 1 deliberately pins existing baseline behavior. Tests use isolated homes, including `Path.home()` for profiles. Counts below are separate receipts and must not be summed as unique tests.

Windows used Git Bash with `HERMES_PYTHON` set to the existing PM-built Windows test interpreter. Linux used the PM-built interpreter at `/var/tmp/sentio-granted-verification/venv/bin/python`. Machine-specific paths are not portable setup instructions.

| Task | Canonical runner arguments (after `scripts/run_tests.sh`) | Recorded result |
|---|---|---|
| 1 | `tests/gateway/test_whatsapp_shared_number_routing.py -v` | 3 passed |
| 2 | `tests/gateway/test_whatsapp_owners.py tests/gateway/test_whatsapp_shared_number_routing.py tests/gateway/test_multiplex_transport_matrix.py -v` | 31 passed |
| 2 defaults fix | `tests/hermes_cli/test_config.py tests/gateway/test_config.py tests/gateway/test_whatsapp_shared_number_routing.py -v` | Windows 274 passed, 2 failed; same two failures reproduced on pre-extraction base |
| 3 final | `tests/hermes_cli/test_whatsapp_users.py -v` | 28 passed after review fixes, including real child process with stale inherited admission |
| 4 | `tests/hermes_cli/test_whatsapp_users_cli.py tests/hermes_cli/test_whatsapp_users.py -v` | 34 passed |
| 5 | `tests/hermes_cli/test_whatsapp_users_api.py tests/hermes_cli/test_dashboard_rest_access.py -v` | 35 passed |
| 6 / 7 | Documentation review and `git diff --check` | Passed within dashboard-only diagnosis limits; no runtime suite |
| 8 | `tests/hermes_cli/test_granted_user_active_profile.py tests/hermes_cli/test_dashboard_rest_access.py -v` | 41 passed |
| 9 | `tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_dashboard_rest_access.py tests/hermes_cli/test_dashboard_document_access.py tests/hermes_cli/test_web_server_files.py tests/hermes_cli/test_web_read_confinement.py -v` | Windows 81 passed, 8 POSIX skips |
| 9 Linux | `tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_web_server_files.py -v` | 37 passed; final granted-file-only rerun 13 passed |
| 10 final fix | `tests/hermes_cli/test_granted_user_files.py -v` | Linux 37 passed after exclusive-staging fix (two new regressions first failed on preceding commit) |
| 10 final Windows | `tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_dashboard_rest_access.py tests/hermes_cli/test_dashboard_document_access.py tests/hermes_cli/test_web_server_files.py tests/hermes_cli/test_web_server_fs.py tests/hermes_cli/test_web_read_confinement.py -v` | 139 passed, 20 POSIX skips |
| 11 Windows | `tests/hermes_cli/test_default_soul_identity.py tests/hermes_cli/test_config.py tests/hermes_cli/test_home_init_soul_symlink.py -v` | 176 passed, 5 failed: three symlink creation privilege errors (WinError 1314) and two previously reproduced config env-writer failures |
| 11 fresh native Linux | Same three Task 11 files, `-v` | **177 passed, 0 failed, 4 Windows-only skipped, exit 0**, 10.4s. Coordinator's standalone native exact-source snapshot includes `7dd9d45ebf`; raw local log `/var/tmp/sentio-task11-native-green.log` |

Task 11's fresh native receipt supersedes the earlier mounted-Windows-worktree wrapper warning/overwritten-log caveat. Earlier Tasks 9/10 mounted runs emitted a precompile Git warning because `.git` points to a Windows path; their recorded pytest summaries completed. Prefer a native Linux clone for fresh combined verification.

Focused health checks passed for the final task deltas. After the narrow `check=False` test lint fix, the full **`python scripts/check` passed all 12 checks, exit 0** on Windows at source commit `6de7cf5bc3`: health compared 26 files against `31c51eb36776`, zero blocking and zero advisory findings. The reviewed narrow BLE001 allowance in shared-user rollback remains: attempt every recovery writer and preserve the initiating failure. Commit hooks reported `Can't find lefthook in PATH`; explicit checks above supply evidence, not hooks. A lint pass is not full regression or release evidence.

The two known Windows config failures are `TestEnvWriteDenylist::test_non_exec_near_misses_still_writable[git_config_parameters]` and `[ld_preload]`. They were reproduced on the Task 2 pre-extraction base. Linux Task 11 config passed; unrelated Windows failures were not changed. The later native Windows junction tests and post-rebase focused result are recorded in the Task 12 status below; POSIX symlink tests alone do not establish Windows reparse behavior.

## Identity and live baseline

Task 11 used the real `agent.system_prompt.build_system_prompt` with `skip_context_files=False`, a real temporary `sentio-sales` profile and the bundled `hermes-agent` skill installed under that profile. `Path.home`, `HOME`, `USERPROFILE` and `HERMES_HOME` were isolated; no user-home writes or model API calls. Assembled prompt contains the exact Sentio SOUL identity, the actual skill index and **`HERMES_AGENT_HELP_GUIDANCE` naming Hermes**. A second real build without skill tools includes **`HERMES_AGENT_HELP_GUIDANCE_NO_SKILLS`, also naming Hermes**, alongside Sentio. Both helper variants and `DEFAULT_AGENT_IDENTITY` intentionally remain upstream per scope. This task does not establish an exclusively Sentio prompt or successful model identity answer.

Read-only live dashboard baseline at `one-agent.sentiolangley.com`: the non-admin account has only the sales profile grant; existing session metadata identifies runtime `sentio-sales` and transport `default`, while `/api/profiles/active` reported `default` and file access returned `admin_required`. The identity probe failed with provider HTTP 401. Native desktop and gateway logs were unavailable through the native pipe. This is a pre-deployment baseline, not evidence that current changes are live or that native desktop has been reproduced. Second- and third-phone acceptance remains pending. No actual phone, email or credentials are needed in this document.

## Decisions and limits to preserve

- Profiles remain independent. Default-bot DMs route through alias-aware `chat_id`; owners still need ordinary allowlist admission. No owners policy retains existing behavior. A routed missing/parked profile never falls back to the host default. Status `live` denotes directory availability, not successful provider or gateway delivery.
- Persisted-only allowlist reads fix loss from stale inherited process values. At final review, assess externally injected environment-only `WHATSAPP_ALLOWED_USERS` when the persisted key is absent before any future change. This is an unresolved compatibility question, not an established defect; do not restore stale environment fallback casually.
- Service conflicts fail closed, including user-id/broad selector overlaps and a second number for an already mapped profile. Review accepted conservative operator resolution. Optional `--delete-profile` cannot reconstruct contents after a partially destructive underlying deletion failure; default removal retains the profile.
- Restricted files require an explicit granted profile. Only `access_allowed_profiles is None` is unrestricted; empty grants fail closed. Canonical selected profile home is the trust anchor; legitimate custom profile homes remain supported. Workspace escapes fail closed; safe internal links remain usable. SSH is bypassed for restricted callers. `/api/git`, `/api/media` and other host APIs remain admin-only.
- Atomic fs saving now uses `tempfile.mkstemp` in the existing validated parent, the returned descriptor, atomic replacement and cleanup. Review found this sound; explicit failure-injection cleanup coverage was not added (nonblocking note). Do not replace it with predictable staging paths or helpers that create parents.
- Existing shared-bot/satellite path, sandbox execution, actual phone delivery, runtime restart and model response still need live acceptance. Do not expose provider credentials or copy production secrets into test environments.
- The Task 7 literal `Tasks 8?11` typo was corrected in Task 11. D17 is recorded; the next available fork decisions for active discovery, file confinement and SOUL remain Task 12 work.

## Task 12 status and next actions

The earlier “Resume on the next machine” procedure above is historical guidance and is superseded by this current status. D18-D20 are already recorded in `FORK_DECISIONS.md`; no additional decision numbering is required.

- **Rebase:** complete, from current main `315e8949ee91a2f4308ae2be848c7970e9d8468a`.
- **Source review:** complete through `61b34c8ed23b3732a224d8926cdd723748e4e314`; whole-branch Luna review found no Critical, Important or Minor findings. Review does not substitute for execution evidence.
- **Windows focused feature batch:** complete at runtime/test head `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`: 15 files, 228 passed, 0 failed, 20 POSIX skips, exit 0. Native junction coverage is included.
- **Windows full lint:** complete at `61b34c8ed2`: 12 checks OK, zero health findings against `315e8949ee91`. The only post-runtime-head source change is a test fixture shebang correction; no runtime behavior changed.
- **Linux `scripts/check`:** passed with exit 0 at `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`, compared with current main `315e8949ee91a2f4308ae2be848c7970e9d8468a`. Linux lint and test receipts are host-local on `thuto-lms` under `/home/deploy/sentio-tests/20261102-shared-whatsapp/receipts/task12/`.
- **Linux broad suite:** complete at `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`: `scripts/run_tests.sh tests/gateway/ tests/hermes_cli/`, 3,396 files, 30,161 passed, 12 failed, 471 skipped, exit 1, 8 workers, 2,131.8s. Receipt: `/home/deploy/sentio-tests/20261102-shared-whatsapp/receipts/task12/broad-final` on `thuto-lms`.
- **Current-main comparison:** all 12 broad failures were reproduced on clean current main `315e8949ee91a2f4308ae2be848c7970e9d8468a`: `test_session_client_env.py` 2 failed/11 passed; `test_gateway_service.py` 1 failure; `test_update_recovery_closure.py` 8 failures; `test_update_post_commit_safety.py` 1 failure. The comparison of the latter three files yielded 10 failures; client-env was compared independently. Receipt: `/home/deploy/sentio-tests/20261102-shared-whatsapp/receipts/task12/baseline-final` on `thuto-lms`. Thus the broad run is not all-green, but no failure group is unique to the feature branch. The earlier interrupted 16-worker timing-threshold failures did not recur in the final 8-worker run and are not final failures.
- **Push and PR:** PR #36 is open at pushed head `38bc40eee56efa4493bcb20d91743138c9d8996e`; all PR checks pass (TypeScript, Python, Dokploy validation, sandbox, Aikido checks, and deep review). The user requested no merge or deployment.
- **Live acceptance:** pending user work after the PR is ready. Automated coverage, source review and old production observations do not complete it.

Historical Linux baseline and known main/base failures remain recorded in `D:/Projects/sentio-linux-verification-20261102.md`; that file is historical evidence, not the refreshed broad result. Raw scratch logs are not committed.

## User desktop and phone acceptance checklist

Run against the explicitly intended revision and environment after the PR is ready. Record pass/fail and evidence for each line in the spec's Verification section. Do not include phone numbers, account emails, tokens or credentials.

### Desktop / granted profile

- [ ] Install or launch the intended revision and confirm its revision in the running desktop/backend.
- [ ] Sign in with the sales-only OIDC account; active profile and profile pill show `sentio-sales`.
- [ ] Workspace listing, read, edit, upload and delete work within the explicitly granted profile's `workspace/` only. Confirm another profile and paths outside the workspace remain inaccessible.
- [ ] Host Git and media APIs remain denied for this non-admin account; verify `/api/git/status` and `/api/media` return the expected authorization denial.
- [ ] Confirm the existing customized `SOUL.md` is untouched. In a fresh profile/session, confirm the seeded Sentio SOUL text is present and record the actual model identity answer separately from prompt inspection.
- [ ] Inspect native desktop and gateway logs/session metadata to confirm the actual runtime profile. Provider authorization and a successful model response are still required to claim a model identity result.

### Shared WhatsApp number

- [ ] Configure the owner number in `gateway.owners`, provision the second phone through `hermes whatsapp add-user`, and restart the gateway after route changes.
- [ ] From the owner phone, confirm the shared number responds on `default`.
- [ ] From the routed second phone, confirm the response runs on its own sandboxed profile.
- [ ] From a third phone that is ordinarily allowlisted but has no route, confirm there is no response and no fallthrough to `default`.
- [ ] Confirm a missing or parked route target fails closed rather than falling back to `default`.
- [ ] Confirm actual sandbox execution and inspect gateway/session logs; fake transports and route metadata alone do not prove delivery or execution.

### Known limits to keep visible

- Prompt assembly intentionally still contains upstream Hermes helper guidance (`HERMES_AGENT_HELP_GUIDANCE` and/or `HERMES_AGENT_HELP_GUIDANCE_NO_SKILLS`) and the upstream `DEFAULT_AGENT_IDENTITY`. The earlier provider probe failed with HTTP 401. Do not promise the model's spoken identity until a successful live answer is observed; record any conflict.
- Docker sandbox `/workspace` is ephemeral and has no bind mount, so uploaded host workspace files are not visible inside that sandbox runtime. This is an existing scope boundary, not a verified file-transfer capability.
- Historical dashboard metadata showed the session runtime as `sentio-sales` behind `default` transport/label; native desktop and gateway logs were unavailable in that earlier inspection. Preserve this as historical baseline, not proof about the current build.
