from __future__ import annotations

import json
import logging
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db.models import (
    GeminiRawLyrics,
    RekordboxTrack,
)


logger = logging.getLogger(__name__)

DEFAULT_GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
TERMINAL_BATCH_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
    # Older API responses used the BATCH_STATE prefix.
    "BATCH_STATE_SUCCEEDED",
    "BATCH_STATE_FAILED",
    "BATCH_STATE_CANCELLED",
    "BATCH_STATE_EXPIRED",
}
SUCCEEDED_BATCH_STATES = {"JOB_STATE_SUCCEEDED", "BATCH_STATE_SUCCEEDED"}

LYRICS_GENRES = (
    "Dancehall",
    "Drill Rap",
    "Funk",
    "Hip-Hop",
    "Atmospheric Trap",
    "Alternative R&B",
    "Pop",
    "R&B",
    "Rage Rap",
    "Rap",
    "Rap & Hip-Hop",
    "Reggae",
    "Reggaeton",
    "Soft Rock",
)

PROMPT_TEMPLATE = '''You are given the following song metadata:
* Song Title: "{song_title}"
* Artist: "{artist}"

Compile the complete lyrics and return only a valid JSON object.
Requirements:
* Include every section in the order it appears.
* Do not merge or omit sections.
* Number repeated sections.
* Each lyric line must be a separate array element.
* Represent blank lines as an empty string ("").
* Return only JSON.
Output format:
{{
  "title": "{song_title}",
  "artist": "{artist}",
  "lyrics": [
    ["intro", [
      "First line",
      "Second line"
    ]],
    ["verse_1", [
      "Line one",
      "Line two",
      "Line three"
    ]],
    ["chorus_1", [
      "Sing along",
      "Everybody"
    ]],
    ["verse_2", [
      "Another line",
      "Another line"
    ]]
  ]
}}'''


class GeminiAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeminiLyricsInput:
    rekordbox_track_id: int
    song_title: str
    artist: str

    @property
    def batch_key(self) -> str:
        return str(self.rekordbox_track_id)


class GeminiBatchClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        api_url: str = DEFAULT_GEMINI_API_URL,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    def upload_jsonl_file(self, path: Path, display_name: str) -> dict[str, Any]:
        total_bytes = path.stat().st_size
        start = self._request(
            "POST",
            f"{self.api_url.replace('/v1beta', '')}/upload/v1beta/files",
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(total_bytes),
                "X-Goog-Upload-Header-Content-Type": "application/jsonl",
                "Content-Type": "application/json",
            },
            payload={"file": {"display_name": display_name}},
            return_response=True,
        )
        upload_url = start.headers.get("x-goog-upload-url")
        if not upload_url:
            raise GeminiAPIError("Gemini upload did not return x-goog-upload-url")
        return self._request(
            "POST",
            upload_url,
            headers={
                "Content-Length": str(total_bytes),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            data=path.read_bytes(),
        )

    def create_batch_job(self, input_file_name: str, display_name: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.api_url}/models/{self.model}:batchGenerateContent",
            payload={
                "batch": {
                    "display_name": display_name,
                    "input_config": {"file_name": input_file_name},
                }
            },
        )

    def get_batch_job(self, batch_name: str) -> dict[str, Any]:
        return self._request("GET", f"{self.api_url}/{batch_name.lstrip('/')}")

    def download_result_file(self, file_name: str) -> bytes:
        response = self._request(
            "GET",
            (
                f"{self.api_url.replace('/v1beta', '')}/download/v1beta/"
                f"{file_name.lstrip('/')}:download?alt=media"
            ),
            return_response=True,
        )
        return response.read()

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        data: bytes | None = None,
        return_response: bool = False,
    ) -> Any:
        request_headers = {"x-goog-api-key": self.api_key, **(headers or {})}
        body = data
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(url, data=body, headers=request_headers, method=method)

        for attempt in range(5):
            try:
                response = urlopen(request, timeout=self.timeout, context=self._ssl_context)
                if return_response:
                    return response
                return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                if exc.code != 429 and exc.code < 500:
                    raise GeminiAPIError(
                        f"Gemini API HTTP {exc.code}: {error_body[:500]}"
                    ) from exc
                if attempt == 4:
                    raise GeminiAPIError(
                        f"Gemini API HTTP {exc.code}: {error_body[:500]}"
                    ) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == 4:
                    raise GeminiAPIError(f"Gemini API request failed: {exc}") from exc
            time.sleep(min(20.0, 0.8 * (2**attempt)))
        raise RuntimeError("unreachable")


