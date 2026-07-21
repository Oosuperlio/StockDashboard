#!/usr/bin/env python3
"""Fetch news from Google News RSS for the morning report."""
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone, timedelta
import ssl

HKT = timezone(timedelta(hours=8))
now_hkt = datetime.now(HKT)
today = now_hkt.strftime("%Y-%m-%d")

# SSL context for older Python
ssl_ctx = ssl._create_unverified_context()

def fetch_rss(url, max_items=5):
    """Fetch and parse Google News RSS."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })
        response = urllib.request.urlopen(req, timeout=15, context=ssl_ctx)
        xml_data = response.read().decode('utf-8', errors='replace')
        root = ET.fromstring(xml_data)
        
        items = []
        for item in root.findall('.//item'):
            title = item.findtext('title', '')
            link = item.findtext('link', '')
            pubdate = item.findtext('pubDate', '')
            source = item.findtext('source', '')
            if title:
                items.append({
                    'title': title,
                    'source': source,
                    'date': pubdate,
                })
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        return [{'title': f'[Error fetching: {e}]', 'source': '', 'date': ''}]

# Search queries
queries = [
    f"stock market today {today}",
    "Federal Reserve interest rate inflation 2026",
    "US China trade tariff 2026",
    "AI semiconductor market news",
    "Hong Kong stock market news",
]

news_by_category = {}

for q in queries:
    encoded_q = urllib.parse.quote(q)
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"
    items = fetch_rss(url, max_items=4)
    news_by_category[q] = items

# Output as JSON
output = {
    "date": today,
    "news": news_by_category
}

with open("/tmp/morning_report_news.json", "w") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"✅ News fetched for {today}")
for cat, items in news_by_category.items():
    print(f"\n  [{cat}]")
    for item in items:
        title = item['title'][:80] + ('...' if len(item['title']) > 80 else '')
        print(f"    • {title}")
