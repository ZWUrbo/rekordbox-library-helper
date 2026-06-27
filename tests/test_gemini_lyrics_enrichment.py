import json
import unittest

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    GeminiRawLyrics,
    RekordboxSpotifyMatch,
    RekordboxTrack,
    SpotifyTrack,
)
from app.enrichment.gemini_lyrics import (
    GeminiLyricsInput,
    build_batch_request_line,
    extract_batch_state,
    import_result_lines,
    list_lyrics_inputs,
    parse_batch_result_line,
)


def result_line(key: int, generated_json: str) -> str:
    return json.dumps(
        {
            "key": str(key),
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": generated_json}],
                        }
                    }
                ]
            },
        }
    )


class GeminiLyricsEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def add_track(
        self,
        session: Session,
        track_id: int,
        genre: str,
        matched: bool = True,
    ) -> None:
        spotify_id = f"spotify-{track_id}"
        session.add(
            RekordboxTrack(
                rekordbox_track_id=track_id,
                title=f"Song {track_id}",
                artist=f"Artist {track_id}",
                genre=genre,
                playlist_name="Test",
            )
        )
        if matched:
            session.add(
                SpotifyTrack(
                    spotify_track_id=spotify_id,
                    title=f"Song {track_id}",
                    artist_names=f"Artist {track_id}",
                )
            )
            session.add(
                RekordboxSpotifyMatch(
                    rekordbox_track_id=track_id,
                    spotify_track_id=spotify_id,
                    match_score=1.0,
                )
            )

    def test_lists_target_genres_without_requiring_spotify_matches(self) -> None:
        with Session(self.engine) as session:
            self.add_track(session, 1, "Hip-Hop")
            self.add_track(session, 2, "soft rock", matched=False)
            self.add_track(session, 3, "House")
            session.add(
                GeminiRawLyrics(
                    rekordbox_track_id=1,
                    raw_json='{"title":"Song 1","artist":"Artist 1","lyrics":[]}',
                )
            )
            session.commit()

            missing = list_lyrics_inputs(session)
            forced = list_lyrics_inputs(session, force=True)

        self.assertEqual([row.rekordbox_track_id for row in missing], [2])
        self.assertEqual([row.rekordbox_track_id for row in forced], [1, 2])

    def test_builds_grounded_prompt_without_incompatible_json_mime_type(self) -> None:
        request_line = build_batch_request_line(
            GeminiLyricsInput(7, "My Song", "The Artist")
        )
        prompt = request_line["request"]["contents"][0]["parts"][0]["text"]

        self.assertEqual(request_line["key"], "7")
        self.assertIn('* Song Title: "My Song"', prompt)
        self.assertIn('* Artist: "The Artist"', prompt)
        self.assertIn('Represent blank lines as an empty string ("").', prompt)
        self.assertIn('"verse_1"', prompt)
        self.assertEqual(
            request_line["request"]["tools"],
            [{"googleSearch": {}}],
        )
        self.assertEqual(request_line["request"]["generationConfig"]["temperature"], 0)
        self.assertNotIn(
            "responseMimeType",
            request_line["request"]["generationConfig"],
        )

    def test_parses_and_stores_the_generated_json_intact(self) -> None:
        generated = json.dumps(
            {
                "title": "Song 1",
                "artist": "Artist 1",
                "lyrics": [["verse_1", ["First line", "", "Second line"]]],
            },
            ensure_ascii=False,
            indent=2,
        )
        line = result_line(1, generated)
        parsed_track_id, parsed_json = parse_batch_result_line(line)

        with Session(self.engine) as session:
            self.add_track(session, 1, "Rap")
            session.commit()
            imported, failed = import_result_lines(session, [line])
            session.commit()
            stored = session.scalar(select(GeminiRawLyrics))

        self.assertEqual(parsed_track_id, 1)
        self.assertEqual(parsed_json, generated)
        self.assertEqual((imported, failed), (1, 0))
        self.assertEqual(stored.rekordbox_track_id, 1)
        self.assertEqual(stored.raw_json, generated)

    def test_parses_markdown_fenced_generated_json(self) -> None:
        generated = '{"title":"Song 2","artist":"Artist 2","lyrics":[]}'
        line = result_line(2, f"```json\n{generated}\n```")

        parsed_track_id, parsed_json = parse_batch_result_line(line)

        self.assertEqual(parsed_track_id, 2)
        self.assertEqual(parsed_json, generated)

    def test_imports_result_for_track_without_spotify_match(self) -> None:
        generated = '{"title":"Song 4","artist":"Artist 4","lyrics":[]}'
        with Session(self.engine) as session:
            self.add_track(session, 4, "Pop", matched=False)
            session.commit()
            imported, failed = import_result_lines(session, [result_line(4, generated)])
            session.commit()
            stored = session.get(GeminiRawLyrics, 4)

        self.assertEqual((imported, failed), (1, 0))
        self.assertEqual(stored.raw_json, generated)

    def test_create_tables_removes_legacy_spotify_id_column(self) -> None:
        from app.db.connection import create_tables

        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE gemini_raw_lyrics ("
                    "rekordbox_track_id INTEGER PRIMARY KEY, "
                    "spotify_track_id VARCHAR(64) NOT NULL, "
                    "raw_json TEXT NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_gemini_raw_lyrics_spotify_track_id "
                    "ON gemini_raw_lyrics (spotify_track_id)"
                )
            )

        create_tables(engine)

        self.assertEqual(
            {column["name"] for column in inspect(engine).get_columns("gemini_raw_lyrics")},
            {"rekordbox_track_id", "raw_json", "fetched_at"},
        )
        engine.dispose()

    def test_rejects_non_json_generated_text(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            parse_batch_result_line(result_line(1, "not json"))

    def test_extracts_batch_state_from_metadata(self) -> None:
        self.assertEqual(
            extract_batch_state(
                {"metadata": {"state": "BATCH_STATE_SUCCEEDED"}}
            ),
            "BATCH_STATE_SUCCEEDED",
        )


if __name__ == "__main__":
    unittest.main()
