import argparse
import os
from contextlib import closing
from pathlib import Path

from . import db, discovery
from .config import db_path_from_env, load_settings


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(load_settings()), host=args.host, port=args.port)


def _open_db():
    conn = db.connect(db_path_from_env())
    db.migrate(conn)
    return closing(conn)


def _discovery_summary(args: argparse.Namespace) -> None:
    with _open_db() as conn:
        print(discovery.format_summary(discovery.summarize(discovery.iter_captures(conn))))


def _export_fixtures(args: argparse.Namespace) -> None:
    with _open_db() as conn:
        written = discovery.export_fixtures(conn, Path(args.out), args.match)
    print(f"wrote {len(written)} fixture(s) to {args.out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="duocompanion")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default=os.environ.get("DUO_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("DUO_PORT", "8000")))
    serve.set_defaults(func=_serve)

    summary = sub.add_parser("discovery-summary", help="list captured endpoints and their JSON keys")
    summary.set_defaults(func=_discovery_summary)

    export = sub.add_parser("export-fixtures", help="write discovery captures as scrubbed test fixtures")
    export.add_argument("--out", default="tests/fixtures/discovery")
    export.add_argument("--match", help="only captures whose URL matches this regex")
    export.set_defaults(func=_export_fixtures)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
