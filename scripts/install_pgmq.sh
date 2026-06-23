#!/bin/bash
# Installs the pgmq extension from source at first Postgres startup.
# This runs as part of docker-entrypoint-initdb.d.
set -e

echo "Installing pgmq extension..."
apt-get update -qq && apt-get install -y -qq git build-essential postgresql-server-dev-15 libclang-dev curl

# Install cargo (needed to build pgmq)
if ! command -v cargo &>/dev/null; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --quiet
  export PATH="$HOME/.cargo/bin:$PATH"
fi

cd /tmp
git clone --depth 1 https://github.com/tembo-io/pgmq.git
cd pgmq/pgmq-extension
make install

psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS pgmq;"
echo "pgmq extension installed."
