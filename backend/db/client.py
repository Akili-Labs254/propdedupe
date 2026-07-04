from supabase import create_client, Client
from backend.common.config import SUPABASE_URL, SUPABASE_KEY

def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)