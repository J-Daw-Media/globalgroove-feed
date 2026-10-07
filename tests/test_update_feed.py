import sys
from datetime import date, timedelta
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update_feed import FeedUpdateError, episode_metadata, latest_episode, plan_update


ART = "https://example.com/global-groove.jpg"
OLD_AUDIO = "https://vod.salto.nl/aod/2026/09/21/old_online.mp3"
NEW_AUDIO = "https://vod.salto.nl/aod/2026/10/06/newid_online.mp3"
OLD = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
 xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
 xmlns:media="http://search.yahoo.com/mrss/">
<channel>
<itunes:image href="{ART}" />

    <item>
  <title><![CDATA[Aflevering 59: Old]]></title>
  <description><![CDATA[Old description]]></description>
  <enclosure url="{OLD_AUDIO}" length="0" type="audio/mpeg"/>
  <pubDate>09/22/26</pubDate>
  <guid>Aflevering-59</guid>
  <itunes:image href="{ART}" />
  <media:content url="{ART}" medium="image" />
  <media:thumbnail url="{ART}" />
</item>
</channel>
</rss>
'''.encode()
META = ("Aflevering 60: New", "A new description – exactly.", NEW_AUDIO, "Aflevering-60", date(2026, 10, 6))


class FeedUpdaterTests(unittest.TestCase):
    def test_new_episode_adds_one_item_and_preserves_original_bytes(self):
        updated = plan_update(OLD, META)
        self.assertIsNotNone(updated)
        old_item = OLD[OLD.index(b"    <item>"):OLD.index(b"</item>") + len(b"</item>")]
        self.assertIn(old_item, updated)
        items = ET.fromstring(updated).find("channel").findall("item")
        self.assertEqual([item.findtext("guid") for item in items], ["Aflevering-60", "Aflevering-59"])
        self.assertEqual(items[0].find("enclosure").get("url"), NEW_AUDIO)
        self.assertEqual(items[0].findtext("description"), "A new description – exactly.")
        self.assertEqual(updated.count(NEW_AUDIO.encode()), 1)

    def test_already_published_episode_makes_no_change(self):
        updated = plan_update(OLD, META)
        self.assertIsNone(plan_update(updated, META))

    def test_conflicting_guid_or_audio_blocks_update(self):
        with self.assertRaises(FeedUpdateError):
            plan_update(OLD, (META[0], META[1], NEW_AUDIO, "Aflevering-59", META[4]))
        with self.assertRaises(FeedUpdateError):
            plan_update(OLD, (META[0], META[1], OLD_AUDIO, "Aflevering-60", META[4]))

    def test_salto_listing_and_metadata_must_be_complete(self):
        day = (date.today() - timedelta(days=1)).strftime("%d-%m-%Y")
        listing = f'''<a class="episode-list-item" href="/programma/global-groove/newid">
        <img src="x"><p class="media-heading">GLOBAL GROOVE - Aflevering 60: New</p>
        <p class="m-b-none">{day}</p></a>'''
        href, listed_title, broadcast = latest_episode(listing)
        self.assertEqual(href, "/programma/global-groove/newid")
        page = f'''<h3 class="m-t-none m-b-lg m-r">Aflevering 60: New</h3>
        <p class="programma-pagina-radio-omschrijving"><p>A new description &ndash; exactly.</p></p>
        <a id="downloadDropdown" download href="{NEW_AUDIO}">Download</a>
        <a class="episode-list-item" href="/programma/global-groove/newid"><p class="m-b-none">{day}</p></a>'''
        self.assertEqual(episode_metadata(page, href, listed_title, broadcast)[1], "A new description – exactly.")
        with self.assertRaises(FeedUpdateError):
            episode_metadata(page.replace("downloadDropdown", "other"), href, listed_title, broadcast)


if __name__ == "__main__":
    unittest.main()
