#!/bin/bash
# NetEngine Supabase Setup & Configuration Script
#
# This script sets up and configures a Supabase project for use with NetEngine.
# It handles:
#   - Environment validation
#   - Connection testing
#   - Schema migration
#   - Database extensions setup
#   - Table and function creation
#   - pgmq queue initialization
#   - Configuration verification
#
# Usage:
#   ./scripts/setup_supabase.sh                    # Interactive mode
#   ./scripts/setup_supabase.sh --validate-only    # Check existing setup
#   ./scripts/setup_supabase.sh --help             # Show help

set -e

# ══════════════════════════════════════════════════════════════════════════════
# Configuration & Colors
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MIGRATIONS_DIR="$PROJECT_ROOT/migrations"

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Flags
VALIDATE_ONLY=false
INTERACTIVE=true
SKIP_PGMQ=false
VERBOSE=false

# ══════════════════════════════════════════════════════════════════════════════
# Utility Functions
# ══════════════════════════════════════════════════════════════════════════════

print_header() {
    echo -e "${BLUE}${BOLD}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

print_section() {
    echo -e "\n${BOLD}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}\n"
}

prompt_input() {
    local prompt_text="$1"
    local default="$2"
    local input

    if [ -z "$default" ]; then
        read -p "$(echo -e ${BLUE})$prompt_text$(echo -e ${NC}) " input
    else
        read -p "$(echo -e ${BLUE})$prompt_text [${default}]$(echo -e ${NC}) " input
        input="${input:-$default}"
    fi

    echo "$input"
}

prompt_secret() {
    local prompt_text="$1"
    local input

    read -sp "$(echo -e ${BLUE})$prompt_text$(echo -e ${NC}) " input
    echo ""
    echo "$input"
}

# ══════════════════════════════════════════════════════════════════════════════
# Help & Usage
# ══════════════════════════════════════════════════════════════════════════════

show_help() {
    cat << 'EOF'
NetEngine Supabase Setup Script

Usage:
  ./scripts/setup_supabase.sh [OPTIONS]

Options:
  --validate-only     Check existing Supabase setup without making changes
  --skip-pgmq         Skip pgmq queue setup (not all Supabase plans support it)
  --verbose           Show detailed output from database operations
  --non-interactive   Use environment variables only (no prompts)
  --help              Show this help message

Environment Variables (used in non-interactive mode):
  SUPABASE_URL                 Supabase project URL (https://xxxxx.supabase.co)
  SUPABASE_SERVICE_KEY         Service role key from Supabase dashboard
  SUPABASE_DB_HOST             Database host (optional, usually inferred from URL)
  SUPABASE_DB_PORT             Database port (optional, default: 5432)
  SUPABASE_DB_USER             Database user (optional, default: postgres)
  SUPABASE_DB_PASSWORD         Database password (optional, required for migrations)

Examples:
  # Interactive setup
  ./scripts/setup_supabase.sh

  # Check existing setup
  ./scripts/setup_supabase.sh --validate-only

  # Non-interactive with env vars
  export SUPABASE_URL="https://xxxxx.supabase.co"
  export SUPABASE_SERVICE_KEY="eyJ..."
  export SUPABASE_DB_PASSWORD="dbpassword123"
  ./scripts/setup_supabase.sh --non-interactive

EOF
}

# ══════════════════════════════════════════════════════════════════════════════
# Parse Command Line Arguments
# ══════════════════════════════════════════════════════════════════════════════

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --validate-only)
                VALIDATE_ONLY=true
                shift
                ;;
            --skip-pgmq)
                SKIP_PGMQ=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --non-interactive)
                INTERACTIVE=false
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

# ══════════════════════════════════════════════════════════════════════════════
# Credential Collection
# ══════════════════════════════════════════════════════════════════════════════