def list_lyrics_inputs(
    session: Session,
    limit: int | None = None,
    force: bool = False,
) -> list[GeminiLyricsInput]:
    target_genres = tuple(genre.casefold() for genre in LYRICS_GENRES)
    statement = (
        select(
            RekordboxTrack.rekordbox_track_id,
            RekordboxTrack.title,
            RekordboxTrack.artist,
        )
        .where(func.lower(func.trim(RekordboxTrack.genre)).in_(target_genres))
        .where(RekordboxTrack.title.is_not(None), RekordboxTrack.artist.is_not(None))
        .order_by(RekordboxTrack.rekordbox_track_id)
    )
    if not force:
        statement = statement.outerjoin(
            GeminiRawLyrics,
            GeminiRawLyrics.rekordbox_track_id == RekordboxTrack.rekordbox_track_id,
        ).where(GeminiRawLyrics.rekordbox_track_id.is_(None))
    if limit is not None:
        statement = statement.limit(limit)

    return [
        GeminiLyricsInput(
            rekordbox_track_id=row.rekordbox_track_id,
            song_title=row.title.strip(),
            artist=row.artist.strip(),
        )
        for row in session.execute(statement)
        if row.title.strip() and row.artist.strip()
    ]


def build_batch_request_line(row: GeminiLyricsInput) -> dict[str, Any]:
    prompt = PROMPT_TEMPLATE.format(
        song_title=row.song_title,
        artist=row.artist,
    )
    return {
        "key": row.batch_key,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {
                "temperature": 0,
                "candidateCount": 1,
            },
        },
    }


def write_batch_jsonl(path: Path, rows: Iterable[GeminiLyricsInput]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(build_batch_request_line(row), ensure_ascii=False) + "\n")


def extract_generated_text(payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts) or None


def normalize_generated_json_text(generated_text: str) -> str:
    raw_json = generated_text.strip()
    if raw_json.startswith("```"):
        first_newline = raw_json.find("\n")
        if first_newline == -1:
            raise json.JSONDecodeError("Gemini response contains an empty code fence", raw_json, 0)
        raw_json = raw_json[first_newline + 1 :].strip()
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3].strip()
    return raw_json


def parse_batch_result_line(raw_line: str) -> tuple[int, str]:
    parsed = json.loads(raw_line)
    key = parsed.get("key")
    if key is None and isinstance(parsed.get("metadata"), dict):
        key = parsed["metadata"].get("key")
    try:
        rekordbox_track_id = int(str(key))
    except (TypeError, ValueError) as exc:
        raise ValueError("Gemini result is missing a numeric Rekordbox track key") from exc

    if parsed.get("error"):
        raise ValueError(f"Gemini batch item failed: {parsed['error']}")
    response = parsed.get("response")
    if not isinstance(response, dict):
        raise ValueError("Gemini batch result is missing its response")
    raw_json = extract_generated_text(response)
    if not raw_json:
        raise ValueError("Gemini batch result contains no generated text")
    raw_json = normalize_generated_json_text(raw_json)
    decoded = json.loads(raw_json)
    if not isinstance(decoded, dict):
        raise ValueError("Gemini lyrics response must be a JSON object")
    return rekordbox_track_id, raw_json


def import_result_lines(session: Session, raw_lines: Iterable[str]) -> tuple[int, int]:
    imported = 0
    failed = 0
    for raw_line in raw_lines:
        if not raw_line.strip():
            continue
        try:
            rekordbox_track_id, raw_json = parse_batch_result_line(raw_line)
            track_exists = session.scalar(
                select(RekordboxTrack.rekordbox_track_id).where(
                    RekordboxTrack.rekordbox_track_id == rekordbox_track_id
                )
            )
            if track_exists is None:
                raise ValueError(
                    f"No Rekordbox track exists for track ID {rekordbox_track_id}"
                )
            values = {
                "rekordbox_track_id": rekordbox_track_id,
                "raw_json": raw_json,
            }
            statement = insert(GeminiRawLyrics).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[GeminiRawLyrics.rekordbox_track_id],
                set_={
                    "raw_json": statement.excluded.raw_json,
                    "fetched_at": func.current_timestamp(),
                },
            )
            session.execute(statement)
            imported += 1
        except (ValueError, json.JSONDecodeError):
            failed += 1
            logger.exception("Could not import Gemini lyrics result line")
    return imported, failed


def extract_batch_state(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("metadata"), payload.get("response")):
        if not isinstance(container, dict):
            continue
        state = container.get("state")
        if isinstance(state, str) and state.strip():
            return state.strip()
    return "JOB_STATE_UNSPECIFIED"


def extract_result_file_name(payload: dict[str, Any]) -> str | None:
    for container_name in ("dest", "output", "response", "metadata"):
        container = payload.get(container_name)
        if not isinstance(container, dict):
            continue
        value = container.get("fileName") or container.get("file_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
        nested = container.get("output")
        if isinstance(nested, dict):
            value = nested.get("responsesFile") or nested.get("responses_file")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
