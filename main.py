import os
import re
import hashlib
import urllib.request
import feedparser
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from supabase import create_client, Client
from calendar import timegm

# --- 1. Safeguard & Helper Functions ---
def contains_stale_date(title):
    title_lower = title.lower()
    now = datetime.now(timezone.utc)
    current_day = now.day

    match = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b', title_lower)
    if match:
        month_str, day_num = match.groups()
        day_num = int(day_num)
        if (current_day - day_num) > 2:
            return True
    return False

def clean_title(title):
    # Strip trailing publisher suffixes appended by RSS feeds
    cleaned = re.sub(r'\s*-\s*[^-]+$', '', title)
    return cleaned.strip()

def extract_image_url(entry):
    if 'media_content' in entry and len(entry.media_content) > 0:
        return entry.media_content[0].get('url')
    if 'enclosures' in entry and len(entry.enclosures) > 0:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href')
    summary = entry.get('summary', '') or entry.get('description', '')
    img_match = re.search(r'<img [^>]*src="([^"]+)"', summary)
    if img_match:
        return img_match.group(1)
    return None

def classify_gs_paper(title):
    t = title.lower()
    
    # GS Paper 2: Polity, Governance, Social Justice & International Relations
    if any(k in t for k in ['bill', 'act', 'constitution', 'parliament', 'judiciary', 'court', 
                           'governance', 'scheme', 'policy', 'election', 'ministry', 'treaty', 
                           'bilateral', 'summit', 'g20', 'un', 'mea', 'polity', 'health', 
                           'education', 'welfare', 'vulnerable', 'hunger', 'diaspora', 'ngo', 
                           'shg', 'regulatory', 'statutory', 'parliamentary', 'supreme court']):
        return "GS Paper 2 (Polity & IR)", "Governance & IR"
        
    # GS Paper 3: Economy, Agriculture, S&T, Environment, Security & Disasters
    elif any(k in t for k in ['rbi', 'gdp', 'economy', 'budget', 'tax', 'isro', 'space', 
                               'defence', 'climate', 'pollution', 'forest', 'wildlife', 
                               'agriculture', 'infrastructure', 'cyber', 'security', 'tech',
                               'msp', 'subsidy', 'food processing', 'land reform', 'disaster', 
                               'money laundering', 'extremism', 'biotech', 'robotic', 'energy',
                               'import', 'export', 'inflation', 'semiconductor']):
        return "GS Paper 3 (Economy, S&T, Env)", "Economy & Tech"
        
    # GS Paper 1: Heritage, Culture, History, Society & World Geography
    elif any(k in t for k in ['heritage', 'culture', 'monument', 'history', 'art', 
                               'geography', 'earthquake', 'cyclone', 'tribal', 'society',
                               'women', 'urbanization', 'globalization', 'communalism', 
                               'secularism', 'tsunami', 'volcanic', 'freedom struggle', 
                               'independence', 'craft', 'temple', 'dynasty']):
        return "GS Paper 1 (Culture & Geo)", "Culture & Society"
        
    # GS Paper 4 or General fallback
    elif any(k in t for k in ['ethics', 'probity', 'corruption', 'integrity', 'rti', 'transparency']):
        return "GS Paper 4 (Ethics)", "Ethics & Integrity"
        
    else:
        return None, None

def fetch_feed_with_user_agent(url):
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read()
        return feedparser.parse(html_content)
    except Exception as e:
        print(f"⚠️ Error fetching feed from {url}: {e}")
        return None

def process_google_news_feed(url, source_name, limit=15):
    print(f"📡 Fetching {source_name}...")
    feed = fetch_feed_with_user_agent(url)
    articles = []
    
    if not feed or not feed.entries:
        return articles

    for entry in feed.entries[:limit]:
        title = clean_title(entry.get("title", ""))
        if not title or contains_stale_date(title):
            continue

        raw_pub = entry.get("published", "")
        iso_pub = datetime.now(timezone.utc).isoformat()
        
        # 1. Try feedparser's robust built-in time parser first (Highly Accurate)
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                dt = datetime.fromtimestamp(timegm(entry.published_parsed), tz=timezone.utc)
                iso_pub = dt.isoformat()
            except Exception:
                pass
        # 2. Fallback to manual string parsing if the above is missing
        elif entry.get("published"):
            try:
                iso_pub = parsedate_to_datetime(entry.get("published")).isoformat()
            except Exception:
                pass

        articles.append({
            "id": hashlib.md5(title.encode('utf-8')).hexdigest(),
            "title": title,
            "link": entry.get("link", ""),
            "image_url": extract_image_url(entry),
            "source": source_name,
            "published_at": iso_pub
        })
    return articles

# --- 2. Main Pipeline Execution ---
def main():
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    supabase: Client = create_client(supabase_url, supabase_key)

    # 1. Clean old records (>365 days)
    one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    supabase.table("raw_upsc_news").delete().lt("published_at", one_year_ago).execute()
    supabase.table("alert_upsc").delete().lt("published_at", one_year_ago).execute()

    # 2. Fetch using Google News Aggregator RSS endpoints to bypass cloud firewalls
    pib_data = process_google_news_feed(
        "https://news.google.com/rss/search?q=site:pib.gov.in+when:1d&hl=en-IN&gl=IN&ceid=IN:en", 
        "PIB (Govt of India)", 
        20
    )
    prs_data = process_google_news_feed(
        "https://news.google.com/rss/search?q=site:prsindia.org+when:7d&hl=en-IN&gl=IN&ceid=IN:en", 
        "PRS Legislative Research", 
        10
    )
    air_data = process_google_news_feed(
        "https://news.google.com/rss/search?q=site:newsonair.gov.in+when:1d&hl=en-IN&gl=IN&ceid=IN:en", 
        "All India Radio (AIR)", 
        15
    )

    # Combine and Deduplicate
    all_dict = {}
    for item in pib_data + prs_data + air_data:
        all_dict[item["title"]] = item

    raw_articles = list(all_dict.values())
    if not raw_articles:
        print("⚠️ Warning: No articles fetched across sources.")
        return

    # 3. Upsert Raw Data
    supabase.table("raw_upsc_news").upsert(raw_articles, on_conflict="title").execute()

    # 4. Process and Upsert Classified Data
    # 4. Process and Upsert Classified Data
    processed = []
    for item in raw_articles:
        gs_paper, category = classify_gs_paper(item["title"])
        
        # 🚨 THE BOUNCER: Skip articles that don't match syllabus keywords
        if not gs_paper:
            continue

        processed.append({
            "id": item["id"],
            "title": item["title"],
            "one_line_summary": item["title"],
            "gs_paper": gs_paper,
            "category": category,
            "source": item["source"],
            "link": item["link"],
            "image_url": item.get("image_url"),
            "published_at": item["published_at"]
        })

    if processed:
        supabase.table("alert_upsc").upsert(processed, on_conflict="title").execute()
        print(f"✅ Successfully processed {len(processed)} UPSC records!")
    else:
        print("✅ Finished processing. No articles matched syllabus keywords today.")

if __name__ == "__main__":
    main()
