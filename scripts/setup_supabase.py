#!/usr/bin/env python3
"""
NetEngine Supabase Setup Utility

Programmatic setup and configuration of Supabase for NetEngine.
Can be used standalone or imported as a module.

Usage:
    # As a script
    python scripts/setup_supabase.py --validate-only
    python scripts/setup_supabase.py --setup

    # As a module
    from setup_supabase import SupabaseSetup
    setup = SupabaseSetup(url="...", key="...", password="...")
    setup.validate()
    setup.run_migrations()
"""

import asyncio
import os
import sys
import argparse
import json
import subprocess
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse
from dataclasses import dataclass
import shutil


@dataclass
class SupabaseConfig:
    """Supabase configuration."""

    url: str
    service_key: str
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str = "postgres"

    @classmethod
    def from_env(cls) -> Optional["SupabaseConfig"]:
        """Load configuration from environment variables."""
        url = os.environ.get("SUPABASE_URL")
        service_key = os.environ.get("SUPABASE_SERVICE_KEY")
        db_password = os.environ.get("SUPABASE_DB_PASSWORD")

        if not url or not service_key or not db_password:
            return None

        # Extract host from URL
        parsed = urlparse(url)
        db_host = parsed.netloc or "localhost"

        db_port = int(os.environ.get("SUPABASE_DB_PORT", 5432))
        db_user = os.environ.get("SUPABASE_DB_USER", "postgres")

        return cls(
            url=url,
            service_key=service_key,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
        )


