# metabase-export

Export Metabase questions to a tree of `.sql` files that mirrors your collection
tree, so your SQL lives in git instead of only inside Metabase.

```bash
export METABASE_PG_PASSWORD='secret'
uvx metabase-export --output reports
```

```
reports/
  Occupancy/
    Rooms/
      TOTAL HOURS USED.sql
    Scanners/
      SCANNER UTILIZATION.sql
  Incidents/
    OPEN INCIDENTS.sql
```

Each file carries a small header and the query exactly as Metabase has it:

```sql
-- Card: 129 | DB: Incidents | Viz: scalar
-- Collection: Incidents
-- Parameters: {{career}} {{code}} {{date}}

SELECT COUNT(*) AS incidents
FROM "Incident"
WHERE {{date}} AND {{code}} AND {{career}}
```

## Why

Metabase keeps its SQL in a database, which means no history, no review, no diff.
This reads the Metabase application database and writes the queries to disk, so
`git diff` tells you exactly what changed and who changed it.

**Every run rewrites the output directory.** A card deleted or archived in
Metabase disappears from disk — that is the point: the export is a mirror, not a
merge. Directories whose name starts with `_` are never touched, so hand written
SQL can live alongside the exported files.

## Install

Nothing to install with [uv](https://docs.astral.sh/uv/):

```bash
uvx metabase-export --help
```

Or install it permanently:

```bash
uv tool install metabase-export     # or: pipx install metabase-export
```

## Usage

You need read access to the **Metabase application database** (the one Metabase
itself runs on, usually `metabaseappdb`), not to the databases it reports against.

```bash
# export everything into ./reports
uvx metabase-export --output reports

# one collection and its children; its own name is dropped from the paths
uvx metabase-export --output reports --root-collection 4

# database only reachable through a jump host: the tunnel is opened for you
uvx metabase-export --output reports --port 15433 --ssh-host prod-box

# see what would change, write nothing
uvx metabase-export --output reports --dry-run
```

Find a collection id in its Metabase URL: `/collection/4-my-reports` → `4`.

### Options

| Option | Default | |
|---|---|---|
| `--output DIR` | `reports` | where to write the `.sql` tree |
| `--dry-run` | off | list what would change, write nothing |
| `--root-collection ID` | all | export only this collection and its children |
| `--host HOST` | `127.0.0.1` | Postgres host |
| `--port PORT` | `5432` | Postgres port |
| `--user NAME` | `postgres` | Postgres user |
| `--dbname NAME` | `metabaseappdb` | Metabase application database |
| `--ssh-host HOST` | — | open an SSH tunnel if `--port` is closed |
| `--remote-port PORT` | `5432` | Postgres port on the far side of the tunnel |

Every option has an environment variable equivalent — `METABASE_EXPORT_DIR`,
`METABASE_PG_HOST`, `METABASE_PG_PORT`, `METABASE_PG_USER`, `METABASE_PG_DB`,
`METABASE_SSH_HOST`, `METABASE_SSH_REMOTE_PORT`, `METABASE_ROOT_COLLECTION` —
so a project can pin its settings once and run the command with no arguments.
The password is read from `METABASE_PG_PASSWORD`, or asked for if unset; it is
never taken from a command line argument, where it would land in your shell
history.

### Keeping it up to date

```bash
uvx metabase-export --output reports
git diff --stat        # exactly what changed in Metabase
git commit -am "sync reports"
```

## Notes

- Only **native (SQL) questions** are exported. Query-builder questions have no
  SQL to write and are skipped.
- Metabase 50+ stores queries in MBQL5 (`dataset_query.stages[0].native`); older
  versions use `dataset_query.native.query`. Both are read.
- Template parameters are listed alphabetically in the header, because the JSON
  key order is not stable and would otherwise produce diffs with no real change.
- Output is deterministic: running twice in a row leaves the tree untouched.
- Exported files keep Metabase's `{{parameter}}` placeholders, so they are not
  runnable verbatim outside Metabase.

## Development

```bash
uv sync
uv run pytest
uv build
uv publish
```

## License

MIT
