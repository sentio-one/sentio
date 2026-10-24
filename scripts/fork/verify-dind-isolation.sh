#!/usr/bin/env bash
# Prove, in real Docker, that a sandboxed agent profile runs inside an isolated container on the company's own
# Docker-in-Docker daemon (`dockerd`), cannot reach the daemon's API or private networks, cannot see `/opt/data`
# or the gateway's secrets, and is bounded by its pinned resource limits (SANDBOX_PLAN.md §3 Phase 0, 1, 3, 5).
#
#   scripts/fork/verify-dind-isolation.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
project="sentio-dind-verify-$$"
daemon="${project}-dockerd-1"
compose=(docker compose -p "$project" -f deploy/dokploy/docker-compose.yml)
use_compose=1
if ! docker compose version >/dev/null 2>&1; then
  use_compose=0
fi
export PUBLIC_URL=https://verify.example OIDC_ISSUER=https://sso.example OIDC_CLIENT_ID=verify ADMIN_GROUP=verify ADMIN_EMAILS=verify@example.test
cli=docker:27.5.1-cli
sock=/var/run/dind/docker.sock
volume="${project}_dockerd-sock"
data_vol="${project}_dockerd-data"
net="${project}_default"
failed=0
tmp_root="$(mktemp -d)"

cleanup() {
  if [ "$use_compose" -eq 1 ]; then
    "${compose[@]}" down -v >/dev/null 2>&1 || true
  else
    docker rm -f "$daemon" >/dev/null 2>&1 || true
    docker volume rm -f "$volume" "$data_vol" >/dev/null 2>&1 || true
    docker network rm "$net" >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp_root" >/dev/null 2>&1 || true
}
trap cleanup EXIT

check() {  # check "<what must hold>" <command...>
  local what=$1; shift
  local out
  if out="$("$@" 2>&1)"; then
    echo "ok    $what"
  else
    local rc=$?
    echo "FAIL  $what (exit $rc)"
    if [ -n "$out" ]; then echo "$out" | sed 's/^/      /'; fi
    failed=1
  fi
}
as_gateway() { docker run --rm --user 10000:10000 -v "$volume:/var/run/dind" -e "DOCKER_HOST=unix://$sock" "$cli" "$@"; }

if [ "$use_compose" -eq 1 ]; then
  "${compose[@]}" up -d dockerd >/dev/null
else
  docker network create "$net" >/dev/null
  docker volume create "$volume" >/dev/null
  docker volume create "$data_vol" >/dev/null
  docker run -d --name "$daemon" --privileged --network "$net" \
    -v "$data_vol:/var/lib/docker" \
    -v "$volume:/var/run/dind" \
    -v "$PWD/deploy/dokploy/dockerd-entrypoint.sh:/opt/dockerd-entrypoint.sh:ro" \
    --entrypoint /bin/sh \
    --health-cmd "test -f /var/run/dind/firewall.ready && docker -H unix://$sock info >/dev/null" \
    --health-interval 5s --health-timeout 5s --health-retries 5 --health-start-period 10s \
    docker:27.5.1-dind /opt/dockerd-entrypoint.sh dockerd "--host=unix://$sock" --group=10000 >/dev/null
fi
for _ in $(seq 1 36); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$daemon")" = healthy ] && break
  sleep 5
done
check "the daemon becomes healthy" test "$(docker inspect -f '{{.State.Health.Status}}' "$daemon")" = healthy

check "the daemon has no tcp listener on 2375 or 2376" \
  bash -c "! docker exec $daemon netstat -ltn | grep -qE ':(2375|2376) '"
check "the gateway user (uid/gid 10000) can use the socket" as_gateway docker info
check "any other user is refused" \
  bash -c "! docker run --rm --user 12345:12345 -v $volume:/var/run/dind -e DOCKER_HOST=unix://$sock $cli docker info"

check "DOCKER-USER firewall drops RFC 1918 and link-local destinations from sandbox bridges" \
  bash -c "docker exec $daemon iptables -S DOCKER-USER | grep -q '10.0.0.0/8' && \
           docker exec $daemon iptables -S DOCKER-USER | grep -q '172.16.0.0/12' && \
           docker exec $daemon iptables -S DOCKER-USER | grep -q '192.168.0.0/16' && \
           docker exec $daemon iptables -S DOCKER-USER | grep -q '169.254.0.0/16'"

# Start an HTTP listener on the compose network inside dockerd (bound to eth0's compose IP on port 8088)
# to simulate a sibling compose service or host port 22 on the compose network.
compose_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$daemon")"
docker exec -d "$daemon" sh -c "while true; do printf 'HTTP/1.0 200 OK\r\n\r\nhost-reached' | nc -l -p 8088; done"

probe="GW=\$(ip route | awk '/default/ {print \$3}')
for port in 2375 2376 8088; do wget -q -T 2 -O- \"http://\$GW:\$port/version\" >/dev/null 2>&1 && exit 11; done
wget -q -T 2 -O- \"http://${compose_ip}:8088/\" >/dev/null 2>&1 && exit 14
wget -q -T 2 -O- \"http://169.254.169.254/\" >/dev/null 2>&1 && exit 15
[ -e /var/run/dind ] && exit 12
[ -e /var/run/docker.sock ] && exit 13
exit 0"
check "a sandbox cannot reach the daemon API, compose network, link-local metadata, or socket" \
  as_gateway docker run --rm alpine:3.20 sh -c "$probe"

# End-to-end profile terminal test against the live dind daemon:
# Seed alpine:3.20 into dind and tag it as the managed sandbox image so the test runs fast without a 1 GB pull.
as_gateway docker tag alpine:3.20 nikolaik/python-nodejs:python3.11-nodejs20
# Ensure bash exists inside the tagged image because Hermes's DockerEnvironment runs `bash`.
cid="$(as_gateway docker create nikolaik/python-nodejs:python3.11-nodejs20 sleep 30)"
as_gateway docker start "$cid" >/dev/null
if ! as_gateway docker exec "$cid" which bash >/dev/null 2>&1; then
  as_gateway docker exec "$cid" apk add --no-cache bash >/dev/null
  as_gateway docker commit "$cid" nikolaik/python-nodejs:python3.11-nodejs20 >/dev/null
fi
as_gateway docker rm -f "$cid" >/dev/null

check "end-to-end routed profile terminal_tool enforces sandbox, strips secrets, hides /opt/data, and sets limits" \
  scripts/run-in-hermes-env python3 scripts/fork/verify_dind_profile_e2e.py "$daemon" "$sock"

if [ "$failed" -ne 0 ]; then echo "dind isolation: FAILED" >&2; exit 1; fi
echo "dind isolation: ok"
