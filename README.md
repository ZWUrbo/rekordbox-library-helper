# DJ Library Helper

## Overview

DJ Library Helper is an early-stage tool for helping DJs discover relevant new music based on the tracks already in their library. The project is intended to analyze a DJ's existing collection, understand musical context, and recommend songs that are likely to fit their sound, sets, and creative mixing style.

The project currently includes initial groundwork for importing Rekordbox playlist data into local CSV and SQLite-backed storage. Recommendation, audio analysis, lyric analysis, and user-facing workflows are still being defined.

## Core Purpose

The application recommends songs for DJs, with a focus on newly released music.

Recommendations should be based on musical similarity to tracks already present in the DJ's library, including factors such as:

- Energy
- Tempo
- Key
- Genre
- Mood
- Groove
- Rhythm and danceability
- Era, style, and contextual fit
- Other relevant musical features

The system should also identify opportunities for creative transitions and wordplay by recommending tracks that share lyrics, phrases, themes, hooks, or vocal references with songs in the DJ's collection. This can help DJs find clever blends, thematic moments, call-and-response transitions, and set-building ideas that go beyond simple audio similarity.

## Target Audience

- Professional DJs
- DJ hobbyists
- Club DJs
- Open-format DJs
- Radio DJs
- Music curators seeking discovery tools

## Key Features

Planned and emerging capabilities include:

- New music discovery
- Library-aware recommendations
- Audio-feature matching
- Lyric and phrase overlap detection
- Transition and mixing insights
- Recommendation explanations

## Intended Tech Stack

Current and intended technologies include:

- **Language:** Python
- **DJ library ingestion:** Rekordbox XML export support via `pyrekordbox`
- **Database:** SQLite with SQLAlchemy
- **Configuration:** Environment variables via `python-dotenv`
- **Data exports:** CSV files for inspection and downstream analysis

Implementation details still to be defined:

- Audio feature extraction provider or library
- Lyrics and phrase matching data source
- New release data source
- Recommendation ranking model
- User interface or command-line workflow
- Deployment or packaging approach

## Future Vision

DJ Library Helper could evolve into an intelligent DJ assistant that continuously analyzes a DJ's library, monitors newly released music, and surfaces tracks that are both musically compatible and creatively useful.

Over time, the platform could provide set-aware recommendations, transition suggestions, lyrical and thematic connections, explainable ranking, playlist generation, and integrations with DJ software or streaming catalogs. The long-term goal is to help DJs spend less time manually digging through releases and more time building distinctive, high-quality sets.

## Project Status

This project is in the planning and early development phase.

Current focus areas:

- Importing and storing DJ library metadata
- Matching Rekordbox tracks to Spotify catalog metadata
- Defining the recommendation model
- Exploring audio, lyric, and release-data sources
- Designing the first practical discovery workflow

## Spotify Enrichment

Create a Spotify developer app and set its client credentials in `.env`:

```text
SPOTIFY_CLIENT_ID=your-client-id
SPOTIFY_CLIENT_SECRET=your-client-secret
SPOTIFY_MARKET=US
```

Then enrich all Rekordbox tracks that do not already have an accepted match:

```bash
.venv/bin/python scripts/enrich_spotify.py
```

Useful options:

```bash
.venv/bin/python scripts/enrich_spotify.py --limit 25
.venv/bin/python scripts/enrich_spotify.py --spotify-search-limit 5 --spotify-rps 2.0 --minimum-match-score 0.85 --force
```

The stage searches Spotify using a cleaned track title, the first listed artist,
and album when available. It scores each returned result using title, artist, and
album similarity, then writes the highest-scoring accepted result to:

- `spotify_tracks`: Spotify metadata keyed by `spotify_track_id`
- `rekordbox_spotify_matches`: one accepted Spotify match, derived score, and
  search query string per `rekordbox_track_id`
- `rekordbox_tracks.spotify_search_query_string`: the same Spotify search query
  string retained on the source track row

### Beatport Top 100 Spotify Lookup

The Beatport Top 100 list is treated as transient discovery input, so it is
extracted into a pandas DataFrame rather than stored in SQLite. The extraction
uses Beautiful Soup selectors from the Beatport track table, then reuses the
Spotify title/artist search and scoring logic above to append Spotify IDs:

