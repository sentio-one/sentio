# Shared WhatsApp number and granted-user experience: portable handoff

Recorded 2026-11-02. Continue on branch `feat/shared-whatsapp-granted-user`. Tasks 1-11 implementation and scoped reviews are complete; Task 7 remains a partial dashboard diagnosis. Task 12 broad regression, whole-branch review, deployment and live acceptance are deferred by the user. The coordinator owns publishing the branch; this handoff does not assert it is already pushed or deployed. Rebase onto `main` before merging and revalidate afterward.

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

All these task commits use the requested Claude coauthor trailer. The handoff/plan status commit follows them. Scoped review approval is not a completed whole-branch review; that remains Task 12.

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

The two known Windows config failures are `TestEnvWriteDenylist::test_non_exec_near_misses_still_writable[git_config_parameters]` and `[ld_preload]`. They were reproduced on the Task 2 pre-extraction base. Linux Task 11 config passed; unrelated Windows failures were not changed. Native Windows junction/reparse behavior is still unverified; POSIX symlink tests do not establish it.

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

## Resume on the next machine

1. Fetch/checkout `feat/shared-whatsapp-granted-user` after the coordinator confirms push. Verify branch/HEAD, clean tree and remote ancestry. Read root and relevant area `AGENTS.md` before edits. Use a native clone on the host being tested; do not treat a mounted Windows worktree `.git` pointer as a native Linux clone.
2. Prepare Python 3.14 through the [PM developer workflow](../../website/docs/reference/package-management.md#developer-workflow), with a disposable development `HERMES_HOME` and runtime directory. Using that prepared interpreter, build a **fresh, nonexistent** test output:

   ```bash
   python -m pm.build_env --source . --out .venv --group dev --group test
   ```

   Do not mutate PM environments with raw pip/uv. On Windows initialize the native C++ build environment first. See [CONTRIBUTING](../../CONTRIBUTING.md#manual-development-and-test-environment). If the disposable output already exists, stop its own processes and intentionally remove only that output before regeneration. Never use a test home as staging/production.
3. Set `HERMES_PYTHON` to this new test interpreter (`.venv/bin/python` on POSIX, `.venv/Scripts/python.exe` on Windows). Use Git Bash on Windows. Run the deferred commands below, saving permanent receipts and distinguishing new regressions from base/host failures:

   ```bash
   python scripts/check
   scripts/run_tests.sh tests/hermes_cli/ -k "files or managed"
   scripts/run_tests.sh tests/hermes_cli/ -k "fs or files or managed or hosted"
   scripts/run_tests.sh tests/gateway/ tests/hermes_cli/
   ```

   Invoke `scripts/check` with the test interpreter explicitly if `python` is the application interpreter. Run host-specific tests on that host; do not fake `sys.platform`. Native Windows reparse testing remains separate. Broad tests and a full branch review have not been performed for this delivery.
4. Complete Task 12 fork decisions using the next available D numbers; record the grant-only discovery contract, explicit file workspace boundary and Sentio SOUL upgrade seam, including intentionally unchanged core Hermes guidance. Review the whole branch after the focused fixes and broad receipts. Rebase onto current `main` before merge and rerun appropriate checks. Do not infer production readiness from focused passes.
5. Arrange deployment with the operator, confirm the intended target/revision and preserve environment separation. Do not deploy merely because source checks pass. Restart the real gateway after provisioning changes; route hot reload is out of scope.
6. Perform Task 12 acceptance with actual owner, second routed phone and third ordinarily allowlisted-but-unrouted phone: owner answers on default, routed person answers on its own sandboxed profile, third is refused. Verify no missing/parked fallback. Use actual phones, not fake-transport test success, for this receipt.
7. Sign into native desktop and dashboard as the sales-only OIDC account. Confirm profile pill/active discovery, workspace listing/edit/upload/delete and refusal of host Git APIs. Inspect native/gateway logs and actual runtime profile. Resolve provider authorization and obtain a real identity answer; record it separately from the assembled prompt and preserve the known Hermes helper conflict. Record pass/fail per acceptance line in the spec. Broad, native, phone and deployment checks remain pending until these receipts exist.
