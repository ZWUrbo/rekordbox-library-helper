import logging
from collections.abc import Iterable

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db.models import RekordboxTrack
from app.services.csv_export import export_tracks_to_csv
from app.services.rekordbox_extraction import ExtractedTrack, RekordboxExtractionService


logger = logging.getLogger(__name__)


def upsert_tracks(session: Session, tracks: Iterable[ExtractedTrack]) -> int:
    rows_by_track_id = {
        track.rekordbox_track_id: track.as_dict()
        for track in tracks
    }
    rows = list(rows_by_track_id.values())
    if not rows:
        return 0

    statement = insert(RekordboxTrack).values(rows)
    update_columns = {
        column.name: getattr(statement.excluded, column.name)
        for column in RekordboxTrack.__table__.columns
        if column.name != "rekordbox_track_id"
    }
    statement = statement.on_conflict_do_update(
        index_elements=[RekordboxTrack.rekordbox_track_id],
        set_=update_columns,
    )
    session.execute(statement)
    return len(rows)


def import_playlist(
    playlist_name: str,
    xml_path: str,
    session: Session,
    export_dir: str = "data/exports",
) -> tuple[int, str]:
    extraction_service = RekordboxExtractionService(xml_path)
    tracks = extraction_service.extract_playlist(playlist_name)

    csv_path = export_tracks_to_csv(tracks, playlist_name, export_dir=export_dir)
    logger.info("Exported %s tracks to %s", len(tracks), csv_path)

    upserted_count = upsert_tracks(session, tracks)
    logger.info("Upserted %s tracks into rekordbox_tracks", upserted_count)
    return upserted_count, str(csv_path)
