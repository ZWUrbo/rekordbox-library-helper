import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.connection import create_tables
from app.db.models import Base, RekordboxSpotifyMatch, RekordboxTrack, SpotifyTrack
from app.enrichment.spotify import (
    RateLimiter,
    SpotifyAPIError,
    SpotifyClient,
    SpotifyEnrichmentService,
    SpotifyTrackCandidate,
    build_spotify_search_query,
    calculate_match_score,
    prepare_artist_search_name,
    prepare_track_search_title,
)


def candidate(
    spotify_track_id: str,
    title: str,
    artist: str,
    album: str | None = "Album",
    duration_ms: int = 180_000,
) -> SpotifyTrackCandidate:
    return SpotifyTrackCandidate(
        spotify_track_id=spotify_track_id,
        title=title,
        artist_names=(artist,),
        album_name=album,
        duration_ms=duration_ms,
        explicit=False,
        spotify_url=f"https://open.spotify.com/track/{spotify_track_id}",
        spotify_uri=f"spotify:track:{spotify_track_id}",
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


class RecordingSpotifyClient(SpotifyClient):
    def __init__(self) -> None:
        super().__init__("client-id", "client-secret")
        self.requested_url: str | None = None

    def _api_request(self, url: str, retry: bool = True) -> dict:
        self.requested_url = url
        return {"tracks": {"items": []}}


class FakeHttpResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return b"{}"


class RecordingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


class SpotifyEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_match_score_prefers_same_track(self) -> None:
        track = RekordboxTrack(
            rekordbox_track_id=1,
            title="One More Time",
            artist="Daft Punk",
            duration=320,
            playlist_name="House",
        )

        exact_score = calculate_match_score(
            track,
            candidate("exact", "One More Time", "Daft Punk", duration_ms=320_000),
        )
        unrelated_score = calculate_match_score(
            track,
            candidate("other", "Around the World", "Daft Punk", duration_ms=430_000),
        )

        self.assertEqual(exact_score, 1.0)
        self.assertGreater(exact_score, unrelated_score)

    def test_spotify_search_requests_default_limit_and_offset(self) -> None:
        client = RecordingSpotifyClient()

        client.search_tracks("One More Time", "Daft Punk", "Discovery")

        query = parse_qs(urlparse(client.requested_url).query)
        self.assertEqual(query["limit"], ["3"])
        self.assertEqual(query["offset"], ["0"])

    def test_rate_limiter_can_be_disabled(self) -> None:
        limiter = RateLimiter(0)

        limiter.wait()

        self.assertEqual(limiter._last_ts, 0.0)

    def test_spotify_http_request_waits_on_limiter(self) -> None:
        client = SpotifyClient("client-id", "client-secret", rps=0)
        limiter = RecordingLimiter()
        client.limiter = limiter

        with patch("app.enrichment.spotify.urlopen", return_value=FakeHttpResponse()):
            client._open_json(Request("https://api.spotify.test/v1/search"))

        self.assertEqual(limiter.calls, 1)

    def test_spotify_http_request_retries_after_rate_limit(self) -> None:
        client = SpotifyClient("client-id", "client-secret", rps=0)
        request = Request("https://api.spotify.test/v1/search")
        rate_limit_error = SpotifyAPIError("Rate limited (HTTP 429)", status_code=429)

        with (
            patch("app.enrichment.spotify.time.sleep") as sleep,
            patch(
                "app.enrichment.spotify.SpotifyClient._open_json_once",
                side_effect=[rate_limit_error, {}],
            ) as open_json_once,
        ):
            client._open_json(request)

        self.assertEqual(open_json_once.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_search_title_removes_non_remix_parenthetical_text(self) -> None:
        self.assertEqual(
            prepare_track_search_title("on my mind (original mix)"),
            "on my mind",
        )
        self.assertEqual(
            prepare_track_search_title("go dj [explicit]"),
            "go dj",
        )
        self.assertEqual(
            prepare_track_search_title("murdah (isoxo remix)"),
            "murdah (isoxo remix)",
        )

    def test_search_artist_uses_first_artist_name(self) -> None:
        self.assertEqual(
            prepare_artist_search_name("skrillex, virtual riot, varg2tm, eurohead"),
            "skrillex",
        )

    def test_match_score_uses_prepared_search_title_and_artist(self) -> None:
        track = RekordboxTrack(
            rekordbox_track_id=1,
            title="on my mind (original mix)",
            artist="diplo, sidepiece",
            duration=189,
            playlist_name="House",
        )

        score = calculate_match_score(
            track,
            candidate("exact", "On My Mind", "Diplo", duration_ms=189_000),
        )

        self.assertEqual(score, 1.0)

    def test_match_score_uses_album_when_available(self) -> None:
        track = RekordboxTrack(
            rekordbox_track_id=1,
            title="One More Time",
            artist="Daft Punk",
            album="Discovery",
            playlist_name="House",
        )

        matching_album_score = calculate_match_score(
            track,
            candidate("exact", "One More Time", "Daft Punk", album="Discovery"),
        )
        different_album_score = calculate_match_score(
            track,
            candidate("other", "One More Time", "Daft Punk", album="Homework"),
        )

        self.assertEqual(matching_album_score, 1.0)
        self.assertGreater(matching_album_score, different_album_score)

    def test_match_score_ignores_album_when_rekordbox_album_is_missing(self) -> None:
        track = RekordboxTrack(
            rekordbox_track_id=1,
            title="One More Time",
            artist="Daft Punk",
            album=None,
            playlist_name="House",
        )

        score = calculate_match_score(
            track,
            candidate("exact", "One More Time", "Daft Punk", album="Discovery"),
        )

        self.assertEqual(score, 1.0)

    def test_match_score_ignores_duration(self) -> None:
        track = RekordboxTrack(
            rekordbox_track_id=1,
            title="One More Time",
            artist="Daft Punk",
            duration=320,
            playlist_name="House",
        )

        score = calculate_match_score(
            track,
            candidate("exact", "One More Time", "Daft Punk", duration_ms=1_000),
        )

        self.assertEqual(score, 1.0)

    def test_spotify_search_uses_prepared_title_and_artist(self) -> None:
        client = RecordingSpotifyClient()

        client.search_tracks(
            "on my mind (original mix)",
            "diplo, sidepiece",
            "On My Mind",
        )

        query = parse_qs(urlparse(client.requested_url).query)
        self.assertEqual(query["q"], ["track: on my mind artist: diplo album: On My Mind"])
        self.assertEqual(
            query["q"],
            [build_spotify_search_query("on my mind (original mix)", "diplo, sidepiece", "On My Mind")],
        )

    def test_spotify_search_uses_blank_album_when_album_is_missing(self) -> None:
        client = RecordingSpotifyClient()

        client.search_tracks("One More Time", "Daft Punk")

        query = parse_qs(urlparse(client.requested_url).query)
        self.assertEqual(query["q"], ["track: One More Time artist: Daft Punk album: "])

    def test_enrichment_persists_accepted_match(self) -> None:
        fake_client = FakeSpotifyClient(
            {
                "One More Time": [
                    candidate("other", "Around the World", "Daft Punk", duration_ms=430_000),
                    candidate(
                        "exact",
                        "One More Time",
                        "Daft Punk",
                        album="Discovery",
                        duration_ms=320_000,
                    ),
                    candidate("wrong", "Harder Better Faster Stronger", "Daft Punk"),
                ]
            }
        )
        service = SpotifyEnrichmentService(fake_client, minimum_match_score=0.75)

        with Session(self.engine) as session:
            session.add(
                RekordboxTrack(
                    rekordbox_track_id=1,
                    title="One More Time",
                    artist="Daft Punk",
                    album="Discovery",
                    duration=320,
                    playlist_name="House",
                )
            )
            session.commit()

            result = service.enrich_tracks(session)
            session.commit()

            match = session.get(RekordboxSpotifyMatch, 1)
            track = session.get(RekordboxTrack, 1)
            spotify_track = session.get(SpotifyTrack, "exact")

        self.assertEqual(result.matched, 1)
        self.assertEqual(fake_client.calls, [("One More Time", "Daft Punk", "Discovery", 3)])
        self.assertEqual(match.spotify_track_id, "exact")
        self.assertEqual(match.match_score, 1.0)
        self.assertEqual(
            match.spotify_search_query_string,
            "track: One More Time artist: Daft Punk album: Discovery",
        )
        self.assertEqual(
            track.spotify_search_query_string,
            "track: One More Time artist: Daft Punk album: Discovery",
        )
        self.assertEqual(spotify_track.title, "One More Time")

    def test_enrichment_skips_low_score_and_existing_matches(self) -> None:
        fake_client = FakeSpotifyClient(
            {"Unmatched": [candidate("other", "Different", "Someone Else")]}
        )
        service = SpotifyEnrichmentService(fake_client, minimum_match_score=0.9)

        with Session(self.engine) as session:
            session.add_all(
                [
                    RekordboxTrack(
                        rekordbox_track_id=1,
                        title="Already Matched",
                        artist="Artist",
                        playlist_name="Test",
                    ),
                    RekordboxTrack(
                        rekordbox_track_id=2,
                        title="Unmatched",
                        artist="Artist",
                        playlist_name="Test",
                    ),
                    SpotifyTrack(
                        spotify_track_id="existing",
                        title="Already Matched",
                        artist_names="Artist",
                    ),
                    RekordboxSpotifyMatch(
                        rekordbox_track_id=1,
                        spotify_track_id="existing",
                        match_score=1.0,
                    ),
                ]
            )
            session.commit()

            result = service.enrich_tracks(session)
            session.commit()
            unmatched_track = session.get(RekordboxTrack, 2)
            match_count = session.scalar(
                select(func.count()).select_from(RekordboxSpotifyMatch)
            )

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.unmatched, 1)
        self.assertEqual(fake_client.calls, [("Unmatched", "Artist", None, 3)])
        self.assertEqual(
            unmatched_track.spotify_search_query_string,
            "track: Unmatched artist: Artist album: ",
        )
        self.assertEqual(match_count, 1)

    def test_create_tables_adds_search_query_string_columns(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE rekordbox_tracks (
                        rekordbox_track_id INTEGER NOT NULL PRIMARY KEY,
                        title TEXT,
                        playlist_name TEXT NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE rekordbox_spotify_matches (
                        rekordbox_track_id INTEGER NOT NULL PRIMARY KEY,
                        spotify_track_id VARCHAR(64) NOT NULL,
                        match_score FLOAT NOT NULL,
                        matched_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                    """
                )
            )

        create_tables(engine)

        with engine.connect() as connection:
            track_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(rekordbox_tracks)"))
            }
            match_columns = {
                row[1]
                for row in connection.execute(
                    text("PRAGMA table_info(rekordbox_spotify_matches)")
                )
            }

        engine.dispose()
        self.assertIn("spotify_search_query_string", track_columns)
        self.assertIn("spotify_search_query_string", match_columns)


if __name__ == "__main__":
    unittest.main()
