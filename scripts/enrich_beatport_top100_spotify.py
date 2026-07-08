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
        description="Extract Beatport Top 100 into a DataFrame and search Spotify IDs."
    )
    parser.add_argument(
        "--output-csv",
        default="data/exports/beatport_top100_spotify.csv",
        help="Optional CSV path for inspecting the transient enriched DataFrame.",
    )
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
        from app.enrichment.spotify import SpotifyClient
        from app.enrichment.spotify_dataframe import enrich_track_dataframe_with_spotify_ids
        from app.ingestion.beatport_top100 import fetch_beatport_top_100_dataframe

        beatport_df = fetch_beatport_top_100_dataframe()
        client = SpotifyClient(
            client_id,
            client_secret,
            market=args.market,
            rps=args.spotify_rps,
        )
        enriched_df = enrich_track_dataframe_with_spotify_ids(
            beatport_df,
            client,
            minimum_match_score=args.minimum_match_score,
            search_limit=args.spotify_search_limit,
        )

        matched_count = int(enriched_df["spotify_track_id"].notna().sum())
        logger.info(
            "Beatport Top 100 Spotify lookup complete: rows=%s matched=%s",
            len(enriched_df),
            matched_count,
        )

        if args.output_csv:
            output_path = Path(args.output_csv)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            enriched_df.to_csv(output_path, index=False)
            logger.info("Wrote enriched DataFrame to %s", output_path)

        return 0
    except ModuleNotFoundError as exc:
        logger.error("Missing dependency %r. Run: pip install -r requirements.txt", exc.name)
        return 2
    except Exception:
        logger.exception("Beatport Top 100 Spotify lookup failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
