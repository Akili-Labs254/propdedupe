from functools import lru_cache

from supabase import create_client, Client, ClientOptions
from backend.common.config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY


# CACHED, and it matters. Every web request calls get_client() — home,
# property, segment, sitemap, admin. Un-cached, each request built a fresh
# Supabase client wrapping its own httpx client, connection pool and SSL
# context, and nothing ever closed them. They accumulated until the 512MB
# Render box died: three OOM crashes on 2026-08-07, roughly 7-8 hours apart,
# getting more frequent as traffic grew.
#
# maxsize=4 covers every combination actually used: service_role or anon,
# web timeout (120) or batch timeout (600).
#
# Trade-off accepted: a long-lived client eventually meets Supabase's
# HTTP/2 GOAWAY after ~10k requests on one connection, where a per-request
# client never did. httpx opens a fresh connection for the next request, so
# the cost is at worst a single failed request — against a crash every seven
# hours. The batch scripts already wrap their calls in _with_retry.
#
# Note: the cached client holds the key it was created with, so rotating
# credentials needs a process restart. A redeploy does that anyway.
@lru_cache(maxsize=4)
def get_client(use_service_role: bool = True, timeout: int = 120) -> Client:
    """Return a Supabase client (cached per argument combination).

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