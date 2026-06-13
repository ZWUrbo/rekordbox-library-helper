#!/usr/bin/env python
import argparse
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from app.enrichment.dj_audio_analysis import (
    DEFAULT_AUDIO_ANALYSIS_PATH,
    DEFAULT_RAPIDAPI_HOST,
    DEFAULT_TRACK_ID_QUERY_PARAM,
)


load_dotenv(ROOT_DIR / ".env")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Enrich accepted Rekordbox-to-Spotify matches with DJ Track Audio "
            "Analysis API results."
        )
    )
    parser.add_argument("--limit", type=int, help="Maximum number of matched tracks to process.")
    parser.add_argument(
        "--rapidapi-host",
        default=os.getenv("RAPIDAPI_DJ_AUDIO_ANALYSIS_HOST", DEFAULT_RAPIDAPI_HOST),
        help="RapidAPI host for the DJ Track Audio Analysis API.",
    )
    parser.add_argument(
        "--endpoint-path",
        default=os.getenv("RAPIDAPI_DJ_AUDIO_ANALYSIS_PATH", DEFAULT_AUDIO_ANALYSIS_PATH),
        help="Endpoint path for Get Several Track's Audio Analysis.",
    )
    parser.add_argument(
        "--track-id-query-param",
        default=os.getenv(
            "RAPIDAPI_DJ_AUDIO_ANALYSIS_IDS_PARAM",
            DEFAULT_TRACK_ID_QUERY_PARAM,
        ),
        help="Query parameter name used for the comma-separated Spotify track IDs.",
    )
    parser.add_argument(
        "--rapidapi-rps",
        type=float,
        default=float(os.getenv("RAPIDAPI_DJ_AUDIO_ANALYSIS_RPS", "1.0")),
        help="Maximum RapidAPI HTTP requests per second. Defaults to 1.0.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fetch analysis again for matches that already have stored results.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()

    rapidapi_key = os.getenv("RAPIDAPI_DJ_AUDIO_ANALYSIS_KEY") or os.getenv("RAPIDAPI_KEY")
    if not rapidapi_key:
        logger.error("Set RAPIDAPI_DJ_AUDIO_ANALYSIS_KEY or RAPIDAPI_KEY.")
        return 2

    try:
        from app.db.connection import create_tables, get_engine, session_scope
        from app.enrichment.dj_audio_analysis import (
            DJAudioAnalysisClient,
            DJAudioAnalysisEnrichmentService,
        )

        engine = get_engine()
        create_tables(engine)
        client = DJAudioAnalysisClient(
            rapidapi_key=rapidapi_key,
            rapidapi_host=args.rapidapi_host,
            endpoint_path=args.endpoint_path,
            track_id_query_param=args.track_id_query_param,
            rps=args.rapidapi_rps,
        )
        service = DJAudioAnalysisEnrichmentService(client)
        with session_scope(engine) as session:
            result = service.enrich_matched_tracks(
                session,
                limit=args.limit,
                force=args.force,
            )
        logger.info(
            "DJ audio analysis enrichment complete: processed=%s enriched=%s failed=%s",
            result.processed,
            result.enriched,
            result.failed,
        )
        return 1 if result.failed else 0
    except Exception:
        logger.exception("DJ audio analysis enrichment failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
