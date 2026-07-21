from supabase import create_client, Client, ClientOptions
from backend.common.config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY


def get_client(use_service_role: bool = True, timeout: int = 120) -> Client:
    """Return a Supabase client.

    Args:
        use_service_role: If True (default), use the service_role key
            which bypasses RLS. Set to False for anon-level access
            (e.g. testing what a frontend user would see).
        timeout: PostgREST HTTP read timeout in seconds. The web app keeps
            the snappy default (120); the batch pipeline (refresh /
            persist_dedup) passes a large value because its bulk
            clears/updates can run for minutes on the free-tier box. With
            the default the client abandons a still-running statement, and
            the retry then collides with the original's row locks
            (Postgres 55P03 "canceling statement due to lock timeout").
    """
    key = SUPABASE_SERVICE_ROLE_KEY if use_service_role else SUPABASE_KEY
    return create_client(
        SUPABASE_URL, key,
        options=ClientOptions(
            postgrest_client_timeout=timeout,
            storage_client_timeout=timeout,
        ),
    )