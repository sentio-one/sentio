#!/bin/sh
# Start the company's Docker-in-Docker daemon with DOCKER-USER and INPUT firewall rules that isolate
# sandbox containers (on docker0 / br-*) from the compose network, the Dokploy host gateway, and all
# RFC 1918 / link-local / loopback ranges while keeping public internet access (SANDBOX_PLAN.md §3 Phase 3).
#
# Firewall readiness is a prerequisite for serving sandbox traffic: rules are installed synchronously before
# dockerd starts, re-verified once dockerd opens its socket before writing /var/run/dind/firewall.ready, and
# any failure terminates PID 1 so the daemon fails closed instead of running without egress isolation.
set -eu

READY_FILE="/var/run/dind/firewall.ready"
SOCK_FILE="/var/run/dind/docker.sock"

install_sandbox_firewall() {
    iptables -N DOCKER-USER 2>/dev/null || true
    iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER

    iptables -F DOCKER-USER

    # Allow return traffic for outbound public-internet connections initiated by sandboxes.
    iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

    # Drop any new packet from a sandbox bridge (any interface other than eth0) to private, link-local,
    # loopback, or multicast ranges (covers the compose network, the Dokploy host gateway, and cloud metadata).
    for cidr in \
        10.0.0.0/8 \
        172.16.0.0/12 \
        192.168.0.0/16 \
        169.254.0.0/16 \
        127.0.0.0/8 \
        100.64.0.0/10
    do
        iptables -A DOCKER-USER ! -i eth0 -d "$cidr" -j DROP
    done

    iptables -A DOCKER-USER -j RETURN

    # Also prevent sandbox bridges from reaching local sockets/services on the dockerd container itself
    # (except return traffic and loopback). iptables forbids multiple -i flags in a single rule, so handle
    # lo and eth0 first and drop everything else.
    iptables -F INPUT
    iptables -A INPUT -i lo -j ACCEPT
    iptables -A INPUT -i eth0 -j ACCEPT
    iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A INPUT -j DROP
}

verify_sandbox_firewall() {
    iptables -C FORWARD -j DOCKER-USER >/dev/null 2>&1 || return 1
    for cidr in \
        10.0.0.0/8 \
        172.16.0.0/12 \
        192.168.0.0/16 \
        169.254.0.0/16 \
        127.0.0.0/8 \
        100.64.0.0/10
    do
        iptables -C DOCKER-USER ! -i eth0 -d "$cidr" -j DROP >/dev/null 2>&1 || return 1
    done
    iptables -C INPUT -j DROP >/dev/null 2>&1 || return 1
}

rm -f "$READY_FILE"

# 1. Install rules synchronously BEFORE dockerd starts; if iptables fails, exit immediately (fail closed).
install_sandbox_firewall
verify_sandbox_firewall

# 2. Once dockerd opens its socket and initializes bridge networking, re-apply and verify the rules
# before marking the daemon ready. If post-start verification fails, terminate PID 1 so the container
# fails closed instead of serving traffic without egress isolation.
(
    while [ ! -S "$SOCK_FILE" ]; do
        sleep 0.1
    done
    if ! install_sandbox_firewall || ! verify_sandbox_firewall; then
        echo "FATAL: sandbox firewall installation failed; terminating dockerd" >&2
        kill -TERM 1 2>/dev/null || true
        sleep 1
        kill -KILL 1 2>/dev/null || true
        exit 1
    fi
    touch "$READY_FILE"
) &

exec dockerd-entrypoint.sh "$@"
