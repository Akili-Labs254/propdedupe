"""Short memorable paths for video CTAs, and the attribution for them.

Someone watching a 12-second TikTok cannot be asked to remember
"fikia.co.ke/apartments-for-rent/ngong-road/2-bedroom". They can remember
"fikia.co.ke/50k".

The second job matters as much as the first: TikTok strips referrers, and
viewers type the URL rather than clicking it, so referrer-based attribution
is impossible: 767 video views produced 9 visits we could only guess at.
Here THE PATH ITSELF IS THE TAG. Each code appends its own ?ref=, so
GoatCounter separates "saw the 50k video" from plain direct traffic without
needing a referrer at all.

302, not 301: these are marketing links whose destination may change as
inventory shifts, and browsers cache a 301 hard: anyone who watched an old
video would be stranded on a stale destination forever.

REGISTRATION: this router owns "/{code}" and MUST be included LAST in
main.py. Anything registered after it gets shadowed.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

router = APIRouter(include_in_schema=False)

# code -> (destination path, ref tag)
# Keep codes short, lowercase, and sayable out loud in a voiceover.
VANITY = {
    "50k":   ("/?category=rent&min_price=45000&max_price=55000", "tt-50k"),
    "karen": ("/apartments-for-rent/karen/3-bedroom",            "tt-karen"),
    "dupes": ("/",                                               "tt-dupes"),
    # Both video CTAs sort cheapest-first. Without it the default
    # newest-first order opened /4bed on 145M-780M Lavington villas for
    # viewers who had just watched a video about a 16M house in Syokimau.
    "20m":  ("/?category=sale&max_price=20000000&sort=price_asc", "tt-20m"),
    "4bed": ("/?category=sale&bedrooms=4&sort=price_asc",         "tt-4bed"),
}


def destination(code: str) -> str:
    """Resolve a code to its full destination including the ref tag."""
    path, ref = VANITY[code]
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}ref={ref}"


@router.get("/{code}")
def vanity(code: str):
    target = VANITY.get(code.lower())
    if not target:
        # Unknown single-segment path: same 404 it would have got anyway.
        raise HTTPException(status_code=404, detail="Not found")
    return RedirectResponse(destination(code.lower()), status_code=302)
