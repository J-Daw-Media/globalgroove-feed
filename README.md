# Global Groove podcast feed

`globalgroove.xml` is updated from [SALTO's Global Groove program page](https://www.salto.nl/programma/global-groove/). The [GitHub Actions updater](.github/workflows/update-feed.yml) checks daily at 23:37 Europe/Amsterdam, including a retry on days when SALTO publishes an episode late. It can also be run from the repository's Actions tab.

The updater reads the newest SALTO episode page, checks its title, description, broadcast date, and MP3 URL, then compares the GUID and MP3 URL with the feed. If the episode is already at the top, it makes no change. For a new episode, it inserts one item, keeps historical XML items intact, validates the result, commits as J Daw Media, pushes to `main`, and verifies GitHub's raw XML. Missing or conflicting metadata stops the run before a feed change.

To check the safeguards locally, run `python -m unittest discover -s tests -v`, then `python scripts/update_feed.py` for a live no-change or update check.
