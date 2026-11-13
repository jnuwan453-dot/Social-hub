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
        "name": "NASA APOD",
        "source": "NASA",
        "urls": ["https://science.nasa.gov/feed/apod-basic/"],
        "category": "Space",
    },
    {
        "name": "NASA",
        "source": "NASA",
        "urls": ["https://www.nasa.gov/feed/"],
        "category": "Space",
    },
    {
        "name": "ESA",
        "source": "ESA",
        "urls": [
            "https://www.esa.int/rss/science.rss",
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
MAX_ITEMS_PER_FEED = 12
FALLBACK_SUMMARY = "Read the original report for the latest details."


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
# SUMMARY CLEANING HELPERS
# --------------------------------------------------

def remove_image_credits(text):

    # Remove credit blocks at the beginning of a summary.
    # Example:
    # X-ray: NASA/...; Optical: SDSS; Image Processing: NASA/...
    # Using NASA's Chandra X-ray Observatory...

    credit_labels = (
        r"(?:X-ray|Optical|Infrared|Radio|"
        r"Ultraviolet|Composite|Illustration|"
        r"Image Processing|Image Credit|"
        r"Photo Credit|Credits?)"
    )

    if re.match(
        r"^\s*" + credit_labels + r"\s*:",
        text,
        re.IGNORECASE,
    ):

        narrative_starters = [
            r"\bUsing\s+",
            r"\bThe\s+",
            r"\bA\s+",
            r"\bAn\s+",
            r"\bAstronomers\s+",
            r"\bScientists\s+",
            r"\bResearchers\s+",
            r"\bNASA\s+",
            r"\bESA\s+",
            r"\bThis\s+",
            r"\bThese\s+",
            r"\bObservations\s+",
        ]

        positions = []

        for pattern in narrative_starters:

            match = re.search(
                pattern,
                text,
                re.IGNORECASE,
            )

            if match:
                positions.append(match.start())

        if positions:

            first_position = min(positions)

            if first_position > 10:
                text = text[first_position:]

    # Remove photographer credits such as:
    # NASA/Amber Jean Notvest The U.S. Air Force...

    text = re.sub(
        r"^\s*NASA\s*/\s*"
        r"[A-Za-z][A-Za-z.'-]*"
        r"(?:\s+[A-Za-z][A-Za-z.'-]*){1,3}"
        r"\s+(?=(?:The|A|An|This|These|Using|"
        r"Astronomers|Scientists|NASA|ESA)\b)",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove inline credits such as:
    # Credits: NASA/Carla Thomas As a new exhibit opens...

    text = re.sub(
        r"\b(?:Image\s+)?Credits?\s*:\s*"
        r"NASA\s*/\s*"
        r"[A-Za-z][A-Za-z.'-]*"
        r"(?:\s+[A-Za-z][A-Za-z.'-]*){0,3}"
        r"\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    # Remove standalone NASA photographer prefix.
    text = re.sub(
        r"^\s*NASA\s*/\s*"
        r"[A-Za-z][A-Za-z.'-]*"
        r"(?:\s+[A-Za-z][A-Za-z.'-]*){1,3}"
        r"\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------
# SUMMARY CLEANING
# --------------------------------------------------

def clean_summary(value, title="", limit=230):

    text = clean_text(value)

    if not text:
        return FALLBACK_SUMMARY

    navigation_markers = [
        r"The History of Hubble in Space",
        r"Hubble by the Numbers",
        r"At the Museum",
        r"FAQs",
        r"Impact & Benefits",
        r"Science Impacts",
        r"Cultural Impact",
        r"Technology Benefits",
        r"Earth EO Explorer",
        r"All Atmosphere",
        r"Heat & Radiation",
        r"Life on Earth",
        r"Human Dimensions",
        r"Natural Events",
        r"Remote Sensing",
        r"Snow & Ice",
        r"More Content",
        r"Global Maps",
        r"Curiosity Instruments",
        r"Exploration Goals",
        r"News and Features",
        r"More Resources",
        r"Mars Missions",
        r"Mars Perseverance Rover",
        r"Mars Curiosity Rover",
        r"Mars Reconnaissance Orbiter",
    ]

    navigation_count = sum(
        1
        for pattern in navigation_markers
        if re.search(pattern, text, re.IGNORECASE)
    )

    if navigation_count >= 2:
        return FALLBACK_SUMMARY

    # Remove reading-time labels.
    text = re.sub(
        r"^\s*\d+\s+Min(?:ute)?s?\s+Read\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove APOD date prefixes.
    text = re.sub(
        r"^\s*\d{4}\s+[A-Za-z]+\s+\d{1,2}\s*[-–|:]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove repeated article title.
    if title:

        title_clean = clean_text(title)

        if title_clean and text.lower().startswith(
            title_clean.lower()
        ):

            text = text[len(title_clean):].lstrip(
                " -–|:;,."
            )

    # Remove unrelated NASA headline.
    text = re.sub(
        r"^\s*Shuttle Endeavour’s Last Flight Was 10 Years Ago\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove APOD credit labels.
    text = re.sub(
        r"\bImage\s+Credit\s*(?:&|and)\s*Explanation\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\bExplanation\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove image credits and photographer prefixes.
    text = remove_image_credits(text)

    boilerplate_patterns = [
        r"Today's\s+APOD\s+Archive",
        r"Today’s\s+APOD\s+Archive",
        r"Today's\s+Archive",
        r"Today’s\s+Archive",
        r"Astronomy\s+Picture\s+of\s+the\s+Day",
        r"Discover\s+the\s+cosmos!?",
        r"Each day a different image or photograph of our fascinating universe is featured, along with a brief explanation written by a professional astronomer\.?",
        r"Earth Observatory",
        r"Image of the Day",
        r"Topics",
        r"All Topics",
        r"Mission Overview",
        r"Science Overview",
        r"Mission Updates",
        r"Mission News",
        r"Multimedia",
        r"Videos",
        r"Audio",
        r"Raw Images",
        r"More Information",
        r"Where is Curiosity\?",
        r"Skip to main content",
        r"Menu\s+Search",
        r"Navigation",
        r"Submissions Index",
        r"Search Calendar RSS Education About Discuss",
        r"Read this release in English here\.?",
        r"Read more\.?",
        r"Continue reading\.?",
        r"Click here\.?",
        r"Share this story\.?",
    ]

    for pattern in boilerplate_patterns:

        text = re.sub(
            pattern,
            " ",
            text,
            flags=re.IGNORECASE,
        )

    # Remove copyright information.
    text = re.sub(
        r"Copyright\s*[:©].*?(?=Explanation|Image Credit|$)",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"©\s*\d{4}[^.]*\.?",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    # Remove author prefixes.
    text = re.sub(
        r"^(?:By|Written by|Author:?|Posted by)\s+"
        r"[^.]{2,120}(?:\.\s*|\s+)(?=[A-Z])",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove date and category prefixes.
    text = re.sub(
        r"^\s*(?:Science|NASA|Space)\s*[-–|:]\s*"
        r"\d{4}\s+[A-Za-z]+\s+\d{1,2}\s*[-–|:]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove APOD prefix.
    text = re.sub(
        r"^\s*APOD\s*[:\-–|]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove placeholder fragments and ellipses.
    text = re.sub(
        r"\[\s*(?:\.\.\.|…)?\s*\]",
        " ",
        text,
    )

    text = re.sub(
        r"(?:\.\.\.|…)+",
        " ",
        text,
    )

    # Clean whitespace.
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -–|•…:;,. ")

    meaningful = re.sub(
        r"[^A-Za-z0-9]",
        "",
        text,
    )

    if len(meaningful) < 35:
        return FALLBACK_SUMMARY

    # Limit summary length.
    if len(text) <= limit:
        return text

    shortened = text[:limit - 1]

    sentence_end = max(
        shortened.rfind(". "),
        shortened.rfind("! "),
        shortened.rfind("? "),
    )

    if sentence_end >= int(limit * 0.55):

        shortened = shortened[:sentence_end + 1]

    else:

        shortened = (
            shortened.rsplit(" ", 1)[0]
            .rstrip(" ,;:-")
            + "…"
        )

    shortened = re.sub(
        r"\s+(?:on\s+)?(?:Sunday,?\s+)?Nov\.$",
        "",
        shortened,
        flags=re.IGNORECASE,
    )

    shortened = re.sub(
        r"\s+see linked…?$",
        "",
        shortened,
        flags=re.IGNORECASE,
    )

    shortened = re.sub(
        r"\s+([.,!?;:])",
        r"\1",
        shortened,
    )

    shortened = re.sub(
        r"\s+",
        " ",
        shortened,
    ).strip()

    if shortened:
        shortened = shortened[0].upper() + shortened[1:]

    return shortened or FALLBACK_SUMMARY


# --------------------------------------------------
# CATEGORY CLASSIFICATION
# --------------------------------------------------

def classify_category(title, summary, fallback="Space"):

    text = f"{title} {summary}".lower()

    # AI and technology.
    if re.search(
        r"\b(artificial intelligence|machine learning|robotics|"
        r"technology|algorithm|computer|cybersecurity|"
        r"quantum computing|semiconductor|microchip|"
        r"generative ai|software)\b",
        text,
    ):
        return "AI & Technology"

    # Space and astronomy take priority over general discovery terms.
    # This prevents galaxy and astronomy stories being misclassified.
    if re.search(
        r"\b(space|mars|moon|lunar|planet|saturn|jupiter|"
        r"astronaut|rocket|rover|telescope|galaxy|galaxies|"
        r"star|stars|orbit|chandra|perseverance|curiosity|"
        r"voyager|satellite|nebula|cosmos|ngc|hubble|"
        r"bepicolombo|euclid|solar orbiter|webb|endeavour|"
        r"andromeda|exoplanet|supernova|black hole|"
        r"stellar|constellation|asteroid|comet)\b",
        text,
    ):
        return "Space"

    # Discoveries and scientific breakthroughs.
    if re.search(
        r"\b(discovery|discoveries|discovered|detection|"
        r"detected|evidence of|newly found|first ever|"
        r"new finding|newly discovered|signs of life|"
        r"breakthrough|scientists reveal)\b",
        text,
    ):
        return "Discoveries"

    # Earth and environmental science.
    if re.search(
        r"\b(climate|weather|clouds?|fog|earth observatory|"
        r"ocean|wildfire|hurricane|atmosphere|ecosystem|"
        r"biodiversity|earth science|reservoir|flood|"
        r"flooding|river|rainfall|drought|water surge|"
        r"water levels|soil|forest|volcano|earthquake|"
        r"flow country|peatland|wetland|habitat|ecology|"
        r"environment|environmental|pollution)\b",
        text,
    ):
        return "Science"

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
        return FALLBACK_SUMMARY

    cleaned = [
        clean_summary(value, title)
        for value in candidates
    ]

    usable = [
        value
        for value in cleaned
        if value != FALLBACK_SUMMARY
    ]

    if usable:
        return max(usable, key=len)

    return FALLBACK_SUMMARY


# --------------------------------------------------
# URL AND IMAGE HELPERS
# --------------------------------------------------

def valid_image_url(url, base_url=""):

    if not url:
        return ""

    url = urljoin(
        base_url,
        html.unescape(url.strip()),
    )

    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
    ):
        return ""

    if (
        "youtube.com" in url.lower()
        or "youtu.be" in url.lower()
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
            "twitter:image:src",
        ):

            content = data.get("content", "")

            if content and not self.image:
                self.image = content


def get_page_image(article_url):

    try:

        request = urllib.request.Request(
            article_url,
            headers=HEADERS,
        )

        with urllib.request.urlopen(
            request,
            timeout=15,
        ) as response:

            content_type = response.headers.get(
                "Content-Type",
                "",
            ).lower()

            if "html" not in content_type:
                return ""

            page_url = response.geturl()

            page_html = response.read(
                1000000
            ).decode(
                "utf-8",
                errors="replace",
            )

        parser = ImageMetaParser()
        parser.feed(page_html)

        image_url = valid_image_url(
            parser.image,
            page_url,
        )

        if image_url:
            return image_url

        patterns = [
            r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                page_html,
                re.IGNORECASE,
            )

            if match:

                image_url = valid_image_url(
                    match.group(1),
                    page_url,
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
                article_url,
            )

            if image_url:
                return image_url

    # 2. Image embedded in RSS description.
    description = get_text(
        item,
        "description",
        "summary",
        "{*}summary",
        "{*}encoded",
    )

    match = re.search(
        r'<img[^>]+src=["\']([^"\']+)',
        description,
        re.IGNORECASE,
    )

    if match:

        image_url = valid_image_url(
            match.group(1),
            article_url,
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

    link_element = item.find("{*}link")

    if link_element is not None:

        href = (
            link_element.attrib.get("href", "")
            or link_element.attrib.get("url", "")
        )

        if href:
            return href

        if link_element.text:
            return link_element.text.strip()

    return get_text(
        item,
        "link",
    )


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
                headers=HEADERS,
            )

            with urllib.request.urlopen(
                request,
                timeout=25,
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
            encoding="utf-8",
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


def refresh_preserved_article(article):

    # Preserve existing fields and image.
    updated = dict(article)

    title = updated.get("title", "")
    summary = updated.get("summary", "")

    # Clean existing summaries too, not just new feed summaries.
    cleaned_summary = clean_summary(
        summary,
        title,
    )

    if cleaned_summary != FALLBACK_SUMMARY:
        updated["summary"] = cleaned_summary

    # Reclassify old stories using the corrected category rules.
    updated["category"] = classify_category(
        title,
        updated.get("summary", ""),
        updated.get("category", "Science"),
    )

    if updated["category"] not in VALID_CATEGORIES:
        updated["category"] = "Science"

    return updated


# --------------------------------------------------
# MAIN NEWS UPDATE
# --------------------------------------------------

def main():

    articles = []
    seen_links = set()

    successful_feeds = set()
    failed_feeds = set()

    existing_articles = load_existing_news()

    # Keep old articles indexed by their original URL.
    old_by_link = {
        article.get("url"): article
        for article in existing_articles
        if isinstance(article, dict)
        and article.get("url")
    }

    for feed in FEEDS:

        feed_name = feed["name"]
        source_name = feed["source"]

        try:

            print(
                f"\nReading {feed_name} feed..."
            )

            items, actual_feed_url = fetch_feed(feed)

            source_count = 0

            for item in items[:MAX_ITEMS_PER_FEED]:

                title = clean_text(
                    get_text(
                        item,
                        "title",
                        "{*}title",
                    )
                )

                raw_link = get_item_link(item)

                link = urljoin(
                    actual_feed_url,
                    raw_link,
                )

                if (
                    not title
                    or not link
                    or link in seen_links
                ):
                    continue

                parsed_link = urlparse(link)

                if parsed_link.scheme not in (
                    "http",
                    "https",
                ):
                    continue

                summary = get_best_summary(
                    item,
                    title,
                )

                # Reuse old summary if the new feed has no useful text.
                old_article = old_by_link.get(
                    link,
                    {},
                )

                if (
                    summary == FALLBACK_SUMMARY
                    and old_article.get("summary")
                ):

                    summary = clean_summary(
                        old_article["summary"],
                        title,
                    )

                category = classify_category(
                    title,
                    summary,
                    feed["category"],
                )

                published = get_text(
                    item,
                    "pubDate",
                    "published",
                    "{*}published",
                    "updated",
                    "{*}updated",
                )

                print(
                    f"Getting image: {title[:60]}"
                )

                image_url = get_image(
                    item,
                    link,
                )

                # Preserve the previous image for this exact article URL
                # when the current feed does not provide an image.
                if not image_url:
                    image_url = old_article.get(
                        "image",
                        "",
                    )

                articles.append({
                    "title": title,
                    "summary": summary,
                    "url": link,
                    "image": image_url,
                    "source": source_name,
                    "category": category,
                    "published": parse_published(published),
                })

                seen_links.add(link)
                source_count += 1

                print(
                    "Image found."
                    if image_url
                    else "No image found for this article."
                )

            if source_count == 0:

                raise RuntimeError(
                    f"{feed_name} returned no usable articles."
                )

            successful_feeds.add(feed_name)

            print(
                f"{feed_name}: {source_count} articles collected."
            )

        except Exception as error:

            failed_feeds.add(feed_name)

            print(
                f"Could not update {feed_name}: {error}"
            )

    # If every feed fails, do not overwrite the existing news.json.
    if not successful_feeds:

        print(
            "\nAll feeds failed. Existing news.json will be preserved."
        )

        return

    # A source is considered partially failed if any of its configured
    # feeds failed. This protects old stories when one feed fails.
    failed_sources = {
        feed["source"]
        for feed in FEEDS
        if feed["name"] in failed_feeds
    }

    configured_sources = {
        feed["source"]
        for feed in FEEDS
    }

    # Preserve previous stories from sources with failed feeds.
    preserved_count = 0

    for old_article in existing_articles:

        if not isinstance(old_article, dict):
            continue

        old_source = old_article.get(
            "source",
            "",
        )

        old_link = old_article.get(
            "url",
            "",
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

            preserved_article = refresh_preserved_article(
                old_article
            )

            articles.append(preserved_article)
            seen_links.add(old_link)
            preserved_count += 1

    print(
        f"Preserved {preserved_count} existing stories."
    )

    # Sort by publication date.
    articles.sort(
        key=lambda article: article.get(
            "published",
            "",
        ),
        reverse=True,
    )

    final_articles = articles[:MAX_ARTICLES]

    # Write safely using a temporary file.
    try:

        with open(
            TEMP_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                final_articles,
                file,
                ensure_ascii=False,
                indent=2,
            )

        # Atomic replacement.
        os.replace(
            TEMP_FILE,
            OUTPUT_FILE,
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
        f"Successful feeds: {', '.join(sorted(successful_feeds))}"
    )

    if failed_feeds:

        print(
            "Feeds requiring attention: "
            f"{', '.join(sorted(failed_feeds))}"
        )


# --------------------------------------------------
# RUN SCRIPT
# --------------------------------------------------

if __name__ == "__main__":
    main()
