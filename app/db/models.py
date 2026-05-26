from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
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
