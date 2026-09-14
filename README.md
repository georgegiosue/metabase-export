# metabase-export

Export Metabase questions to `.sql` files that mirror your collection tree, so
your SQL lives in git.

```bash
export METABASE_PG_PASSWORD='secret'
uvx metabase-export --output reports
```

```
reports/
  Occupancy/
    Rooms/TOTAL HOURS USED.sql
  Incidents/OPEN INCIDENTS.sql
```

```sql
-- Card: 129 | DB: Incidents | Viz: scalar
-- Collection: Incidents
-- Parameters: {{date}}

SELECT COUNT(*) FROM "Incident" WHERE {{date}}
```

**Every run rewrites the output directory.** A card deleted or archived in
Metabase disappears from disk — it is a mirror, not a merge. Directories whose
name starts with `_` are never touched.

## Usage

Needs read access to the **Metabase application database** (`metabaseappdb`),
not to the databases Metabase reports against.

```bash
uvx metabase-export --output reports                      # everything
uvx metabase-export --output reports --root-collection 4  # one collection
uvx metabase-export --output reports --dry-run            # change nothing
uvx metabase-export --output reports --port 15433 --ssh-host prod-box
```

Collection id comes from its URL: `/collection/4-my-reports` → `4`.

| Option | Default | |
|---|---|---|
| `--output DIR` | `reports` | where to write the tree |
| `--dry-run` | off | list changes, write nothing |
| `--root-collection ID` | all | export only this collection and its children |
| `--host` `--port` `--user` `--dbname` | `127.0.0.1` `5432` `postgres` `metabaseappdb` | connection |
| `--ssh-host HOST` | — | tunnel `--port` through this host if it is closed |
| `--remote-port PORT` | `5432` | Postgres port on the far side |

Each option has an env var (`METABASE_EXPORT_DIR`, `METABASE_PG_HOST`, …; see
`--help`), so a project can pin its settings and run with no arguments. The
password comes from `METABASE_PG_PASSWORD` or a prompt — never from a flag.

## Notes

- Only native (SQL) questions. Query-builder questions have no SQL and are skipped.
- Reads MBQL5 (Metabase 50+) and the older `native.query` format.
- Output is deterministic: two runs in a row leave the tree untouched.
- Files keep `{{parameter}}` placeholders, so they are not runnable outside Metabase.

## Development

```bash
uv sync && uv run pytest
```

MIT
