import logging
from collections.abc import Mapping

import pandas as pd
import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

BEATPORT_TOP_100_URL = "https://www.beatport.com/top-100"
BEATPORT_TOP_100_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    )
}
BEATPORT_TOP_100_COLUMNS = ["rank", "title", "artists"]


def fetch_beatport_top_100_html(
    url: str = BEATPORT_TOP_100_URL,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20,
) -> str:
    response = requests.get(
        url,
        headers=dict(headers or BEATPORT_TOP_100_HEADERS),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def parse_beatport_top_100(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")

    songs = []
    rows = soup.select('[data-testid="tracks-table-row"]')

    for row in rows:
        rank_el = row.select_one(".controls div[class*='TrackNo']")
        title_el = row.select_one(".cell.title a[href^='/track/']")
        artist_els = row.select(".ArtistNames-sc-72a97679-0 a")

        rank = rank_el.get_text(strip=True) if rank_el else None
        title = title_el.get("title") if title_el else None
        artists = [artist.get_text(strip=True) for artist in artist_els]

        songs.append(
            {
                "rank": rank,
                "title": title,
                "artists": ", ".join(artists),
            }
        )

    return pd.DataFrame(songs, columns=BEATPORT_TOP_100_COLUMNS)


def fetch_beatport_top_100_dataframe(
    url: str = BEATPORT_TOP_100_URL,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20,
) -> pd.DataFrame:
    html = fetch_beatport_top_100_html(url=url, headers=headers, timeout=timeout)
    dataframe = parse_beatport_top_100(html)
    if dataframe.empty:
        logger.warning("No Beatport Top 100 tracks were extracted from %s", url)
    return dataframe
