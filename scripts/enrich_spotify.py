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


load_dotenv(ROOT_DIR / ".env")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich Rekordbox tracks with Spotify track IDs and metadata."
    )
    parser.add_argument("--limit", type=int, help="Maximum number of Rekordbox tracks to process.")
    parser.add_argument(
        "--minimum-match-score",
        type=float,
        default=float(os.getenv("SPOTIFY_MINIMUM_MATCH_SCORE", "0.75")),
        help="Accept matches at or above this score. Defaults to 0.75.",
    )
    parser.add_argument(
        "--spotify-search-limit",
        type=int,
        default=int(os.getenv("SPOTIFY_SEARCH_LIMIT", "3")),
        help="Number of Spotify search results to score per track. Defaults to 3.",
    )
    parser.add_argument(
        "--market",
        default=os.getenv("SPOTIFY_MARKET"),
        help="Optional ISO 3166-1 alpha-2 market code, such as US.",
    )
    parser.add_argument(
        "--spotify-rps",
        type=float,
        default=float(os.getenv("SPOTIFY_RPS", "2.0")),
        help="Maximum Spotify HTTP requests per second. Defaults to 2.0.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Search all Rekordbox tracks again, including existing matches.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.error("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
        return 2

    try:
        from app.db.connection import create_tables, get_engine, session_scope
        from app.enrichment.spotify import SpotifyClient, SpotifyEnrichmentService

        engine = get_engine()
        create_tables(engine)
        client = SpotifyClient(
            client_id,
            client_secret,
            market=args.market,
            rps=args.spotify_rps,
        )
        service = SpotifyEnrichmentService(
            client,
            minimum_match_score=args.minimum_match_score,
            search_limit=args.spotify_search_limit,
        )
        with session_scope(engine) as session:
            result = service.enrich_tracks(session, limit=args.limit, force=args.force)
        logger.info(
            "Spotify enrichment complete: processed=%s matched=%s unmatched=%s failed=%s",
            result.processed,
            result.matched,
            result.unmatched,
            result.failed,
        )
        return 1 if result.failed else 0
    except Exception:
        logger.exception("Spotify enrichment failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
