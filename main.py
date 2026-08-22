import os
import re
import hashlib
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from supabase import create_client, Client

# --- 1. UPSC Classification Helper ---
def classify_gs_paper(title):
    t = title.lower()
    if any(k in t for k in ['bill', 'act', 'constitution', 'parliament', 'judiciary', 'court', 
                            'governance', 'scheme', 'policy', 'election', 'ministry', 'treaty', 
                            'bilateral', 'summit', 'g20', 'un', 'mea', 'polity']):
        return "GS Paper 2 (Polity & IR)", "Governance & IR"
    elif any(k in t for k in ['rbi', 'gdp', 'economy', 'budget', 'tax', 'isro', 'space', 
                             'defence', 'climate', 'pollution', 'forest', 'wildlife', 
                             'agriculture', 'infrastructure', 'cyber', 'security', 'tech']):
        return "GS Paper 3 (Economy, S&T, Env)", "Economy & Tech"
    elif any(k in t for k in ['heritage', 'culture', 'monument', 'history', 'art', 
                             'geography', 'earthquake', 'cyclone', 'tribal', 'society']):
        return "GS Paper 1 (Culture & Geo)", "Culture & Society"
    else:
        return "GS Paper 4 / General Policy", "General Update"

def clean_title(title):
    return re.sub(r'\s*-\s*[^-]+$', '', title).strip()

# --- 2. Source Fetchers ---
def fetch_pib():
    print("📰 Fetching PIB Press Releases...")
    feed = feedparser.parse("https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1")
    articles = []
    
    for entry in feed.entries[:20]:
        title = clean_title(entry.get("title", ""))
        raw_pub = entry.get("published", "")
        
        iso_pub = datetime.now(timezone.utc).isoformat()
        if raw_pub:
            try:
                iso_pub = parsedate_to_datetime(raw_pub).isoformat()
            except Exception:
                pass
                
        articles.append({
            "id": hashlib.md5(title.encode('utf-8')).hexdigest(),
            "title": title,
            "link": entry.get("link", ""),
            "source": "PIB (Govt of India)",
            "published_at": iso_pub
        })
    return articles

def fetch_prs():
    print("⚖️ Fetching PRS Legislative Research...")
    articles = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get("https://prsindia.org/billtrack", headers=headers, timeout=10)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            for card in soup.find_all('div', class_='bill-title')[:10]:
                a_tag = card.find('a')
                if a_tag:
                    title = clean_title(a_tag.text.strip())
                    link = "https://prsindia.org" + a_tag['href'] if a_tag['href'].startswith('/') else a_tag['href']
                    articles.append({
                        "id": hashlib.md5(title.encode('utf-8')).hexdigest(),
                        "title": title,
                        "link": link,
                        "source": "PRS Legislative Research",
                        "published_at": datetime.now(timezone.utc).isoformat()
                    })
    except Exception as e:
        print(f"⚠️ PRS Error: {e}")
    return articles

def fetch_newsonair():
    print("🎙️ Fetching NewsOnAIR...")
    feed = feedparser.parse("https://news.google.com/rss/search?q=site:newsonair.gov.in+when:1d&hl=en-IN&gl=IN&ceid=IN:en")
    articles = []
    
    for entry in feed.entries[:15]:
        title = clean_title(entry.get("title", ""))
        raw_pub = entry.get("published", "")
        iso_pub = datetime.now(timezone.utc).isoformat()
        
        if raw_pub:
            try:
                iso_pub = parsedate_to_datetime(raw_pub).isoformat()
            except Exception:
                pass
                
        articles.append({
            "id": hashlib.md5(title.encode('utf-8')).hexdigest(),
            "title": title,
            "link": entry.get("link", ""),
            "source": "All India Radio (AIR)",
            "published_at": iso_pub
        })
    return articles

# --- 3. Main Execution ---
def main():
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    supabase: Client = create_client(supabase_url, supabase_key)

    # Clean old records (> 14 days)
    fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    supabase.table("raw_upsc_news").delete().lt("published_at", fourteen_days_ago).execute()
    supabase.table("alert_upsc").delete().lt("published_at", fourteen_days_ago).execute()

    # Collect data
    raw_articles = fetch_pib() + fetch_prs() + fetch_newsonair()
    if not raw_articles:
        print("No articles fetched.")
        return

    # 1. Insert into Raw Table
    supabase.table("raw_upsc_news").upsert(raw_articles, on_conflict="title").execute()

    # 2. Process and Insert into Structured Table
    processed = []
    for item in raw_articles:
        gs_paper, category = classify_gs_paper(item["title"])
        processed.append({
            "id": item["id"],
            "title": item["title"],
            "one_line_summary": item["title"],
            "gs_paper": gs_paper,
            "category": category,
            "source": item["source"],
            "link": item["link"],
            "published_at": item["published_at"]
        })

    supabase.table("alert_upsc").upsert(processed, on_conflict="title").execute()
    print(f"✅ Successfully processed {len(processed)} UPSC updates!")

if __name__ == "__main__":
    main()
