"""
Event Brand Scanner
Seeds brands.json with verified indie brands from fragrance events.
Run manually after each major event (FUMED, ScentFest, Esxence, etc.)

Usage:
  python event_scan.py --event fumed_2026
  python event_scan.py --event scentfest_2026
  python event_scan.py --event custom --brands "Brand A, Brand B, Brand C"
"""

import os
import json
import datetime
import argparse
from pathlib import Path

import anthropic

# ── Config ────────────────────────────────────────────────────────────────────

DATA_FILE         = "data/brands.json"
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# ── Known event brand lists ───────────────────────────────────────────────────

EVENTS = {
    "fumed_2026": {
        "name": "FUMED Indie Perfume Expo 2026",
        "date": "2026-03",
        "location": "Chicago, IL",
        "brands": [
            "86 West",
            "American Perfumer",
            "Amphora Parfum",
            "Atwood Parfum",
            "Bañomaria",
            "Blackcliff",
            "Cirrus Parfum",
            "CLST Perfumes",
            "Cult of Kaori",
            "Domestica",
            "Elysian",
            "Faeral Parfum",
            "Fleurit",
            "Free Yourself",
            "Hez Parfums",
            "House of Iyrah",
            "House of Mammoth",
            "IDA",
            "Jean Robert",
            "Kelly+Jones",
            "Koromas Aromas",
            "L'Aventura Perfumes",
            "LEIƏR",
            "Maher Olfactive",
            "Maya Njie",
            "Mitti",
            "MOCO Fragrances",
            "Nose of Gatsby",
            "Nostos",
            "OSM",
            "Paraphrase Perfume",
            "Parfums Obim",
            "Pearfat Parfum",
            "Pictura Fragrans",
            "Perfume Who",
            "Punk and Dandy",
            "Puppet Parfum",
            "Qhue New York",
            "Scent Trunk",
            "Syd Botanica",
            "The Alloy Studio",
        ]
    },

    "scentfest_2026": {
        "name": "ScentFest SF 2026",
        "date": "2026-06",
        "location": "San Francisco, CA",
        "brands": [
            # Add brands here once the list is published (June 26-28, 2026)
            # Check: https://scentfestsf.com/exhibitors
        ]
    },
}


# ── Verify and enrich brands via Claude ──────────────────────────────────────

def verify_brands(brand_names: list[str], event_name: str) -> list[dict]:
    """
    Ask Claude to verify and add metadata for a list of event brands.
    Event brands get a lower bar for verification since their presence
    at a curated indie event is itself a quality signal.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    batch_size = 15
    batches = [brand_names[i:i+batch_size] for i in range(0, len(brand_names), batch_size)]
    brand_meta = {}

    for i, batch in enumerate(batches):
        print(f"  Verifying batch {i+1}/{len(batches)}...")
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""You are an expert on niche and indie fragrance brands.

These brands were exhibitors at {event_name}, a curated indie fragrance event.
Their presence there confirms they are real fragrance brands — focus on adding metadata.

For each brand, provide:
- country of origin (best guess based on name/known info, or "unknown")
- approximate founding year (or "unknown")
- price tier: "indie" (under $80/50ml), "accessible niche" ($80-200), "luxury niche" ($200+), or "unknown"
- confidence: "high", "medium", or "low"

Return ONLY a JSON object (no markdown):
{{
  "BrandName": {{
    "real": true,
    "country": "USA",
    "founded": 2019,
    "price_tier": "indie",
    "confidence": "medium"
  }}
}}

BRANDS:
{json.dumps(batch)}"""
                }]
            )
            response_text = message.content[0].text.strip()
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            meta = json.loads(response_text)
            brand_meta.update(meta)
        except Exception as e:
            print(f"  Warning: verification error on batch {i+1}: {e}")

    enriched = []
    for name in brand_names:
        meta = brand_meta.get(name, {})
        enriched.append({
            "name": name,
            "mentions": 1,
            "sentiment": "neutral",
            "context": f"Exhibitor at {event_name}",
            "source": "event",
            "country": meta.get("country", "unknown"),
            "founded": meta.get("founded", "unknown"),
            "price_tier": meta.get("price_tier", "indie"),
            "confidence": meta.get("confidence", "low"),
        })

    return enriched


# ── Seed history ──────────────────────────────────────────────────────────────

def load_history() -> dict:
    path = Path(DATA_FILE)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_history(history: dict):
    Path(DATA_FILE).parent.mkdir(exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(history, f, indent=2)


def seed_event_brands(brands: list[dict], event_name: str, event_date: str):
    """
    Add event brands to history with a special event week tag.
    Uses the event date as the week key so they don't interfere
    with the current weekly run's baseline calculations.
    """
    history = load_history()

    # Use the event month as the week key (e.g. 2026-W12 for March 2026)
    # This anchors them in time without inflating current-week counts
    event_dt = datetime.datetime.strptime(event_date, "%Y-%m")
    week_key = event_dt.strftime("%Y-W%W")

    added = 0
    updated = 0

    for brand in brands:
        name = brand["name"]
        if name not in history:
            history[name] = {
                "weeks": {},
                "context": brand["context"],
                "source": brand["source"],
                "country": brand["country"],
                "founded": brand["founded"],
                "price_tier": brand["price_tier"],
                "events": [event_name],
            }
            added += 1
        else:
            # Brand already exists — add event tag, update metadata if better
            if "events" not in history[name]:
                history[name]["events"] = []
            if event_name not in history[name]["events"]:
                history[name]["events"].append(event_name)
            for field in ["country", "founded", "price_tier"]:
                if brand.get(field) and brand[field] != "unknown":
                    history[name][field] = brand[field]
            updated += 1

        # Seed with 1 mention at event week — low enough not to skew baselines
        if week_key not in history[name]["weeks"]:
            history[name]["weeks"][week_key] = 1

    save_history(history)
    print(f"  Added {added} new brands, updated {updated} existing brands.")
    print(f"  History now tracks {len(history)} total brands.")
    return history


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed brands from fragrance events")
    parser.add_argument("--event", required=True,
                        help="Event key (e.g. fumed_2026) or 'custom'")
    parser.add_argument("--brands", default="",
                        help="Comma-separated brand names (for --event custom)")
    args = parser.parse_args()

    if args.event == "custom":
        if not args.brands:
            print("Error: --brands required when using --event custom")
            return
        brand_names = [b.strip() for b in args.brands.split(",") if b.strip()]
        event_info = {
            "name": "Custom Event",
            "date": datetime.date.today().strftime("%Y-%m"),
            "brands": brand_names,
        }
    elif args.event in EVENTS:
        event_info = EVENTS[args.event]
        brand_names = event_info["brands"]
    else:
        print(f"Unknown event '{args.event}'. Available: {', '.join(EVENTS.keys())}")
        return

    if not brand_names:
        print(f"No brands listed for {args.event} yet. Add them to the EVENTS dict.")
        return

    print(f"── Event: {event_info['name']}")
    print(f"── Brands to process: {len(brand_names)}")

    print("── Step 1: Verifying brands + adding metadata...")
    enriched = verify_brands(brand_names, event_info["name"])

    print("── Step 2: Seeding into brands.json...")
    seed_event_brands(enriched, event_info["name"], event_info["date"])

    print(f"── Done ✓ — {len(enriched)} brands from {event_info['name']} are now in your tracker.")
    print("   They will appear in momentum reports as soon as they gain traction in the wild.")


if __name__ == "__main__":
    main()
