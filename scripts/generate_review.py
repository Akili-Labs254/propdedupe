"""
Generate an HTML report of all scored cross-source candidate pairs
for visual side-by-side verification.
"""
from typing import Dict, List
from backend.db.client import get_client
from backend.dedup.scorer import score_pair
from scripts.cross_source_match import fetch_distinct_hashes, find_cross_matches


def fetch_listing_details(db, ids: List[int]) -> Dict[int, dict]:
    details = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        result = (
            db.table("listings")
            .select("id, source_id, title, location, price, bedrooms, baths, size_sqm, image_url, url")
            .in_("id", chunk)
            .execute()
        )
        for row in result.data:
            details[row["id"]] = row
    return details


def fmt_price(p):
    if p is None:
        return "N/A"
    return f"KSh {int(float(p)):,}"


def build_card(row):
    source = "BRK" if row.get("source_id") == 1 else "P24"
    url = row.get("url") or "#"
    return f"""
    <div class="listing">
      <div class="source-badge {'brk' if source == 'BRK' else 'p24'}">{source}</div>
      <img src="{row.get('image_url', '')}" alt="listing photo"
           onerror="this.src=''; this.alt='Image unavailable'">
      <div class="details">
        <strong>{row.get('title', 'N/A')}</strong><br>
        📍 {row.get('location', 'N/A')}<br>
        💰 {fmt_price(row.get('price'))}<br>
        🛏 {row.get('bedrooms', '?')} bed &nbsp; 🚿 {row.get('baths', '?')} bath<br>
        📐 {row.get('size_sqm') or '?'} m²<br>
        <a href="{url}" target="_blank">View listing ↗</a>
      </div>
    </div>"""


