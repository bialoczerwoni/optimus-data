# Optimus Data

Daily generated streaming-service data for the Optimus Kodi add-on.

The repository is intentionally separate from the add-on source. It only stores generated JSON and the script/workflow needed to refresh it.

## Output

The add-on can read:

```text
data/streaming_catalog.json
```

The JSON contains service/region sections for:

- Top 10 movies
- Top 10 shows
- Recently added movies and shows

## GitHub Action

`.github/workflows/update-streaming-catalog.yml` runs once per day and can also be started manually from the Actions tab.

To include TMDB ids and recently added lists, add one repository secret:

```text
TMDB_READ_TOKEN
```

Use a TMDB v4 read access token. Without it, the script still writes FlixPatrol title/rank data where available, but TMDB ids and recently added entries are skipped.

## Local Run

```powershell
python -m pip install -r requirements.txt
python scripts/update_streaming_catalog.py --output data/streaming_catalog.json
```

For a quick smoke test:

```powershell
python scripts/update_streaming_catalog.py --services netflix --regions CA --skip-recent --output data/streaming_catalog.json
```

