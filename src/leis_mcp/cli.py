"""Command line: `leis-mcp serve|index|update|status|warm|read`."""

import argparse
import json
import logging
import sys
import threading
from dataclasses import asdict
from typing import Any

from . import messages
from .catalog import GitRepository, IndexBuilder, PathParser
from .config import TRANSPORTS, Settings, default_host, default_port
from .errors import LeisMcpError
from .services import LegislationService

log = logging.getLogger(__name__)


class Cli:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or Settings.from_env()
        self._repository = GitRepository(self._settings)
        self._parser = self._build_parser()

    def run(self, argv: list[str] | None = None) -> None:
        args = self._parser.parse_args(argv)
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            stream=sys.stderr,  # stdout belongs to the stdio transport
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        try:
            args.handler(args)
        except LeisMcpError as exc:
            print(messages.CLI_ERROR.format(error=exc), file=sys.stderr)
            sys.exit(2)

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="leis-mcp", description="MCP server for Brazilian legislation.")
        parser.add_argument("-v", "--verbose", action="store_true")
        parser.set_defaults(handler=self._serve, transport=TRANSPORTS[0], host=default_host(), port=default_port())
        commands = parser.add_subparsers(title="commands")

        serve = commands.add_parser("serve", help="start the MCP server (default)")
        serve.add_argument("--transport", choices=TRANSPORTS, default=TRANSPORTS[0])
        serve.add_argument("--host", default=default_host(), help="default: $LEIS_MCP_HOST or 127.0.0.1")
        serve.add_argument("--port", type=int, default=default_port(), help="default: $LEIS_MCP_PORT, $PORT or 8000")
        serve.set_defaults(handler=self._serve)

        commands.add_parser("index", help="blobless clone if needed + path index").set_defaults(handler=self._index)
        commands.add_parser("update", help="git fetch + reindex").set_defaults(handler=self._update)
        commands.add_parser("status", help="indexed commit, totals, cache").set_defaults(handler=self._status)

        warm = commands.add_parser("warm", help="prefetch norms into the cache (default: aliased laws)")
        warm.add_argument("--no-aliases", action="store_true")
        warm.add_argument("--sphere")
        warm.add_argument("--kind")
        warm.add_argument("--year", type=int)
        warm.add_argument("--limit", type=int)
        warm.set_defaults(handler=self._warm)

        read = commands.add_parser("read", help="read a provision without an MCP client")
        read.add_argument("norm")
        read.add_argument("article")
        read.add_argument("-p", "--paragraph")
        read.add_argument("-c", "--clause", help="inciso")
        read.add_argument("-s", "--subclause", help="alínea")
        read.add_argument("-b", "--block")
        read.set_defaults(handler=self._read)
        return parser

    def _serve(self, args: argparse.Namespace) -> None:
        from .server import LeisServer  # keeps the MCP stack out of the other commands

        if not self._settings.db_path.exists():
            log.info("Index missing: building it (first run only)...")
            self._builder().build()
        if args.transport != "stdio" and self._settings.warm_on_start:
            threading.Thread(target=self._warm_in_background, name="warm", daemon=True).start()
        LeisServer(self._service()).run(args.transport, args.host, args.port, self._settings.api_key)

    def _warm_in_background(self) -> None:
        try:  # own service instance: its own SQLite connection and HTTP client
            result = self._service().warm_up()
            log.info("Warm-up done: %s norms, %s errors", result["norms"], len(result["errors"]))
        except Exception:
            log.exception("Warm-up failed (the server keeps running)")

    def _index(self, _: argparse.Namespace) -> None:
        self._print(asdict(self._builder().build()))

    def _update(self, _: argparse.Namespace) -> None:
        self._repository.clone()
        self._print(asdict(self._builder().build(self._repository.fetch_latest())))

    def _status(self, _: argparse.Namespace) -> None:
        self._print(self._service().status())

    def _warm(self, args: argparse.Namespace) -> None:
        self._print(self._service().warm_up(not args.no_aliases, args.sphere, args.kind, args.year, args.limit))

    def _read(self, args: argparse.Namespace) -> None:
        provision = self._service().read_provision(
            args.norm, args.article, args.paragraph, args.clause, args.subclause, args.block
        )
        self._print(provision)

    def _service(self) -> LegislationService:
        return LegislationService.from_settings(self._settings, self._repository)

    def _builder(self) -> IndexBuilder:
        return IndexBuilder(self._settings, self._repository, PathParser())

    @staticmethod
    def _print(payload: dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    Cli().run(argv)
