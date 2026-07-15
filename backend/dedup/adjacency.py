"""
Nairobi suburb adjacency map for PropDeDupe cross-source dedup.

PURPOSE: When P24 says "Lavington" and BRK says "Kilimani" for the same house,
the scorer needs to know they border each other (score ~0.3) rather than
treating them as completely different locations (score 1.0).

TWO LAYERS:
1. Name normalization — strips prefixes ("Off", "Near", "Along"), collapses
   road variants ("Rd"→"Road"), lowercases, and maps known aliases to their
   canonical P24 suburb name.
2. Adjacency graph — which suburbs physically border each other, built from:
   - P24 address hierarchy (e.g. "Kitisuru, Runda, Nairobi" → Runda nests
     under Kitisuru area)
   - Nairobi sub-county/ward groupings (administrative proximity)
   - Wikipedia/geographic sources (physical borders)
   - Confirmed false positives from pipeline validation (Karen↔Runda gap, etc.)

DATA SOURCES:
- P24 canonical suburb list: ~130 suburbs from /search/suburbsuggestions endpoint
- Sub-county groupings: Westlands (Runda, Kitisuru, Kangemi...),
  Dagoretti North (Kilimani, Kileleshwa, Kawangware...), Langata (Karen, Hardy...)
- Wikipedia Kilimani article: "north of Woodley; east of Kileleshwa; borders
  Lavington in the west"
- Pipeline false positives: Karen↔Runda, South C↔Syokimau were missing edges
"""

import re
from typing import Optional


# ── Step 1: Name normalization ──────────────────────────────────────────
#
# WHY: BRK and P24 don't always use the same string for the same place.
# BRK might say "Off Kiambu Road" while P24 says "Kiambu Road".
# Or "Ngong Road" vs "Ngong Rd". Normalization collapses these to one
# canonical form before the adjacency lookup happens.

# Prefixes that agents prepend to suburb names — stripped because
# "Off Kiambu Road" IS Kiambu Road for dedup purposes.
_STRIP_PREFIXES = re.compile(
    r'^(off|near|along|opposite|behind|next\s+to|adjacent\s+to)\s+',
    re.IGNORECASE
)

# Road/street suffix variants → canonical form
_ROAD_VARIANTS = {
    ' rd': ' road',
    ' st': ' street',
    ' ave': ' avenue',
    ' dr': ' drive',
    ' ln': ' lane',
}