collect_credentials() {
    print_section "Supabase Credentials"

    # Try to load from .env if it exists
    local env_file="$PROJECT_ROOT/.env"
    if [ -f "$env_file" ]; then
        print_info "Loading existing credentials from .env..."
        # Source .env carefully (only our specific vars)
        if grep -q "^SUPABASE_URL=" "$env_file"; then
            SUPABASE_URL=$(grep "^SUPABASE_URL=" "$env_file" | cut -d'=' -f2-)
        fi
        if grep -q "^SUPABASE_SERVICE_KEY=" "$env_file"; then
            SUPABASE_SERVICE_KEY=$(grep "^SUPABASE_SERVICE_KEY=" "$env_file" | cut -d'=' -f2-)
        fi
        if grep -q "^SUPABASE_DB_PASSWORD=" "$env_file"; then
            SUPABASE_DB_PASSWORD=$(grep "^SUPABASE_DB_PASSWORD=" "$env_file" | cut -d'=' -f2-)
        fi
    fi

    # Prompt for credentials if not set
    if [ -z "$SUPABASE_URL" ]; then
        print_info "Get your Supabase URL from: https://app.supabase.com/project/[project-ref]/settings/api"
        SUPABASE_URL=$(prompt_input "Supabase URL (https://xxxxx.supabase.co):")
    fi

    if [ -z "$SUPABASE_SERVICE_KEY" ]; then
        print_info "Copy the 'service_role' secret from the API keys section"
        SUPABASE_SERVICE_KEY=$(prompt_secret "Service Role Key (will not echo):")
    fi

    # Database credentials
    extract_db_host_from_url

    if [ -z "$SUPABASE_DB_PASSWORD" ]; then
        print_info "Database password is stored in Supabase dashboard under Settings > Database"
        SUPABASE_DB_PASSWORD=$(prompt_secret "Database Password (will not echo):")
    fi

    export SUPABASE_URL
    export SUPABASE_SERVICE_KEY
    export SUPABASE_DB_PASSWORD
    export SUPABASE_DB_HOST
    export SUPABASE_DB_PORT
    export SUPABASE_DB_USER
}

extract_db_host_from_url() {
    # Extract host from URL like https://xxxxx.supabase.co
    local url_part="${SUPABASE_URL#https://}"
    url_part="${url_part#http://}"
    SUPABASE_DB_HOST="${url_part%%/}"

    # For Supabase, port is usually 5432
    SUPABASE_DB_PORT="${SUPABASE_DB_PORT:-5432}"
    SUPABASE_DB_USER="${SUPABASE_DB_USER:-postgres}"
}

# ══════════════════════════════════════════════════════════════════════════════
# Connection Testing
# ══════════════════════════════════════════════════════════════════════════════

test_connection() {
    print_section "Testing Database Connection"

    print_info "Testing psql connection to Supabase database..."

    local pgpassword="$SUPABASE_DB_PASSWORD"
    export PGPASSWORD="$pgpassword"

    if psql \
        -h "$SUPABASE_DB_HOST" \
        -p "$SUPABASE_DB_PORT" \
        -U "$SUPABASE_DB_USER" \
        -d "postgres" \
        -c "SELECT version();" > /dev/null 2>&1; then
        print_success "✓ Database connection successful"

        # Get version info
        local version=$(psql \
            -h "$SUPABASE_DB_HOST" \
            -p "$SUPABASE_DB_PORT" \
            -U "$SUPABASE_DB_USER" \
            -d "postgres" \
            -t -c "SELECT version();" 2>/dev/null | head -1)
        print_info "Database version: $version"
        return 0
    else
        print_error "✗ Failed to connect to database"
        print_error "Host: $SUPABASE_DB_HOST:$SUPABASE_DB_PORT"
        print_error "User: $SUPABASE_DB_USER"
        print_info "Common issues:"
        print_info "  - Database password is incorrect"
        print_info "  - Database is not accessible from your IP (check Supabase firewall)"
        print_info "  - psql is not installed (install postgresql-client)"
        unset PGPASSWORD
        return 1
    fi

    unset PGPASSWORD
}

# ══════════════════════════════════════════════════════════════════════════════
# Schema Migration
# ══════════════════════════════════════════════════════════════════════════════

