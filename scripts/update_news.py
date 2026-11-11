import json
import os
import re
import html
import urllib.request
import xml.etree.ElementTree as ET

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


# Keep the existing feed sources and the existing NASA/ESA collection workflow.
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheCivicBriefNewsBot/1.0)"
}


class VisibleTextParser(HTMLParser):
    """Extract readable text while ignoring scripts, styles and page navigation."""

    def _init_(self):
        super()._init_()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1
        elif tag.lower() in ("p", "div", "br", "li", "h1", "h2", "h3"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript", "svg") and self.skip_depth:
            self.skip_depth -= 1
        elif tag.lower() in ("p", "div", "br", "li", "h1", "h2", "h3"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def clean_text(value):
    parser = VisibleTextParser()
    try:
        parser.feed(value or "")
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value or "")

    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_summary(value, title="", limit=230):
    text = clean_text(value)

    if not text:
        return "Read the original report for the latest details."

    # Remove common feed/page navigation fragments and generic labels.
    boilerplate = [
        r"\bAPOD\b",
        r"Today’s APOD Archive",
        r"Today's APOD Archive",
        r"Today’s Archive",
        r"Today's Archive",
        r"Submissions Index",
        r"Search Calendar RSS Education About Discuss",
        r"Astronomy Picture of the Day",
        r"Discover the cosmos!?",
        r"Read this release in English here\.?",
        r"Read more\s*\.?",
        r"Continue reading\s*\.?",
        r"Click here\s*\.?",
        r"Share this story\s*\.?",
    ]

    for pattern in boilerplate:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    # Remove author/byline prefixes without deleting the article body.
    text = re.sub(
        r"^(?:By|Written by|Author:?|Posted by)\s+[^.]{2,120}(?:\.\s*|\s+)(?=[A-Z])",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s+", " ", text).strip(" -|•…")

    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" :–—-.")

    # Avoid returning empty or navigation-only summaries.
    if len(re.sub(r"[^A-Za-z0-9]", "", text)) < 35:
        return "Read the original report for the latest details."

    if len(text) <= limit:
        return text

    shortened = text[:limit - 1]

    # Prefer a complete sentence when one is available near the limit.
    sentence_end = max(
        shortened.rfind(". "),
        shortened.rfind("! "),
        shortened.rfind("? ")
    )

    if sentence_end >= int(limit * 0.55):
        shortened = shortened[:sentence_end + 1]
    else:
        shortened = shortened.rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"

    return shortened


def classify_category(title, summary, fallback="Space"):
    """Use conservative title/summary keywords; otherwise retain feed category."""

    text = f"{title} {summary}".lower()

    if re.search(
        r"\b(artificial intelligence|\bai\b|machine learning|robotics|technology|algorithm|computer|cybersecurity)\b",
        text
    ):
        return "AI & Technology"

    if re.search(
        r"\b(climate|weather|clouds?|fog|earth observatory|ocean|wildfire|hurricane|atmosphere|ecosystem|biodiversity|earth science)\b",
        text
    ):
        return "Science"

    if re.search(
        r"\b(discover(?:y|ies|ed)|finds? source|detect(?:s|ed|ion)|evidence of|newly found|first ever|mysterious outburst|rubies on mars)\b",
        text
    ):
        return "Discoveries"

    if re.search(
        r"\b(space|mars|moon|lunar|planet|saturn|jupiter|astronaut|rocket|rover|telescope|galaxy|star|orbit|chandra|perseverance|curiosity)\b",
        text
    ):
        return "Space"

    valid_categories = {
        "Space",
        "Science",
        "Discoveries",
        "AI & Technology",
        "World News"
    }

    return fallback if fallback in valid_categories else "Science"


def get_text(item, *names):
    for name in names:
        element = item.find(name)

        if element is not None and element.text:
            return element.text.strip()

    return ""


def valid_image_url(url, base_url=""):
    if not url:
        return ""

    url = html.unescape(url.strip())
    url = urljoin(base_url, url)
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""

    return url


class ImageMetaParser(HTMLParser):

    def _init_(self):
        super()._init_()
        self.image = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return

        data = {
            key.lower(): value
            for key, value in attrs
            if key
        }

        property_name = (
            data.get("property", "") or
            data.get("name", "")
        ).lower()

        if property_name in (
            "og:image",
            "og:image:url",
            "twitter:image",
            "twitter:image:src"
        ):
            content = data.get("content", "")

            if content and not self.image:
                self.image = content


def get_page_image(article_url):
    try:
        request = urllib.request.Request(
            article_url,
            headers=HEADERS
        )

        with urllib.request.urlopen(request, timeout=12) as response:
            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if "html" not in content_type:
                return ""

            page_url = response.geturl()

            page_html = response.read(1000000).decode(
                "utf-8",
                errors="replace"
            )

        parser = ImageMetaParser()
        parser.feed(page_html)

        image_url = valid_image_url(
            parser.image,
            page_url
        )

        if image_url:
            return image_url

        match = re.search(
            r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
            page_html,
            re.IGNORECASE
        )

        if match:
            return valid_image_url(
                match.group(1),
                page_url
            )

    except Exception as error:
        print(f"Could not get article image: {error}")

    return ""


def get_image(item, article_url):

    # 1. RSS media fields.
    for element in item.iter():
        tag = element.tag.lower()

        if (
            tag.endswith("}thumbnail") or
            tag.endswith("}content") or
            tag.endswith("}enclosure")
        ):
            image_url = valid_image_url(
                element.attrib.get("url", ""),
                article_url
            )

            if image_url:
                return image_url

    # 2. Image embedded in the RSS description/summary.
    description = get_text(
        item,
        "description",
        "summary",
        "{*}summary"
    )

    match = re.search(
        r'<img[^>]+src=["\']([^"\']+)',
        description,
        re.IGNORECASE
    )

    if match:
        image_url = valid_image_url(
            match.group(1),
            article_url
        )

        if image_url:
            return image_url

    # 3. Original article page metadata.
    return get_page_image(article_url)


def parse_published(value):

    if value:
        try:
            parsed = parsedate_to_datetime(value)

            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed.astimezone(
                timezone.utc
            ).isoformat()

        except Exception:
            try:
                parsed = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )

                if parsed.tzinfo is None:
                    parsed = parsed.replace(
                        tzinfo=timezone.utc
                    )

                return parsed.astimezone(
                    timezone.utc
                ).isoformat()

            except Exception:
                pass

    return datetime.now(timezone.utc).isoformat()