# Known aliases: maps non-canonical names → canonical P24 suburb name.
# Built reactively from real BRK/P24 mismatches.
_ALIASES = {
    # ── P24 variant names ──
    'hurlingham': 'kilimani',       # Hurlingham is part of greater Kilimani
    'upper hill': 'kilimani',       # Upper Hill is administratively part of Kilimani
    'new kitisuru': 'kitisuru',
    'new muthaiga': 'muthaiga',
    'old muthaiga': 'muthaiga',     # BRK uses "Old Muthaiga"
    'muthaiga north': 'muthaiga',
    'kahawa sukari': 'kahawa',
    'kahawa wendani': 'kahawa',
    'kahawa west': 'kahawa',
    'eastleigh north': 'eastleigh',
    'eastleigh south': 'eastleigh',
    'kariobangi north': 'kariobangi',
    'kariobangi south': 'kariobangi',
    'githurai 44': 'githurai',
    'ngumo estate': 'ngumo',
    'garden city': 'garden estate',
    'old runda': 'runda',
    'loresho ridge road': 'loresho',
    'komarock': 'koma rock',        # BRK spelling vs P24 "Koma Rock"

    # ── BRK road names → suburb ──
    # BRK locations are often street-level; these map to the suburb
    # the street is in, so the scorer can compare against P24's suburb.
    'rhapta road': 'westlands',         # Rhapta Road is in Westlands
    'ole dume road': 'kilimani',        # Ole Dume Rd is in Kilimani
    'njumbi road': 'lavington',         # Njumbi Road is in Lavington
    'chalbi drive': 'lavington',        # Chalbi Drive is in Lavington
    'james gichuru road': 'lavington',  # James Gichuru Rd runs through Lavington
    'convent drive': 'lavington',       # Convent Drive is in Lavington
    'kabarsiran avenue': 'lavington',   # Kabarsiran Ave is in Lavington
    'kabarsiran road': 'lavington',
    'muthangari road': 'muthangari',
    'muthangari drive': 'muthangari',
    'shanzu road': 'lavington',         # Shanzu Rd is in Lavington area
    'muhoho avenue': 'lavington',       # In Lavington
    'school lane': 'westlands',         # School Lane is in Westlands
    'waiyaki way': 'westlands',         # Waiyaki Way runs Westlands→Kangemi
    'langata road': 'langata',          # Map road to suburb
    'ridgeways road': 'ridgeways',      # Strip "Road" → suburb
    'garden estate road': 'garden estate',
    'naivasha road': 'riruta',          # Naivasha Rd runs through Riruta area
    'jogoo road': 'buruburu',           # Jogoo Rd runs through Buru Buru area
    'oloitokitok road': 'karen',        # In Karen area
    'karen ridge road': 'karen',
    'paradise lost road': 'kitisuru',   # Near Paradise Lost in Kitisuru area
    'ngecha road': 'lower kabete',      # Ngecha Rd runs through Lower Kabete
    'ruaka drive': 'ruaka',
    'kitengela road': 'katani',         # Towards Kitengela
    'kangudo road': 'ruai',             # Eastern bypass area
    'eastern bypass': 'ruai',           # Eastern side of Nairobi
    'st.mary road': 'lavington',        # St Mary's area in Lavington
    'st michaels road': 'westlands',    # In Westlands area
    'msanaka road': 'loresho',          # Msanaka area borders Loresho
    'msufi road': 'loresho',            # In Loresho area
    'mumwe drive': 'runda',             # Mumwe Drive is in Runda
    'naushad merali drive': 'runda',    # In Runda area

    # ── BRK compound/estate names → suburb ──
    'runda mimosa': 'runda',            # Estate within Runda
    'runda mumwe': 'runda',             # Estate within Runda
    'runda nairobi kenya': 'runda',     # BRK verbose location
    'gigiri runda': 'runda',            # Between Gigiri and Runda
    'runda grove': 'runda',             # After prefix strip: "Off Runda Grove"
    'runda road': 'runda',
    'kigwa ridge': 'runda',             # Kigwa Ridge is in Runda
    'lavington green': 'lavington',     # Estate within Lavington
    'lavington green.': 'lavington',    # Trailing period variant
    'kyuna rise': 'kyuna',              # Kyuna Rise is in Kyuna
    'loresho ridge': 'loresho',         # After prefix strip
    'brookside grove': 'westlands',     # Brookside is in Westlands
    'brookside garden': 'westlands',
    'westlands area': 'westlands',      # Redundant suffix
    'rosslyn lone tree estate road': 'rosslyn',  # After prefix strip
    'un avenue': 'gigiri',              # UN Avenue is in Gigiri (UN compound)
    'apple cross': 'karen',             # Apple Cross area in Karen
    'eden ville': 'ngong road',         # Edenville is off Ngong Road
    'baraka estate': 'ngong road',      # Along Ngong Road
    'jacaranda': 'kahawa',              # Jacaranda estate in Kahawa area
    'jacaranda avenue': 'kahawa',
    'jacaranda avenure': 'kahawa',      # BRK typo
    'northlands city': 'ruiru',         # Northlands is near Ruiru
    'fourways junction estate': 'kiambu road',  # Along Kiambu Road
    'lower kabate lane': 'lower kabete',  # BRK typo: "Kabate" → "Kabete"
    'near the westgate': 'westlands',   # After prefix strip leaves "the westgate"
    'the westgate': 'westlands',        # Westgate mall is in Westlands
    'near boma hotel': 'upper hill',    # Boma Hotel is on Bellevue, Upper Hill
    'boma hotel': 'upper hill',
    'westgate': 'westlands',            # After "Near The" strip → "westgate"
    'maziwa': 'ruaka',                  # Maziwa area near Ruaka
    'cianda kiambu': 'kiambu',          # Cianda is in greater Kiambu
    'easy access to westlands link road': 'westlands',  # Agent garbage, best guess
}


