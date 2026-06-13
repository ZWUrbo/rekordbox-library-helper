from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RekordboxTrack(Base):
    __tablename__ = "rekordbox_tracks"

    rekordbox_track_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    artist: Mapped[str | None] = mapped_column(Text, nullable=True)
    album: Mapped[str | None] = mapped_column(Text, nullable=True)
    genre: Mapped[str | None] = mapped_column(Text, nullable=True)
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    key: Mapped[str | None] = mapped_column("key", String(64), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    date_added: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    playlist_name: Mapped[str] = mapped_column(Text, nullable=False)
    spotify_search_query_string: Mapped[str | None] = mapped_column(Text, nullable=True)


class SpotifyTrack(Base):
    __tablename__ = "spotify_tracks"

    spotify_track_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    artist_names: Mapped[str] = mapped_column(Text, nullable=False)
    album_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explicit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    spotify_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    spotify_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )


class RekordboxSpotifyMatch(Base):
    __tablename__ = "rekordbox_spotify_matches"

    rekordbox_track_id: Mapped[int] = mapped_column(
        ForeignKey("rekordbox_tracks.rekordbox_track_id"),
        primary_key=True,
    )
    spotify_track_id: Mapped[str] = mapped_column(
        ForeignKey("spotify_tracks.spotify_track_id"),
        index=True,
        nullable=False,
    )
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    spotify_search_query_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )
