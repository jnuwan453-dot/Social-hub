import json
import os
import re
import html
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

FEEDS = [
    {
        "name": "NASA",
        "url": "https://www.nasa.gov/feed/",
        "category": "Space",
    },
    {
        "name": "ESA",
        "url": "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "category": "Science",
    },
]

def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return html.unescape(" ".join(value.split()))

def get_text(item, *names):
    for name in names:
        element = item.find(name)
        if element is not None and element.text:
            return element.text.strip()
    return ""

def get_image(item):
    for element in item.iter():
        tag = element.tag.lower()
        if tag.endswith("}thumbnail") or tag.endswith("}content"):
            url = element.attrib.get("url", "")
            if url.startswith("https://"):
                return url

    description = get_text(item, "description", "summary")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)', description)
    if match and match.group(1).startswith("https://"):
        return match.group(1)

    return ""

def main():
    articles = []
    seen_links = set()

    for feed in FEEDS:
        try:
            request = urllib.request.Request(
                feed["url"],
                headers={"User-Agent": "TheCivicBriefNewsBot/1.0"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                root = ET.fromstring(response.read())

            items = root.findall(".//item")
            if not items:
                items = root.findall(".//{*}entry")

            for item in items[:12]:
                title = clean_text(get_text(item, "title", "{*}title"))
                link = get_text(item, "link", "{*}link")

                if not link:
                    link_element = item.find("{*}link")
                    if link_element is not None:
                        link = link_element.attrib.get("href", "")

                if not title or not link or link in seen_links:
                    continue

                seen_links.add(link)
                summary = clean_text(
                    get_text(item, "description", "summary", "{*}summary")
                )

                published = get_text(
                    item, "pubDate", "published", "{*}published",
                    "updated", "{*}updated"
                )

                try:
                    date = parsedate_to_datetime(published).isoformat()
                except Exception:
                    try:
                        date = datetime.fromisoformat(
                            published.replace("Z", "+00:00")
                        ).isoformat()
                    except Exception:
                        date = datetime.now(timezone.utc).isoformat()

                articles.append({
                    "title": title,
                    "summary": summary[:500],
                    "url": link,
                    "image": get_image(item),
                    "source": feed["name"],
                    "category": feed["category"],
                    "published": date,
                })

        except Exception as error:
            print(f"Could not read {feed['name']} feed: {error}")

    if not articles:
        if os.path.exists("news.json"):
            print("No new feed items; keeping existing news.json.")
            return
        raise RuntimeError("No news received from any feed.")

    articles.sort(key=lambda article: article["published"], reverse=True)

    with open("news.json", "w", encoding="utf-8") as file:
        json.dump(articles[:24], file, ensure_ascii=False, indent=2)

    print(f"Saved {min(len(articles), 24)} news stories.")

if __name__ == "__main__":
    main()