def normalize_suburb(raw: str) -> str:
    """Normalize a suburb name to its canonical form.

    Steps:
    1. Strip whitespace, lowercase, remove trailing punctuation
    2. Remove agent prefixes ("Off", "Near", "Along", etc.)
    3. Collapse road/street abbreviations ("Rd" → "Road")
    4. Map known aliases to canonical P24 name
    5. Fallback: if not in graph, check if a known suburb is a prefix
       (handles "Runda Mimosa" → "runda" if not explicitly aliased)
    """
    if not raw:
        return ''

    name = raw.strip().lower()

    # Strip trailing punctuation (BRK has "Lavington Green.")
    name = name.rstrip('.,;:!?')

    # Strip prefixes like "Off Kiambu Road" → "kiambu road"
    name = _STRIP_PREFIXES.sub('', name).strip()

    # Strip leading "the" (after prefix strip: "Near The Westgate" → "the westgate")
    if name.startswith('the '):
        name = name[4:]

    # Collapse road variants: "ngong rd" → "ngong road"
    for abbrev, full in _ROAD_VARIANTS.items():
        if name.endswith(abbrev):
            name = name[:-len(abbrev)] + full

    # Apply alias mapping
    name = _ALIASES.get(name, name)

    # Step 5 (was documented above but never implemented): if the name
    # isn't a known suburb, check whether one is its PREFIX — catches
    # street/estate-level locations like "Lavington Elmolo Drive" →
    # "lavington" without needing an alias per street. Longest suburb
    # first so "lower kabete ..." matches "lower kabete", not "kabete".
    if name not in ADJACENCY:
        for suburb in sorted(ADJACENCY, key=len, reverse=True):
            if name.startswith(suburb + " "):
                return suburb

    return name


# ── Step 2: Adjacency graph ────────────────────────────────────────────
#
# HOW IT WORKS: each suburb maps to a set of its direct neighbors.
# The graph is symmetric — if A is adjacent to B, B is adjacent to A.
# We define edges once and symmetrize with _build_adjacency().
#
# SCORING (in the scorer, not here):
#   same suburb           → 0.0  (perfect match)
#   adjacent suburbs      → 0.3  (nearby — plausible same property)
#   same sub-county       → 0.5  (same general area)
#   different / unknown   → 0.8  (penalize but don't veto — rule #8)
#
# WHY NOT 1.0 FOR UNKNOWN: standing rule #5 says no single field may veto
# a match. An unknown location pair could still be a real match (BRK might
# use a suburb name we haven't mapped yet). 0.8 penalizes but leaves room
# for strong image + price signals to override.

