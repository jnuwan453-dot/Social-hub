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


# --------------------------------------------------
# FEED CONFIGURATION
# --------------------------------------------------

FEEDS = [
    {
        "name": "NASA",
        "urls": [
            "https://www.nasa.gov/feed/",
        ],
        "category": "Space",
    },
    {
        "name": "ESA",
        "urls": [
            "https://www.esa.int/rssfeed/Science_Exploration/Space_Science",
            "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        ],
        "category": "Science",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheCivicBriefNewsBot/1.0)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, /",
}

VALID_CATEGORIES = {
    "Space",
    "Science",
    "Discoveries",
    "AI & Technology",
    "World News",
}

OUTPUT_FILE = "news.json"
TEMP_FILE = "news.json.tmp"
MAX_ARTICLES = 24


# --------------------------------------------------
# HTML TEXT EXTRACTION
# --------------------------------------------------

class VisibleTextParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "svg"}

    def handle_starttag(self, tag, attrs):

        tag = tag.lower()

        if tag in self.skip_tags:
            self.skip_depth += 1

        elif not self.skip_depth and tag in (
            "p", "div", "br", "li",
            "h1", "h2", "h3", "section"
        ):
            self.parts.append(" ")

    def handle_endtag(self, tag):

        tag = tag.lower()

        if tag in self.skip_tags:

            if self.skip_depth:
                self.skip_depth -= 1

        elif not self.skip_depth and tag in (
            "p", "div", "br", "li",
            "h1", "h2", "h3", "section"
        ):
            self.parts.append(" ")

    def handle_data(self, data):

        if not self.skip_depth:
            self.parts.append(data)


def clean_text(value):

    if not value:
        return ""

    parser = VisibleTextParser()

    try:
        parser.feed(str(value))
        text = " ".join(parser.parts)

    except Exception:
        text = re.sub(r"<[^>]+>", " ", str(value))

    text = html.unescape(text)

    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------
# NASA APOD AND GENERAL SUMMARY CLEANING
# --------------------------------------------------

