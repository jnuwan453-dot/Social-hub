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
        "url": "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "category": "Science",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheCivicBriefNewsBot/1.0)"
}


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return html.unescape(" ".join(value.split()))


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

    if parsed.scheme not in ("http", "https"):
        return ""

    if not parsed.netloc:
        return ""

    return url


class ImageMetaParser(HTMLParser):
    def _init_(self):
        super()._init_()
        self.image = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return

        data = {key.lower(): value for key, value in attrs if key}

        property_name = (
            data.get("property", "") or
            data.get("name", "")
        ).lower()

        if property_name in (
            "og:image",
            "og:image:url",
            "twitter:image",
            "twitter:image:src",
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

        with urllib.request.urlopen(
            request,
            timeout=12
        ) as response:

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if "html" not in content_type:
                return ""

            page_url = response.geturl()

            page_html = response.read(1000000).decode(
                "utf-8", errors="replace"
            )

        parser = ImageMetaParser()
        parser.feed(page_html)

        image_url = valid_image_url(
            parser.image,
            page_url
        )

        if image_url:
            return image_url

        # Try rel=image_src if Open Graph metadata is missing.
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
    # 1. First try RSS image fields.
    for element in item.iter():

        tag = element.tag.lower()

        if (
            tag.endswith("}thumbnail") or
            tag.endswith("}content") or
            tag.endswith("}enclosure")
        ):

            url = element.attrib.get("url", "")

            image_url = valid_image_url(
                url,
                article_url
            )

            if image_url:
                return image_url

    # 2. Try an image inside RSS description.
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

    # 3. Visit the article page and get its real image.
    return get_page_image(article_url)


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

                link = get_text(
                    item,
                    "link",
                    "{*}link"
                )

                if not link:
                    link_element = item.find("{*}link")

                    if link_element is not None:
                        link = link_element.attrib.get(
                            "href", ""
                        )

                if not title or not link:
                    continue

                if link in seen_links:
                    continue

                seen_links.add(link)

                summary = clean_text(
                    get_text(
                        item,
                        "description",
                        "summary",
                        "{*}summary"
                    )
                )

                published = get_text(
                    item,
                    "pubDate",
                    "published",
                    "{*}published",
                    "updated",
                    "{*}updated"
                )

                try:
                    date = parsedate_to_datetime(
                        published
                    ).isoformat()

                except Exception:
                    try:
                        date = datetime.fromisoformat(
                            published.replace(
                                "Z", "+00:00"
                            )
                        ).isoformat()

                    except Exception:
                        date = datetime.now(
                            timezone.utc
                        ).isoformat()

                print(f"Getting image: {title[:60]}")

                image_url = get_image(
                    item,
                    link
                )

                if image_url:
                    print("Image found.")
                else:
                    print("No image found for this article.")

                articles.append({
                    "title": title,
                    "summary": summary[:500],
                    "url": link,
                    "image": image_url,
                    "source": feed["name"],
                    "category": feed["category"],
                    "published": date,
                })

        except Exception as error:
            print(
                f"Could not read {feed['name']} feed: {error}"
            )

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
        1 for article in articles[:24]
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
