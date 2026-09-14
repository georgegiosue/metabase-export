"""Export Metabase native questions to a tree of .sql files.

The output mirrors the Metabase collection tree, so a file's path is its card's
path. Every run rewrites the output directory: a card deleted or archived in
Metabase disappears from disk, which makes `git diff` an accurate record of what
changed. Directories whose name starts with an underscore are left alone, so
hand written SQL can live next to the exported files.

Run with --help for usage.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import __version__

FORBIDDEN_IN_FILENAME = re.compile(r"[/\\:*?\"<>|]")

COLLECTIONS_QUERY = """
    select id, name, location
    from collection
    where not archived and type is distinct from 'trash'
"""

CARDS_QUERY = """
    select c.id, c.collection_id, c.name, c.display,
           d.name as database_name, c.dataset_query::json as dataset_query
    from report_card c
    left join metabase_database d on d.id = c.database_id
    where not c.archived and c.query_type = 'native'
    order by c.id
"""


class QueryError(RuntimeError):
    pass


def run_query(sql: str, conn: dict) -> list[dict]:
    try:
        with psycopg.connect(row_factory=dict_row, **conn) as connection:
            return connection.execute(sql).fetchall()
    except psycopg.Error as error:
        raise QueryError(str(error).strip()) from error


def port_is_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_tunnel(ssh_host: str, local_port: int, remote_port: int) -> None:
    if port_is_open(local_port):
        return
    print(f"-> opening SSH tunnel {local_port}:localhost:{remote_port} via {ssh_host}")
    result = subprocess.run(
        ["ssh", "-f", "-N", "-L", f"{local_port}:localhost:{remote_port}", ssh_host],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Could not open the SSH tunnel: {result.stderr.strip()}")
    for _ in range(20):
        if port_is_open(local_port):
            return
        time.sleep(0.25)
    sys.exit(f"Tunnel started but port {local_port} is not accepting connections.")


def fetch_collection_paths(conn: dict, root: int | None) -> dict[int, str]:
    """Map collection id to its relative path, restricted to `root`'s subtree.

    `root` itself maps to "" and is dropped, so cards sitting directly in it are
    written to the top level of the output directory.
    """
    rows = run_query(COLLECTIONS_QUERY, conn)
    names = {row["id"]: row["name"] for row in rows}
    parents = {}
    for row in rows:
        segments = [s for s in row["location"].split("/") if s]
        parents[row["id"]] = int(segments[-1]) if segments else None

    def path_of(collection_id: int) -> str | None:
        segments: list[str] = []
        seen: set[int] = set()
        while collection_id is not None and collection_id != root:
            if collection_id in seen or collection_id not in names:
                return None
            seen.add(collection_id)
            segments.append(safe_name(names[collection_id]))
            collection_id = parents.get(collection_id)
        if root is not None and collection_id != root:
            return None
        return "/".join(reversed(segments))

    paths = {}
    for collection_id in names:
        path = path_of(collection_id)
        if path:
            paths[collection_id] = path
    return paths


def native_stage(dataset_query: dict) -> dict:
    """Metabase 50+ stores the query under stages[0]; older versions under native."""
    stages = dataset_query.get("stages") or []
    return stages[0] if stages else (dataset_query.get("native") or {})


def extract_sql(dataset_query: dict) -> str:
    stage = native_stage(dataset_query)
    return stage.get("native") or stage.get("query") or ""


def extract_parameters(dataset_query: dict) -> list[str]:
    # Sorted because the JSON key order is not stable and would cause empty diffs.
    return sorted(native_stage(dataset_query).get("template-tags") or {})


def safe_name(name: str) -> str:
    cleaned = FORBIDDEN_IN_FILENAME.sub("-", name).strip().rstrip(". ")
    return cleaned or "unnamed"


def render(card: dict, collection_path: str) -> str:
    body = extract_sql(card["dataset_query"]).replace("\r\n", "\n").replace("\r", "\n")
    body = "\n".join(line.rstrip() for line in body.split("\n")).strip("\n")

    header = [
        f"-- Card: {card['id']} | DB: {card['database_name'] or '?'} | Viz: {card['display'] or '?'}",
        f"-- Collection: {collection_path}",
    ]
    parameters = extract_parameters(card["dataset_query"])
    if parameters:
        header.append("-- Parameters: " + " ".join("{{%s}}" % p for p in parameters))

    return "\n".join(header) + "\n\n" + body + "\n"


def is_preserved(path: Path, output: Path) -> bool:
    return any(part.startswith("_") for part in path.relative_to(output).parts)


def clear_output(output: Path, dry_run: bool) -> int:
    if not output.exists():
        return 0
    removed = 0
    for sql_file in sorted(output.rglob("*.sql")):
        if is_preserved(sql_file, output):
            continue
        if not dry_run:
            sql_file.unlink()
        removed += 1
    if not dry_run:
        for directory in sorted(output.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if directory.is_dir() and not is_preserved(directory, output) and not any(directory.iterdir()):
                directory.rmdir()
    return removed


def plan_files(cards: list[dict], paths: dict[int, str], output: Path):
    """Resolve each card to a destination, skipping cards with no SQL."""
    targets, skipped = [], []
    taken: set[tuple[str, str]] = set()
    for card in cards:
        collection_path = paths.get(card["collection_id"])
        if collection_path is None:
            continue
        if not extract_sql(card["dataset_query"]).strip():
            skipped.append(card)
            continue
        name = safe_name(card["name"])
        key = (collection_path, name.lower())
        if key in taken:
            # Two cards named the same in one collection: disambiguate, never overwrite.
            name = f"{name} (card {card['id']})"
        taken.add(key)
        targets.append((card, collection_path, output / collection_path / f"{name}.sql"))
    return targets, skipped


HELP_EPILOG = """
examples:
  # export everything into ./reports
  export METABASE_PG_PASSWORD='secret'
  uvx metabase-export --output reports

  # one collection, database reachable only through a jump host
  uvx metabase-export --output reports --root-collection 4 \\
                      --port 15433 --ssh-host prod-box

  # show what would change, write nothing
  uvx metabase-export --output reports --dry-run

