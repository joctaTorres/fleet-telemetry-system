#!/bin/sh
# Replica entrypoint: bootstrap a physical hot standby from the primary, then run
# it as a streaming replica. The data dir is a tmpfs, so it is empty on every
# start and the basebackup always runs (no stale state to reconcile).
set -e

PGDATA="${PGDATA:-/var/lib/postgresql/data}"

# This entrypoint overrides the image default, so it runs as root; the server
# itself must run as the postgres user. A freshly-mounted tmpfs is root-owned.
mkdir -p "$PGDATA"
chown -R postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"

# Wait for the primary to accept connections before cloning it.
until pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
  echo "replica: waiting for primary ${PRIMARY_HOST}:${PRIMARY_PORT}..."
  sleep 1
done

# Clone the primary on first start. -R writes standby.signal + primary_conninfo
# so the server comes up as a streaming hot standby; -Xs streams WAL during the
# backup so the clone is consistent without needing an archive.
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "replica: cloning primary via pg_basebackup..."
  gosu postgres pg_basebackup \
    --host="$PRIMARY_HOST" --port="$PRIMARY_PORT" --username="$POSTGRES_USER" \
    --pgdata="$PGDATA" --wal-method=stream --write-recovery-conf --progress
fi

echo "replica: starting hot standby streaming from ${PRIMARY_HOST}..."
exec gosu postgres postgres
