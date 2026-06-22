import os

from supabase import Client, create_client

_supabase: Client | None = None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Set it in your .env file or environment before starting NetEngine."
        )
    return value


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        url = _require_env("SUPABASE_URL")
        key = _require_env("SUPABASE_SERVICE_KEY")
        _supabase = create_client(url, key)
    return _supabase
