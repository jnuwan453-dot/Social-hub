#!/usr/bin/env python3
"""Publish scheduled articles for theglobalsocialhub.com.

Reads queue/queue.json. Each entry whose "date" is today or earlier
(Asia/Colombo) and whose "status" is "queued" gets published:
  1. queue/<file> is copied to articles/<file>
  2. A card for it is inserted at the top of the Featured Articles
     grid in index.html
  3. Its URL is appended to sitemap.xml
  4. The entry is marked "published" in queue/queue.json

The script is idempotent: already-published entries, existing article
files, existing homepage cards and existing sitemap URLs are all
skipped safely. It never touches news.json or the news workflow.

For testing, set PUBLISH_DATE=YYYY-MM-DD to override "today".
"""

import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    def today_local() -> date:
        return datetime.now(ZoneInfo("Asia/Colombo")).date()

except Exception:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    from datetime import timedelta, timezone

    def today_local() -> date:
        return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


ROOT = Path(__file__).resolve().parent.parent
QUEUE_DIR = ROOT / "queue"
QUEUE_JSON = QUEUE_DIR / "queue.json"
ARTICLES_DIR = ROOT / "articles"
INDEX_HTML = ROOT / "index.html"
SITEMAP_XML = ROOT / "sitemap.xml"
SITE = "https://theglobalsocialhub.com"

CARD_TEMPLATE = """      <article class="feature-card" data-category="{card_category}">
        <div class="story-image">
          <img src="{image}" alt="{image_alt}" loading="lazy">
        </div>
        <div class="story-content">
          <span class="category">{card_category}</span>
          <h3>{title}</h3>
          <p>{card_text}</p>
          <div class="story-meta">Global Social Hub &middot; {display_date} &middot; {read_time} min read</div>
          <a class="read-link" href="articles/{file}">Read Article &rarr;</a>
        </div>
      </article>

"""

SITEMAP_TEMPLATE = """  <url>
    <loc>{site}/articles/{file}</loc>
    <lastmod>{date}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
"""


def publish_entry(entry: dict, index_text: str, sitemap_text: str):
    """Publish one queue entry. Returns (index_text, sitemap_text, changed)."""
    changed = False
    fname = entry["file"]

    # 1. Copy the article file into articles/
    src = QUEUE_DIR / fname
    dst = ARTICLES_DIR / fname
    if not dst.exists():
        if not src.exists():
            print(f"ERROR: queued file missing: {src}")
            sys.exit(1)
        shutil.copyfile(src, dst)
        changed = True
        print(f"Copied {fname} -> articles/")

    # 2. Insert the homepage card at the top of the Featured Articles grid
    href = f"articles/{fname}"
    if href not in index_text:
        anchor = '<div class="feature-grid">'
        pos = index_text.find(anchor)
        if pos == -1:
            print("ERROR: feature-grid anchor not found in index.html")
            sys.exit(1)
        insert_at = pos + len(anchor) + 1  # just after the newline
        card = CARD_TEMPLATE.format(**entry)
        index_text = index_text[:insert_at] + "\n" + card + index_text[insert_at:]
        changed = True
        print(f"Added homepage card for {fname}")

    # 3. Append the URL to sitemap.xml
    loc = f"{SITE}/articles/{fname}"
    if loc not in sitemap_text:
        block = SITEMAP_TEMPLATE.format(site=SITE, file=fname, date=entry["date"])
        sitemap_text = sitemap_text.replace("</urlset>", block + "</urlset>")
        changed = True
        print(f"Added {fname} to sitemap.xml")

    return index_text, sitemap_text, changed


def main() -> int:
    override = os.environ.get("PUBLISH_DATE")
    today = date.fromisoformat(override) if override else today_local()
    print(f"Publish run for {today.isoformat()} (Asia/Colombo)")

    with open(QUEUE_JSON, encoding="utf-8") as fh:
        queue = json.load(fh)

    with open(INDEX_HTML, encoding="utf-8") as fh:
        index_text = fh.read()
    with open(SITEMAP_XML, encoding="utf-8") as fh:
        sitemap_text = fh.read()

    published_any = False
    for entry in queue:
        if entry.get("status") != "queued":
            continue
        if date.fromisoformat(entry["date"]) > today:
            continue
        index_text, sitemap_text, _ = publish_entry(entry, index_text, sitemap_text)
        entry["status"] = "published"
        entry["published_on"] = today.isoformat()
        published_any = True
        print(f"PUBLISHED: {entry['date']} - {entry['title']}")

    if not published_any:
        print("No article due today.")
        return 0

    with open(INDEX_HTML, "w", encoding="utf-8") as fh:
        fh.write(index_text)
    with open(SITEMAP_XML, "w", encoding="utf-8") as fh:
        fh.write(sitemap_text)
    with open(QUEUE_JSON, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("Saved index.html, sitemap.xml and queue/queue.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