class SupabaseSetup:
    """Supabase setup and configuration utility."""

    def __init__(self, config: SupabaseConfig, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.project_root = Path(__file__).parent.parent
        self.migrations_dir = self.project_root / "migrations"

    def _run_psql(
        self, sql: str = "", sql_file: Optional[Path] = None, quiet: bool = True
    ) -> Tuple[int, str, str]:
        """Execute psql command."""
        env = os.environ.copy()
        env["PGPASSWORD"] = self.config.db_password

        cmd = [
            "psql",
            "-h",
            self.config.db_host,
            "-p",
            str(self.config.db_port),
            "-U",
            self.config.db_user,
            "-d",
            self.config.db_name,
        ]

        if sql_file:
            cmd.extend(["-f", str(sql_file)])
        else:
            cmd.extend(["-c", sql])

        if quiet:
            cmd.append("-q")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            raise RuntimeError("psql command not found. Install postgresql-client.")
        except subprocess.TimeoutExpired:
            raise RuntimeError("psql command timed out")

    def test_connection(self) -> bool:
        """Test database connection."""
        returncode, stdout, stderr = self._run_psql("SELECT version();", quiet=False)
        if returncode != 0:
            if self.verbose:
                print(f"Connection error: {stderr}", file=sys.stderr)
            return False

        version = stdout.strip().split("\n")[0] if stdout else "Unknown"
        if self.verbose:
            print(f"✓ Connected to: {version}")

        return True

    def validate(self) -> bool:
        """Validate Supabase setup."""
        print("Validating Supabase setup...")

        tables = [
            "runtime_state",
            "world_registry",
            "address_pools",
            "address_leases",
            "domain_records",
            "operator_log",
        ]

        missing_tables = []
        for table in tables:
            returncode, stdout, _ = self._run_psql(
                f"SELECT to_regclass('public.{table}');"
            )
            if returncode != 0 or not stdout.strip():
                missing_tables.append(table)

        if missing_tables:
            print(f"✗ Missing tables: {', '.join(missing_tables)}")
            return False

        print(f"✓ All required tables exist ({len(tables)} tables)")

        # Check functions
        functions = ["pgmq_send", "pgmq_pop", "pgmq_delete"]
        missing_functions = []

        for func in functions:
            returncode, stdout, _ = self._run_psql(
                f"SELECT to_regprocedure('{func}(text, text)');"
            )
            if returncode != 0 or not stdout.strip():
                missing_functions.append(func)

        if missing_functions:
            print(
                f"⚠ Missing functions: {', '.join(missing_functions)} "
                "(pgmq may not be available in your plan)"
            )
        else:
            print(f"✓ All pgmq functions exist ({len(functions)} functions)")

        return len(missing_tables) == 0

    def run_migrations(self) -> bool:
        """Run database migrations."""
        migration_file = self.migrations_dir / "001_initial.sql"

        if not migration_file.exists():
            raise FileNotFoundError(f"Migration file not found: {migration_file}")

        print(f"Applying migrations from: {migration_file}")

        returncode, stdout, stderr = self._run_psql(
            sql_file=migration_file,
            quiet=not self.verbose,
        )

        if returncode != 0:
            if self.verbose:
                print(f"Migration output:\n{stdout}\n{stderr}", file=sys.stderr)
            print(f"✗ Migrations failed")
            if "pgmq" in stderr.lower():
                print(
                    "⚠ pgmq extension not available. "
                    "Your Supabase plan may not support it."
                )
            return False

        print("✓ Migrations applied successfully")
        return True

    def save_env(self, env_path: Optional[Path] = None) -> bool:
        """Save configuration to .env file."""
        if env_path is None:
            env_path = self.project_root / ".env"

        # Backup existing
        if env_path.exists():
            backup_path = env_path.with_suffix(f".backup.{int(__import__('time').time())}")
            shutil.copy(env_path, backup_path)
            print(f"Backed up existing .env to: {backup_path}")

        # Read existing or start fresh
        env_content = ""
        if env_path.exists():
            with open(env_path) as f:
                env_content = f.read()

        # Update or add Supabase variables
        lines = env_content.split("\n") if env_content else []
        updated_lines = []
        updated_vars = set()

        for line in lines:
            if line.startswith("SUPABASE_URL="):
                updated_lines.append(f"SUPABASE_URL={self.config.url}")
                updated_vars.add("SUPABASE_URL")
            elif line.startswith("SUPABASE_SERVICE_KEY="):
                updated_lines.append(f"SUPABASE_SERVICE_KEY={self.config.service_key}")
                updated_vars.add("SUPABASE_SERVICE_KEY")
            else:
                updated_lines.append(line)

        # Add missing variables
        if "SUPABASE_URL" not in updated_vars:
            updated_lines.append(f"SUPABASE_URL={self.config.url}")
        if "SUPABASE_SERVICE_KEY" not in updated_vars:
            updated_lines.append(f"SUPABASE_SERVICE_KEY={self.config.service_key}")

        # Write back
        final_content = "\n".join(updated_lines).strip() + "\n"
        with open(env_path, "w") as f:
            f.write(final_content)

        # Restrict permissions
        os.chmod(env_path, 0o600)

        print(f"✓ Configuration saved to: {env_path}")
        return True

    def setup(self) -> bool:
        """Run complete setup process."""
        print("Starting Supabase setup...\n")

        # Test connection
        print("1. Testing connection...")
        if not self.test_connection():
            print("✗ Cannot connect to database", file=sys.stderr)
            return False

        print()

        # Run migrations
        print("2. Running migrations...")
        if not self.run_migrations():
            print("✗ Migrations failed", file=sys.stderr)
            if not self.verbose:
                print("Run with --verbose for more details", file=sys.stderr)
            return False

        print()

        # Validate
        print("3. Validating setup...")
        if not self.validate():
            print("✗ Validation failed", file=sys.stderr)
            return False

        print()

        # Save environment
        print("4. Saving configuration...")
        if not self.save_env():
            print("✗ Failed to save configuration", file=sys.stderr)
            return False

        print()
        print("✓ Supabase setup complete!")
        return True


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="NetEngine Supabase Setup Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full setup (interactive)
  python scripts/setup_supabase.py --setup

  # Validate existing setup
  python scripts/setup_supabase.py --validate-only

  # Non-interactive with environment variables
  export SUPABASE_URL="https://xxxxx.supabase.co"
  export SUPABASE_SERVICE_KEY="eyJ..."
  export SUPABASE_DB_PASSWORD="password"
  python scripts/setup_supabase.py --setup --non-interactive

  # Verbose output
  python scripts/setup_supabase.py --setup --verbose
        """,
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run complete setup process",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate existing setup",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Only run migrations",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Only test database connection",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use environment variables only (no prompts)",
    )

    args = parser.parse_args()

    # Load configuration
    config = SupabaseConfig.from_env()
    if not config:
        print("Error: Supabase credentials not found in environment", file=sys.stderr)
        print(
            "Set: SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_DB_PASSWORD",
            file=sys.stderr,
        )
        sys.exit(1)

    setup = SupabaseSetup(config, verbose=args.verbose)

    try:
        if args.test_connection:
            if setup.test_connection():
                print("✓ Connection successful")
                sys.exit(0)
            else:
                print("✗ Connection failed", file=sys.stderr)
                sys.exit(1)

        elif args.migrate:
            if setup.run_migrations():
                sys.exit(0)
            else:
                sys.exit(1)

        elif args.validate_only:
            if setup.validate():
                print("\n✓ Setup is valid")
                sys.exit(0)
            else:
                print("\n✗ Setup validation failed", file=sys.stderr)
                sys.exit(1)

        elif args.setup:
            if setup.setup():
                sys.exit(0)
            else:
                sys.exit(1)

        else:
            parser.print_help()
            sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
