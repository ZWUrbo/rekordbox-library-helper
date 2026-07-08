import base64
import json
import logging
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db.models import RekordboxSpotifyMatch, RekordboxTrack, SpotifyTrack


logger = logging.getLogger(__name__)

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
DEFAULT_SPOTIFY_SEARCH_LIMIT = 3
SPOTIFY_SEARCH_OFFSET = 0


class SpotifyAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _is_retryable_spotify_error(exc: SpotifyAPIError) -> bool:
    if exc.status_code is None:
        return True
    return exc.status_code == 429 or exc.status_code >= 500


def _retry_delay_seconds(attempt: int) -> float:
    return min(20.0, max(1.0, 0.8 * (2**attempt)))


@dataclass
class RateLimiter:
    rps: float
    _last_ts: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def wait(self) -> None:
        if self.rps <= 0:
            return
        with self._lock:
            min_interval = 1.0 / self.rps
            now = time.time()
            elapsed = now - self._last_ts
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_ts = time.time()


@dataclass(frozen=True)
class SpotifyTrackCandidate:
    spotify_track_id: str
    title: str
    artist_names: tuple[str, ...]
    album_name: str | None
    duration_ms: int | None
    explicit: bool | None
    spotify_url: str | None
    spotify_uri: str | None

    @classmethod
    def from_api_payload(cls, payload: dict[str, Any]) -> "SpotifyTrackCandidate":
        return cls(
            spotify_track_id=payload["id"],
            title=payload["name"],
            artist_names=tuple(
                artist["name"]
                for artist in payload.get("artists", [])
                if artist.get("name")
            ),
            album_name=(payload.get("album") or {}).get("name"),
            duration_ms=payload.get("duration_ms"),
            explicit=payload.get("explicit"),
            spotify_url=(payload.get("external_urls") or {}).get("spotify"),
            spotify_uri=payload.get("uri"),
        )

    def as_spotify_track_row(self) -> dict[str, Any]:
        return {
            "spotify_track_id": self.spotify_track_id,
            "title": self.title,
            "artist_names": ", ".join(self.artist_names),
            "album_name": self.album_name,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
            "spotify_url": self.spotify_url,
            "spotify_uri": self.spotify_uri,
        }


@dataclass(frozen=True)
class SpotifyTrackSearchInput:
    title: str | None
    artist: str | None = None
    album: str | None = ""


@dataclass(frozen=True)
class EnrichmentResult:
    processed: int = 0
    matched: int = 0
    unmatched: int = 0
    failed: int = 0