def main():
    db = get_client()

    print("Fetching hashes...")
    brk_hashes = fetch_distinct_hashes(db, 1)
    p24_hashes = fetch_distinct_hashes(db, 2)

    print("Finding candidates...")
    matches = find_cross_matches(brk_hashes, p24_hashes, threshold=8)

    all_ids = set()
    for _, _, _, brk_ids, p24_ids in matches:
        all_ids.update(brk_ids)
        all_ids.update(p24_ids)

    print(f"Fetching details for {len(all_ids)} listings...")
    details = fetch_listing_details(db, list(all_ids))

    results = []
    for brk_hash, p24_hash, dist, brk_ids, p24_ids in matches:
        brk_row = details.get(brk_ids[0], {})
        p24_row = details.get(p24_ids[0], {})

        total, tier, signals = score_pair(
            phash_distance=dist,
            price_a=float(brk_row["price"]) if brk_row.get("price") else None,
            price_b=float(p24_row["price"]) if p24_row.get("price") else None,
            beds_a=brk_row.get("bedrooms"),
            beds_b=p24_row.get("bedrooms"),
            baths_a=float(brk_row["baths"]) if brk_row.get("baths") else None,
            baths_b=float(p24_row["baths"]) if p24_row.get("baths") else None,
            location_a=brk_row.get("location"),
            location_b=p24_row.get("location"),
        )
        results.append({
            "brk_ids": brk_ids,
            "p24_ids": p24_ids,
            "brk_row": brk_row,
            "p24_row": p24_row,
            "dist": dist,
            "total": total,
            "tier": tier,
            "signals": signals,
        })

    results.sort(key=lambda r: r["total"])

    # Build HTML
    cards_html = ""
    for r in results:
        sig = r["signals"]
        signal_bars = ""
        for name, val in sig.items():
            pct = int(val * 100)
            color = "#4caf50" if val < 0.3 else "#ff9800" if val < 0.6 else "#f44336"
            signal_bars += f"""
            <div class="signal-row">
              <span class="signal-name">{name}</span>
              <div class="signal-bar-bg">
                <div class="signal-bar" style="width:{pct}%; background:{color}"></div>
              </div>
              <span class="signal-val">{val:.2f}</span>
            </div>"""

        tier_class = r["tier"].replace(" ", "-")
        cards_html += f"""
    <div class="pair-card {tier_class}">
      <div class="pair-header">
        <span class="tier-badge {tier_class}">{r['tier'].upper()}</span>
        <span class="score">Score: {r['total']:.3f}</span>
        <span class="dist">Image dist: {r['dist']}</span>
        <span class="ids">BRK {r['brk_ids']} ↔ P24 {r['p24_ids']}</span>
      </div>
      <div class="pair-body">
        {build_card(r['brk_row'])}
        <div class="vs">VS</div>
        {build_card(r['p24_row'])}
      </div>
      <div class="signals">
        <strong>Signal breakdown:</strong>
        {signal_bars}
      </div>
    </div>"""

    counts = {"auto-merge": 0, "review": 0, "distinct": 0}
    for r in results:
        counts[r["tier"]] += 1

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PropDeDupe — Cross-Source Match Review</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; padding: 20px; }}
  h1 {{ margin-bottom: 10px; }}
  .summary {{ margin-bottom: 20px; font-size: 14px; color: #666; }}
  .summary span {{ font-weight: bold; padding: 2px 8px; border-radius: 4px; margin-right: 8px; }}
  .summary .auto-merge {{ background: #e8f5e9; color: #2e7d32; }}
  .summary .review {{ background: #fff3e0; color: #ef6c00; }}
  .summary .distinct {{ background: #ffebee; color: #c62828; }}
  .pair-card {{ background: white; border-radius: 8px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.12); overflow: hidden; }}
  .pair-card.auto-merge {{ border-left: 4px solid #4caf50; }}
  .pair-card.review {{ border-left: 4px solid #ff9800; }}
  .pair-card.distinct {{ border-left: 4px solid #f44336; }}
  .pair-header {{ padding: 10px 16px; background: #fafafa; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; font-size: 13px; }}
  .tier-badge {{ padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; }}
  .tier-badge.auto-merge {{ background: #e8f5e9; color: #2e7d32; }}
  .tier-badge.review {{ background: #fff3e0; color: #ef6c00; }}
  .tier-badge.distinct {{ background: #ffebee; color: #c62828; }}
  .score {{ font-weight: bold; }}
  .ids {{ color: #999; font-size: 11px; }}
  .pair-body {{ display: flex; align-items: stretch; padding: 16px; gap: 8px; }}
  .listing {{ flex: 1; }}
  .listing img {{ width: 100%; height: 200px; object-fit: cover; border-radius: 4px; }}
  .listing .details {{ padding: 8px 0; font-size: 13px; line-height: 1.6; }}
  .source-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-bottom: 4px; }}
  .source-badge.brk {{ background: #e3f2fd; color: #1565c0; }}
  .source-badge.p24 {{ background: #f3e5f5; color: #7b1fa2; }}
  .vs {{ display: flex; align-items: center; font-weight: bold; color: #999; padding: 0 8px; }}
  .signals {{ padding: 8px 16px 12px; border-top: 1px solid #eee; }}
  .signal-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
  .signal-name {{ width: 80px; font-size: 12px; color: #666; }}
  .signal-bar-bg {{ flex: 1; height: 8px; background: #eee; border-radius: 4px; }}
  .signal-bar {{ height: 100%; border-radius: 4px; }}
  .signal-val {{ width: 35px; font-size: 11px; color: #999; text-align: right; }}
  a {{ color: #1976d2; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <h1>PropDeDupe — Cross-Source Match Review</h1>
  <div class="summary">
    <span class="auto-merge">{counts['auto-merge']} auto-merge</span>
    <span class="review">{counts['review']} review</span>
    <span class="distinct">{counts['distinct']} distinct</span>
    — {len(results)} total candidate pairs
  </div>
  {cards_html}
</body>
</html>"""

    with open("data/review.html", "w") as f:
        f.write(html)

    print(f"Report saved to data/review.html")
    print(f"  {counts['auto-merge']} auto-merge, {counts['review']} review, {counts['distinct']} distinct")


if __name__ == "__main__":
    main()