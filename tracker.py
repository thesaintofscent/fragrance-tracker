"""
Fragrance Brand Momentum Tracker
Sources: Fragrantica (new brand discovery) + Google Trends (momentum signals)
No API keys required for data collection — only Claude API + optional Gmail.
Runs weekly via GitHub Actions and emails a curated momentum report.
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
 
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_SENDER      = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT   = os.environ.get("EMAIL_RECIPIENT", "")
 
# Fragrantica pages to scrape for brand discovery
FRAGRANTICA_URLS = [
    "https://www.fragrantica.com/news/",                        # latest news & brand coverage
    "https://www.fragrantica.com/news/niche-perfumery/",        # dedicated niche brand news
    "https://www.fragrantica.com/niche-perfume/",               # niche brand listings
    "https://www.fragrantica.com/community/",                   # community discussions
    "https://www.fragrantica.com/board/viewforum.php?id=2",     # General Perfume Talk forum
    "https://www.fragrantica.com/board/viewforum.php?id=6",     # New Fragrance Releases forum
]
 
# Seed search terms to find trending fragrance brands on Google Trends
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
    """Scrape Fragrantica pages for brand names and community discussion text."""
    all_text = []
 
    for url in FRAGRANTICA_URLS:
        print(f"  Fetching {url}...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
 
            # Remove nav, footer, scripts, ads
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
 
            # Grab meaningful text blocks
            for element in soup.find_all(["h1", "h2", "h3", "p", "a", "li"]):
                text = element.get_text(separator=" ", strip=True)
                if len(text) > 20:
                    all_text.append(text)
 
            time.sleep(random.uniform(2, 4))  # polite delay between requests
 
        except requests.RequestException as e:
            print(f"  Warning: could not fetch {url}: {e}")
 
    # Also scrape the "new perfumes" section which lists recent brand releases
    new_perfumes_url = "https://www.fragrantica.com/search/?word=&categories[]=new"
    try:
        print(f"  Fetching new perfumes listing...")
        resp = requests.get(new_perfumes_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        for element in soup.find_all(["h1", "h2", "h3", "p", "span", "a"]):
            text = element.get_text(strip=True)
            if len(text) > 10:
                all_text.append(text)
        time.sleep(random.uniform(2, 4))
    except requests.RequestException as e:
        print(f"  Warning: could not fetch new perfumes: {e}")
 
    combined = "\n".join(all_text)
    print(f"  Collected {len(combined):,} characters from Fragrantica.")
    return combined
 
 
# ── Step 1b: Google Trends — rising queries ───────────────────────────────────
 
def get_trending_brands() -> list[dict]:
    """
    Use pytrends to find rising search queries related to fragrance.
    Returns list of {name, trend_score} dicts.
    """
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    trending = {}
 
    for term in TREND_SEED_TERMS:
        print(f"  Fetching Google Trends related queries for '{term}'...")
        try:
            pytrends.build_payload([term], timeframe="now 7-d", geo="")
            related = pytrends.related_queries()
 
            if related and term in related:
                rising_df = related[term].get("rising")
                if rising_df is not None and not rising_df.empty:
                    for _, row in rising_df.iterrows():
                        query = str(row["query"]).strip()
                        value = int(row["value"])
                        # Filter out generic terms, keep brand-like queries
                        if (len(query) > 3
                                and "perfume" not in query.lower()
                                and "fragrance" not in query.lower()
                                and "how" not in query.lower()
                                and "what" not in query.lower()):
                            if query not in trending or trending[query] < value:
                                trending[query] = value
 
            time.sleep(random.uniform(3, 6))  # respect rate limits
 
        except Exception as e:
            print(f"  Warning: Google Trends error for '{term}': {e}")
            time.sleep(10)
 
    result = [{"name": k, "trend_score": v} for k, v in trending.items()]
    result.sort(key=lambda x: x["trend_score"], reverse=True)
    print(f"  Found {len(result)} rising trend queries.")
    return result
 
 
# ── Step 2: Extract brand names via Claude ────────────────────────────────────
 
def extract_brands_from_text(raw_text: str, trend_hints: list[dict]) -> list[dict]:
    """
    Send Fragrantica text + trend hints to Claude to extract niche brand names.
    Returns list of {name, mentions, sentiment, context, source}.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
 
    chunk_size = 12000
    chunks = [raw_text[i:i+chunk_size] for i in range(0, len(raw_text), chunk_size)]
    all_brands = {}
 
    # Include trend hints as extra context in first chunk prompt
    trend_hint_text = ""
    if trend_hints:
        top_hints = [t["name"] for t in trend_hints[:20]]
        trend_hint_text = (
            f"\n\nADDITIONAL CONTEXT — these terms are currently rising on Google "
            f"Trends and may be brand names worth identifying: {', '.join(top_hints)}"
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
 
EXCLUDE: major mainstream brands (Chanel, Dior, YSL, Tom Ford, Creed, Jo Malone,
Maison Margiela, Guerlain, Hermès, Armani, Versace, Calvin Klein, Hugo Boss).
INCLUDE: small indie houses, niche perfumers, cult/emerging brands, DTC brands,
artisan perfumers, lesser-known niche houses.
 
Return ONLY a JSON array (no markdown, no preamble, no explanation):
[
  {{
    "name": "BrandName",
    "mentions": 2,
    "sentiment": "positive",
    "context": "one sentence describing what was said or why it appeared"
  }}
]
 
If no qualifying brands found, return exactly: []
{extra}
TEXT TO ANALYZE:
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
 
    # Merge in Google Trends brands (higher weight — active search intent)
    for trend in trend_hints:
        name = trend["name"]
        score = trend["trend_score"]
        if name in all_brands:
            # Boost mention count proportionally to trend score
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
 
 
# ── Step 3: Load / save / update history ─────────────────────────────────────
 
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
 
 
def update_history(history: dict, brands: list[dict]) -> dict:
    week = datetime.date.today().strftime("%Y-W%W")
    for brand in brands:
        name = brand["name"]
        if name not in history:
            history[name] = {
                "weeks": {},
                "context": brand.get("context", ""),
                "source": brand.get("source", "unknown"),
            }
        history[name]["weeks"][week] = brand.get("mentions", 1)
        if brand.get("context"):
            history[name]["context"] = brand["context"]
        if brand.get("source"):
            history[name]["source"] = brand["source"]
    return history
 
 
# ── Step 4: Detect cusp engagement ───────────────────────────────────────────
 
def detect_momentum(history: dict) -> list[dict]:
    """
    Flag brands where this week's mentions are 2x+ above their historical average.
    Brands seen only this week need 3+ mentions to qualify.
    """
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
                "status": "CUSP" if momentum >= 3.0 else "RISING",
            })
 
    return sorted(flagged, key=lambda x: x["momentum_score"], reverse=True)
 
 
