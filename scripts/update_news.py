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


FEEDS = [
    {
        "name": "NASA",
        "url": "https://www.nasa.gov/feed/",
        "category": "Space",
    },
    {
        "name": "ESA",
       "url": "https://www.esa.int/rssfeed/Science_Exploration/Space_Science",
        "category": "Science",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheCivicBriefNewsBot/1.0)"
}

VALID_CATEGORIES = {
    "Space",
    "Science",
    "Discoveries",
    "AI & Technology",
    "World News",
}


class VisibleTextParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1
        elif tag.lower() in ("p", "div", "br", "li", "h1", "h2", "h3"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript", "svg"):
            if self.skip_depth:
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

    # Remove common NASA APOD navigation and page labels.
    boilerplate_patterns = [
        r"Today's APOD Archive",
        r"Today’s APOD Archive",
        r"Today's Archive",
        r"Today’s Archive",
        r"Submissions Index",
        r"Search Calendar RSS Education About Discuss",
        r"Astronomy Picture of the Day",
        r"Discover the cosmos!?",
        r"Each day a different image or photograph of our fascinating universe is featured, along with a brief explanation written by a professional astronomer\.?",
        r"\bAPOD\b",
        r"Read this release in English here\.?",
        r"Read more\s*\.?",
        r"Continue reading\s*\.?",
        r"Click here\s*\.?",
        r"Share this story\s*\.?",
    ]

    for pattern in boilerplate_patterns:
        text = re.sub(
            pattern,
            " ",
            text,
            flags=re.IGNORECASE
        )

    # Remove common navigation sequences.
    text = re.sub(
        r"\b(?:Archive|Submissions Index|Search|Calendar|RSS|Education|About|Discuss)\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    # Remove author/byline prefixes.
    text = re.sub(
        r"^(?:By|Written by|Author:?|Posted by)\s+[^.]{2,120}(?:\.\s*|\s+)(?=[A-Z])",
        "",
        text,
        flags=re.IGNORECASE
    )

    # Remove the title if it is repeated at the start of the summary.
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):]

    # Remove date/title-like APOD prefix if present.
    text = re.sub(
        r"^\s*(?:Science\s*[-–|]\s*)?\d{4}\s+[A-Za-z]+\s+\d{1,2}\s*[-–|:]\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s+", " ", text).strip(" -|•…:;,")

    # Avoid navigation-only or empty summaries.
    meaningful = re.sub(r"[^A-Za-z0-9]", "", text)

    if len(meaningful) < 35:
        return "Read the original report for the latest details."

    if len(text) <= limit:
        return text

    shortened = text[:limit - 1]

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

    text = f"{title} {summary}".lower()

    # AI and technology.
    if re.search(
        r"\b(artificial intelligence|machine learning|robotics|"
        r"technology|algorithm|computer|cybersecurity|"
        r"quantum computing|semiconductor|microchip)\b",
        text
    ):
        return "AI & Technology"

    # Earth and environmental science.
    if re.search(
        r"\b(climate|weather|clouds?|fog|earth observatory|"
        r"ocean|wildfire|hurricane|atmosphere|ecosystem|"
        r"biodiversity|earth science|reservoir|flood|flooding|"
        r"river|rainfall|drought|water surge|water levels|"
        r"soil|forest|volcano|earthquake)\b",
        text
    ):
        return "Science"

    # Discoveries and scientific findings.
    if re.search(
        r"\b(discovery|discoveries|discovered|detection|detected|"
        r"evidence of|newly found|first ever|new finding|"
        r"newly discovered|exoplanet|signs of life|"
        r"mysterious outburst)\b",
        text
    ):
        return "Discoveries"

    # Space missions, astronomy and exploration.
    if re.search(
        r"\b(space|mars|moon|lunar|planet|saturn|jupiter|"
        r"astronaut|rocket|rover|telescope|galaxy|galaxies|"
        r"star|stars|orbit|chandra|perseverance|curiosity|"
        r"voyager|satellite|nebula|cosmos|ngc)\b",
        text
    ):
        return "Space"

    if fallback in VALID_CATEGORIES:
        return fallback

    return "Science"


def get_text(item, *names):

    for name in names:
        element = item.find(name)

        if element is not None:
            if element.text:
                return element.text.strip()

            # Support RSS elements containing nested content.
            full_text = "".join(element.itertext()).strip()

            if full_text:
                return full_text

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

    def __init__(self):
        super().__init__()
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

        with urllib.request.urlopen(request, timeout=15) as response:

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

        # Alternative metadata patterns.
        patterns = [
            r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                page_html,
                re.IGNORECASE
            )

            if match:
                image_url = valid_image_url(
                    match.group(1),
                    page_url
                )

                if image_url:
                    return image_url

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

    # 2. Image embedded in RSS description.
    description = get_text(
        item,
        "description",
        "summary",
        "{*}summary",
        "{*}content"
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

    # Protect existing news.json if all feeds fail.
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

    # Write safely using a temporary file.
    temporary_file = "news.json.tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            articles[:24],
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temporary_file,
        "news.json"
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
