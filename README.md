# DJ Library Helper

## Overview

DJ Library Helper is a working V1 recommendation pipeline for DJ library-aware
music discovery. It analyzes a DJ's existing Rekordbox library, enriches matched
tracks with catalog and audio-analysis metadata, learns style neighborhoods from
the library, and projects new Beatport Top 100 tracks into those neighborhoods.

The current MVP output is a CSV of recommended Beatport songs. A Beatport track
is treated as a current recommendation when it is successfully assigned to an
existing full-feature HDBSCAN EOM cluster learned from the Rekordbox library.
Tracks assigned to `noise` or missing required features are kept as review
candidates, but are not counted as current recommendations.

## Core Purpose

The application recommends songs for DJs, with a focus on new and high-signal
discovery sources.

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

In V1, the shipped recommendation logic is audio-feature and cluster based.
Lyrics, genre semantics, release recency, key/harmony-aware ranking, and
explainable scoring are active refinement areas.

## Target Audience

- Professional DJs
- DJ hobbyists
- Club DJs
- Open-format DJs
- Radio DJs
- Music curators seeking discovery tools

## Key Features

V1 capabilities include:

- Rekordbox library ingestion into local SQLite-backed storage
- Spotify matching for Rekordbox tracks and Beatport Top 100 discovery tracks
- DJ Track Audio Analysis enrichment for matched Spotify tracks
- HDBSCAN clustering over matched Rekordbox library tracks
- Full-feature EOM cluster projection for Beatport Top 100 tracks
- Current recommended-song export for Beatport tracks assigned to existing
  `full_eom_cluster` neighborhoods
- Notebook-based inspection of cluster profiles, representative tracks, boundary
  tracks, noise tracks, and recommendation candidates

Planned refinements include:

- Human-readable cluster labels
- Better recommendation ranking within assigned clusters
- Genre, key, harmony, lyrics, phrase, and release-date features
- Transition and mixing insights
- Recommendation explanations
- A more user-facing workflow beyond notebooks and CSV exports

## Intended Tech Stack

Current technologies include:

- **Language:** Python
- **DJ library ingestion:** Rekordbox XML export support via `pyrekordbox`
- **Database:** SQLite with SQLAlchemy
- **Configuration:** Environment variables via `python-dotenv`
- **Catalog matching:** Spotify API
- **Audio analysis:** DJ Track Audio Analysis via RapidAPI
- **Lyrics exploration:** Gemini batch enrichment
- **Modeling and analysis:** pandas, scikit-learn, HDBSCAN, notebooks
- **Data exports:** CSV files for inspection and downstream use

## Future Vision

DJ Library Helper could evolve into an intelligent DJ assistant that continuously analyzes a DJ's library, monitors newly released music, and surfaces tracks that are both musically compatible and creatively useful.

Over time, the platform could provide set-aware recommendations, transition suggestions, lyrical and thematic connections, explainable ranking, playlist generation, and integrations with DJ software or streaming catalogs. The long-term goal is to help DJs spend less time manually digging through releases and more time building distinctive, high-quality sets.

## Project Status

This project is at MVP / V1 delivery.

The pipeline is functional end to end for the current workflow:

1. Import Rekordbox library data.
2. Match library tracks to Spotify.
3. Enrich matched tracks with DJ audio-analysis features.
4. Cluster the library with HDBSCAN.
5. Pull Beatport Top 100 discovery input.
6. Match and enrich Beatport tracks.
7. Project Beatport tracks into the selected full-feature EOM cluster model.
8. Export Beatport tracks assigned to existing `full_eom_cluster` values as the
   current recommended songs.

The main refinements still underway are recommendation quality, ranking,
feature coverage, cluster interpretation, and a smoother user-facing workflow.

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
