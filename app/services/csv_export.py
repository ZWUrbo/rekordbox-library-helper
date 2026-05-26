import csv
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


FIELDNAMES = [
    "rekordbox_track_id",
    "title",
    "artist",
    "album",
    "genre",
    "bpm",
    "key",
    "rating",
    "comments",
    "duration",
    "date_added",
    "file_path",
    "playlist_name",
]


def safe_export_name(playlist_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", playlist_name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "playlist"


def _row(track: Any) -> dict[str, Any]:
    if hasattr(track, "as_dict"):
        data = track.as_dict()
    elif is_dataclass(track):
        data = asdict(track)
    else:
        data = dict(track)

    row = {field: data.get(field) for field in FIELDNAMES}
    if isinstance(row["date_added"], datetime):
        row["date_added"] = row["date_added"].isoformat()
    return row


def export_tracks_to_csv(
    tracks: Iterable[Any],
    playlist_name: str,
    export_dir: str | Path = "data/exports",
) -> Path:
    output_dir = Path(export_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_export_name(playlist_name)}.csv"

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        for track in tracks:
            writer.writerow(_row(track))

    return output_path