```bash
.venv/bin/python scripts/enrich_beatport_top100_spotify.py
```

By default, the enriched DataFrame is also written to
`data/exports/beatport_top100_spotify.csv` for inspection. Useful options:

```bash
.venv/bin/python scripts/enrich_beatport_top100_spotify.py --spotify-search-limit 5 --minimum-match-score 0.85
.venv/bin/python scripts/enrich_beatport_top100_spotify.py --output-csv /tmp/beatport_top100_spotify.csv
```

To continue from the matched Beatport Top 100 Spotify IDs into DJ Track Audio
Analysis, set the RapidAPI credentials described below and run:

```bash
.venv/bin/python scripts/enrich_beatport_top100_analysis.py
```

This keeps the Beatport workflow out of SQLite. It writes separate pandas
DataFrame CSV exports under `data/exports/beatport_top100_analysis/`:

- `spotify_matches.csv`
- `track_analysis.csv`
- `rhythm.csv`
- `harmony.csv`
- `score.csv`
- `genres.csv`

Each analysis DataFrame uses `spotify_track_id` as the join key, so the files
can be merged into a holistic Beatport Top 100 analysis without relying on
Rekordbox table IDs.

The `notebooks/hdbscan_track_clustering.ipynb` notebook can then project the
analyzed Beatport tracks into the existing full-feature HDBSCAN EOM cluster
profiles learned from the Rekordbox library. For now, Beatport tracks that are
successfully assigned to one of these existing `full_eom_cluster` values are the
current recommended songs: they are new/discovery candidates whose
audio-analysis profile falls inside a learned neighborhood of the DJ's library.
Tracks assigned to `noise` or missing required features remain useful review
candidates, but they are not treated as current recommendations. The notebook
exports those assigned Beatport tracks separately as
`data/exports/beatport_top100_current_recommended_songs.csv`.

## DJ Track Audio Analysis Enrichment

After Spotify matching, set RapidAPI credentials in `.env`:

```text
RAPIDAPI_DJ_AUDIO_ANALYSIS_KEY=your-rapidapi-key
RAPIDAPI_DJ_AUDIO_ANALYSIS_HOST=dj-track-audio-analysis-api.p.rapidapi.com
RAPIDAPI_DJ_AUDIO_ANALYSIS_PATH=/v2/audio-analysis
RAPIDAPI_DJ_AUDIO_ANALYSIS_IDS_PARAM=ids
```

Then enrich matched tracks that do not already have stored DJ analysis results:

```bash
.venv/bin/python scripts/enrich_dj_audio_analysis.py
```

Useful options:

```bash
.venv/bin/python scripts/enrich_dj_audio_analysis.py --limit 25
.venv/bin/python scripts/enrich_dj_audio_analysis.py --rapidapi-rps 1.0 --force
```

The stage reads accepted matches from `rekordbox_spotify_matches`, batches up to
five Spotify track IDs per RapidAPI request, skips rows already present in
`track_analysis`, and stores the five response categories in:

- `track_analysis`
- `rhythm`
- `harmony`
- `score`
- `genres`

## Gemini Lyrics Batch Enrichment

Set a Gemini API key in `.env`:

```text
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash
```

Create a Gemini JSONL batch for all playlist tracks whose Rekordbox genre is in
the configured lyric genres. Each request enables Grounding with Google Search.
Gemini does not support Search tool use together with JSON response MIME mode,
so JSON-only output is enforced by the prompt and validated during import:

```bash
.venv/bin/python scripts/enrich_gemini_lyrics.py --limit 1000
```

The command writes its request JSONL and manifest under `data/interim/gemini/`.
If the job is still running, poll and import it later using the batch name logged
by the first command:

```bash
.venv/bin/python scripts/enrich_gemini_lyrics.py --batch-name <batch-name> --wait
```

Completed responses are validated as JSON objects and stored without reshaping
in `gemini_raw_lyrics.raw_json`, alongside `rekordbox_track_id`. Existing rows
are skipped unless `--force` is supplied.