# ── Step 5: Generate report via Claude ───────────────────────────────────────
 
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
 
Write a weekly momentum report based on the data below. Date: {today}
 
Guidelines:
- Lead with a 2-sentence summary of what this week's signals say about the market overall
- For each CUSP brand: give a paragraph explaining the signal and what might be driving it
- For each RISING brand: a shorter note (2-3 sentences)
- For NEW brands: brief mention with a note that there's no history yet
- End with a "Patterns" section if you notice common threads (ingredient trends, aesthetics, geography)
- Tone: smart industry newsletter. Confident, specific, never hype-y. No fluff.
- Format in clean markdown
 
STATUS KEY:
CUSP = mention velocity 3x+ above baseline (strong signal)
RISING = 2x–3x above baseline (worth watching)
NEW = first appearance with 3+ mentions (no history yet)
 
BRAND DATA:
{json.dumps(flagged, indent=2)}"""
        }]
    )
 
    report = f"# Fragrance Brand Momentum Report — {today}\n\n"
    report += message.content[0].text
    return report
 
 
# ── Step 6: Send email ────────────────────────────────────────────────────────
 
def send_email(report: str):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("  Email credentials not configured — report saved to file only.")
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
        print("  Email sent successfully.")
    except Exception as e:
        print(f"  Email failed: {e}")
 
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
def main():
    print("── Step 1a: Scraping Fragrantica...")
    fragrantica_text = scrape_fragrantica()
 
    print("── Step 1b: Fetching Google Trends rising queries...")
    trend_data = get_trending_brands()
 
    print("── Step 2: Extracting brand names via Claude...")
    brands = extract_brands_from_text(fragrantica_text, trend_data)
    print(f"  Found {len(brands)} unique brand candidates.")
 
    print("── Step 3: Updating historical data...")
    history = load_history()
    history = update_history(history, brands)
    save_history(history)
    print(f"  History now tracks {len(history)} brands total.")
 
    print("── Step 4: Detecting cusp engagement...")
    flagged = detect_momentum(history)
    print(f"  {len(flagged)} brands flagged.")
    for b in flagged[:5]:
        print(f"    [{b['status']}] {b['name']}: {b['momentum_score']}x  ({b['this_week']} mentions, src: {b['source']})")
 
    print("── Step 5: Generating report...")
    report = generate_report(flagged)
    Path(REPORT_FILE).parent.mkdir(exist_ok=True)
    with open(REPORT_FILE, "w") as f:
        f.write(report)
    print(f"  Report saved → {REPORT_FILE}")
 
    print("── Step 6: Sending email...")
    send_email(report)
 
    print("── Done ✓")
 
 
if __name__ == "__main__":
    main()
 
