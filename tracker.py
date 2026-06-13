"""
Fragrance Brand Momentum Tracker
Sources: Fragrantica + TikTok hashtags + Fragrance newsletters + Google Trends
Features: Brand verification with metadata, Notion database sync, email reports
Runs weekly via GitHub Actions.
"""
 
import os
import json
import datetime
import smtplib
import time
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
 
import requests
from bs4 import BeautifulSoup
from pytrends.request import TrendReq
import anthropic
 
# ── Config ────────────────────────────────────────────────────────────────────
 
DATA_FILE   = "data/brands.json"
REPORT_FILE = "data/latest_report.md"
 
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
EMAIL_SENDER       = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD     = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT    = os.environ.get("EMAIL_RECIPIENT", "")
NOTION_TOKEN       = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
 
# Fragrantica pages
FRAGRANTICA_URLS = [
    "https://www.fragrantica.com/news/",
    "https://www.fragrantica.com/news/niche-perfumery/",
    "https://www.fragrantica.com/niche-perfume/",
    "https://www.fragrantica.com/community/",
    "https://www.fragrantica.com/board/viewforum.php?id=2",
    "https://www.fragrantica.com/board/viewforum.php?id=6",
]
 
# Fragrance newsletters & blogs (publicly accessible)
NEWSLETTER_URLS = [
    "https://cafleurebon.com/",
    "https://www.fragrantica.com/news/interviews/",
    "https://perfumeshrine.blogspot.com/",
    "https://www.nowsmellthis.com/",
    "https://luckyscent.com/blog",
]
 
# TikTok hashtag pages (public, no login required)
TIKTOK_URLS = [
    "https://www.tiktok.com/tag/fragrancetok",
    "https://www.tiktok.com/tag/nichefragrance",
    "https://www.tiktok.com/tag/indieperfume",
    "https://www.tiktok.com/tag/perfumetok",
]
 
ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "")
 
# Etsy API search terms for finding indie perfume shops
ETSY_SEARCH_TERMS = [
    "indie perfume",
    "artisan perfume",
    "natural perfume house",
    "niche fragrance",
]
 
# Google Trends seed terms
TREND_SEED_TERMS = [
    "indie perfume house",
    "Asian perfume house",
    "natural perfume brand",
    "niche perfume sample",
]
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
 
 
# ── Step 1a: Scrape Fragrantica ───────────────────────────────────────────────
 
