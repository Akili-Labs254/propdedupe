# Frontend (deferred — decision pending)

Built last and deliberately kept thin; it's the least important part for
backend / fintech roles. Two options, decided when we get here:

- **Next.js on Vercel** — the target; free hosting, good portfolio signal.
- **Minimal HTML + Supabase JS** — the scope-cut fallback if time is short.

Its only job: read deduplicated `properties` from the API / Supabase and show a
clean, dupe-free list.
