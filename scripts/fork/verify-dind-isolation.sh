#!/usr/bin/env bash
# Prove, in real Docker, that a sandboxed agent container cannot reach the company's privileged Docker daemon.
#
# The daemon (deploy/dokploy/docker-compose.yml, service `dockerd`) is root on the host for anyone who can speak to its
# API. A sandbox runs on that daemon's own bridge and CAN reach any tcp port it serves, so the daemon must have no tcp
# listener at all; only a unix socket in a volume the gateway shares and the sandbox is never given. This script starts
# the real service and checks each part of that. Needs Docker with compose v2; runs in about a minute.
#
#   scripts/fork/verify-dind-isolation.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
project="sentio-dind-verify-$$"
compose=(docker compose -p "$project" -f deploy/dokploy/docker-compose.yml)
export PUBLIC_URL=https://verify.example OIDC_ISSUER=https://sso.example OIDC_CLIENT_ID=verify ADMIN_GROUP=verify
cli=docker:27.5.1-cli
sock=/var/run/dind/docker.sock
volume="${project}_dockerd-sock"
failed=0

cleanup() { "${compose[@]}" down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

check() {  # check "<what must hold>" <command...>
  local what=$1; shift
  if "$@" >/dev/null 2>&1; then echo "ok    $what"; else echo "FAIL  $what"; failed=1; fi
}
as_gateway() { docker run --rm --user 10000:10000 -v "$volume:/var/run/dind" -e "DOCKER_HOST=unix://$sock" "$cli" "$@"; }

"${compose[@]}" up -d dockerd >/dev/null
for _ in $(seq 1 36); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "${project}-dockerd-1")" = healthy ] && break
  sleep 5
done
check "the daemon becomes healthy" test "$(docker inspect -f '{{.State.Health.Status}}' "${project}-dockerd-1")" = healthy

check "the daemon has no tcp listener on 2375 or 2376" \
  bash -c "! docker exec ${project}-dockerd-1 netstat -ltn | grep -qE ':(2375|2376) '"
check "the gateway user (uid/gid 10000) can use the socket" as_gateway docker info
check "any other user is refused" \
  bash -c "! docker run --rm --user 12345:12345 -v $volume:/var/run/dind -e DOCKER_HOST=unix://$sock $cli docker info"

probe='GW=$(ip route | awk "/default/ {print \$3}")
for port in 2375 2376; do wget -q -T 4 -O- "http://$GW:$port/version" >/dev/null 2>&1 && exit 11; done
[ -e /var/run/dind ] && exit 12
[ -e /var/run/docker.sock ] && exit 13
exit 0'
check "a sandbox cannot reach the daemon API or see its socket" as_gateway docker run --rm alpine:3.20 sh -c "$probe"

if [ "$failed" -ne 0 ]; then echo "dind isolation: FAILED" >&2; exit 1; fi
echo "dind isolation: ok"