# Raw adjacency edges. Each tuple is (suburb_a, suburb_b).
# Suburbs use normalized (lowercase) canonical names.
_RAW_EDGES = [
    # ── Inner Premium Belt ──
    # These four form a connected cluster in central-west Nairobi.
    # Source: Wikipedia (Kilimani borders), neighborhood guides, sub-county data.
    ('kilimani', 'kileleshwa'),      # Separated by Kirichwa River
    ('kilimani', 'lavington'),       # Kilimani borders Lavington to the west
    ('kilimani', 'woodley'),         # Kilimani is north of Woodley
    ('kilimani', 'ngong road'),      # Ngong Rd runs along Kilimani's south edge
    ('kilimani', 'valley arcade'),   # Valley Arcade sits between Kilimani and Lavington
    ('kilimani', 'nairobi cbd'),     # Kilimani is ~5km west of CBD
    ('kileleshwa', 'lavington'),     # Adjacent — Kileleshwa between Lavington and Kilimani
    ('kileleshwa', 'westlands'),     # Kileleshwa connects to Westlands
    ('lavington', 'valley arcade'),  # Valley Arcade borders Lavington
    ('lavington', 'muthangari'),     # Muthangari is adjacent to Lavington
    ('lavington', 'jamhuri'),        # Jamhuri borders south Lavington
    ('lavington', 'kawangware'),     # Kawangware is west of Lavington

    # ── Westlands cluster ──
    # Westlands sub-county is a large area; many suburbs nest within it.
    ('westlands', 'parklands'),      # Parklands borders Westlands
    ('westlands', 'spring valley'),  # Spring Valley is the quieter part of Westlands
    ('westlands', 'riverside'),      # Riverside is in the Westlands area
    ('westlands', 'highridge'),      # Highridge borders Westlands
    ('parklands', 'highridge'),      # Adjacent
    ('parklands', 'pangani'),        # Pangani is east of Parklands
    ('spring valley', 'loresho'),    # Spring Valley connects to Loresho
    ('spring valley', 'riverside'),  # Adjacent

    # ── Northwest / Kitisuru cluster ──
    # P24 address hierarchy confirms: Lower Kabete, Runda, Rosslyn all
    # show "Kitisuru" as parent area in address format.
    ('kitisuru', 'loresho'),         # Adjacent — both in northwest Nairobi
    ('kitisuru', 'runda'),           # P24 hierarchy: Runda nests under Kitisuru
    ('kitisuru', 'lower kabete'),    # P24 hierarchy: Lower Kabete under Kitisuru
    ('kitisuru', 'nyari'),           # Nyari is part of the Kitisuru area
    ('kitisuru', 'kyuna'),           # Kyuna borders Kitisuru
    ('kitisuru', 'rosslyn'),         # P24 hierarchy: Rosslyn under Kitisuru
    ('runda', 'gigiri'),             # Adjacent — both diplomatic/luxury area
    ('runda', 'rosslyn'),            # Rosslyn borders Runda
    ('runda', 'redhill'),            # Redhill is north of Runda
    ('runda', 'ruaka'),              # Ruaka borders Runda to the north
    ('gigiri', 'muthaiga'),          # Adjacent — both embassy area
    ('gigiri', 'peponi'),            # Peponi Road area borders Gigiri
    ('muthaiga', 'peponi'),          # Peponi is the Muthaiga/Parklands border area
    ('muthaiga', 'ridgeways'),       # Ridgeways borders Muthaiga
    ('loresho', 'mountain view'),    # Mountain View borders Loresho
    ('loresho', 'kangemi'),          # Kangemi is south of Loresho
    ('loresho', 'westlands'),        # Loresho connects to Westlands
    ('lower kabete', 'kangemi'),     # Adjacent
    ('ruaka', 'kiambu road'),        # Ruaka is along Kiambu Road
    ('ruaka', 'redhill'),            # Adjacent
    ('kiambu road', 'ridgeways'),    # Kiambu Road area connects to Ridgeways
    ('kiambu road', 'mucatha'),      # Mucatha is along Kiambu Road
    ('nyari', 'kyuna'),              # Adjacent within the Kitisuru area

    # ── Karen / Langata cluster ──
    # Source: P24 address groupings, neighborhood guides
    ('karen', 'langata'),            # Classic pairing — Karen is south of Langata
    ('karen', 'hardy'),              # Hardy is within the greater Karen area
    ('karen', 'kerarapon'),          # Kerarapon borders Karen to the south
    ('karen', 'ngong road'),         # Karen connects via Ngong Rd
    ('karen', 'dagoretti'),          # Dagoretti borders Karen
    ('karen', 'riruta'),             # Riruta is between Karen and Kawangware
    ('langata', 'nairobi west'),     # Nairobi West is north of Langata
    ('langata', 'mugumoini'),        # Mugumoini is in Langata sub-county
    ('langata', 'mbagathi way'),     # Mbagathi Way area borders Langata
    ('langata', 'kibera'),           # Kibera borders Langata
    ('langata', 'woodley'),          # Woodley connects to Langata
    ('langata', 'south c'),          # ← THIS WAS MISSING — caused false positives

    # ── South / Southeast cluster ──
    ('south b', 'south c'),          # Adjacent — classic pairing
    ('south c', 'nairobi west'),     # South C borders Nairobi West
    ('south c', 'syokimau'),         # ← THIS WAS MISSING — caused false positives
    ('south c', 'mombasa road'),     # South C is along Mombasa Road
    ('south b', 'mombasa road'),     # South B is near Mombasa Road
    ('syokimau', 'mlolongo'),        # Adjacent along Mombasa Road corridor
    ('syokimau', 'katani'),          # Katani borders Syokimau
    ('syokimau', 'mombasa road'),    # Syokimau is off Mombasa Road
    ('imara daima', 'mombasa road'), # Imara Daima is along Mombasa Road
    ('imara daima', 'pipeline'),     # Pipeline borders Imara Daima
    ('embakasi', 'pipeline'),        # Adjacent
    ('embakasi', 'utawala'),         # Utawala borders Embakasi

    # ── North / Thika Road corridor ──
    ('garden estate', 'thome'),      # Adjacent — both off Thika Road
    ('garden estate', 'ridgeways'),   # Ridgeways borders Garden Estate
    ('garden estate', 'mirema'),     # Mirema is near Garden Estate
    ('ridgeways', 'muthaiga'),       # Ridgeways borders Muthaiga
    ('thome', 'kasarani'),           # Thome connects to Kasarani
    ('kasarani', 'roysambu'),        # Adjacent along Thika Road
    ('roysambu', 'mirema'),          # Mirema is in Roysambu area
    ('roysambu', 'zimmerman'),       # Zimmerman is in Roysambu area
    ('kahawa', 'mwihoko'),           # Adjacent

    # ── Central / CBD area ──
    ('nairobi cbd', 'upper hill'),   # Upper Hill is south of CBD
    ('nairobi cbd', 'ngara'),        # Ngara is north of CBD
    ('nairobi cbd', 'pangani'),      # Pangani is northeast of CBD
    ('nairobi cbd', 'eastleigh'),    # Eastleigh is east of CBD
    ('upper hill', 'nairobi west'),  # Adjacent
    ('ngara', 'parklands'),          # Ngara connects to Parklands

    # ── Eastern suburbs ──
    ('donholm', 'buruburu'),         # Adjacent
    ('donholm', 'savannah'),         # Adjacent
    ('buruburu', 'umoja'),           # Adjacent
    ('kayole', 'umoja'),             # Adjacent
    ('koma rock', 'kayole'),         # Adjacent

    # ── Misc connections that matter for dedup ──
    ('milimani', 'upper hill'),      # Milimani borders Upper Hill
    ('milimani', 'nairobi cbd'),     # Milimani is edge of CBD
    ('lenana', 'kilimani'),          # Lenana area borders Kilimani
    ('lenana', 'lavington'),         # And Lavington
    ('hillview', 'loresho'),         # Hillview area borders Loresho
    ('waithaka', 'dagoretti'),       # Adjacent
    ('uthiru', 'kangemi'),           # Adjacent
    ('balozi', 'muthaiga'),          # Balozi estate borders Muthaiga
    ('thigiri', 'gigiri'),           # Thigiri Ridge borders Gigiri
    ('thigiri', 'kitisuru'),         # And Kitisuru
    ('savannah', 'lakeview'),        # Adjacent

    # ── Road corridors (agents say "Ngong Rd" meaning anywhere along it) ──
    ('ngong road', 'woodley'),       # Ngong Rd passes through Woodley
    ('ngong road', 'dagoretti'),     # Ngong Rd continues through Dagoretti
    ('ngong road', 'lavington'),     # Ngong Rd borders Lavington

    # ── Runda ↔ Karen (separate clusters, NOT adjacent) ──
    # NOTE: these are deliberately NOT connected. They're ~15km apart.
    # The session summary flagged Karen↔Runda false positives — the fix
    # is ensuring they DON'T share an edge, not adding one.
    # Similar modern houses in different areas should score as distinct.
]


