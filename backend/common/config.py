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
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# ── Scraper politeness ──
# The contact address must be REAL. This shipped as "you@example.com" for
# two months — a politeness header inviting contact at an address nobody
# reads, which is worse than no address: it looks like boilerplate that was
# never meant. When BuyRentKenya's team saw this crawler in their logs, they
# had no way to ask about it. They blocked it instead (ADR 0010).
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Fikia/1.0 (+https://www.fikia.co.ke; contact: tjamieode@gmail.com)",
)

# Raised from 2s on 2026-09-02. The old rate, combined with crawling to
# page 600 when robots.txt permits 10, is the most plausible trigger for
# the block. Politeness is cheap: these are nightly batch jobs, and an
# extra 3s per page costs hours we are not waiting on anyway.
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "5"))
