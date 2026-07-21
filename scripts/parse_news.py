#!/usr/bin/env python3
"""Parse Google News RSS feeds for the morning report."""
import xml.etree.ElementTree as ET

feeds = {
    "/tmp/news_market.xml": "📊 市場",
    "/tmp/news_fed.xml": "🏦 聯儲局",
    "/tmp/news_trade.xml": "🌐 貿易",
    "/tmp/news_ai.xml": "🤖 AI/半導體",
    "/tmp/news_hk.xml": "🇭🇰 香港"
}

all_news = []
seen_titles = set()

for path, category in feeds.items():
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        items = root.findall('.//item')
        for item in items[:4]:
            title = item.find('title').text if item.find('title') is not None else ''
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_news.append((category, title))
    except Exception as e:
        print(f"Error parsing {path}: {e}")

print(f"=== 重點新聞 (共 {len(all_news)} 條) ===\n")
for cat, title in all_news:
    print(f"[{cat}] {title}")