def scrape_fragrantica() -> str:
    all_text = []
    for url in FRAGRANTICA_URLS:
        print(f"  Fetching {url}...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            for element in soup.find_all(["h1", "h2", "h3", "p", "a", "li"]):
                text = element.get_text(separator=" ", strip=True)
                if len(text) > 20:
                    all_text.append(text)
            time.sleep(random.uniform(2, 4))
        except requests.RequestException as e:
            print(f"  Warning: could not fetch {url}: {e}")
 
    try:
        print("  Fetching new perfumes listing...")
        resp = requests.get(
            "https://www.fragrantica.com/search/?word=&categories[]=new",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        for element in soup.find_all(["h1", "h2", "h3", "p", "span", "a"]):
            text = element.get_text(strip=True)
            if len(text) > 10:
                all_text.append(text)
        time.sleep(random.uniform(2, 4))
    except requests.RequestException as e:
        print(f"  Warning: could not fetch new perfumes: {e}")
 
    combined = "\n".join(all_text)
    print(f"  Collected {len(combined):,} chars from Fragrantica.")
    return combined
 
 
# ── Step 1b: Scrape newsletters & blogs ──────────────────────────────────────
 
def scrape_newsletters() -> str:
    all_text = []
    for url in NEWSLETTER_URLS:
        print(f"  Fetching newsletter {url}...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            for element in soup.find_all(["h1", "h2", "h3", "p", "a"]):
                text = element.get_text(separator=" ", strip=True)
                if len(text) > 20:
                    all_text.append(text)
            time.sleep(random.uniform(2, 4))
        except requests.RequestException as e:
            print(f"  Warning: could not fetch {url}: {e}")
 
    combined = "\n".join(all_text)
    print(f"  Collected {len(combined):,} chars from newsletters.")
    return combined
 
 
# ── Step 1c: Scrape TikTok hashtag pages ─────────────────────────────────────
 
def scrape_tiktok() -> str:
    """
    Scrape TikTok hashtag pages for brand mentions in video titles/descriptions.
    TikTok's public tag pages render some content without login.
    """
    all_text = []
    tiktok_headers = {
        **HEADERS,
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    }
 
    for url in TIKTOK_URLS:
        print(f"  Fetching TikTok {url}...")
        try:
            resp = requests.get(url, headers=tiktok_headers, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
 
            # TikTok embeds data in script tags as JSON
            for script in soup.find_all("script", type="application/json"):
                try:
                    data = json.loads(script.string or "")
                    text = json.dumps(data)
                    # Extract readable brand-mention fragments
                    if any(kw in text.lower() for kw in ["fragrance", "perfume", "scent", "parfum"]):
                        all_text.append(text[:3000])
                except (json.JSONDecodeError, TypeError):
                    pass
 
            # Also grab any visible text
            for element in soup.find_all(["h1", "h2", "p", "span"]):
                text = element.get_text(strip=True)
                if len(text) > 15:
                    all_text.append(text)
 
            time.sleep(random.uniform(3, 6))
        except requests.RequestException as e:
            print(f"  Warning: could not fetch TikTok {url}: {e}")
 
    combined = "\n".join(all_text)
    print(f"  Collected {len(combined):,} chars from TikTok.")
    return combined
 
 
# ── Step 1d: Etsy API ────────────────────────────────────────────────────────
 
def scrape_etsy() -> str:
    """
    Use Etsy Open API v3 to find new indie perfume listings and shop names.
    Sorted by creation date to surface the newest shops first.
    Falls back gracefully if the API key is not yet active.
    """
    if not ETSY_API_KEY:
        print("  Etsy API key not configured — skipping.")
        return ""
 
    all_text = []
    shop_names = set()
 
    api_headers = {
        "x-api-key": ETSY_API_KEY,
        "Accept": "application/json",
    }
 
    base_url = "https://openapi.etsy.com/v3/application"
 
    for term in ETSY_SEARCH_TERMS:
        print(f"  Etsy API search: '{term}'...")
        try:
            # Search active listings, sorted by creation date (newest first)
            params = {
                "keywords": term,
                "limit": 50,
                "sort_on": "created",
                "sort_order": "desc",
                "taxonomy_id": 1223,  # Etsy taxonomy ID for Fragrance
            }
            resp = requests.get(
                f"{base_url}/listings/active",
                headers=api_headers,
                params=params,
                timeout=15,
            )
 
            if resp.status_code == 401:
                print("  Etsy API key pending approval — skipping Etsy this run.")
                return ""
 
            resp.raise_for_status()
            data = resp.json()
            listings = data.get("results", [])
 
            for listing in listings:
                # Collect shop IDs to fetch shop names
                shop_id = listing.get("shop_id")
                title = listing.get("title", "")
                description = (listing.get("description") or "")[:300]
 
                if title:
                    all_text.append(f"ETSY LISTING: {title}")
                if description:
                    all_text.append(description)
 
                # Fetch shop name for each unique shop
                if shop_id and shop_id not in shop_names:
                    try:
                        shop_resp = requests.get(
                            f"{base_url}/shops/{shop_id}",
                            headers=api_headers,
                            timeout=10,
                        )
                        if shop_resp.status_code == 200:
                            shop_data = shop_resp.json()
                            shop_name = shop_data.get("shop_name", "")
                            if shop_name:
                                shop_names.add(shop_id)
                                all_text.append(f"ETSY SHOP: {shop_name}")
                        time.sleep(0.3)  # gentle rate limiting
                    except requests.RequestException:
                        pass
 
            print(f"  Found {len(listings)} listings, {len(shop_names)} unique shops so far.")
            time.sleep(random.uniform(2, 4))
 
        except requests.RequestException as e:
            print(f"  Warning: Etsy API error for '{term}': {e}")
 
    combined = "\n".join(all_text)
    print(f"  Collected {len(combined):,} chars from Etsy ({combined.count('ETSY SHOP:')} shop mentions).")
    return combined
 
 
# ── Step 1e: Google Trends ────────────────────────────────────────────────────
 
def get_trending_brands() -> list[dict]:
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    trending = {}
 
    for term in TREND_SEED_TERMS:
        print(f"  Google Trends: '{term}'...")
        success = False
        for attempt in range(3):  # retry up to 3 times
            try:
                time.sleep(random.uniform(15, 25))  # longer delay to avoid 429
                pytrends.build_payload([term], timeframe="now 7-d", geo="")
                related = pytrends.related_queries()
                if related and term in related:
                    rising_df = related[term].get("rising")
                    if rising_df is not None and not rising_df.empty:
                        for _, row in rising_df.iterrows():
                            query = str(row["query"]).strip()
                            value = int(row["value"])
                            if (len(query) > 3
                                    and "perfume" not in query.lower()
                                    and "fragrance" not in query.lower()
                                    and "how" not in query.lower()
                                    and "what" not in query.lower()):
                                if query not in trending or trending[query] < value:
                                    trending[query] = value
                success = True
                break
            except Exception as e:
                wait = 30 * (attempt + 1)
                print(f"  Warning: Google Trends error (attempt {attempt+1}): {e} — waiting {wait}s")
                time.sleep(wait)
        if not success:
            print(f"  Skipping '{term}' after 3 failed attempts.")
 
    result = [{"name": k, "trend_score": v} for k, v in trending.items()]
    result.sort(key=lambda x: x["trend_score"], reverse=True)
    print(f"  Found {len(result)} rising trend queries.")
    return result
 
 
# ── Step 2: Extract brand names via Claude ────────────────────────────────────
 
def extract_brands_from_text(raw_text: str, trend_hints: list[dict]) -> list[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    chunk_size = 12000
    chunks = [raw_text[i:i+chunk_size] for i in range(0, len(raw_text), chunk_size)]
    all_brands = {}
 
    trend_hint_text = ""
    if trend_hints:
        top_hints = [t["name"] for t in trend_hints[:20]]
        trend_hint_text = (
            f"\n\nADDITIONAL CONTEXT — rising Google Trends queries that may be brands: "
            f"{', '.join(top_hints)}"
        )
 
    for i, chunk in enumerate(chunks):
        print(f"  Extracting brands from chunk {i+1}/{len(chunks)}...")
        extra = trend_hint_text if i == 0 else ""
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                messages=[{
                    "role": "user",
                    "content": f"""You are an expert in the niche and indie fragrance world.
 
Extract every niche, indie, or artisan fragrance BRAND NAME mentioned in the text below.
 
Lines starting with "ETSY SHOP:" are Etsy store names — treat these as potential
indie brand names and include them if they appear to be serious perfumers
(not candles, bath bombs, or hobbyist products).
 
EXCLUDE: major mainstream brands (Chanel, Dior, YSL, Tom Ford, Creed, Jo Malone,
Maison Margiela, Guerlain, Hermès, Armani, Versace, Calvin Klein, Hugo Boss),
and Etsy shops that are clearly not fragrance brands (candles only, wax melts, etc).
INCLUDE: small indie houses, niche perfumers, cult/emerging brands, DTC brands,
artisan perfumers, lesser-known niche houses, promising Etsy perfumers.
 
Return ONLY a JSON array (no markdown, no preamble):
[
  {{
    "name": "BrandName",
    "mentions": 2,
    "sentiment": "positive",
    "context": "one sentence about what was said"
  }}
]
 
If no qualifying brands found, return exactly: []
{extra}
TEXT:
{chunk}"""
                }]
            )
            response_text = message.content[0].text.strip()
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            brands = json.loads(response_text)
            for brand in brands:
                name = brand["name"].strip()
                if not name:
                    continue
                if name in all_brands:
                    all_brands[name]["mentions"] += brand.get("mentions", 1)
                else:
                    all_brands[name] = {
                        "name": name,
                        "mentions": brand.get("mentions", 1),
                        "sentiment": brand.get("sentiment", "neutral"),
                        "context": brand.get("context", ""),
                        "source": "fragrantica",
                    }
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  Warning: parse error on chunk {i+1}: {e}")
        except Exception as e:
            print(f"  Warning: Claude API error on chunk {i+1}: {e}")
        time.sleep(0.5)
 
    for trend in trend_hints:
        name = trend["name"]
        score = trend["trend_score"]
        if name in all_brands:
            all_brands[name]["mentions"] += max(1, score // 20)
            all_brands[name]["source"] = "fragrantica+trends"
        else:
            all_brands[name] = {
                "name": name,
                "mentions": max(1, score // 20),
                "sentiment": "neutral",
                "context": f"Rising Google search query (score: {score})",
                "source": "google_trends",
            }
 
    return list(all_brands.values())
 
 
# ── Step 3: Brand verification + metadata ────────────────────────────────────
 
def verify_and_enrich_brands(brands: list[dict]) -> list[dict]:
    """
    For each extracted brand, ask Claude to verify it's real and add metadata:
    country, founding year, price tier, and a confidence score.
    Processes in batches to keep API costs low.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    enriched = []
    batch_size = 15
 
    brand_names = [b["name"] for b in brands]
    batches = [brand_names[i:i+batch_size] for i in range(0, len(brand_names), batch_size)]
 
    brand_meta = {}
 
    for i, batch in enumerate(batches):
        print(f"  Verifying brands batch {i+1}/{len(batches)}...")
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""You are an expert on niche and indie fragrance brands worldwide.
 
For each brand name below, determine:
1. Is it a real fragrance brand? (yes/no)
2. If yes: country of origin, approximate founding year, price tier
 
Price tiers:
- "indie" = small batch, typically under $80/50ml
- "accessible niche" = $80–200/50ml (e.g. Byredo, Le Labo level)
- "luxury niche" = $200+/50ml (e.g. Roja Dove, Clive Christian level)
- "unknown" = can't determine
 
Return ONLY a JSON object (no markdown):
{{
  "BrandName": {{
    "real": true,
    "country": "France",
    "founded": 2015,
    "price_tier": "accessible niche",
    "confidence": "high"
  }},
  "NotABrand": {{
    "real": false
  }}
}}
 
Confidence: "high" = you're sure, "medium" = fairly sure, "low" = guessing.
If you don't recognize a brand but the name sounds like a plausible perfume house
(not a generic word, not a person's full name, not obviously a candle/bath brand),
set real to true with confidence "low" and price_tier "indie" — it may be a
micro-brand too new to be in your training data.
Only set real to false if it's clearly not a fragrance brand.
 
BRANDS TO CHECK:
{json.dumps(batch)}"""
                }]
            )
            response_text = message.content[0].text.strip()
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            meta = json.loads(response_text)
            brand_meta.update(meta)
        except Exception as e:
            print(f"  Warning: verification error on batch {i+1}: {e}")
        time.sleep(0.5)
 
    # Merge metadata back into brands, filter out non-real ones
    for brand in brands:
        name = brand["name"]
        meta = brand_meta.get(name, {})
        if meta.get("real") is False:
            print(f"  Filtered out '{name}' — not a real brand.")
            continue
        brand["country"]    = meta.get("country", "unknown")
        brand["founded"]    = meta.get("founded", "unknown")
        brand["price_tier"] = meta.get("price_tier", "unknown")
        brand["confidence"] = meta.get("confidence", "low")
        enriched.append(brand)
 
    print(f"  {len(enriched)} verified brands (filtered {len(brands) - len(enriched)} non-brands).")
    return enriched
 
 
# ── Step 4: Load / save / update history ─────────────────────────────────────
 
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
 
 
def update_history(history: dict, brands: list[dict], verified_names: set) -> dict:
    """
    Update history with this week's verified brands.
    Also removes any previously stored brands that have since been
    identified as non-brands (i.e. not in verified_names and have
    been flagged as fake in a prior run).
    """
    week = datetime.date.today().strftime("%Y-W%W")
 
    # Remove brands from history that were previously stored but are
    # now confirmed non-brands (appeared this week and got filtered out)
    this_week_seen = {b["name"] for b in brands}  # all raw candidates this week
    for name in list(history.keys()):
        if name in this_week_seen and name not in verified_names:
            print(f"  Removing '{name}' from history — confirmed non-brand.")
            del history[name]
 
    for brand in brands:
        name = brand["name"]
        if name not in history:
            history[name] = {
                "weeks": {},
                "context": brand.get("context", ""),
                "source": brand.get("source", "unknown"),
                "country": brand.get("country", "unknown"),
                "founded": brand.get("founded", "unknown"),
                "price_tier": brand.get("price_tier", "unknown"),
            }
        history[name]["weeks"][week] = brand.get("mentions", 1)
        for field in ["context", "source", "country", "founded", "price_tier"]:
            if brand.get(field) and brand[field] != "unknown":
                history[name][field] = brand[field]
    return history
 
 
# ── Step 5: Detect cusp engagement ───────────────────────────────────────────
 
def detect_momentum(history: dict) -> list[dict]:
    week = datetime.date.today().strftime("%Y-W%W")
    flagged = []
 
    for name, data in history.items():
        weeks = data["weeks"]
        if week not in weeks:
            continue
 
        this_week = weeks[week]
        past_weeks = [v for k, v in weeks.items() if k != week]
 
        if not past_weeks:
            if this_week >= 3:
                flagged.append({
                    "name": name,
                    "this_week": this_week,
                    "avg_before": 0,
                    "momentum_score": float(this_week),
                    "weeks_tracked": 1,
                    "context": data.get("context", ""),
                    "source": data.get("source", ""),
                    "country": data.get("country", "unknown"),
                    "founded": data.get("founded", "unknown"),
                    "price_tier": data.get("price_tier", "unknown"),
                    "status": "NEW",
                })
            continue
 
        avg = sum(past_weeks) / len(past_weeks)
        if avg == 0:
            avg = 0.5
        momentum = this_week / avg
 
        if momentum >= 2.0 and this_week >= 3:
            flagged.append({
                "name": name,
                "this_week": this_week,
                "avg_before": round(avg, 1),
                "momentum_score": round(momentum, 2),
                "weeks_tracked": len(weeks),
                "context": data.get("context", ""),
                "source": data.get("source", ""),
                "country": data.get("country", "unknown"),
                "founded": data.get("founded", "unknown"),
                "price_tier": data.get("price_tier", "unknown"),
                "status": "CUSP" if momentum >= 3.0 else "RISING",
            })
 
    return sorted(flagged, key=lambda x: x["momentum_score"], reverse=True)
 
 
# ── Step 6: Generate report via Claude ───────────────────────────────────────
 
def generate_report(flagged: list[dict]) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    if not flagged:
        return (
            f"# Fragrance Brand Momentum Report — {today}\n\n"
            "No brands showed notable momentum this week. "
            "The tracker is still building baselines — signals will sharpen over coming weeks.\n"
        )
 
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""You are a sharp trend analyst covering the niche fragrance market.
 
Write a weekly momentum report. Date: {today}
 
Guidelines:
- Lead with a 2-sentence market overview
- For CUSP brands: a paragraph explaining the signal and what might be driving it
- For RISING brands: 2-3 sentences each
- For NEW brands: brief mention noting no history yet; include country/price tier if known
- End with a Patterns section noting ingredient trends, geography, aesthetics
- Tone: smart industry newsletter — confident, specific, never hype-y
- Format in clean markdown
 
STATUS KEY:
CUSP = 3x+ above baseline | RISING = 2x–3x | NEW = first appearance
 
BRAND DATA:
{json.dumps(flagged, indent=2)}"""
        }]
    )
 
    report = f"# Fragrance Brand Momentum Report — {today}\n\n"
    report += message.content[0].text
    return report
 
 
# ── Step 7: Send email ────────────────────────────────────────────────────────
 
def send_email(report: str):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("  Email not configured — skipping.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌸 Fragrance Momentum Report — {datetime.date.today()}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT
    msg.attach(MIMEText(report, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print("  Email sent.")
    except Exception as e:
        print(f"  Email failed: {e}")
 
 
# ── Step 8: Sync to Notion ────────────────────────────────────────────────────
 
def sync_to_notion(flagged: list[dict], report: str):
    """
    Add a row to the Notion database for this week's run.
    Columns: Date, Top Brand, Brands Flagged, CUSP Count, Report
    """
    if not all([NOTION_TOKEN, NOTION_DATABASE_ID]):
        print("  Notion not configured — skipping.")
        return
 
    today = datetime.date.today().isoformat()
    cusp_count   = sum(1 for b in flagged if b["status"] == "CUSP")
    rising_count = sum(1 for b in flagged if b["status"] == "RISING")
    new_count    = sum(1 for b in flagged if b["status"] == "NEW")
    top_brand    = flagged[0]["name"] if flagged else "None"
 
    # Build a summary of flagged brands for the Notion row
    brand_summary = ", ".join(
        f"{b['name']} ({b['status']}, {b.get('price_tier','?')}, {b.get('country','?')})"
        for b in flagged[:10]
    )
 
    # Truncate report to fit Notion's 2000 char limit per rich text block
    report_excerpt = report[:1900] + "..." if len(report) > 1900 else report
 
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Date": {
                "title": [{"text": {"content": today}}]
            },
            "Top Brand": {
                "rich_text": [{"text": {"content": top_brand}}]
            },
            "Brands Flagged": {
                "number": len(flagged)
            },
            "CUSP": {
                "number": cusp_count
            },
            "RISING": {
                "number": rising_count
            },
            "NEW": {
                "number": new_count
            },
            "Brand Summary": {
                "rich_text": [{"text": {"content": brand_summary[:1900]}}]
            },
            "Report": {
                "rich_text": [{"text": {"content": report_excerpt}}]
            },
        }
    }
 
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
 
    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json=payload,
            timeout=15,
        )
        if resp.status_code == 200:
            print("  Notion row created successfully.")
        else:
            print(f"  Notion error {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"  Notion sync failed: {e}")
 
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
def main():
    print("── Step 1a: Scraping Fragrantica...")
    fragrantica_text = scrape_fragrantica()
 
    print("── Step 1b: Scraping newsletters & blogs...")
    newsletter_text = scrape_newsletters()
 
    print("── Step 1c: Scraping TikTok hashtag pages...")
    tiktok_text = scrape_tiktok()
 
    print("── Step 1d: Scraping Etsy for new indie perfumers...")
    etsy_text = scrape_etsy()
 
    print("── Step 1e: Fetching Google Trends...")
    trend_data = get_trending_brands()
 
    # Combine all text sources
    all_text = "\n\n".join([fragrantica_text, newsletter_text, tiktok_text, etsy_text])
    print(f"  Total text collected: {len(all_text):,} characters.")
 
    print("── Step 2: Extracting brand names via Claude...")
    brands = extract_brands_from_text(all_text, trend_data)
    print(f"  Found {len(brands)} raw brand candidates.")
 
    print("── Step 3: Verifying brands + adding metadata...")
    verified_brands = verify_and_enrich_brands(brands)
    verified_names = {b["name"] for b in verified_brands}
 
    print("── Step 4: Updating historical data...")
    history = load_history()
    history = update_history(history, verified_brands, verified_names)
    save_history(history)
    print(f"  History now tracks {len(history)} verified brands.")
 
    print("── Step 5: Detecting cusp engagement...")
    flagged = detect_momentum(history)
    print(f"  {len(flagged)} brands flagged.")
    for b in flagged[:5]:
        print(f"    [{b['status']}] {b['name']} — {b.get('country','?')}, "
              f"{b.get('price_tier','?')}, {b['momentum_score']}x")
 
    print("── Step 6: Generating report...")
    report = generate_report(flagged)
    Path(REPORT_FILE).parent.mkdir(exist_ok=True)
    with open(REPORT_FILE, "w") as f:
        f.write(report)
    print(f"  Report saved → {REPORT_FILE}")
 
    print("── Step 7: Sending email...")
    send_email(report)
 
    print("── Step 8: Syncing to Notion...")
    sync_to_notion(flagged, report)
 
    print("── Done ✓")
 
 
if __name__ == "__main__":
    main()
 
