import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, GeminiRawLyrics, Genres, Harmony, Rhythm, Score, TrackAnalysis


load_dotenv()


def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    sqlite_path = Path(os.getenv("SQLITE_DB_PATH", "data/rekordbox_tracks.sqlite3"))
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path}"


def get_engine() -> Engine:
    return create_engine(build_database_url(), future=True)


def create_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _ensure_sqlite_columns(engine)


def _ensure_sqlite_columns(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    if "rekordbox_tracks" not in inspector.get_table_names():
        return

    rekordbox_track_columns = {
        column["name"]
        for column in inspector.get_columns("rekordbox_tracks")
    }
    with engine.begin() as connection:
        if "spotify_search_query_string" not in rekordbox_track_columns:
            connection.execute(
                text("ALTER TABLE rekordbox_tracks ADD COLUMN spotify_search_query_string TEXT")
            )

        if "rekordbox_spotify_matches" not in inspector.get_table_names():
            return

        match_columns = {
            column["name"]
            for column in inspect(connection).get_columns("rekordbox_spotify_matches")
        }
        if "spotify_search_query_string" not in match_columns:
            connection.execute(
                text(
                    "ALTER TABLE rekordbox_spotify_matches "
                    "ADD COLUMN spotify_search_query_string TEXT"
                )
            )

        _ensure_sqlite_table_columns(
            connection,
            [TrackAnalysis, Rhythm, Harmony, Score, Genres, GeminiRawLyrics],
        )
        _remove_legacy_gemini_spotify_column(connection)


def _ensure_sqlite_table_columns(connection, models: list[type[Base]]) -> None:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())

    for model in models:
        table = model.__table__
        if table.name not in table_names:
            continue

        existing_columns = {
            column["name"]
            for column in inspector.get_columns(table.name)
        }
        for column in table.columns:
            if column.name in existing_columns or column.primary_key:
                continue
            column_type = column.type.compile(dialect=connection.dialect)
            connection.execute(
                text(
                    f"ALTER TABLE {_quote_sqlite_identifier(table.name)} "
                    f"ADD COLUMN {_quote_sqlite_identifier(column.name)} {column_type}"
                )
            )


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _remove_legacy_gemini_spotify_column(connection) -> None:
    inspector = inspect(connection)
    if "gemini_raw_lyrics" not in inspector.get_table_names():
        return
    columns = {
        column["name"] for column in inspector.get_columns("gemini_raw_lyrics")
    }
    if "spotify_track_id" not in columns:
        return

    for index in inspector.get_indexes("gemini_raw_lyrics"):
        if "spotify_track_id" in index.get("column_names", []):
            connection.execute(
                text(f"DROP INDEX {_quote_sqlite_identifier(index['name'])}")
            )
    connection.execute(
        text("ALTER TABLE gemini_raw_lyrics DROP COLUMN spotify_track_id")
    )


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session_factory = get_session_factory(engine)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