def get_item_link(item):

    link = get_text(
        item,
        "link",
        "{*}link"
    )

    if link:
        return link

    link_element = item.find("{*}link")

    if link_element is not None:
        return link_element.attrib.get("href", "")

    return ""


def main():

    articles = []
    seen_links = set()

    for feed in FEEDS:

        try:
            print(f"Reading {feed['name']} feed...")

            request = urllib.request.Request(
                feed["url"],
                headers=HEADERS
            )

            with urllib.request.urlopen(
                request,
                timeout=20
            ) as response:
                root = ET.fromstring(response.read())

            items = root.findall(".//item")

            if not items:
                items = root.findall(".//{*}entry")

            for item in items[:12]:

                title = clean_text(
                    get_text(item, "title", "{*}title")
                )

                link = get_item_link(item)

                if not title or not link or link in seen_links:
                    continue

                seen_links.add(link)

                raw_summary = get_text(
                    item,
                    "description",
                    "summary",
                    "{*}summary",
                    "{*}content"
                )

                summary = clean_summary(
                    raw_summary,
                    title
                )

                category = classify_category(
                    title,
                    summary,
                    feed["category"]
                )

                published = get_text(
                    item,
                    "pubDate",
                    "published",
                    "{*}published",
                    "updated",
                    "{*}updated"
                )

                date = parse_published(published)

                print(f"Getting image: {title[:60]}")

                image_url = get_image(item, link)

                print(
                    "Image found."
                    if image_url
                    else "No image found for this article."
                )

                articles.append({
                    "title": title,
                    "summary": summary,
                    "url": link,
                    "image": image_url,
                    "source": feed["name"],
                    "category": category,
                    "published": date,
                })

        except Exception as error:
            print(
                f"Could not read {feed['name']} feed: {error}"
            )

    # Never replace a working news.json with an empty/failed feed result.
    if not articles:

        if os.path.exists("news.json"):
            print(
                "No new feed items; keeping existing news.json."
            )
            return

        raise RuntimeError(
            "No news received from any feed."
        )

    articles.sort(
        key=lambda article: article["published"],
        reverse=True
    )

    with open(
        "news.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            articles[:24],
            file,
            ensure_ascii=False,
            indent=2
        )

    image_count = sum(
        1
        for article in articles[:24]
        if article["image"]
    )

    print(
        f"Saved {min(len(articles), 24)} news stories."
    )

    print(
        f"Images found: {image_count}"
    )


if __name__ == "__main__":
    main()
