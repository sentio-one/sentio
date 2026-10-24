"""``hermes profile trim-skills`` (private fork, D16): cut an already-created sandboxed profile down to the company baseline.

New sandboxed profiles are seeded with the baseline only (``tools/skills_sync_company.py``); this fixes the ones created
before that, or after an admin changed ``skills.company_baseline``. Unmodified bundled skills outside the baseline are
removed; anything the user edited or installed stays.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from hermes_cli.profile_cmd import _die

_TRIM = "import json; from tools.skills_sync_company import trim_to_company_baseline as t; print(json.dumps(t()))"


def profile_trim_skills(args) -> None:
    from hermes_cli.profiles import get_profile_dir, profile_exists
    from tools.terminal_scope_sandbox import is_sandboxed_profile

    name = args.profile_name
    if name == "default" or not profile_exists(name):
        _die(f"Error: '{name}' is not a named profile.")
    home = get_profile_dir(name)
    if not is_sandboxed_profile(home):
        _die(f"Error: '{name}' is not a sandboxed company profile (no access policy applies), so there is no baseline to trim to.")
    done = subprocess.run([sys.executable, "-c", _TRIM], env={**os.environ, "HERMES_HOME": str(home)},
                          cwd=str(Path(__file__).parent.parent.resolve()), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        _die(f"Error: trimming skills failed: {done.stderr.strip()[:300]}")
    result = json.loads(done.stdout.strip().splitlines()[-1])
    print(f"Profile '{name}': removed {len(result['removed'])} bundled skill(s) outside the company baseline"
          + (f"; kept {len(result['kept_modified'])} edited" if result["kept_modified"] else "") + ".")