run_migrations() {
    print_section "Running Database Migrations"

    if [ ! -f "$MIGRATIONS_DIR/001_initial.sql" ]; then
        print_error "Migration file not found: $MIGRATIONS_DIR/001_initial.sql"
        return 1
    fi

    print_info "Applying migrations from: $MIGRATIONS_DIR/001_initial.sql"

    export PGPASSWORD="$SUPABASE_DB_PASSWORD"

    # Run migration
    if [ "$VERBOSE" = true ]; then
        psql \
            -h "$SUPABASE_DB_HOST" \
            -p "$SUPABASE_DB_PORT" \
            -U "$SUPABASE_DB_USER" \
            -d "postgres" \
            -f "$MIGRATIONS_DIR/001_initial.sql" \
            -v ON_ERROR_STOP=1
    else
        psql \
            -h "$SUPABASE_DB_HOST" \
            -p "$SUPABASE_DB_PORT" \
            -U "$SUPABASE_DB_USER" \
            -d "postgres" \
            -f "$MIGRATIONS_DIR/001_initial.sql" \
            -v ON_ERROR_STOP=1 \
            -q 2>&1 | grep -v "^$" || true
    fi

    if [ $? -eq 0 ]; then
        print_success "✓ Migrations applied successfully"
    else
        print_error "✗ Migration failed"
        print_error "Note: Some pgmq functionality may not be available in Supabase"
        print_info "You can skip pgmq setup with: ./scripts/setup_supabase.sh --skip-pgmq"
        unset PGPASSWORD
        return 1
    fi

    unset PGPASSWORD
}

# ══════════════════════════════════════════════════════════════════════════════
# Validation & Checks
# ══════════════════════════════════════════════════════════════════════════════

