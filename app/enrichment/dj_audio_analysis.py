import json
import logging
import ssl
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import certifi
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Genres,
    Harmony,
    RekordboxSpotifyMatch,
    Rhythm,
    Score,
    TrackAnalysis,
)
from app.enrichment.spotify import RateLimiter, _retry_delay_seconds


logger = logging.getLogger(__name__)

DEFAULT_RAPIDAPI_HOST = "dj-track-audio-analysis-api.p.rapidapi.com"
DEFAULT_AUDIO_ANALYSIS_PATH = "/v2/audio-analysis"
DEFAULT_TRACK_ID_QUERY_PARAM = "ids"
MAX_AUDIO_ANALYSIS_BATCH_SIZE = 5


class DJAudioAnalysisAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _is_retryable_rapidapi_error(exc: DJAudioAnalysisAPIError) -> bool:
    if exc.status_code is None:
        return True
    return exc.status_code == 429 or exc.status_code >= 500


def _as_json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return _as_json_text(value)
    return str(value)


def _as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    return None


def _first_value(payload: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        current: Any = payload
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _prefixed_value(
    payload: dict[str, Any],
    prefix: str,
    names: Sequence[str],
) -> Any:
    keys = []
    for name in names:
        keys.extend([name, f"{prefix}_{name}"])
    return _first_value(payload, keys)


@dataclass(frozen=True)
class DJAudioAnalysisRecord:
    spotify_track_id: str
    track_analysis: dict[str, Any]
    rhythm: dict[str, Any]
    harmony: dict[str, Any]
    score: dict[str, Any]
    genres: list[str]


@dataclass(frozen=True)
class DJAudioAnalysisEnrichmentResult:
    processed: int = 0
    enriched: int = 0
    failed: int = 0


class DJAudioAnalysisClient:
    def __init__(
        self,
        rapidapi_key: str,
        rapidapi_host: str = DEFAULT_RAPIDAPI_HOST,
        endpoint_path: str = DEFAULT_AUDIO_ANALYSIS_PATH,
        track_id_query_param: str = DEFAULT_TRACK_ID_QUERY_PARAM,
        timeout: float = 15.0,
        rps: float = 2.0,
    ):
        if not rapidapi_key:
            raise ValueError("RapidAPI key is required")
        if not rapidapi_host:
            raise ValueError("RapidAPI host is required")
        self.rapidapi_key = rapidapi_key
        self.rapidapi_host = rapidapi_host
        self.endpoint_path = (
            endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        )
        self.track_id_query_param = track_id_query_param
        self.timeout = timeout
        self.limiter = RateLimiter(rps)
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    def get_several_track_audio_analysis(
        self,
        spotify_track_ids: Sequence[str],
    ) -> list[DJAudioAnalysisRecord]:
        track_ids = [track_id for track_id in spotify_track_ids if track_id]
        if len(track_ids) > MAX_AUDIO_ANALYSIS_BATCH_SIZE:
            raise ValueError("DJ audio analysis requests support at most 5 track IDs")
        if not track_ids:
            return []

        params = urlencode({self.track_id_query_param: ",".join(track_ids)})
        request = Request(
            f"https://{self.rapidapi_host}{self.endpoint_path}?{params}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "dj-library-helper/0.1",
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": self.rapidapi_host,
            },
        )
        payload = self._open_json(request)
        return parse_analysis_response(payload, requested_track_ids=track_ids)

    def _open_json(self, request: Request) -> Any:
        for attempt in range(5):
            try:
                return self._open_json_once(request)
            except DJAudioAnalysisAPIError as exc:
                if not _is_retryable_rapidapi_error(exc) or attempt == 4:
                    raise
                delay = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else _retry_delay_seconds(attempt)
                )
                time.sleep(delay)

        raise RuntimeError("unreachable")

    def _open_json_once(self, request: Request) -> Any:
        try:
            self.limiter.wait()
            with urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise DJAudioAnalysisAPIError(
                (
                    f"DJ Track Audio Analysis API HTTP {exc.code} "
                    f"for {_safe_request_url(request)}: {body}"
                ),
                status_code=exc.code,
                retry_after=float(retry_after) if retry_after else None,
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DJAudioAnalysisAPIError(
                f"DJ Track Audio Analysis API request failed: {exc}"
            ) from exc


def _safe_request_url(request: Request) -> str:
    parsed_url = urlsplit(request.full_url)
    return f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"


def parse_analysis_response(
    payload: Any,
    requested_track_ids: Sequence[str] | None = None,
) -> list[DJAudioAnalysisRecord]:
    items = _extract_response_items(payload)
    requested = list(requested_track_ids or [])
    if isinstance(payload, dict) and len(items) == 1 and len(requested) == 1:
        items[0].setdefault("spotify_track_id", requested[0])

    records = []
    for item in items:
        spotify_track_id = _extract_spotify_track_id(item)
        if not spotify_track_id:
            continue
        records.append(
            DJAudioAnalysisRecord(
                spotify_track_id=spotify_track_id,
                track_analysis=_extract_category(item, "track_analysis", "track"),
                rhythm=_extract_category(item, "rhythm"),
                harmony=_extract_category(item, "harmony"),
                score=_extract_category(item, "score"),
                genres=_extract_category(item, "genres"),
            )
        )
    return records


def _extract_response_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in (
        "tracks",
        "track_analyses",
        "trackAnalyses",
        "audio_analysis",
        "audioAnalysis",
        "analyses",
        "analysis",
        "results",
        "data",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return _items_from_mapping(value)

    if all(isinstance(value, dict) for value in payload.values()):
        mapped_items = _items_from_mapping(payload)
        if mapped_items:
            return mapped_items

    return [payload]


def _items_from_mapping(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        item = dict(value)
        if not _extract_spotify_track_id(item):
            item["spotify_track_id"] = key
        items.append(item)
    return items


def _extract_spotify_track_id(payload: dict[str, Any]) -> str | None:
    value = _first_value(
        payload,
        (
            "spotify_track_id",
            "spotify_id",
            "track_id",
            "id",
            "ids.spotify",
            "ids.spotify_id",
            "ids.spotify_track_id",
            "track_analysis.ids.spotify",
            "track_analysis.ids.spotify_id",
            "track_analysis.ids.spotify_track_id",
            "trackAnalysis.ids.spotify",
            "trackAnalysis.ids.spotify_id",
            "trackAnalysis.ids.spotify_track_id",
            "track.ids.spotify",
            "track.ids.spotify_id",
            "track.ids.spotify_track_id",
        ),
    )
    return str(value) if value else None


def _extract_category(payload: dict[str, Any], *names: str) -> Any:
    aliases = {
        "track_analysis": ("track_analysis", "trackAnalysis", "track", "audio_analysis"),
        "rhythm": ("rhythm",),
        "harmony": ("harmony",),
        "score": ("score",),
        "genres": ("genres",),
    }
    candidate_names = []
    for name in names:
        candidate_names.extend(aliases.get(name, (name,)))

    for name in candidate_names:
        value = payload.get(name)
        if isinstance(value, (dict, list)):
            return value
    if "track_analysis" in names:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"rhythm", "harmony", "score", "genres"}
        }
    return {}


class DJAudioAnalysisEnrichmentService:
    def __init__(self, client: DJAudioAnalysisClient):
        self.client = client

    def enrich_matched_tracks(
        self,
        session: Session,
        limit: int | None = None,
        force: bool = False,
    ) -> DJAudioAnalysisEnrichmentResult:
        statement = (
            select(
                RekordboxSpotifyMatch.rekordbox_track_id,
                RekordboxSpotifyMatch.spotify_track_id,
            )
            .order_by(RekordboxSpotifyMatch.rekordbox_track_id)
        )
        if not force:
            statement = statement.outerjoin(
                TrackAnalysis,
                RekordboxSpotifyMatch.rekordbox_track_id
                == TrackAnalysis.rekordbox_track_id,
            ).where(TrackAnalysis.rekordbox_track_id.is_(None))
        if limit is not None:
            statement = statement.limit(limit)

        matches = session.execute(statement).all()
        total_matches = len(matches)
        logger.info("Selected %s matched tracks for DJ audio analysis", total_matches)

        processed = enriched = failed = 0
        for match_batch in _chunk_match_rows(matches, MAX_AUDIO_ANALYSIS_BATCH_SIZE):
            processed += len(match_batch)
            track_ids = _unique_spotify_track_ids(match_batch)
            try:
                records = self.client.get_several_track_audio_analysis(track_ids)
                records_by_track_id = {
                    record.spotify_track_id: record
                    for record in records
                }
                for rekordbox_track_id, spotify_track_id in match_batch:
                    record = records_by_track_id.get(spotify_track_id)
                    if record is None:
                        failed += 1
                        logger.warning(
                            "DJ audio analysis response missing Spotify track %s",
                            spotify_track_id,
                        )
                        continue
                    self._upsert_analysis(
                        session,
                        rekordbox_track_id=rekordbox_track_id,
                        spotify_track_id=spotify_track_id,
                        record=record,
                    )
                    enriched += 1
            except Exception:
                failed += len(match_batch)
                logger.exception(
                    "DJ audio analysis enrichment failed for batch starting at match %s",
                    match_batch[0][0] if match_batch else None,
                )

        return DJAudioAnalysisEnrichmentResult(
            processed=processed,
            enriched=enriched,
            failed=failed,
        )

    @staticmethod
    def _upsert_analysis(
        session: Session,
        rekordbox_track_id: int,
        spotify_track_id: str,
        record: DJAudioAnalysisRecord,
    ) -> None:
        _upsert_category(
            session,
            TrackAnalysis,
            _track_analysis_row(
                rekordbox_track_id,
                spotify_track_id,
                record.track_analysis,
            ),
        )
        _upsert_category(
            session,
            Rhythm,
            _rhythm_row(rekordbox_track_id, spotify_track_id, record.rhythm),
        )
        _upsert_category(
            session,
            Harmony,
            _harmony_row(rekordbox_track_id, spotify_track_id, record.harmony),
        )
        _upsert_category(
            session,
            Score,
            _score_row(rekordbox_track_id, spotify_track_id, record.score),
        )
        _upsert_category(
            session,
            Genres,
            _genres_row(rekordbox_track_id, spotify_track_id, record.genres),
        )


def _chunk_match_rows(rows: Sequence[Any], size: int) -> Iterable[list[tuple[int, str]]]:
    normalized_rows = [(row[0], row[1]) for row in rows]
    for index in range(0, len(normalized_rows), size):
        yield normalized_rows[index:index + size]


def _unique_spotify_track_ids(match_batch: Sequence[tuple[int, str]]) -> list[str]:
    return list(dict.fromkeys(spotify_track_id for _, spotify_track_id in match_batch))


def _upsert_category(session: Session, model: type[Any], row: dict[str, Any]) -> None:
    statement = insert(model).values(row)
    statement = statement.on_conflict_do_update(
        index_elements=[model.rekordbox_track_id],
        set_={
            column.name: statement.excluded[column.name]
            for column in model.__table__.columns
            if column.name not in {"rekordbox_track_id", "fetched_at"}
        }
        | {"fetched_at": func.current_timestamp()},
    )
    session.execute(statement)


def _track_analysis_row(
    rekordbox_track_id: int,
    spotify_track_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    ids = payload.get("ids") if isinstance(payload.get("ids"), dict) else {}
    return {
        "rekordbox_track_id": rekordbox_track_id,
        "spotify_track_id": spotify_track_id,
        "ids_spotify": _as_text(
            _first_value(ids, ("spotify", "spotify_id", "spotify_track_id"))
            or _prefixed_value(
                payload,
                "ids",
                ("spotify", "spotify_id", "spotify_track_id"),
            )
            or spotify_track_id
        ),
        "ids_isrc": _as_text(
            _first_value(ids, ("isrc",)) or _prefixed_value(payload, "ids", ("isrc",))
        ),
        "href": _as_text(_prefixed_value(payload, "track", ("href",))),
        "name": _as_text(_prefixed_value(payload, "track", ("name",))),
        "popularity": _as_int(_prefixed_value(payload, "track", ("popularity",))),
        "duration": _as_text(_prefixed_value(payload, "track", ("duration",))),
        "duration_s": _as_float(_prefixed_value(payload, "track", ("duration_s",))),
        "duration_ms": _as_int(
            _prefixed_value(payload, "track", ("duration_ms",))
        ),
        "loudness": _as_text(_prefixed_value(payload, "track", ("loudness",))),
        "loudness_db": _as_float(
            _prefixed_value(payload, "track", ("loudness_db",))
        ),
        "is_vocal_heavy": _as_bool(
            _prefixed_value(payload, "track", ("is_vocal_heavy",))
        ),
        "is_acoustic": _as_bool(
            _prefixed_value(payload, "track", ("is_acoustic",))
        ),
        "is_instrumental": _as_bool(
            _prefixed_value(payload, "track", ("is_instrumental",))
        ),
        "is_live_recording": _as_bool(
            _prefixed_value(payload, "track", ("is_live_recording",))
        ),
        "is_club_loud": _as_bool(
            _prefixed_value(payload, "track", ("is_club_loud",))
        ),
        "raw_payload": _as_json_text(payload),
    }


def _rhythm_row(
    rekordbox_track_id: int,
    spotify_track_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rekordbox_track_id": rekordbox_track_id,
        "spotify_track_id": spotify_track_id,
        "bpm": _as_float(_prefixed_value(payload, "rhythm", ("bpm",))),
        "tempo": _as_text(_prefixed_value(payload, "rhythm", ("tempo",))),
        "bucket": _as_text(_prefixed_value(payload, "rhythm", ("bucket",))),
        "beats": _as_int(_prefixed_value(payload, "rhythm", ("beats",))),
        "beats_per_bar": _as_int(
            _prefixed_value(payload, "rhythm", ("beats_per_bar",))
        ),
        "beat_duration_ms": _as_int(
            _prefixed_value(payload, "rhythm", ("beat_duration_ms",))
        ),
        "bars": _as_int(_prefixed_value(payload, "rhythm", ("bars",))),
        "time_signature": _as_text(
            _prefixed_value(payload, "rhythm", ("time_signature", "timeSignature"))
        ),
        "half_time_bpm": _as_float(
            _prefixed_value(payload, "rhythm", ("half_time_bpm",))
        ),
        "double_time_bpm": _as_float(
            _prefixed_value(payload, "rhythm", ("double_time_bpm",))
        ),
        "phrases_s_bar_1": _as_float(
            _first_value(payload, ("phrases_s.bar_1", "phrases_s_bar_1"))
        ),
        "phrases_s_bar_2": _as_float(
            _first_value(payload, ("phrases_s.bar_2", "phrases_s_bar_2"))
        ),
        "phrases_s_bar_4": _as_float(
            _first_value(payload, ("phrases_s.bar_4", "phrases_s_bar_4"))
        ),
        "phrases_s_bar_8": _as_float(
            _first_value(payload, ("phrases_s.bar_8", "phrases_s_bar_8"))
        ),
        "phrases_s_bar_16": _as_float(
            _first_value(payload, ("phrases_s.bar_16", "phrases_s_bar_16"))
        ),
        "phrases_s_bar_32": _as_float(
            _first_value(payload, ("phrases_s.bar_32", "phrases_s_bar_32"))
        ),
        "phrases_s_bar_64": _as_float(
            _first_value(payload, ("phrases_s.bar_64", "phrases_s_bar_64"))
        ),
        "phrases_count_bar_16": _as_int(
            _first_value(
                payload,
                ("phrases_count.bar_16", "phrases_count_bar_16"),
            )
        ),
        "phrases_count_bar_32": _as_int(
            _first_value(
                payload,
                ("phrases_count.bar_32", "phrases_count_bar_32"),
            )
        ),
        "raw_payload": _as_json_text(payload),
    }


def _harmony_row(
    rekordbox_track_id: int,
    spotify_track_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rekordbox_track_id": rekordbox_track_id,
        "spotify_track_id": spotify_track_id,
        "key": _as_int(_prefixed_value(payload, "harmony", ("key",))),
        "mode": _as_text(_prefixed_value(payload, "harmony", ("mode",))),
        "camelot": _as_text(_prefixed_value(payload, "harmony", ("camelot",))),
        "camelot_number": _as_int(
            _prefixed_value(payload, "harmony", ("camelot_number",))
        ),
        "camelot_letter": _as_text(
            _prefixed_value(payload, "harmony", ("camelot_letter",))
        ),
        "note": _as_text(_prefixed_value(payload, "harmony", ("note",))),
        "raw_payload": _as_json_text(payload),
    }


def _score_row(
    rekordbox_track_id: int,
    spotify_track_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rekordbox_track_id": rekordbox_track_id,
        "spotify_track_id": spotify_track_id,
        "danceability": _as_float(
            _prefixed_value(payload, "score", ("danceability",))
        ),
        "energy": _as_float(_prefixed_value(payload, "score", ("energy",))),
        "speechiness": _as_float(
            _prefixed_value(payload, "score", ("speechiness",))
        ),
        "acousticness": _as_float(
            _prefixed_value(payload, "score", ("acousticness",))
        ),
        "instrumentalness": _as_float(
            _prefixed_value(payload, "score", ("instrumentalness",))
        ),
        "liveness": _as_float(_prefixed_value(payload, "score", ("liveness",))),
        "valence": _as_float(_prefixed_value(payload, "score", ("valence",))),
        "dance_floor": _as_float(
            _prefixed_value(payload, "score", ("dance_floor",))
        ),
        "chill": _as_float(_prefixed_value(payload, "score", ("chill",))),
        "aggressive": _as_float(
            _prefixed_value(payload, "score", ("aggressive",))
        ),
        "hype": _as_float(_prefixed_value(payload, "score", ("hype",))),
        "groove": _as_float(_prefixed_value(payload, "score", ("groove",))),
        "warmup": _as_float(_prefixed_value(payload, "score", ("warmup",))),
        "peak_time": _as_float(
            _prefixed_value(payload, "score", ("peak_time",))
        ),
        "blendability": _as_float(
            _prefixed_value(payload, "score", ("blendability",))
        ),
        "vocal_risk": _as_float(
            _prefixed_value(payload, "score", ("vocal_risk",))
        ),
        "raw_payload": _as_json_text(payload),
    }


def _genres_row(
    rekordbox_track_id: int,
    spotify_track_id: str,
    payload: Any,
) -> dict[str, Any]:
    return {
        "rekordbox_track_id": rekordbox_track_id,
        "spotify_track_id": spotify_track_id,
        "values": _as_json_text(payload if isinstance(payload, list) else []),
        "raw_payload": _as_json_text(payload),
    }
