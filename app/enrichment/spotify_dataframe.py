import logging
from typing import Any

import pandas as pd

from app.enrichment.spotify import (
    DEFAULT_SPOTIFY_SEARCH_LIMIT,
    SpotifyClient,
    SpotifyTrackSearchInput,
    select_best_spotify_match,
)


logger = logging.getLogger(__name__)


def enrich_track_dataframe_with_spotify_ids(
    dataframe: pd.DataFrame,
    client: SpotifyClient,
    title_column: str = "title",
    artist_column: str = "artists",
    album_column: str | None = None,
    minimum_match_score: float = 0.75,
    search_limit: int = DEFAULT_SPOTIFY_SEARCH_LIMIT,
) -> pd.DataFrame:
    if title_column not in dataframe.columns:
        raise ValueError(f"DataFrame is missing required title column: {title_column}")
    if artist_column not in dataframe.columns:
        raise ValueError(f"DataFrame is missing required artist column: {artist_column}")
    if album_column and album_column not in dataframe.columns:
        raise ValueError(f"DataFrame is missing album column: {album_column}")
    if not 0 <= minimum_match_score <= 1:
        raise ValueError("minimum_match_score must be between 0 and 1")
    if not 1 <= search_limit <= 50:
        raise ValueError("search_limit must be between 1 and 50")

    enriched = pd.DataFrame(index=dataframe.index, columns=_spotify_output_columns())

    total_tracks = len(enriched)
    for position, (index, row) in enumerate(dataframe.iterrows(), start=1):
        title = _clean_cell(row.get(title_column))
        artist = _clean_cell(row.get(artist_column))
        album = _clean_cell(row.get(album_column)) if album_column else ""

        if not title:
            enriched.at[index, "spotify_lookup_error"] = "missing title"
            continue

        try:
            if position == 1 or position == total_tracks or position % 25 == 0:
                logger.info("Spotify dataframe search %s/%s", position, total_tracks)

            candidates = client.search_tracks(
                title,
                artist,
                album,
                limit=search_limit,
            )
            source_track = SpotifyTrackSearchInput(title=title, artist=artist, album=album)
            best_score, best_candidate = select_best_spotify_match(source_track, candidates)
            enriched.at[index, "spotify_match_score"] = best_score

            if best_candidate is None or best_score < minimum_match_score:
                continue

            enriched.at[index, "spotify_track_id"] = best_candidate.spotify_track_id
            enriched.at[index, "spotify_title"] = best_candidate.title
            enriched.at[index, "spotify_artists"] = ", ".join(best_candidate.artist_names)
            enriched.at[index, "spotify_album"] = best_candidate.album_name
        except Exception as exc:
            logger.exception("Spotify dataframe enrichment failed for row %s", index)
            enriched.at[index, "spotify_lookup_error"] = str(exc)

    return enriched


def _spotify_output_columns() -> list[str]:
    return [
        "spotify_track_id",
        "spotify_match_score",
        "spotify_title",
        "spotify_artists",
        "spotify_album",
        "spotify_lookup_error",
    ]


def _clean_cell(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    cleaned = str(value).strip()
    return cleaned or None
