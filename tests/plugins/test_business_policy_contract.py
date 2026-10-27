"""Required business policy callbacks are discovered and fail closed."""

import json
import os
import subprocess
import sys
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = Path(os.environ.get("SALES_PLUGIN_WORKTREE")
              or ROOT.parents[1] / "sentio-plugins" / ".worktrees" / "hermes-whatsapp-native")

pytestmark = pytest.mark.skipif(not (PLUGIN / "plugin.yaml").exists(),
                                reason="isolated sales plugin checkout not available")


def _run_python(tmp_path, script, *, isolation="in_process", required="whatsapp-sales-ops"):
    home = tmp_path / "home"
    home.mkdir()
    shutil.copytree(PLUGIN, home / "plugins" / "whatsapp-sales-ops", ignore=shutil.ignore_patterns(".git", "node_modules"))
    (home / "sales-ops.json").write_text(json.dumps({"profile": str(home), "company": "ExampleCo",
        "agent_name": "Sam", "product_name": "Example Product", "allowed_domains": ["example.co.za"]}))
    (home / "config.yaml").write_text(
        "plugins:\n  isolation: " + isolation + "\n  enabled: [whatsapp-sales-ops]\n"
        "platforms:\n  whatsapp:\n    extra:\n      business:\n        mode: shadow\n        required_policies: [" + required + "]\n",
        encoding="utf-8")
    env = dict(os.environ, HERMES_HOME=str(home), PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_missing_timeout_or_invalid_required_policy_blocks(tmp_path):
    result = _run_python(tmp_path, """
import asyncio, json
from gateway.business.models import Scope, EpisodeRef, Candidate
from gateway.business.policy import collect_policy, check_candidate
scope = Scope('default','default','acct','contact')
episode = EpisodeRef('episode', 1)
async def main():
    bundle = await collect_policy(scope, episode)
    report = await check_candidate(scope, Candidate('text','Hi there',{},(),(),()), bundle=bundle)
    print(json.dumps({'missing': bundle.missing, 'ok': report.ok, 'issues': report.issues}))
asyncio.run(main())
    """, required="not-installed")
    assert result["missing"] == ["not-installed"]
    assert result["ok"] is False
    assert result["issues"][0]["code"] == "policy_unavailable"


def test_callback_owner_cannot_be_spoofed(tmp_path):
    result = _run_python(tmp_path, """
import asyncio, json
from gateway.business.models import Scope, EpisodeRef
from gateway.business.policy import collect_policy
async def main():
    bundle = await collect_policy(Scope('default','default','acct','contact'), EpisodeRef('episode', 1))
    print(json.dumps({'missing': bundle.missing}))
asyncio.run(main())
""", required="forged-owner")
    assert "forged-owner" in result["missing"]


def test_required_callback_timeout_fails_closed(tmp_path):
    result = _run_python(tmp_path, """
import asyncio, json, time
from gateway.business.models import Scope, EpisodeRef, Candidate
from gateway.business import policy
from hermes_cli.plugins import discover_plugins, get_plugin_manager
discover_plugins()
policy.POLICY_TIMEOUT_SECONDS = .01
manager = get_plugin_manager()
registration = next(r for rows in manager._ownership_ledger.values() for r in rows
                    if r.kind == 'hook' and r.key == 'business_context')
registration.callback = lambda **kwargs: time.sleep(.2) or {}
async def main():
    scope = Scope('default','default','acct','contact')
    bundle = await policy.collect_policy(scope, EpisodeRef('episode', 1))
    report = await policy.check_candidate(scope, Candidate('text','hello',{},(),(),()), bundle=bundle)
    print(json.dumps({'issues': report.issues}))
asyncio.run(main())
""")
    assert result["issues"][0]["code"] == "policy_unavailable"


def test_isolated_plugin_exposes_business_callback(tmp_path):
    result = _run_python(tmp_path, """
import json
from hermes_cli.plugins import iter_hook_callbacks
from hermes_cli.plugins import discover_plugins
discover_plugins()
print(json.dumps({'loaded': bool(iter_hook_callbacks('business_context'))}))
""", isolation="host")
    assert result["loaded"] is True


def test_native_sales_checks_match_existing_cases(tmp_path):
    result = _run_python(tmp_path, """
import asyncio, json
from gateway.business.models import Scope, EpisodeRef, Candidate
from gateway.business.policy import collect_policy, check_candidate
from hermes_cli.plugins import discover_plugins, iter_hook_callbacks
discover_plugins()
def loaded_plugin_has_callback(name):
    return bool(iter_hook_callbacks(name))
async def main():
    scope = Scope('default','default','acct','contact')
    bundle = await collect_policy(scope, EpisodeRef('episode', 1))
    candidate = Candidate('text','This is a trial at R999!',{},(),(),())
    report = await check_candidate(scope, candidate, bundle=bundle)
    print(json.dumps({'issues': report.issues, 'loaded': loaded_plugin_has_callback('business_context')}))
asyncio.run(main())
""")
    assert result["loaded"] is True
    assert [issue["code"] for issue in result["issues"]] == ["exclamation", "price", "trial"]
