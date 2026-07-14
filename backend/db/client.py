from supabase import create_client, Client
from backend.common.config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY


def get_client(use_service_role: bool = True) -> Client:
    """Return a Supabase client.

    Args:
        use_service_role: If True (default), use the service_role key
            which bypasses RLS. Set to False for anon-level access
            (e.g. testing what a frontend user would see).
    """
    key = SUPABASE_SERVICE_ROLE_KEY if use_service_role else SUPABASE_KEY
    return create_client(SUPABASE_URL, key)