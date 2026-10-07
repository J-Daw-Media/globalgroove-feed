"""Update the Global Groove RSS feed from SALTO, preserving historical XML bytes."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROGRAM_URL = "https://www.salto.nl/programma/global-groove/"
RAW_URL = "https://raw.githubusercontent.com/J-Daw-Media/globalgroove-feed/main/globalgroove.xml"
FEED = Path(__file__).resolve().parents[1] / "globalgroove.xml"
ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
MEDIA = "http://search.yahoo.com/mrss/"
USER_AGENT = "GlobalGrooveFeedUpdater/1.0 (+https://github.com/J-Daw-Media/globalgroove-feed)"


class FeedUpdateError(Exception):
    """A source, feed, or validation failure that must stop the update."""


def fetch(url: str, *, method: str = "GET") -> bytes:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"}, method=method)
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise FeedUpdateError(f"{url}: HTTP {response.status}")
            return response.read()
    except FeedUpdateError:
        raise
    except Exception as exc:
        raise FeedUpdateError(f"Could not fetch {url}: {exc}") from exc


class EpisodeListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.episodes: list[tuple[str, str, date]] = []
        self.current: dict[str, str] | None = None
        self.field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").split()
        if tag == "a" and "episode-list-item" in classes:
            href = attrs_dict.get("href") or ""
            if re.fullmatch(r"/programma/global-groove/[A-Za-z0-9]+/?", href):
                self.current = {"href": href, "title": "", "date": ""}
                return
        if self.current is not None:
            if tag == "p" and "media-heading" in classes:
                self.field = "title"
            elif tag == "p" and "m-b-none" in classes:
                self.field = "date"

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "p":
            self.field = None
        if tag == "a":
            try:
                broadcast = datetime.strptime(self.current["date"].strip(), "%d-%m-%Y").date()
                title = self.current["title"].strip()
                if not title:
                    raise ValueError("empty title")
                self.episodes.append((self.current["href"], title, broadcast))
            except ValueError:
                pass
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.field is not None:
            self.current[self.field] += data


def latest_episode(listing: str) -> tuple[str, str, date]:
    parser = EpisodeListParser()
    parser.feed(listing)
    if not parser.episodes:
        raise FeedUpdateError("SALTO episode list has no complete episode link, title, and broadcast date")
    return max(parser.episodes, key=lambda episode: episode[2])


def required(pattern: str, page: str, field: str) -> str:
    match = re.search(pattern, page, re.I | re.S)
    if not match:
        raise FeedUpdateError(f"SALTO episode page is missing {field}")
    value = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    if not value:
        raise FeedUpdateError(f"SALTO episode page has empty {field}")
    return value


def episode_metadata(page: str, href: str, listed_title: str, broadcast: date) -> tuple[str, str, str, str, date]:
    title = required(r'<h3 class="m-t-none m-b-lg m-r">\s*(.*?)\s*</h3>', page, "title")
    description = required(
        r'<p class="programma-pagina-radio-omschrijving">\s*<p>(.*?)</p>\s*</p>',
        page,
        "description",
    )
    audio = required(r'<a id="downloadDropdown"[^>]*\bhref="([^"]+)"', page, "MP3 URL")
    if not re.fullmatch(r"https://vod\.salto\.nl/aod/\d{4}/\d{2}/\d{2}/[A-Za-z0-9]+_online\.mp3", audio):
        raise FeedUpdateError(f"Unexpected SALTO MP3 URL: {audio}")
    if listed_title != f"GLOBAL GROOVE - {title}":
        raise FeedUpdateError(f"SALTO listing/detail title mismatch: {listed_title!r} vs {title!r}")
    episode_id = href.rstrip("/").rsplit("/", 1)[-1]
    detail_date = required(
        rf'<a class="episode-list-item" href="{re.escape(href.rstrip("/"))}/?"[^>]*>.*?<p class="m-b-none">(\d{{2}}-\d{{2}}-\d{{4}})</p>',
        page,
        "broadcast date",
    )
    try:
        detail_broadcast = datetime.strptime(detail_date, "%d-%m-%Y").date()
    except ValueError as exc:
        raise FeedUpdateError(f"Invalid SALTO broadcast date: {detail_date}") from exc
    if detail_broadcast != broadcast:
        raise FeedUpdateError(f"SALTO listing/detail broadcast dates disagree: {broadcast} vs {detail_broadcast}")
    if f"/{episode_id}_online.mp3" not in audio:
        raise FeedUpdateError("SALTO episode link and MP3 identifier differ")
    episode_number = re.fullmatch(r"Aflevering (\d+): .+", title, re.I)
    if not episode_number:
        raise FeedUpdateError(f"Cannot derive episode GUID from title: {title}")
    try:
        today = datetime.now(ZoneInfo("Europe/Amsterdam")).date()
    except ZoneInfoNotFoundError:
        # Windows test hosts may lack IANA tzdata; the deployed Linux runner has it.
        today = datetime.now().astimezone().date()
    if broadcast > today:
        raise FeedUpdateError(f"SALTO episode is future-dated: {broadcast.isoformat()}")
    return title, description, audio, f"Aflevering-{episode_number.group(1)}", broadcast


def parse_feed(data: bytes) -> tuple[ET.Element, list[ET.Element]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedUpdateError(f"globalgroove.xml does not parse: {exc}") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedUpdateError("globalgroove.xml has no RSS channel")
    items = channel.findall("item")
    if not items:
        raise FeedUpdateError("globalgroove.xml has no historical items")
    return channel, items


def plan_update(feed: bytes, metadata: tuple[str, str, str, str, date]) -> bytes | None:
    title, description, audio, guid, broadcast = metadata
    channel, items = parse_feed(feed)
    guids = [item.findtext("guid") for item in items]
    audios = [item.find("enclosure").get("url") if item.find("enclosure") is not None else None for item in items]
    if guids[0] == guid and audios[0] == audio:
        if guids.count(guid) != 1 or audios.count(audio) != 1:
            raise FeedUpdateError("Newest SALTO GUID or MP3 URL is duplicated in the feed")
        return None
    if guid in guids or audio in audios:
        raise FeedUpdateError("Newest SALTO GUID or MP3 is already present with conflicting feed position/metadata")
    top_number = re.fullmatch(r"Aflevering-(\d+)", guids[0] or "")
    new_number = int(guid.split("-")[-1])
    if top_number is None or new_number <= int(top_number.group(1)):
        raise FeedUpdateError(f"SALTO episode {guid} is not newer than feed top {guids[0]}")
    if "]]>" in title or "]]>" in description:
        raise FeedUpdateError("SALTO title or description cannot be represented in CDATA")
    artwork = channel.find(f"{{{ITUNES}}}image")
    top_itunes = items[0].find(f"{{{ITUNES}}}image")
    top_content = items[0].find(f"{{{MEDIA}}}content")
    top_thumbnail = items[0].find(f"{{{MEDIA}}}thumbnail")
    if artwork is None or any(part is None for part in (top_itunes, top_content, top_thumbnail)):
        raise FeedUpdateError("Existing Global Groove artwork fields are missing")
    image_url = artwork.get("href")
    if not image_url or any(part.get("href" if part is top_itunes else "url") != image_url for part in (top_itunes, top_content, top_thumbnail)):
        raise FeedUpdateError("Existing Global Groove artwork fields disagree")
    newline = "\r\n" if b"\r\n" in feed[:1000] else "\n"
    match = re.search(rb"(?m)^[ \t]*<item>[ \t]*\r?\n", feed)
    if match is None:
        raise FeedUpdateError("Cannot locate the first XML item without rewriting history")
    indent = feed[match.start():match.start() + 4].decode("ascii")
    if indent != "    ":
        raise FeedUpdateError("Unexpected first-item indentation")
    block = newline.join(
        [
            "    <item>",
            f"  <title><![CDATA[{title}]]></title>",
            f"  <description><![CDATA[{description}]]></description>",
            f'  <enclosure url="{audio}" length="0" type="audio/mpeg"/>',
            f"  <pubDate>{broadcast:%m/%d/%y}</pubDate>",
            f"  <guid>{guid}</guid>",
            f'  <itunes:image href="{image_url}" />',
            f'  <media:content url="{image_url}" medium="image" />',
            f'  <media:thumbnail url="{image_url}" />',
            "</item>",
            "",
            "",
        ]
    ).encode("utf-8")
    updated = feed[:match.start()] + block + feed[match.start():]
    _, updated_items = parse_feed(updated)
    if len(updated_items) != len(items) + 1:
        raise FeedUpdateError("XML item count changed unexpectedly")
    if [ET.tostring(item) for item in updated_items[1:]] != [ET.tostring(item) for item in items]:
        raise FeedUpdateError("Historical XML items changed")
    if updated_items[0].findtext("guid") != guid or updated_items[0].find("enclosure").get("url") != audio:
        raise FeedUpdateError("New XML item does not match SALTO metadata")
    if updated.count(guid.encode()) != 1 or updated.count(audio.encode()) != 1:
        raise FeedUpdateError("New GUID or MP3 URL is not unique")
    return updated


def verify_remote() -> None:
    local = FEED.read_bytes()
    parse_feed(local)
    for attempt in range(6):
        remote = fetch(f"{RAW_URL}?verify={time.time_ns()}")
        if remote == local:
            print("GitHub raw XML matches the validated local feed")
            return
        if attempt < 5:
            time.sleep(5)
    raise FeedUpdateError("GitHub raw XML does not match the pushed feed after 6 checks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-remote", action="store_true", help="verify pushed raw XML matches this checkout")
    args = parser.parse_args()
    if args.verify_remote:
        verify_remote()
        return
    listing = fetch(PROGRAM_URL).decode("utf-8")
    href, listed_title, broadcast = latest_episode(listing)
    episode_url = urljoin(PROGRAM_URL, href)
    page = fetch(episode_url).decode("utf-8")
    metadata = episode_metadata(page, href, listed_title, broadcast)
    current = FEED.read_bytes()
    updated = plan_update(current, metadata)
    if updated is None:
        print(f"No new episode: {metadata[3]} already leads the feed with the same MP3 URL")
        return
    fetch(metadata[2], method="HEAD")
    temporary = FEED.with_suffix(".xml.tmp")
    temporary.write_bytes(updated)
    os.replace(temporary, FEED)
    print(f"Added {metadata[3]}: {metadata[0]} ({broadcast.isoformat()})")
    print(f"MP3: {metadata[2]}")
    print("XML valid; historical items unchanged; GUID and MP3 URL unique")


if __name__ == "__main__":
    try:
        main()
    except (FeedUpdateError, OSError, UnicodeError) as exc:
        print(f"Feed update blocked: {exc}", file=sys.stderr)
        sys.exit(1)