def clean_summary(value, title="", limit=230):

    text = clean_text(value)

    if not text:
        return "Read the original report for the latest details."

    # Remove NASA APOD navigation and archive labels.
    boilerplate_patterns = [

        r"Today's\s+APOD\s+Archive",
        r"Today’s\s+APOD\s+Archive",
        r"Today's\s+Archive",
        r"Today’s\s+Archive",

        r"Submissions\s+Index",

        r"Search\s+Calendar\s+RSS\s+Education\s+About\s+Discuss",

        r"Astronomy\s+Picture\s+of\s+the\s+Day",

        r"Discover\s+the\s+cosmos!?",

        r"Each day a different image or photograph "
        r"of our fascinating universe is featured, "
        r"along with a brief explanation written by "
        r"a professional astronomer\.?",

        r"Read this release in English here\.?",
        r"Read more\.?",
        r"Continue reading\.?",
        r"Click here\.?",
        r"Share this story\.?",
        r"Skip to main content",
        r"Menu\s+Search",
        r"Navigation",
    ]

    for pattern in boilerplate_patterns:

        text = re.sub(
            pattern,
            " ",
            text,
            flags=re.IGNORECASE
        )

    # Remove navigation words and menu sequences.
    text = re.sub(
        r"\b(?:Archive|Submissions Index|Search|Calendar|"
        r"Education|Discuss|Navigation)\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    # Remove author/byline prefixes.
    text = re.sub(
        r"^(?:By|Written by|Author:?|Posted by)\s+"
        r"[^.]{2,120}(?:\.\s*|\s+)(?=[A-Z])",
        "",
        text,
        flags=re.IGNORECASE
    )

    # Remove date/category prefixes.
    text = re.sub(
        r"^\s*(?:Science|NASA|Space)\s*[-–|:]\s*"
        r"\d{4}\s+[A-Za-z]+\s+\d{1,2}\s*[-–|:]\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    # Remove APOD prefix.
    text = re.sub(
        r"^\s*APOD\s*[:\-–|]\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    title_variants = [
            title_clean,
            re.sub(
                r"^APOD\s*[:\-–|]\s*",
                "",
                title_clean,
                flags=re.IGNORECASE
            ),
        ]

        # Remove APOD date prefix from title variants too.
        for variant in list(title_variants):

            variant = re.sub(
                r"^\d{4}\s+[A-Za-z]+\s+\d{1,2}\s*[-–|:]\s*",
                "",
                variant,
                flags=re.IGNORECASE
            )

            if variant:
                title_variants.append(variant)

        for title_variant in title_variants:

            if not title_variant:
                continue

            if text.lower().startswith(title_variant.lower()):

                text = text[len(title_variant):].lstrip(
                    " -–|:;,."
                )
                break

    # Remove remaining placeholder-only fragments.
    text = re.sub(
        r"\[\s*(?:\.\.\.|…)?\s*\]",
        " ",
        text
    )

    # Remove standalone ellipsis.
    text = re.sub(r"(?:\.\.\.|…)+", " ", text)

    # Clean repeated punctuation and whitespace.
    text = re.sub(r"\s+", " ", text)

    text = text.strip(" -–|•…:;,. ")

    # Reject navigation-only or meaningless summaries.
    meaningful = re.sub(
        r"[^A-Za-z0-9]",
        "",
        text
    )

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

        shortened = (
            shortened.rsplit(" ", 1)[0]
            .rstrip(" ,;:-")
            + "…"
        )

    return shortened


# --------------------------------------------------
# CATEGORY CLASSIFICATION
# --------------------------------------------------

def classify_category(title, summary, fallback="Space"):

    text = f"{title} {summary}".lower()

    if re.search(
        r"\b(artificial intelligence|machine learning|"
        r"robotics|technology|algorithm|computer|"
        r"cybersecurity|quantum computing|"
        r"semiconductor|microchip)\b",
        text
    ):
        return "AI & Technology"

    if re.search(
        r"\b(climate|weather|clouds?|fog|earth observatory|"
        r"ocean|wildfire|hurricane|atmosphere|ecosystem|"
        r"biodiversity|earth science|reservoir|flood|"
        r"flooding|river|rainfall|drought|water surge|"
        r"water levels|soil|forest|volcano|earthquake)\b",
        text
    ):
        return "Science"

    if re.search(
        r"\b(discovery|discoveries|discovered|detection|"
        r"detected|evidence of|newly found|first ever|"
        r"new finding|newly discovered|exoplanet|"
        r"signs of life|mysterious outburst)\b",
        text
    ):
        return "Discoveries"

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


# --------------------------------------------------
# RSS / ATOM TEXT EXTRACTION
# --------------------------------------------------

def get_text(item, *names):

    for name in names:

        try:
            element = item.find(name)

        except Exception:
            element = None

        if element is not None:

            if element.text and element.text.strip():
                return element.text.strip()

            full_text = "".join(
                element.itertext()
            ).strip()

            if full_text:
                return full_text

    return ""


def get_best_summary(item, title):

    candidates = []

    names = [
        "description",
        "summary",
        "{*}summary",
        "{*}encoded",
        "{*}content",
    ]

    for name in names:

        value = get_text(item, name)

        if value and value not in candidates:
            candidates.append(value)

    if not candidates:
        return clean_summary("", title)

    cleaned = [
        clean_summary(value, title)
        for value in candidates
    ]

    # Prefer the most informative usable summary.
    usable = [
        value for value in cleaned
        if value != "Read the original report for the latest details."
    ]

    if usable:
        return max(usable, key=len)

    return cleaned[0]


# --------------------------------------------------
# URL AND IMAGE HELPERS
# --------------------------------------------------

def valid_image_url(url, base_url=""):

    if not url:
        return ""

    url = html.unescape(url.strip())
    url = urljoin(base_url, url)

    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
    ):
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
            data.get("property", "")
            or data.get("name", "")
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

        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if "html" not in content_type:
                return ""

            page_url = response.geturl()

            page_html = response.read(
                1000000
            ).decode(
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

        patterns = [
            r'<link[^>]+rel=["\']image_src["\']'
            r'[^>]+href=["\']([^"\']+)',

            r'<meta[^>]+content=["\']([^"\']+)'
            r'["\'][^>]+property=["\']og:image["\']',
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

        print(
            f"Could not get article image: {error}"
        )

    return ""


def get_image(item, article_url):

    # 1. RSS media and enclosure fields.
    for element in item.iter():

        if not isinstance(element.tag, str):
            continue

        tag = element.tag.lower()

        if (
            tag.endswith("thumbnail")
            or tag.endswith("content")
            or tag.endswith("enclosure")
        ):

            image_url = valid_image_url(
                element.attrib.get("url", "")
                or element.attrib.get("href", ""),
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
        "{*}encoded"
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


# --------------------------------------------------
# DATE HANDLING
# --------------------------------------------------

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


# --------------------------------------------------
# RSS / ATOM LINK EXTRACTION
# --------------------------------------------------

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

        return (
            link_element.attrib.get("href", "")
            or link_element.attrib.get("url", "")
        )

    return ""


# --------------------------------------------------
# FEED FETCHING WITH FALLBACK
# --------------------------------------------------

def fetch_feed(feed):

    last_error = None

    for feed_url in feed["urls"]:

        try:

            print(
                f"Trying {feed['name']} feed: {feed_url}"
            )

            request = urllib.request.Request(
                feed_url,
                headers=HEADERS
            )

            with urllib.request.urlopen(
                request,
                timeout=25
            ) as response:

                content = response.read()

            root = ET.fromstring(content)

            items = root.findall(".//item")

            if not items:
                items = root.findall(".//{*}entry")

            if not items:

                raise ValueError(
                    "Feed returned no RSS or Atom entries."
                )

            print(
                f"{feed['name']} feed connected successfully."
            )

            return items, feed_url

        except Exception as error:

            last_error = error

            print(
                f"{feed['name']} endpoint failed: {error}"
            )

    raise RuntimeError(
        f"All {feed['name']} feed endpoints failed: {last_error}"
    )


# --------------------------------------------------
# EXISTING NEWS PRESERVATION
# --------------------------------------------------

def load_existing_news():

    if not os.path.exists(OUTPUT_FILE):
        return []

    try:

        with open(
            OUTPUT_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):

            print(
                f"Loaded {len(data)} existing news stories."
            )

            return data

    except Exception as error:

        print(
            f"Could not read existing news.json: {error}"
        )

    return []


# --------------------------------------------------
# MAIN NEWS UPDATE
# --------------------------------------------------

def main():

    articles = []
    seen_links = set()

    successful_sources = set()
    failed_sources = set()

    existing_articles = load_existing_news()

    configured_sources = {
        feed["name"]
        for feed in FEEDS
    }

    for feed in FEEDS:

        source_name = feed["name"]

        try:

            print(
                f"\nReading {source_name} feed..."
            )

            items, actual_feed_url = fetch_feed(feed)

            source_count = 0

            for item in items[:12]:

                title = clean_text(
                    get_text(
                        item,
                        "title",
                        "{*}title"
                    )
                )

                raw_link = get_item_link(item)

                link = urljoin(
                    actual_feed_url,
                    raw_link
                )

                if (
                    not title
                    or not link
                    or link in seen_links
                ):
                    continue

                parsed_link = urlparse(link)

                if parsed_link.scheme not in (
                    "http", "https"
                ):
                    continue

                seen_links.add(link)

                summary = get_best_summary(
                    item,
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

                date = parse_published(
                    published
                )

                print(
                    f"Getting image: {title[:60]}"
                )

                image_url = get_image(
                    item,
                    link
                )

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
                    "source": source_name,
                    "category": category,
                    "published": date,

                })

                source_count += 1

            if source_count == 0:

                raise RuntimeError(
                    f"{source_name} returned no usable articles."
                )

            successful_sources.add(
                source_name
            )

            print(
                f"{source_name}: {source_count} articles collected."
            )

        except Exception as error:

            failed_sources.add(
                source_name
            )

            print(
                f"Could not update {source_name}: {error}"
            )

    # If all feeds fail, never replace the existing file.
    if not successful_sources:

        print(
            "\nAll feeds failed. Existing news.json will be preserved."
        )

        return

    # Preserve old stories belonging to failed feeds.
    # Also preserve stories from other existing sources.
    preserved_count = 0

    for old_article in existing_articles:

        if not isinstance(old_article, dict):
            continue

        old_source = old_article.get(
            "source", ""
        )

        old_link = old_article.get(
            "url", ""
        )

        should_preserve = (
            old_source in failed_sources
            or old_source not in configured_sources
        )

        if (
            should_preserve
            and old_link
            and old_link not in seen_links
        ):

            articles.append(
                old_article
            )

            seen_links.add(
                old_link
            )

            preserved_count += 1

    print(
        f"Preserved {preserved_count} existing stories."
    )

    # Sort by publication date.
    articles.sort(
        key=lambda article: article.get(
            "published", ""
        ),
        reverse=True
    )

    final_articles = articles[:MAX_ARTICLES]

    # Write safely using a temporary file.
    try:

        with open(
            TEMP_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                final_articles,
                file,
                ensure_ascii=False,
                indent=2
            )

        # Atomic replacement.
        os.replace(
            TEMP_FILE,
            OUTPUT_FILE
        )

    except Exception as error:

        if os.path.exists(TEMP_FILE):

            try:
                os.remove(TEMP_FILE)
            except OSError:
                pass

        raise RuntimeError(
            f"Could not safely update news.json: {error}"
        )

    image_count = sum(
        1
        for article in final_articles
        if article.get("image")
    )

    print(
        f"\nSaved {len(final_articles)} news stories."
    )

    print(
        f"Images found: {image_count}"
    )

    print(
        f"Successful feeds: {', '.join(sorted(successful_sources))}"
    )

    if failed_sources:

        print(
            f"Feeds requiring attention: {', '.join(sorted(failed_sources))}"
        )


if __name__ == "__main__":
    main()