validate_setup() {
    print_section "Validating Setup"

    local failed=0

    export PGPASSWORD="$SUPABASE_DB_PASSWORD"

    # Check tables exist
    local tables=("runtime_state" "world_registry" "address_pools" "address_leases" "domain_records" "operator_log")

    for table in "${tables[@]}"; do
        if psql -h "$SUPABASE_DB_HOST" -p "$SUPABASE_DB_PORT" -U "$SUPABASE_DB_USER" \
               -d "postgres" -t -c "SELECT to_regclass('public.$table');" 2>/dev/null | grep -q "$table"; then
            print_success "✓ Table '$table' exists"
        else
            print_warning "⚠ Table '$table' not found"
            ((failed++))
        fi
    done

    # Check functions exist
    local functions=("pgmq_send" "pgmq_pop" "pgmq_delete")

    for func in "${functions[@]}"; do
        if psql -h "$SUPABASE_DB_HOST" -p "$SUPABASE_DB_PORT" -U "$SUPABASE_DB_USER" \
               -d "postgres" -t -c "SELECT to_regprocedure('$func(text, text)');" 2>/dev/null | grep -q "$func"; then
            print_success "✓ Function '$func' exists"
        else
            print_info "ℹ Function '$func' not found (pgmq may not be available)"
        fi
    done

    unset PGPASSWORD

    if [ $failed -gt 0 ]; then
        print_warning "⚠ Some validations failed, but setup may still work"
        return 1
    fi

    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
# Environment Configuration
# ══════════════════════════════════════════════════════════════════════════════

save_configuration() {
    print_section "Saving Configuration"

    local env_file="$PROJECT_ROOT/.env"

    # Backup existing .env if it exists
    if [ -f "$env_file" ]; then
        local backup_file="${env_file}.backup.$(date +%s)"
        cp "$env_file" "$backup_file"
        print_info "Backed up existing .env to: $backup_file"
    fi

    # Create or update .env with Supabase config
    if [ -f "$env_file" ]; then
        # Update existing variables
        sed -i.bak "s|^SUPABASE_URL=.*|SUPABASE_URL=$SUPABASE_URL|" "$env_file"
        sed -i.bak "s|^SUPABASE_SERVICE_KEY=.*|SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY|" "$env_file"

        # Add if not present
        if ! grep -q "^SUPABASE_URL=" "$env_file"; then
            echo "SUPABASE_URL=$SUPABASE_URL" >> "$env_file"
        fi
        if ! grep -q "^SUPABASE_SERVICE_KEY=" "$env_file"; then
            echo "SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY" >> "$env_file"
        fi
    else
        # Create new .env from example
        if [ -f "$PROJECT_ROOT/.env.example" ]; then
            cp "$PROJECT_ROOT/.env.example" "$env_file"
        fi

        # Add Supabase config
        echo "SUPABASE_URL=$SUPABASE_URL" >> "$env_file"
        echo "SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY" >> "$env_file"
    fi

    # Make .env readable only by owner (security)
    chmod 600 "$env_file"

    print_success "✓ Configuration saved to: $env_file"
    print_warning "⚠ Keep your .env file secure — it contains sensitive credentials"
}

# ══════════════════════════════════════════════════════════════════════════════
# Next Steps
# ══════════════════════════════════════════════════════════════════════════════

show_next_steps() {
    print_section "Setup Complete!"

    cat << EOF
${GREEN}Your Supabase project is now configured for NetEngine.${NC}

${BOLD}Next Steps:${NC}

1. ${BOLD}Verify Environment Variables${NC}
   Source your .env file:
   ${BLUE}cd $PROJECT_ROOT${NC}

2. ${BOLD}Run Migrations${NC}
   Apply the complete database schema:
   ${BLUE}poetry run python -m netengine.utils.run_migrations${NC}

3. ${BOLD}Start NetEngine${NC}
   Bootstrap a world using your Supabase database:
   ${BLUE}poetry run netengine up examples/minimal.yaml${NC}

4. ${BOLD}Monitor Status${NC}
   Check the world status at any time:
   ${BLUE}poetry run netengine status${NC}

${YELLOW}Important Notes:${NC}
- pgmq functionality may be limited in Supabase (depending on plan)
- Keep your .env file secure — it contains database credentials
- Supabase Free tier has query limits; consider Professional for production
- Database backups are managed by Supabase — configure in the dashboard

${BLUE}For more information:${NC}
- NetEngine docs: https://github.com/Forebase/NetEngine#readme
- Supabase docs: https://supabase.com/docs

EOF
}

show_validation_only() {
    print_section "Validation Summary"

    cat << EOF
${GREEN}Your Supabase setup is valid and ready to use.${NC}

${BOLD}Current Configuration:${NC}
- URL: $SUPABASE_URL
- Database Host: $SUPABASE_DB_HOST
- Database User: $SUPABASE_DB_USER

${BOLD}To use with NetEngine:${NC}
${BLUE}export SUPABASE_URL="$SUPABASE_URL"${NC}
${BLUE}export SUPABASE_SERVICE_KEY="[your-service-key]"${NC}
${BLUE}poetry run netengine up examples/minimal.yaml${NC}

EOF
}

# ══════════════════════════════════════════════════════════════════════════════
# Main Execution
# ══════════════════════════════════════════════════════════════════════════════

main() {
    # Print banner
    echo -e "${BLUE}${BOLD}"
    cat << 'EOF'
╔═══════════════════════════════════════════════════════════╗
║  NetEngine Supabase Setup & Configuration Script         ║
║  Configure your Supabase project for NetEngine           ║
╚═══════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}\n"

    # Parse arguments
    parse_args "$@"

    # Load from environment if non-interactive
    if [ "$INTERACTIVE" = false ]; then
        if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_SERVICE_KEY" ]; then
            print_error "Missing required environment variables for non-interactive mode"
            print_info "Required: SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_DB_PASSWORD"
            exit 1
        fi
        extract_db_host_from_url
    else
        # Collect credentials interactively
        collect_credentials
    fi

    # Test connection
    if ! test_connection; then
        print_error "Cannot proceed without a valid database connection"
        exit 1
    fi

    # Validation-only mode
    if [ "$VALIDATE_ONLY" = true ]; then
        if validate_setup; then
            show_validation_only
            exit 0
        else
            print_warning "Some validations failed, but basic setup appears complete"
            show_validation_only
            exit 0
        fi
    fi

    # Run migrations
    if ! run_migrations; then
        print_warning "⚠ Migrations failed — pgmq may not be available in your Supabase plan"
        if [ "$SKIP_PGMQ" = false ] && [ "$INTERACTIVE" = true ]; then
            local response=$(prompt_input "Continue without pgmq? (y/n)" "n")
            if [[ "$response" != "y" && "$response" != "Y" ]]; then
                print_info "Aborting setup. Try again with --skip-pgmq if pgmq is not needed"
                exit 1
            fi
        fi
    fi

    # Validate
    validate_setup

    # Save configuration
    save_configuration

    # Show next steps
    show_next_steps
}

# ══════════════════════════════════════════════════════════════════════════════
# Error Handling
# ══════════════════════════════════════════════════════════════════════════════

trap 'print_error "Script interrupted"; exit 130' INT TERM
trap 'print_error "An error occurred"; exit 1' ERR

# ══════════════════════════════════════════════════════════════════════════════
# Run Main Function
# ══════════════════════════════════════════════════════════════════════════════

main "$@"
