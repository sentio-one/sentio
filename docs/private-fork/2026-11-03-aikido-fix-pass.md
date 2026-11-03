# PR 36: Aikido fix pass

This follow-up supersedes the earlier handoff's source-review readiness assessment.
All six review findings have fixes and regression coverage in this fix pass for
PR #36 into `main`. Deployment and live acceptance remain separate.

## Changes

- Generic profile deletion revokes static routes naming that profile as runtime or bot
  owner. Rename migrates those references. Both YAML locations are updated, and an
  authoritative legacy `gateway.json` route list gets a YAML override. Managed or
  expanded settings that cannot be rewritten safely stop the operation before name
  reuse. Shared-user writes and the entire delete/rename operation share a reentrant
  process/file lock.
- Lifecycle changes require a running gateway to acknowledge `reload-profile-routes`
  before the directory changes. This also runs when the persisted route was already
  removed, covering `remove-user --delete-profile` with a stale gateway cache. Failed
  renames restore the original route configuration; failed directory deletion keeps
  access revoked. An older or unresponsive gateway must be restarted before retrying.
- A restricted file browser requires the workspace anchor to resolve to the actual
  directory under the canonical selected profile home. Workspace symlinks/junctions
  to the profile home, its private subdirectories or outside paths are rejected;
  safe links inside the real workspace remain supported.
- Shared-user management uses the effective user loader and the gateway's actual
  top-level-over-nested precedence, including empty-list and null-fallback behavior.
  Writes preserve the authoritative location and reject administrator-managed routes.
- Repeating `add-user` repairs a missing persisted allowlist grant, preserving the
  existing profile and route. Repair failures roll back the grant. The earlier test
  that intentionally expected an incomplete repeat to remain incomplete was updated.
- The owner gate uses the real route matcher to distinguish an explicit route to
  `default` from fallback. Ordinary authorization still applies; inactive multiplex
  routes and another bot's routes cannot bypass the gate.
- Shared-number CLI commands select default scope before environment loading when
  `-p` is omitted, regardless of sticky selection. Explicit named `-p` remains an
  error with a working `-p default` recovery message.

Small internal startup and clone-staging helpers moved into topical modules to keep
the existing facade file-size ratchets satisfied. Searches found no documentation
references to their old internal symbol paths.

## Verification

All Python tests used `scripts/run_tests.sh` and isolated homes.

- Before production edits, five regression files produced **10 failures** covering
  all six findings, including native Windows junctions.
- Final Windows feature batch: **12 files, 154 passed, 0 failed, 17 skipped**. Includes
  native junctions, named-pipe control reload, stale-cache removal, lifecycle rollback,
  configuration precedence, grant repair, sticky CLI selection and ordinary admission.
- Linux scoped batch: **26 files, 451 passed, 0 failed, 15 skipped**, exit 0. Includes
  POSIX workspace-anchor symlinks, Unix control transport, profile CRUD, the surrounding
  file/authorization APIs and startup/profile flag handling.
- The wider Windows batch had **10 failures** in `test_profiles.py` (8) and
  `test_startup_fast_guards.py` (2). All ten were reproduced on an untouched snapshot
  of pre-fix HEAD `698a4a56de`; these are Windows privilege/platform expectations,
  placeholder-env expectations and path-separator assertions, not new regressions.
- Final `python scripts/check`: **all 12 checks passed**, exit 0; code health has
  zero blocking and zero advisory findings against `315e8949ee91`.
- A SHA-256 comparison confirmed that all **19 changed runtime/test files** on the
  Linux verification copy match the final local worktree byte for byte.

Local raw receipts are ignored under `.superpowers/aikido-fixes/`. Linux receipts are
on `thuto-lms` under `/home/deploy/sentio-tests/20261103-aikido-fixes/`; that source copy
uses the existing PM test interpreter, without changing any deployment.

The Linux snapshot was a Git archive of pre-fix HEAD plus the changed/new files, then
a final overlay. Archive SHA-256:
`b36f8f5aa57abfa8f8704e652ea46e17072640bcd0b6373740f63b256d6b9a42`.
Overlay SHA-256:
`d98f1593246422d0e3a079e7574c8f23828a0c3f7e6555fb8b7e0558a39b36de`.
The source archive was moved outside the local checkout after verification to satisfy
the repository's archive guard. Hashes identify test artifacts, not a deployed commit.

The earlier handoff's live desktop/phone acceptance checklist remains pending.
