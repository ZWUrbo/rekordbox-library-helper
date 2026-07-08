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
            "Extract Beatport Top 100, search Spotify IDs, then fetch DJ Track "
            "Audio Analysis API results into separate DataFrame CSV exports."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="data/exports/beatport_top100_analysis",
        help="Directory for the Spotify match and DJ audio analysis DataFrame CSVs.",
    )
    parser.add_argument(
        "--minimum-match-score",
        type=float,
        default=float(os.getenv("SPOTIFY_MINIMUM_MATCH_SCORE", "0.75")),
        help="Accept Spotify matches at or above this score. Defaults to 0.75.",
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
        help="Query parameter name used for comma-separated Spotify track IDs.",
    )
    parser.add_argument(
        "--rapidapi-rps",
        type=float,
        default=float(os.getenv("RAPIDAPI_DJ_AUDIO_ANALYSIS_RPS", "1.0")),
        help="Maximum RapidAPI HTTP requests per second. Defaults to 1.0.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()

    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not spotify_client_id or not spotify_client_secret:
        logger.error("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
        return 2

    rapidapi_key = os.getenv("RAPIDAPI_DJ_AUDIO_ANALYSIS_KEY") or os.getenv("RAPIDAPI_KEY")
    if not rapidapi_key:
        logger.error("Set RAPIDAPI_DJ_AUDIO_ANALYSIS_KEY or RAPIDAPI_KEY.")
        return 2

    try:
        from app.enrichment.dj_audio_analysis import (
            DJAudioAnalysisClient,
            enrich_spotify_dataframe_with_dj_audio_analysis,
        )
        from app.enrichment.spotify import SpotifyClient
        from app.enrichment.spotify_dataframe import enrich_track_dataframe_with_spotify_ids
        from app.ingestion.beatport_top100 import fetch_beatport_top_100_dataframe

        beatport_df = fetch_beatport_top_100_dataframe()
        spotify_client = SpotifyClient(
            spotify_client_id,
            spotify_client_secret,
            market=args.market,
            rps=args.spotify_rps,
        )
        spotify_matches_df = enrich_track_dataframe_with_spotify_ids(
            beatport_df,
            spotify_client,
            minimum_match_score=args.minimum_match_score,
            search_limit=args.spotify_search_limit,
        )
        matched_spotify_df = spotify_matches_df[
            spotify_matches_df["spotify_track_id"].notna()
        ].copy()

        audio_analysis_client = DJAudioAnalysisClient(
            rapidapi_key=rapidapi_key,
            rapidapi_host=args.rapidapi_host,
            endpoint_path=args.endpoint_path,
            track_id_query_param=args.track_id_query_param,
            rps=args.rapidapi_rps,
        )
        analysis_frames = enrich_spotify_dataframe_with_dj_audio_analysis(
            matched_spotify_df,
            audio_analysis_client,
        )

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        spotify_matches_df.to_csv(output_dir / "spotify_matches.csv", index=False)
        analysis_frames.track_analysis.to_csv(
            output_dir / "track_analysis.csv",
            index=False,
        )
        analysis_frames.rhythm.to_csv(output_dir / "rhythm.csv", index=False)
        analysis_frames.harmony.to_csv(output_dir / "harmony.csv", index=False)
        analysis_frames.score.to_csv(output_dir / "score.csv", index=False)
        analysis_frames.genres.to_csv(output_dir / "genres.csv", index=False)

        logger.info(
            "Beatport Top 100 analysis complete: beatport_rows=%s spotify_matches=%s "
            "analysis_rows=%s output_dir=%s",
            len(beatport_df),
            len(matched_spotify_df),
            len(analysis_frames.track_analysis),
            output_dir,
        )
        return 0
    except ModuleNotFoundError as exc:
        logger.error("Missing dependency %r. Run: pip install -r requirements.txt", exc.name)
        return 2
    except Exception:
        logger.exception("Beatport Top 100 analysis failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