environment variables (each replaces the matching option):
  METABASE_EXPORT_DIR        METABASE_SSH_HOST
  METABASE_PG_HOST           METABASE_SSH_REMOTE_PORT
  METABASE_PG_PORT           METABASE_ROOT_COLLECTION
  METABASE_PG_USER           METABASE_PG_PASSWORD  (asked for if unset)
  METABASE_PG_DB

Set them in your shell profile and the command runs with no arguments.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(
        prog="metabase-export",
        description="Export Metabase native questions to .sql files that mirror the\n"
                    "collection tree.\n\n"
                    "Each run rewrites the output directory, so a card deleted in Metabase\n"
                    "disappears from disk and `git diff` shows exactly what changed.\n"
                    "Directories whose name starts with '_' are never touched.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    output = parser.add_argument_group("output")
    output.add_argument("--output", metavar="DIR", type=Path,
                        default=Path(env("METABASE_EXPORT_DIR", "reports")),
                        help="where to write the .sql tree (default: reports)")
    output.add_argument("--dry-run", action="store_true",
                        help="list what would change without writing anything")

    selection = parser.add_argument_group("what to export")
    selection.add_argument("--root-collection", metavar="ID", type=int,
                           default=int(env("METABASE_ROOT_COLLECTION")) if env("METABASE_ROOT_COLLECTION") else None,
                           help="only this collection and everything under it; "
                                "its own name is dropped from the paths (default: all collections)")

    database = parser.add_argument_group(
        "metabase application database",
        "The database Metabase itself runs on, not the databases it reports against.",
    )
    database.add_argument("--host", metavar="HOST", default=env("METABASE_PG_HOST", "127.0.0.1"),
                          help="default: 127.0.0.1")
    database.add_argument("--port", metavar="PORT", type=int, default=int(env("METABASE_PG_PORT", 5432)),
                          help="default: 5432")
    database.add_argument("--user", metavar="NAME", default=env("METABASE_PG_USER", "postgres"),
                          help="default: postgres")
    database.add_argument("--dbname", metavar="NAME", default=env("METABASE_PG_DB", "metabaseappdb"),
                          help="default: metabaseappdb")

    tunnel = parser.add_argument_group(
        "ssh tunnel (optional)",
        "Only needed when the database is not directly reachable.",
    )
    tunnel.add_argument("--ssh-host", metavar="HOST", default=env("METABASE_SSH_HOST"),
                        help="forward --port to this host, unless the port is already open")
    tunnel.add_argument("--remote-port", metavar="PORT", type=int,
                        default=int(env("METABASE_SSH_REMOTE_PORT", 5432)),
                        help="postgres port on the far side (default: 5432)")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    password = os.environ.get("METABASE_PG_PASSWORD") or getpass.getpass(
        f"Password for {args.user}@{args.host}:{args.port}/{args.dbname}: "
    )
    conn = {
        "host": args.host, "port": args.port, "user": args.user,
        "dbname": args.dbname, "password": password,
    }

    if args.ssh_host:
        open_tunnel(args.ssh_host, args.port, args.remote_port)

    try:
        paths = fetch_collection_paths(conn, args.root_collection)
        cards = run_query(CARDS_QUERY, conn)
    except QueryError as error:
        sys.exit(f"Failed to read Metabase: {error}")

    targets, skipped = plan_files(cards, paths, args.output)

    removed = clear_output(args.output, args.dry_run)
    for card, collection_path, destination in targets:
        if args.dry_run:
            print(f"  would write {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render(card, collection_path), encoding="utf-8")

    per_collection: dict[str, int] = {}
    for _, collection_path, _ in targets:
        per_collection[collection_path] = per_collection.get(collection_path, 0) + 1
    for collection_path in sorted(per_collection):
        print(f"  {per_collection[collection_path]:3d}  {collection_path}")

    action = "would be written" if args.dry_run else "written"
    gone = "would be removed" if args.dry_run else "removed"
    print(f"\n{len(targets)} files {action} across {len(per_collection)} collections "
          f"({removed} existing .sql {gone}).")
    if skipped:
        print(f"\nWarning: {len(skipped)} native cards have no SQL and were skipped:")
        for card in skipped:
            print(f"    card {card['id']}: {card['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
