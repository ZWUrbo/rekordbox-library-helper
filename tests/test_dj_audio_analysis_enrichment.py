import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.connection import create_tables
from app.db.models import (
    Base,
    Genres,
    Harmony,
    RekordboxSpotifyMatch,
    RekordboxTrack,
    Rhythm,
    Score,
    SpotifyTrack,
    TrackAnalysis,
)
from app.enrichment.dj_audio_analysis import (
    DJAudioAnalysisClient,
    DJAudioAnalysisEnrichmentService,
    DJAudioAnalysisRecord,
    MAX_AUDIO_ANALYSIS_BATCH_SIZE,
    enrich_spotify_dataframe_with_dj_audio_analysis,
    parse_analysis_response,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dj_audio_analysis_response.json"


def load_fixture_records() -> dict[str, DJAudioAnalysisRecord]:
    payload = json.loads(FIXTURE_PATH.read_text())
    return {
        record.spotify_track_id: record
        for record in parse_analysis_response(payload)
    }


class FakeDJAudioAnalysisClient:
    def __init__(self, records: dict[str, DJAudioAnalysisRecord]):
        self.records = records
        self.calls: list[list[str]] = []

    def get_several_track_audio_analysis(
        self,
        spotify_track_ids: list[str],
    ) -> list[DJAudioAnalysisRecord]:
        self.calls.append(list(spotify_track_ids))
        if len(spotify_track_ids) > MAX_AUDIO_ANALYSIS_BATCH_SIZE:
            raise AssertionError("batch exceeded API maximum")
        return [
            self.records[spotify_track_id]
            for spotify_track_id in spotify_track_ids
            if spotify_track_id in self.records
        ]


class RecordingDJAudioAnalysisClient(DJAudioAnalysisClient):
    def __init__(self) -> None:
        super().__init__("rapidapi-key")
        self.requested_url: str | None = None
        self.request_headers: dict[str, str] = {}

    def _open_json(self, request):
        self.requested_url = request.full_url
        self.request_headers = dict(request.header_items())
        return {"audio_analysis": []}


class DJAudioAnalysisEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.records = load_fixture_records()

    def tearDown(self) -> None:
        self.engine.dispose()

    def add_matched_track(
        self,
        session: Session,
        rekordbox_track_id: int,
        spotify_track_id: str,
    ) -> None:
        session.add(
            RekordboxTrack(
                rekordbox_track_id=rekordbox_track_id,
                title=f"Track {rekordbox_track_id}",
                artist="Artist",
                playlist_name="Test",
            )
        )
        session.add(
            SpotifyTrack(
                spotify_track_id=spotify_track_id,
                title=f"Track {rekordbox_track_id}",
                artist_names="Artist",
            )
        )
        session.add(
            RekordboxSpotifyMatch(
                rekordbox_track_id=rekordbox_track_id,
                spotify_track_id=spotify_track_id,
                match_score=1.0,
            )
        )

    def test_enrichment_batches_requests_in_groups_of_five(self) -> None:
        records = {
            f"spotify-{index}": DJAudioAnalysisRecord(
                spotify_track_id=f"spotify-{index}",
                track_analysis={"ids": {"spotify": f"spotify-{index}"}},
                rhythm={},
                harmony={},
                score={},
                genres=[],
            )
            for index in range(1, 7)
        }
        fake_client = FakeDJAudioAnalysisClient(records)
        service = DJAudioAnalysisEnrichmentService(fake_client)

        with Session(self.engine) as session:
            for index in range(1, 7):
                self.add_matched_track(session, index, f"spotify-{index}")
            session.commit()

            result = service.enrich_matched_tracks(session)
            session.commit()

        self.assertEqual(
            fake_client.calls,
            [
                ["spotify-1", "spotify-2", "spotify-3", "spotify-4", "spotify-5"],
                ["spotify-6"],
            ],
        )
        self.assertEqual(result.processed, 6)
        self.assertEqual(result.enriched, 6)
        self.assertEqual(result.failed, 0)

    def test_client_requests_default_v2_audio_analysis_endpoint(self) -> None:
        client = RecordingDJAudioAnalysisClient()

        client.get_several_track_audio_analysis(["spotify-1", "spotify-2"])

        parsed_url = urlparse(client.requested_url)
        query = parse_qs(parsed_url.query)
        self.assertEqual(parsed_url.path, "/v2/audio-analysis")
        self.assertEqual(query["ids"], ["spotify-1,spotify-2"])
        self.assertIn("ids=spotify-1%2Cspotify-2", client.requested_url)
        self.assertEqual(client.request_headers["Accept"], "application/json")
        self.assertEqual(client.request_headers["Content-type"], "application/json")
        self.assertEqual(client.request_headers["User-agent"], "dj-library-helper/0.1")

    def test_enrichment_persists_all_five_response_categories(self) -> None:
        fake_client = FakeDJAudioAnalysisClient(self.records)
        service = DJAudioAnalysisEnrichmentService(fake_client)

        with Session(self.engine) as session:
            self.add_matched_track(session, 1, "spotify-1")
            session.commit()

            result = service.enrich_matched_tracks(session)
            session.commit()

            track_analysis = session.get(TrackAnalysis, 1)
            rhythm = session.get(Rhythm, 1)
            harmony = session.get(Harmony, 1)
            score = session.get(Score, 1)
            genres = session.get(Genres, 1)

        self.assertEqual(result.enriched, 1)
        self.assertEqual(track_analysis.ids_isrc, "GBDUW0000053")
        self.assertEqual(track_analysis.name, "One More Time")
        self.assertEqual(track_analysis.popularity, 80)
        self.assertEqual(track_analysis.duration, "05:20")
        self.assertEqual(track_analysis.duration_s, 320.4)
        self.assertEqual(track_analysis.duration_ms, 320357)
        self.assertEqual(track_analysis.loudness, "-8.6 dB")
        self.assertEqual(track_analysis.loudness_db, -8.618)
        self.assertFalse(track_analysis.is_instrumental)
        self.assertFalse(track_analysis.is_club_loud)
        self.assertEqual(rhythm.tempo, "123.00 BPM")
        self.assertEqual(rhythm.bpm, 122.746)
        self.assertEqual(rhythm.bucket, "allegro")
        self.assertEqual(rhythm.beats, 655)
        self.assertEqual(rhythm.beat_duration_ms, 489)
        self.assertEqual(rhythm.time_signature, "4/4")
        self.assertEqual(rhythm.half_time_bpm, 61.373)
        self.assertEqual(rhythm.phrases_s_bar_32, 62.6)
        self.assertEqual(rhythm.phrases_count_bar_16, 10)
        self.assertEqual(harmony.key, 2)
        self.assertEqual(harmony.camelot, "10B")
        self.assertEqual(harmony.camelot_number, 10)
        self.assertEqual(harmony.camelot_letter, "B")
        self.assertEqual(harmony.note, "D")
        self.assertEqual(score.danceability, 0.613)
        self.assertEqual(score.speechiness, 0.133)
        self.assertEqual(score.dance_floor, 0.651)
        self.assertEqual(score.peak_time, 0.672)
        self.assertEqual(score.vocal_risk, 0.48)
        self.assertEqual(
            json.loads(genres.values),
            ["french house", "electronic", "electro"],
        )

    def test_enrichment_skips_matches_with_existing_track_analysis(self) -> None:
        fake_client = FakeDJAudioAnalysisClient(self.records)
        service = DJAudioAnalysisEnrichmentService(fake_client)

        with Session(self.engine) as session:
            self.add_matched_track(session, 1, "spotify-1")
            self.add_matched_track(session, 2, "spotify-2")
            session.add(
                TrackAnalysis(
                    rekordbox_track_id=1,
                    spotify_track_id="spotify-1",
                    ids_spotify="spotify-1",
                )
            )
            session.commit()

            result = service.enrich_matched_tracks(session)
            session.commit()
            track_analysis_count = session.scalar(
                select(func.count()).select_from(TrackAnalysis)
            )

        self.assertEqual(fake_client.calls, [["spotify-2"]])
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.enriched, 1)
        self.assertEqual(track_analysis_count, 2)

    def test_create_tables_adds_missing_analysis_columns_to_existing_tables(self) -> None:
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
                    CREATE TABLE spotify_tracks (
                        spotify_track_id VARCHAR(64) NOT NULL PRIMARY KEY,
                        title TEXT NOT NULL,
                        artist_names TEXT NOT NULL
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
                        match_score FLOAT NOT NULL
                    )
                    """
                )
            )
            for table_name in ("track_analysis", "rhythm", "harmony", "score", "genres"):
                connection.execute(
                    text(
                        f"""
                        CREATE TABLE {table_name} (
                            rekordbox_track_id INTEGER NOT NULL PRIMARY KEY,
                            spotify_track_id VARCHAR(64) NOT NULL
                        )
                        """
                    )
                )

        create_tables(engine)

        with engine.connect() as connection:
            columns_by_table = {
                table_name: {
                    row[1]
                    for row in connection.execute(text(f"PRAGMA table_info({table_name})"))
                }
                for table_name in ("track_analysis", "rhythm", "harmony", "score", "genres")
            }

        engine.dispose()
        self.assertIn("loudness_db", columns_by_table["track_analysis"])
        self.assertIn("phrases_s_bar_64", columns_by_table["rhythm"])
        self.assertIn("camelot_number", columns_by_table["harmony"])
        self.assertIn("blendability", columns_by_table["score"])
        self.assertIn("values", columns_by_table["genres"])

    def test_dataframe_enrichment_returns_joinable_table_shaped_frames(self) -> None:
        fake_client = FakeDJAudioAnalysisClient(self.records)
        spotify_df = pd.DataFrame(
            {
                "spotify_track_id": [
                    "spotify-1",
                    "spotify-2",
                    None,
                    "spotify-1",
                ]
            }
        )

        frames = enrich_spotify_dataframe_with_dj_audio_analysis(
            spotify_df,
            fake_client,
        )

        self.assertEqual(fake_client.calls, [["spotify-1", "spotify-2"]])
        self.assertEqual(frames.track_analysis.columns.tolist(), [
            "spotify_track_id",
            "ids_spotify",
            "ids_isrc",
            "href",
            "name",
            "popularity",
            "duration",
            "duration_s",
            "duration_ms",
            "loudness",
            "loudness_db",
            "is_vocal_heavy",
            "is_acoustic",
            "is_instrumental",
            "is_live_recording",
            "is_club_loud",
            "raw_payload",
        ])
        self.assertEqual(frames.rhythm.columns.tolist(), [
            "spotify_track_id",
            "bpm",
            "tempo",
            "bucket",
            "beats",
            "beats_per_bar",
            "beat_duration_ms",
            "bars",
            "time_signature",
            "half_time_bpm",
            "double_time_bpm",
            "phrases_s_bar_1",
            "phrases_s_bar_2",
            "phrases_s_bar_4",
            "phrases_s_bar_8",
            "phrases_s_bar_16",
            "phrases_s_bar_32",
            "phrases_s_bar_64",
            "phrases_count_bar_16",
            "phrases_count_bar_32",
            "raw_payload",
        ])
        self.assertEqual(frames.harmony.columns.tolist(), [
            "spotify_track_id",
            "key",
            "mode",
            "camelot",
            "camelot_number",
            "camelot_letter",
            "note",
            "raw_payload",
        ])
        self.assertEqual(frames.score.columns.tolist(), [
            "spotify_track_id",
            "danceability",
            "energy",
            "speechiness",
            "acousticness",
            "instrumentalness",
            "liveness",
            "valence",
            "dance_floor",
            "chill",
            "aggressive",
            "hype",
            "groove",
            "warmup",
            "peak_time",
            "blendability",
            "vocal_risk",
            "raw_payload",
        ])
        self.assertEqual(frames.genres.columns.tolist(), [
            "spotify_track_id",
            "values",
            "raw_payload",
        ])
        self.assertNotIn("rekordbox_track_id", frames.track_analysis.columns)
        self.assertNotIn("fetched_at", frames.track_analysis.columns)

        joined = (
            frames.track_analysis
            .merge(frames.rhythm, on="spotify_track_id", suffixes=("", "_rhythm"))
            .merge(frames.harmony, on="spotify_track_id", suffixes=("", "_harmony"))
            .merge(frames.score, on="spotify_track_id", suffixes=("", "_score"))
            .merge(frames.genres, on="spotify_track_id", suffixes=("", "_genres"))
        )

        self.assertEqual(set(joined["spotify_track_id"]), {"spotify-1", "spotify-2"})
        self.assertIn("bpm", joined.columns)
        self.assertIn("camelot", joined.columns)
        self.assertIn("danceability", joined.columns)
        self.assertIn("values", joined.columns)


if __name__ == "__main__":
    unittest.main()
