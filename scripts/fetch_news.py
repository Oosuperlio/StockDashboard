#!/usr/bin/env python3
"""Fetch news from Google News RSS."""
import urllib.request
import urllib.parse
import ssl
import xml.etree.ElementTree as ET
import re
from datetime import datetime

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch_rss_news(query, max_results=3):
    """Fetch news from Google News RSS."""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            xml_data = resp.read().decode('utf-8', errors='replace')
        
        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall('.//item')[:max_results]:
            title = item.findtext('title', '')
            source = item.findtext('source', '')
            link = item.findtext('link', '')
            pubdate = item.findtext('pubDate', '')
            
            # Clean HTML tags from title
            title = re.sub(r'<[^>]+>', '', title)
            
            items.append({
                'title': title,
                'source': source,
                'link': link,
                'pubdate': pubdate
            })
        return items
    except Exception as e:
        return [{'title': f'Error: {str(e)}', 'source': '', 'link': '', 'pubdate': ''}]

queries = [
    "stock market today July 16 2026",
    "Federal Reserve interest rate inflation 2026",
    "US China trade tariff 2026",
    "AI semiconductor news",
    "Hong Kong stock market news"
]

categories = [
    "美股市場",
    "聯儲局/利率",
    "中美貿易/關稅",
    "AI/半導體",
    "香港市場"
]

all_news = []
for i, query in enumerate(queries):
    news = fetch_rss_news(query, 2)
    all_news.append((categories[i], news))

print("=== NEWS ===")
for cat, items in all_news:
    print(f"CATEGORY|{cat}")
    for item in items:
        title = item['title'].replace('\n', ' ').strip()
        source = item['source']
        print(f"ITEM|{title}|{source}")

print("=== DONE ===")