def _normalize_for_matching(value: str | None) -> str:
    if not value:
        return ""
    value = value.casefold()
    value = re.sub(r"\b(feat|ft)\.?\s+.*$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _similarity(left: str | None, right: str | None) -> float:
    normalized_left = _normalize_for_matching(left)
    normalized_right = _normalize_for_matching(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def prepare_track_search_title(title: str | None) -> str:
    if not title:
        return ""

    title = re.sub(r"\s+", " ", title).strip()
    if re.search(r"\bremix\b", title, flags=re.IGNORECASE):
        return title

    title = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*", " ", title)
    title = re.sub(r"[^a-zA-Z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def prepare_artist_search_name(artist: str | None) -> str | None:
    if not artist:
        return None

    artist = re.split(
        r"\s*(?:,|&|\+|\band\b|\bfeat\.?\b|\bft\.?\b)\s*",
        artist,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    artist = re.sub(r"\s+", " ", artist).strip()
    return artist or None


def prepare_album_search_name(album: str | None) -> str:
    return re.sub(r"\s+", " ", album or "").strip()


def build_spotify_search_query(
    title: str,
    artist: str | None,
    album: str | None = "",
) -> str:
    query = f"track: {prepare_track_search_title(title)}"
    artist_search_name = prepare_artist_search_name(artist)
    if artist_search_name:
        query += f" artist: {artist_search_name}"
    query += f" album: {prepare_album_search_name(album)}"
    return query


def calculate_match_score(
    source_track: RekordboxTrack | SpotifyTrackSearchInput,
    spotify_track: SpotifyTrackCandidate,
) -> float:
    components = [
        (
            0.6,
            _similarity(prepare_track_search_title(source_track.title), spotify_track.title),
        ),
        (
            0.3,
            _similarity(
                prepare_artist_search_name(source_track.artist),
                ", ".join(spotify_track.artist_names),
            ),
        ),
    ]
    album_search_name = prepare_album_search_name(source_track.album)
    if album_search_name:
        components.append((0.1, _similarity(album_search_name, spotify_track.album_name)))

    total_weight = sum(weight for weight, _ in components)
    return round(sum(weight * score for weight, score in components) / total_weight, 4)


def select_best_spotify_match(
    source_track: RekordboxTrack | SpotifyTrackSearchInput,
    candidates: list[SpotifyTrackCandidate],
) -> tuple[float, SpotifyTrackCandidate | None]:
    scored_candidates = [
        (calculate_match_score(source_track, candidate), candidate)
        for candidate in candidates
    ]
    return max(scored_candidates, key=lambda candidate: candidate[0], default=(0.0, None))


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        market: str | None = None,
        timeout: float = 15.0,
        rps: float = 2.0,
    ):
        if not client_id or not client_secret:
            raise ValueError("Spotify client ID and client secret are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.market = market
        self.timeout = timeout
        self.limiter = RateLimiter(rps)
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def search_tracks(
        self,
        title: str,
        artist: str | None,
        album: str | None = "",
        limit: int = DEFAULT_SPOTIFY_SEARCH_LIMIT,
    ) -> list[SpotifyTrackCandidate]:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")

        query = build_spotify_search_query(title, artist, album)
        params: dict[str, str | int] = {
            "q": query,
            "type": "track",
            "limit": limit,
            "offset": SPOTIFY_SEARCH_OFFSET,
        }
        if self.market:
            params["market"] = self.market

        payload = self._api_request(f"{SPOTIFY_SEARCH_URL}?{urlencode(params)}")
        items = (payload.get("tracks") or {}).get("items") or []
        return [
            SpotifyTrackCandidate.from_api_payload(item)
            for item in items
            if item.get("id") and item.get("name")
        ]

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            SPOTIFY_TOKEN_URL,
            data=urlencode({"grant_type": "client_credentials"}).encode("ascii"),
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        payload = self._open_json(request)
        self._access_token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60
        return self._access_token

    def _api_request(self, url: str, retry: bool = True) -> dict[str, Any]:
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self._get_access_token()}"},
        )
        try:
            return self._open_json(request)
        except SpotifyAPIError as exc:
            if retry and exc.status_code == 401:
                self._access_token = None
                return self._api_request(url, retry=False)
            raise

    def _open_json(self, request: Request) -> dict[str, Any]:
        for attempt in range(5):
            try:
                return self._open_json_once(request)
            except SpotifyAPIError as exc:
                if not _is_retryable_spotify_error(exc) or attempt == 4:
                    raise
                time.sleep(_retry_delay_seconds(attempt))

        raise RuntimeError("unreachable")

    def _open_json_once(self, request: Request) -> dict[str, Any]:
        try:
            self.limiter.wait()
            with urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise SpotifyAPIError(
                f"Spotify API HTTP {exc.code}: {body}",
                status_code=exc.code,
                retry_after=float(retry_after) if retry_after else None,
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            raise SpotifyAPIError(f"Spotify API request failed: {exc}") from exc


class SpotifyEnrichmentService:
    def __init__(
        self,
        client: SpotifyClient,
        minimum_match_score: float = 0.75,
        search_limit: int = DEFAULT_SPOTIFY_SEARCH_LIMIT,
    ):
        if not 0 <= minimum_match_score <= 1:
            raise ValueError("minimum_match_score must be between 0 and 1")
        if not 1 <= search_limit <= 50:
            raise ValueError("search_limit must be between 1 and 50")
        self.client = client
        self.minimum_match_score = minimum_match_score
        self.search_limit = search_limit

    def enrich_tracks(
        self,
        session: Session,
        limit: int | None = None,
        force: bool = False,
    ) -> EnrichmentResult:
        statement = select(RekordboxTrack).order_by(RekordboxTrack.rekordbox_track_id)
        if not force:
            statement = statement.outerjoin(
                RekordboxSpotifyMatch,
                RekordboxTrack.rekordbox_track_id
                == RekordboxSpotifyMatch.rekordbox_track_id,
            ).where(RekordboxSpotifyMatch.rekordbox_track_id.is_(None))
        if limit is not None:
            statement = statement.limit(limit)

        tracks = session.scalars(statement).all()
        total_tracks = len(tracks)
        progress_log_interval = 100
        logger.info("Selected %s Rekordbox tracks for Spotify enrichment", total_tracks)

        processed = matched = unmatched = failed = 0
        for track in tracks:
            processed += 1
            if not track.title:
                unmatched += 1
                continue
            try:
                search_query_string = build_spotify_search_query(
                    track.title,
                    track.artist,
                    track.album,
                )
                session.execute(
                    update(RekordboxTrack)
                    .where(RekordboxTrack.rekordbox_track_id == track.rekordbox_track_id)
                    .values(spotify_search_query_string=search_query_string)
                )
                if (
                    processed == 1
                    or processed == total_tracks
                    or processed % progress_log_interval == 0
                ):
                    logger.info("search %s/%s", processed, total_tracks)
                candidates = self.client.search_tracks(
                    track.title,
                    track.artist,
                    track.album,
                    limit=self.search_limit,
                )
                best_score, best_candidate = select_best_spotify_match(track, candidates)
                if best_candidate is None or best_score < self.minimum_match_score:
                    unmatched += 1
                    continue

                self._upsert_match(
                    session,
                    track,
                    best_candidate,
                    best_score,
                    search_query_string,
                )
                matched += 1
            except Exception:
                failed += 1
                logger.exception(
                    "Spotify enrichment failed for rekordbox track %s",
                    track.rekordbox_track_id,
                )

        return EnrichmentResult(
            processed=processed,
            matched=matched,
            unmatched=unmatched,
            failed=failed,
        )

    @staticmethod
    def _upsert_match(
        session: Session,
        rekordbox_track: RekordboxTrack,
        spotify_track: SpotifyTrackCandidate,
        match_score: float,
        spotify_search_query_string: str,
    ) -> None:
        spotify_statement = insert(SpotifyTrack).values(spotify_track.as_spotify_track_row())
        spotify_statement = spotify_statement.on_conflict_do_update(
            index_elements=[SpotifyTrack.spotify_track_id],
            set_={
                column.name: getattr(spotify_statement.excluded, column.name)
                for column in SpotifyTrack.__table__.columns
                if column.name not in {"spotify_track_id", "fetched_at"}
            }
            | {"fetched_at": func.current_timestamp()},
        )
        session.execute(spotify_statement)

        match_statement = insert(RekordboxSpotifyMatch).values(
            rekordbox_track_id=rekordbox_track.rekordbox_track_id,
            spotify_track_id=spotify_track.spotify_track_id,
            match_score=match_score,
            spotify_search_query_string=spotify_search_query_string,
        )
        match_statement = match_statement.on_conflict_do_update(
            index_elements=[RekordboxSpotifyMatch.rekordbox_track_id],
            set_={
                "spotify_track_id": match_statement.excluded.spotify_track_id,
                "match_score": match_statement.excluded.match_score,
                "spotify_search_query_string": (
                    match_statement.excluded.spotify_search_query_string
                ),
                "matched_at": func.current_timestamp(),
            },
        )
        session.execute(match_statement)
