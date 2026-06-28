#!/bin/bash

# Run tests in watch mode - re-runs on file changes

set -e

WORKSPACE="${WORKSPACE:-.}"
TEST_PATH="${TEST_PATH:-tests}"

# Install test dependencies
pip install -q pytest pytest-watch pytest-cov

echo "Starting pytest-watch in $WORKSPACE..."
echo "Tests will re-run on file changes in: $TEST_PATH"

cd "$WORKSPACE"

# Run pytest-watch (ptw)
ptw --runner pytest -- \
  "$TEST_PATH" \
  -v \
  --tb=short \
  -x \
  --strict-markers
