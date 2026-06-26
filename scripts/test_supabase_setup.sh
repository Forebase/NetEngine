#!/bin/bash
# Test suite for Supabase setup scripts
# Validates both bash and Python setup scripts

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
test_start() {
    echo -e "${BLUE}Testing: $1${NC}"
    ((TESTS_RUN++))
}

test_pass() {
    echo -e "${GREEN}✓ PASS${NC}"
    ((TESTS_PASSED++))
}

test_fail() {
    local msg="$1"
    echo -e "${RED}✗ FAIL${NC}: $msg"
    ((TESTS_FAILED++))
}

# ══════════════════════════════════════════════════════════════════════════════
# Test: Script Existence
# ══════════════════════════════════════════════════════════════════════════════

test_start "Setup scripts exist"
if [ -f "$SCRIPT_DIR/setup_supabase.sh" ] && [ -f "$SCRIPT_DIR/setup_supabase.py" ]; then
    test_pass
else
    test_fail "Scripts not found"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Bash Script Syntax
# ══════════════════════════════════════════════════════════════════════════════

test_start "Bash script syntax"
if bash -n "$SCRIPT_DIR/setup_supabase.sh" 2>/dev/null; then
    test_pass
else
    test_fail "Bash syntax error"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Bash Script Executable
# ══════════════════════════════════════════════════════════════════════════════

test_start "Bash script is executable"
if [ -x "$SCRIPT_DIR/setup_supabase.sh" ]; then
    test_pass
else
    test_fail "Script not executable"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Bash Script Help
# ══════════════════════════════════════════════════════════════════════════════

test_start "Bash script --help works"
if "$SCRIPT_DIR/setup_supabase.sh" --help 2>&1 | grep -q "NetEngine Supabase"; then
    test_pass
else
    test_fail "--help output incorrect"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Python Script Syntax
# ══════════════════════════════════════════════════════════════════════════════

test_start "Python script syntax"
if python3 -m py_compile "$SCRIPT_DIR/setup_supabase.py" 2>/dev/null; then
    test_pass
else
    test_fail "Python syntax error"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Python Script Help
# ══════════════════════════════════════════════════════════════════════════════

test_start "Python script --help works"
if python3 "$SCRIPT_DIR/setup_supabase.py" --help 2>&1 | grep -q "Supabase"; then
    test_pass
else
    test_fail "--help output incorrect"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Documentation Files
# ══════════════════════════════════════════════════════════════════════════════

test_start "Documentation exists"
if [ -f "$PROJECT_ROOT/docs/SUPABASE_SETUP.md" ]; then
    test_pass
else
    test_fail "Documentation not found"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Migration File Exists
# ══════════════════════════════════════════════════════════════════════════════

test_start "Migration file exists"
if [ -f "$PROJECT_ROOT/migrations/001_initial.sql" ]; then
    test_pass
else
    test_fail "Migration file not found"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: .env.example is Present
# ══════════════════════════════════════════════════════════════════════════════

test_start ".env.example exists"
if [ -f "$PROJECT_ROOT/.env.example" ]; then
    test_pass
else
    test_fail ".env.example not found"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: psql Command Available (if needed)
# ══════════════════════════════════════════════════════════════════════════════

test_start "psql command available"
if command -v psql &>/dev/null; then
    test_pass
else
    echo -e "${YELLOW}⚠ psql not found (install postgresql-client to use scripts)${NC}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Python 3.13+ Available (if needed for main CLI)
# ══════════════════════════════════════════════════════════════════════════════

test_start "Python version compatible"
python_version=$(python3 --version 2>&1 | awk '{print $2}')
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" 2>/dev/null; then
    test_pass
else
    echo -e "${YELLOW}⚠ Python 3.13+ recommended (found $python_version)${NC}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Bash Scripts Contain Key Functions
# ══════════════════════════════════════════════════════════════════════════════

test_start "Bash script has setup function"
if grep -q "collect_credentials\|test_connection\|run_migrations" "$SCRIPT_DIR/setup_supabase.sh"; then
    test_pass
else
    test_fail "Key functions missing"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Test: Python Script Has Key Classes
# ══════════════════════════════════════════════════════════════════════════════

test_start "Python script has setup class"
if grep -q "class SupabaseSetup\|def test_connection\|def run_migrations" "$SCRIPT_DIR/setup_supabase.py"; then
    test_pass
else
    test_fail "Key classes missing"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${BLUE}═════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}═════════════════════════════════════════════════${NC}"

echo "Total tests: $TESTS_RUN"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
else
    echo -e "${GREEN}Failed: 0${NC}"
fi

echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
