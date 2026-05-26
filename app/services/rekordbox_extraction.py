import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pyrekordbox import RekordboxXml


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedTrack:
    rekordbox_track_id: int
    title: str | None
    artist: str | None
    album: str | None
    genre: str | None
    bpm: float | None
    key: str | None
    rating: int | None
    comments: str | None
    duration: float | None
    date_added: datetime | None
    file_path: str | None
    playlist_name: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rekordbox_track_id": self.rekordbox_track_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "bpm": self.bpm,
            "key": self.key,
            "rating": self.rating,
            "comments": self.comments,
            "duration": self.duration,
            "date_added": self.date_added,
            "file_path": self.file_path,
            "playlist_name": self.playlist_name,
        }


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None

    normalized = re.sub(r"\s+", " ", str(value).strip().lower())
    return normalized or None


def _track_get(track: Any, key: str, default: Any = None) -> Any:
    try:
        return track.get(key, default)
    except AttributeError:
        try:
            return track[key]
        except Exception:
            return default
    except Exception:
        return default


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    for date_format in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Could not parse DateAdded value %r", value)
        return None


def _get_bpm(track: Any) -> float | None:
    bpm = _coerce_float(_track_get(track, "AverageBpm"))
    if bpm is not None:
        return bpm

    tempos = getattr(track, "tempos", None) or []
    if tempos:
        return _coerce_float(getattr(tempos[0], "Bpm", None))

    return None


def _playlist_path_parts(playlist_name: str) -> list[str]:
    parts = [part.strip() for part in playlist_name.split("/") if part.strip()]
    return parts or [playlist_name]


class RekordboxExtractionService:
    def __init__(self, xml_path: str | Path):
        self.xml_path = Path(xml_path).expanduser()

    def extract_playlist(self, playlist_name: str) -> list[ExtractedTrack]:
        if not self.xml_path.exists():
            raise FileNotFoundError(f"Rekordbox XML file does not exist: {self.xml_path}")

        logger.info("Loading Rekordbox XML from %s", self.xml_path)
        xml = RekordboxXml(self.xml_path)
        playlist = xml.get_playlist(*_playlist_path_parts(playlist_name))
        track_keys = playlist.get_tracks()

        logger.info("Extracting %s tracks from playlist %r", len(track_keys), playlist_name)
        tracks: list[ExtractedTrack] = []
        for track_key in track_keys:
            try:
                if playlist.key_type == "Location":
                    track = xml.get_track(Location=track_key)
                else:
                    track = xml.get_track(TrackID=track_key)
                tracks.append(self._map_track(track, playlist_name))
            except Exception:
                logger.exception(
                    "Skipping track key %r from playlist %r after extraction failure",
                    track_key,
                    playlist_name,
                )

        return tracks

    def _map_track(self, track: Any, playlist_name: str) -> ExtractedTrack:
        track_id = _coerce_int(_track_get(track, "TrackID"))
        if track_id is None:
            raise ValueError(f"Track is missing a valid TrackID: {track!r}")

        return ExtractedTrack(
            rekordbox_track_id=track_id,
            title=normalize_text(_track_get(track, "Name")),
            artist=normalize_text(_track_get(track, "Artist")),
            album=_track_get(track, "Album"),
            genre=_track_get(track, "Genre"),
            bpm=_get_bpm(track),
            key=_track_get(track, "Tonality"),
            rating=_coerce_int(_track_get(track, "Rating")),
            comments=_track_get(track, "Comments"),
            duration=_coerce_float(_track_get(track, "TotalTime")),
            date_added=_parse_datetime(_track_get(track, "DateAdded")),
            file_path=_track_get(track, "Location"),
            playlist_name=playlist_name,
        )
