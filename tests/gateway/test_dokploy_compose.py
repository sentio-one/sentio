"""Invariants of the per-company Dokploy compose file (deploy/dokploy/docker-compose.yml).

Docker is not needed: these pin the properties that make the deployment safe to expose through Traefik and a
Cloudflare Tunnel, so an edit that weakens one fails here instead of in production.
"""
import re
from pathlib import Path

import pytest

import hermes_yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "dokploy"
REPO = DEPLOY.parents[1]


@pytest.fixture(scope="module")
def compose():
    return hermes_yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def service(compose):
    return compose["services"]["hermes"]


def test_the_image_is_built_from_this_repository_root(service):
    context = (DEPLOY / service["build"]["context"]).resolve()
    assert context == REPO and (context / service["build"]["dockerfile"]).is_file()


def test_nothing_is_published_on_the_host_and_the_network_is_not_the_hosts(service):
    assert "ports" not in service and service.get("network_mode") != "host"
    assert service["expose"] == ["9119"]


def test_the_web_gateway_is_enabled_and_bound_for_the_proxy(service):
    env = service["environment"]
    assert env["HERMES_DASHBOARD"] == "1" and env["HERMES_DASHBOARD_HOST"] == "0.0.0.0"
    assert service["command"] == ["gateway", "run"]


def test_per_company_settings_are_required_so_a_deploy_cannot_start_half_configured(service):
    env = service["environment"]
    for key in ("HERMES_DASHBOARD_PUBLIC_URL", "HERMES_DASHBOARD_OIDC_ISSUER", "HERMES_DASHBOARD_OIDC_CLIENT_ID",
                "HERMES_ADMIN_EMAILS"):
        assert re.search(r"\$\{[A-Z_]+:\?", env[key]), key  # `${VAR:?message}` aborts the deploy when unset


def test_the_env_example_names_every_setting_the_compose_file_reads():
    read = set(re.findall(r"\$\{([A-Z_0-9]+)", (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")))
    example = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z_0-9]+)=", example, flags=re.M))
    assert read == declared
    assert all(not value for key, value in re.findall(r"^([A-Z_0-9]+)=(.*)$", example, flags=re.M)
               if any(word in key for word in ("SECRET", "API_KEY", "TOKEN", "PASSWORD")))


def test_the_secrets_come_from_the_environment_never_the_file(service):
    for key, value in service["environment"].items():
        if any(word in key for word in ("SECRET", "API_KEY", "TOKEN", "PASSWORD")):
            assert re.fullmatch(r"\$\{[A-Z_]+:-\}", str(value)), key


def test_the_pinned_policy_and_healthcheck_are_mounted_read_only_and_data_is_persistent(service, compose):
    mounts = {entry.split(":")[1]: entry for entry in service["volumes"]}
    assert mounts["/etc/hermes"].endswith(":ro") and mounts["/opt/healthcheck.py"].endswith(":ro")
    assert mounts["/opt/data"].startswith("hermes-data:") and "hermes-data" in compose["volumes"]
    assert (DEPLOY / "managed" / "config.yaml").is_file() and (DEPLOY / "healthcheck.py").is_file()
    assert service["environment"]["HERMES_MANAGED_DIR"] == "/etc/hermes"


def test_the_container_goes_unhealthy_when_the_policy_is_missing(service):
    check = service["healthcheck"]
    assert check["test"][-1] == "/opt/healthcheck.py" and int(check["retries"]) >= 1
    assert re.fullmatch(r"\d+s", check["start_period"]) and int(check["start_period"][:-1]) >= 120


def test_agent_sandboxes_run_on_the_companys_own_docker_daemon_never_the_hosts(compose, service):
    """A host docker.sock in the gateway container would be root on the Dokploy host and reach other companies."""
    daemon = compose["services"]["dockerd"]
    sock = "/var/run/dind/docker.sock"
    assert service["environment"]["DOCKER_HOST"] == f"unix://{sock}"
    for svc in (service, daemon):  # no bind mount of the host's docker socket (or of any host path) into either
        assert not any(entry.split(":")[0].startswith("/") or "/var/run/docker.sock" in entry for entry in svc["volumes"])
    assert not service.get("privileged") and service.get("pid") != "host"
    assert service["depends_on"]["dockerd"]["condition"] == "service_healthy"
    assert daemon["privileged"] is True  # Docker-in-Docker needs it; this is the one privileged service
    assert [name for name, svc in compose["services"].items() if svc.get("privileged")] == ["dockerd"]
    assert daemon["image"].split(":", 1)[1] not in ("latest", "dind")  # pinned
    assert "dockerd-data:/var/lib/docker" in daemon["volumes"] and "dockerd-data" in compose["volumes"]
    assert "environment" not in daemon or not any("KEY" in k or "SECRET" in k for k in daemon["environment"])


def test_the_privileged_daemon_has_no_network_surface_a_sandbox_could_reach(compose, service):
    """Verified in real Docker: a sandbox container reaches any TCP port the daemon serves (it answered /version at the
    bridge gateway, 172.17.0.1:2375), and that API is root on the host. It cannot reach a socket file it was not given."""
    daemon, sock = compose["services"]["dockerd"], "/var/run/dind/docker.sock"
    assert "ports" not in daemon and "expose" not in daemon and daemon.get("network_mode") != "host"
    command = daemon["command"]
    assert command[0] == "dockerd"  # the image entrypoint appends a tcp listener unless the first argument is dockerd
    assert f"--host=unix://{sock}" in command and sum(arg.startswith("--host") for arg in command) == 1
    assert not any("tcp" in arg or "2375" in arg or "2376" in arg for arg in command)
    assert f"--group={service['environment']['HERMES_GID']}" in command  # socket usable by the gateway user only
    assert "dockerd-sock:/var/run/dind" in daemon["volumes"] and "dockerd-sock:/var/run/dind" in service["volumes"]
    assert "dockerd-sock" in compose["volumes"]
    assert "unix://" in " ".join(daemon["healthcheck"]["test"])


def test_the_daemon_blocks_sandbox_egress_to_private_and_link_local_networks(compose):
    """Phase 3 (SANDBOX_PLAN.md G6): dockerd installs DOCKER-USER rules dropping RFC 1918 and link-local destinations
    from sandbox bridges while allowing public internet, and gates healthcheck readiness on firewall installation."""
    daemon = compose["services"]["dockerd"]
    assert daemon["entrypoint"] == ["/bin/sh", "/opt/dockerd-entrypoint.sh"]
    assert "./dockerd-entrypoint.sh:/opt/dockerd-entrypoint.sh:ro" in daemon["volumes"]
    assert "/var/run/dind/firewall.ready" in " ".join(daemon["healthcheck"]["test"])
    entrypoint = (DEPLOY / "dockerd-entrypoint.sh").read_text(encoding="utf-8")
    assert "DOCKER-USER" in entrypoint
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"):
        assert cidr in entrypoint, cidr
    assert "kill -TERM 1" in entrypoint
    assert "exec dockerd-entrypoint.sh" in entrypoint


def test_the_model_provider_keys_the_company_uses_reach_the_container(service):
    for key in ("COMMANDCODE_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        assert key in service["environment"]
