#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from app.enrichment.gemini_lyrics import (
    DEFAULT_GEMINI_API_URL,
    DEFAULT_GEMINI_MODEL,
    SUCCEEDED_BATCH_STATES,
    TERMINAL_BATCH_STATES,
    GeminiBatchClient,
    extract_batch_state,
    extract_result_file_name,
    import_result_lines,
    list_lyrics_inputs,
    write_batch_jsonl,
)


load_dotenv(ROOT_DIR / ".env")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create, poll, and import Gemini batch jobs for playlist-track lyrics."
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Include tracks that already have a gemini_raw_lyrics row.",
    )
    parser.add_argument("--jsonl-path", type=Path)
    parser.add_argument("--manifest-path", type=Path)
    parser.add_argument(
        "--batch-name",
        help="Poll/import an existing Gemini batch instead of creating one.",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        help="Import a previously downloaded Gemini batch results JSONL file.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll until the batch reaches a terminal state.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=int(os.getenv("GEMINI_POLL_INTERVAL_SECONDS", "3600")),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("GEMINI_API_URL", DEFAULT_GEMINI_API_URL),
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()
    try:
        from app.db.connection import create_tables, get_engine, session_scope

        engine = get_engine()
        create_tables(engine)
        gemini_dir = ROOT_DIR / "data" / "interim" / "gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)

        if args.results_path:
            result_path = resolve_project_path(args.results_path)
            with session_scope(engine) as session:
                imported, failed = import_result_lines(
                    session,
                    result_path.read_text(encoding="utf-8").splitlines(),
                )
            logger.info("Imported Gemini lyrics: rows=%s failed=%s", imported, failed)
            return 1 if failed else 0

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("Set GEMINI_API_KEY.")
            return 2
        client = GeminiBatchClient(
            api_key=api_key,
            model=args.model,
            api_url=args.api_url,
        )

        if args.batch_name:
            manifest = load_manifest(args.manifest_path) if args.manifest_path else {}
            manifest["batch_name"] = args.batch_name
            return process_batch(
                client,
                engine,
                manifest,
                args.manifest_path,
                gemini_dir,
                args.wait,
                args.poll_interval_seconds,
            )

        with session_scope(engine) as session:
            rows = list_lyrics_inputs(
                session,
                limit=args.limit,
                force=args.force,
            )
        if not rows:
            logger.info("No eligible matched tracks need Gemini lyrics enrichment.")
            return 0

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        jsonl_path = (
            args.jsonl_path
            or gemini_dir / f"gemini_lyrics_batch_requests_{timestamp}.jsonl"
        ).resolve()
        manifest_path = (
            args.manifest_path
            or gemini_dir / f"gemini_lyrics_batch_manifest_{timestamp}.json"
        ).resolve()

        write_batch_jsonl(jsonl_path, rows)
        logger.info("Wrote Gemini lyrics JSONL: %s (requests=%s)", jsonl_path, len(rows))
        upload = client.upload_jsonl_file(jsonl_path, jsonl_path.stem)
        input_file_name = str((upload.get("file") or {}).get("name") or "").strip()
        if not input_file_name:
            raise RuntimeError("Gemini upload did not return a file name")

        batch = client.create_batch_job(
            input_file_name,
            f"gemini-raw-lyrics-{timestamp}",
        )
        batch_name = str(batch.get("name") or "").strip()
        if not batch_name:
            raise RuntimeError("Gemini batch creation did not return a batch name")
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": args.model,
            "batch_name": batch_name,
            "input_file_name": input_file_name,
            "input_jsonl_path": str(jsonl_path),
            "request_count": len(rows),
        }
        write_manifest(manifest_path, manifest)
        logger.info("Created Gemini lyrics batch: %s", batch_name)
        logger.info("Saved batch manifest: %s", manifest_path)
        return process_batch(
            client,
            engine,
            manifest,
            manifest_path,
            gemini_dir,
            args.wait,
            args.poll_interval_seconds,
        )
    except Exception:
        logger.exception("Gemini lyrics enrichment failed")
        return 1


def process_batch(
    client: GeminiBatchClient,
    engine,
    manifest: dict[str, Any],
    manifest_path: Path | None,
    gemini_dir: Path,
    wait: bool,
    poll_interval_seconds: int,
) -> int:
    logger = logging.getLogger(__name__)
    batch_name = str(manifest.get("batch_name") or "").strip()
    if not batch_name:
        raise ValueError("A Gemini batch name is required")

    payload = client.get_batch_job(batch_name)
    state = extract_batch_state(payload)
    logger.info("Gemini lyrics batch state: %s (%s)", state, batch_name)
    while wait and state not in TERMINAL_BATCH_STATES:
        time.sleep(max(1, poll_interval_seconds))
        payload = client.get_batch_job(batch_name)
        state = extract_batch_state(payload)
        logger.info("Gemini lyrics batch state: %s (%s)", state, batch_name)

    result_file_name = extract_result_file_name(payload)
    manifest["last_seen_state"] = state
    manifest["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if result_file_name:
        manifest["result_file_name"] = result_file_name
    if manifest_path:
        write_manifest(manifest_path.resolve(), manifest)

    if state not in SUCCEEDED_BATCH_STATES:
        if state in TERMINAL_BATCH_STATES:
            logger.error("Gemini batch ended in %s: %s", state, payload.get("error"))
            return 1
        logger.info(
            "Batch is still running. Re-run with --batch-name %s --wait to import it.",
            batch_name,
        )
        return 0
    if not result_file_name:
        raise RuntimeError("Succeeded Gemini batch has no result file")

    result_path = gemini_dir / f"{sanitize_name(batch_name)}_results.jsonl"
    result_path.write_bytes(client.download_result_file(result_file_name))
    manifest["result_jsonl_path"] = str(result_path.resolve())
    if manifest_path:
        write_manifest(manifest_path.resolve(), manifest)

    from app.db.connection import session_scope

    with session_scope(engine) as session:
        imported, failed = import_result_lines(
            session,
            result_path.read_text(encoding="utf-8").splitlines(),
        )
    logger.info("Imported Gemini lyrics: rows=%s failed=%s", imported, failed)
    return 1 if failed else 0


def load_manifest(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Manifest does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_project_path(path: Path) -> Path:
    if not path.is_absolute():
        path = ROOT_DIR / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Results file does not exist: {path}")
    return path


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sanitize_name(value: str) -> str:
    return value.replace("/", "_").replace(":", "_")


if __name__ == "__main__":
    raise SystemExit(main())
