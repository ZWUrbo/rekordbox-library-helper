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
        description="Import tracks from a Rekordbox playlist into CSV and SQLite."
    )
    parser.add_argument("--playlist", required=True, help="Rekordbox playlist name or folder/path/name")
    parser.add_argument(
        "--xml-path",
        default=os.getenv("REKORDBOX_XML_PATH"),
        help="Path to Rekordbox XML export. Defaults to REKORDBOX_XML_PATH.",
    )
    parser.add_argument(
        "--export-dir",
        default=os.getenv("REKORDBOX_EXPORT_DIR", "data/exports"),
        help="Directory for CSV exports.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()

    if not args.xml_path:
        logger.error("Set REKORDBOX_XML_PATH or pass --xml-path.")
        return 2

    try:
        from sqlalchemy.exc import SQLAlchemyError

        from app.db.connection import create_tables, get_engine, session_scope
        from app.ingestion.playlist_importer import import_playlist

        engine = get_engine()
        create_tables(engine)
        with session_scope(engine) as session:
            count, csv_path = import_playlist(
                playlist_name=args.playlist,
                xml_path=args.xml_path,
                session=session,
                export_dir=args.export_dir,
            )
        logger.info("Import complete: %s tracks processed; CSV written to %s", count, csv_path)
        return 0
    except ModuleNotFoundError as exc:
        logger.error("Missing dependency %r. Run: pip install -r requirements.txt", exc.name)
        return 2
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except SQLAlchemyError:
        logger.exception("Database import failed")
        return 1
    except Exception:
        logger.exception("Playlist import failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
