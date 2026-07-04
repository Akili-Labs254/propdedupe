"""Central configuration. Reads secrets from a local .env file.

Copy .env.example to .env and fill in your values. .env is gitignored — never
commit real credentials.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── Supabase / Postgres connection ──
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ── Scraper politeness ──
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "PropDeDupe/0.1 (student portfolio project; contact: you@example.com)",
)
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "2"))
