import unittest
from unittest.mock import patch

import pandas as pd

from app.enrichment.spotify import SpotifyTrackCandidate
from app.enrichment.spotify_dataframe import enrich_track_dataframe_with_spotify_ids
from app.ingestion.beatport_top100 import (
    BEATPORT_TOP_100_HEADERS,
    parse_beatport_top_100,
)


class FakeSpotifyClient:
    def __init__(self, results: dict[str, list[SpotifyTrackCandidate]]):
        self.results = results
        self.calls: list[tuple[str, str | None, str | None, int]] = []

    def search_tracks(
        self,
        title: str,
        artist: str | None,
        album: str | None = "",
        limit: int = 3,
    ) -> list[SpotifyTrackCandidate]:
        self.calls.append((title, artist, album, limit))
        return self.results.get(title, [])[:limit]


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


def candidate(
    spotify_track_id: str,
    title: str,
    artist: str,
    album: str | None = "Album",
) -> SpotifyTrackCandidate:
    return SpotifyTrackCandidate(
        spotify_track_id=spotify_track_id,
        title=title,
        artist_names=(artist,),
        album_name=album,
        duration_ms=180_000,
        explicit=False,
        spotify_url=f"https://open.spotify.com/track/{spotify_track_id}",
        spotify_uri=f"spotify:track:{spotify_track_id}",
    )


class BeatportTop100Tests(unittest.TestCase):
    def test_parse_beatport_top_100_extracts_rank_title_and_artists(self) -> None:
        html = """
        <div data-testid="tracks-table-row">
          <div class="controls"><div class="TrackNo-sc-abc">1</div></div>
          <div class="cell title">
            <a href="/track/example/123" title="Example Song"></a>
          </div>
          <div class="ArtistNames-sc-72a97679-0">
            <a>Artist One</a><a>Artist Two</a>
          </div>
        </div>
        """

        dataframe = parse_beatport_top_100(html)

        self.assertEqual(dataframe.to_dict("records"), [
            {
                "rank": "1",
                "title": "Example Song",
                "artists": "Artist One, Artist Two",
            }
        ])

    def test_beatport_dataframe_uses_spotify_matching_without_database(self) -> None:
        beatport_df = pd.DataFrame(
            [{"rank": "1", "title": "One More Time", "artists": "Daft Punk"}]
        )
        client = FakeSpotifyClient(
            {
                "One More Time": [
                    candidate("other", "Around the World", "Daft Punk"),
                    candidate("exact", "One More Time", "Daft Punk"),
                ]
            }
        )

        enriched = enrich_track_dataframe_with_spotify_ids(
            beatport_df,
            client,
            minimum_match_score=0.75,
        )

        self.assertEqual(client.calls, [("One More Time", "Daft Punk", "", 3)])
        self.assertEqual(enriched.columns.tolist(), [
            "spotify_track_id",
            "spotify_match_score",
            "spotify_title",
            "spotify_artists",
            "spotify_album",
            "spotify_lookup_error",
        ])
        self.assertEqual(enriched.loc[0, "spotify_track_id"], "exact")
        self.assertEqual(enriched.loc[0, "spotify_match_score"], 1.0)
        self.assertEqual(enriched.loc[0, "spotify_title"], "One More Time")
        self.assertEqual(enriched.loc[0, "spotify_artists"], "Daft Punk")
        self.assertEqual(enriched.loc[0, "spotify_album"], "Album")

    def test_fetch_uses_supplied_user_agent_headers(self) -> None:
        from app.ingestion.beatport_top100 import fetch_beatport_top_100_html

        with patch("app.ingestion.beatport_top100.requests.get") as get:
            get.return_value = FakeResponse("<html></html>")

            html = fetch_beatport_top_100_html()

        self.assertEqual(html, "<html></html>")
        self.assertEqual(get.call_args.kwargs["headers"], BEATPORT_TOP_100_HEADERS)
        self.assertEqual(get.call_args.kwargs["timeout"], 20)


if __name__ == "__main__":
    unittest.main()