def _build_adjacency():
    """Build symmetric adjacency dict from raw edge list.

    Returns a dict where each suburb maps to a frozenset of its neighbors.
    Symmetric: if A→B exists, B→A is guaranteed.
    """
    adj = {}
    for a, b in _RAW_EDGES:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    # Freeze sets for safety (no accidental mutation)
    return {k: frozenset(v) for k, v in adj.items()}


ADJACENCY = _build_adjacency()


def location_dissimilarity(loc_a: str, loc_b: str) -> float:
    """Score how far apart two suburb names are for dedup purposes.

    Returns:
        0.0  — same suburb (after normalization)
        0.3  — adjacent suburbs (share a physical border)
        0.5  — both known but not adjacent (same city, far apart)
        0.8  — one or both unknown (unmapped suburb name)

    WHY THESE VALUES:
    - 0.0: identical location is strong evidence of same property
    - 0.3: adjacent is common for real duplicates (same house listed under
      two overlapping suburb names, e.g. "Lavington" vs "Kilimani")
    - 0.5: same city but far apart — weak negative signal, not a veto
    - 0.8: unknown — penalize but leave room for image/price signals
      to override (standing rule #5: no single field vetoes)
    """
    norm_a = normalize_suburb(loc_a)
    norm_b = normalize_suburb(loc_b)

    if not norm_a or not norm_b:
        return 0.8  # Missing data — unknown

    if norm_a == norm_b:
        return 0.0

    # Check adjacency (symmetric — only need one direction)
    neighbors_a = ADJACENCY.get(norm_a, frozenset())
    if norm_b in neighbors_a:
        return 0.3

    # Both known suburbs but not adjacent
    if norm_a in ADJACENCY and norm_b in ADJACENCY:
        return 0.5

    # One or both not in the graph at all
    return 0.8


