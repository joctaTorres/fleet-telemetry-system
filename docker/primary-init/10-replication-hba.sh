#!/bin/sh
# Primary init hook: allow the streaming read replica to connect for physical
# replication. Runs once during the primary's first-boot initdb phase; the
# appended rule is picked up when the official entrypoint starts the real server.
#
# `trust` is acceptable here because this is the throwaway, compose-network-only
# integration-test stack — no credentials are baked into application source.
set -e

cat >> "${PGDATA:?PGDATA must be set}/pg_hba.conf" <<'EOF'

# Streaming replication for the hot-standby replica (test-only trust).
host replication all all trust
EOF
