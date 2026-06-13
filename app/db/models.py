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


class TrackAnalysis(Base):
    __tablename__ = "track_analysis"

    rekordbox_track_id: Mapped[int] = mapped_column(
        ForeignKey("rekordbox_spotify_matches.rekordbox_track_id"),
        primary_key=True,
    )
    spotify_track_id: Mapped[str] = mapped_column(
        ForeignKey("spotify_tracks.spotify_track_id"),
        index=True,
        nullable=False,
    )
    ids_spotify: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ids_isrc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    href: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    popularity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loudness: Mapped[str | None] = mapped_column(String(32), nullable=True)
    loudness_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_vocal_heavy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_acoustic: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_instrumental: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_live_recording: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_club_loud: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )


class Rhythm(Base):
    __tablename__ = "rhythm"

    rekordbox_track_id: Mapped[int] = mapped_column(
        ForeignKey("rekordbox_spotify_matches.rekordbox_track_id"),
        primary_key=True,
    )
    spotify_track_id: Mapped[str] = mapped_column(
        ForeignKey("spotify_tracks.spotify_track_id"),
        index=True,
        nullable=False,
    )
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    tempo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    beats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beats_per_bar: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beat_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_signature: Mapped[str | None] = mapped_column(String(16), nullable=True)
    half_time_bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    double_time_bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_4: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_8: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_16: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_32: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_s_bar_64: Mapped[float | None] = mapped_column(Float, nullable=True)
    phrases_count_bar_16: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phrases_count_bar_32: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )


class Harmony(Base):
    __tablename__ = "harmony"

    rekordbox_track_id: Mapped[int] = mapped_column(
        ForeignKey("rekordbox_spotify_matches.rekordbox_track_id"),
        primary_key=True,
    )
    spotify_track_id: Mapped[str] = mapped_column(
        ForeignKey("spotify_tracks.spotify_track_id"),
        index=True,
        nullable=False,
    )
    key: Mapped[int | None] = mapped_column("key", Integer, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    camelot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    camelot_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    camelot_letter: Mapped[str | None] = mapped_column(String(1), nullable=True)
    note: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )


class Score(Base):
    __tablename__ = "score"

    rekordbox_track_id: Mapped[int] = mapped_column(
        ForeignKey("rekordbox_spotify_matches.rekordbox_track_id"),
        primary_key=True,
    )
    spotify_track_id: Mapped[str] = mapped_column(
        ForeignKey("spotify_tracks.spotify_track_id"),
        index=True,
        nullable=False,
    )
    danceability: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy: Mapped[float | None] = mapped_column(Float, nullable=True)
    speechiness: Mapped[float | None] = mapped_column(Float, nullable=True)
    acousticness: Mapped[float | None] = mapped_column(Float, nullable=True)
    instrumentalness: Mapped[float | None] = mapped_column(Float, nullable=True)
    liveness: Mapped[float | None] = mapped_column(Float, nullable=True)
    valence: Mapped[float | None] = mapped_column(Float, nullable=True)
    dance_floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    chill: Mapped[float | None] = mapped_column(Float, nullable=True)
    aggressive: Mapped[float | None] = mapped_column(Float, nullable=True)
    hype: Mapped[float | None] = mapped_column(Float, nullable=True)
    groove: Mapped[float | None] = mapped_column(Float, nullable=True)
    warmup: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    blendability: Mapped[float | None] = mapped_column(Float, nullable=True)
    vocal_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )


class Genres(Base):
    __tablename__ = "genres"

    rekordbox_track_id: Mapped[int] = mapped_column(
        ForeignKey("rekordbox_spotify_matches.rekordbox_track_id"),
        primary_key=True,
    )
    spotify_track_id: Mapped[str] = mapped_column(
        ForeignKey("spotify_tracks.spotify_track_id"),
        index=True,
        nullable=False,
    )
    values: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.current_timestamp(),
    )