# ── Quick sanity checks ────────────────────────────────────────────────
if __name__ == '__main__':
    # These match the confirmed false positives from pipeline validation
    # that were caused by adjacency map gaps.

    print("=== Normalization tests ===")
    tests = [
        # Basic normalization
        ("Off Kiambu Road", "kiambu road"),
        ("KILIMANI", "kilimani"),
        ("Ngong Rd", "ngong road"),
        ("Hurlingham", "kilimani"),
        ("  lavington  ", "lavington"),
        ("Near Runda", "runda"),
        # BRK-specific: trailing punctuation
        ("Lavington Green.", "lavington"),
        # BRK-specific: road names → suburb
        ("Ole Dume Rd", "kilimani"),
        ("Chalbi Drive", "lavington"),
        ("Off James Gichuru Rd", "lavington"),
        ("Rhapta Road", "westlands"),
        ("Muthangari Dr", "muthangari"),
        ("Langata Road", "langata"),
        # BRK-specific: estate/compound names
        ("Runda Mimosa", "runda"),
        ("Runda Mumwe", "runda"),
        ("Gigiri Runda", "runda"),
        ("Old Muthaiga", "muthaiga"),
        ("Kyuna Rise", "kyuna"),
        ("Off Loresho Ridge", "loresho"),
        # BRK-specific: weird names
        ("Near The Westgate", "westlands"),
        ("Westlands Area", "westlands"),
        ("Off Un Avenue", "gigiri"),
        ("Jacaranda Avenure", "kahawa"),  # Typo in BRK data
        ("Runda Nairobi Kenya", "runda"),
        ("Komarock", "koma rock"),
        ("Lower Kabate Ln", "lower kabete"),
        # Garbage — should return something, won't match anything
        ("-00200", "-00200"),
    ]
    for raw, expected in tests:
        result = normalize_suburb(raw)
        status = "✓" if result == expected else f"✗ (got '{result}')"
        print(f"  '{raw}' → '{result}' {status}")

    print("\n=== Adjacency tests ===")
    pairs = [
        ("Kilimani", "Lavington", 0.3),     # Adjacent
        ("Kilimani", "Kilimani", 0.0),       # Same
        ("Karen", "Runda", 0.5),             # Known but NOT adjacent (was false positive)
        ("South C", "Syokimau", 0.3),        # Adjacent (was missing, caused false positive)
        ("Off Kiambu Road", "Ridgeways", 0.3),  # After normalization, adjacent
        ("Kilimani", "Banana", 0.8),         # Unknown suburb
        # BRK vs P24 cross-source pairs
        ("Ole Dume Rd", "Kilimani", 0.0),    # Ole Dume normalizes to kilimani
        ("Chalbi Drive", "Kilimani", 0.3),   # Chalbi→lavington, adjacent to kilimani
        ("Runda Mimosa", "Gigiri", 0.3),     # Runda Mimosa→runda, adjacent to gigiri
        ("Off Un Avenue", "Runda", 0.3),     # UN Ave→gigiri, adjacent to runda
        ("Old Muthaiga", "Gigiri", 0.3),     # Old Muthaiga→muthaiga, adjacent to gigiri
    ]
    for a, b, expected in pairs:
        result = location_dissimilarity(a, b)
        status = "✓" if result == expected else f"✗ (got {result})"
        print(f"  '{a}' vs '{b}' → {result} {status}")

    print(f"\n=== Graph stats ===")
    print(f"  Suburbs in graph: {len(ADJACENCY)}")
    print(f"  Total edges: {sum(len(v) for v in ADJACENCY.values()) // 2}")
