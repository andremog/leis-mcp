"""Command line: `leis-mcp serve|index|update|status|warm|read`."""

import argparse
import json
import logging
import shutil
import sys
from dataclasses import asdict
from typing import Any

from . import messages
from .catalog import GitRepository, IndexBuilder, IndexSnapshot, PathParser
from .config import DEFAULT_HOST, DEFAULT_PORT, TRANSPORTS, Settings
from .errors import LeisMcpError, SnapshotError
from .models import IndexStats
from .services import LegislationService, ServiceProvider

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
        parser.set_defaults(handler=self._serve, transport=TRANSPORTS[0], host=DEFAULT_HOST, port=DEFAULT_PORT)
        commands = parser.add_subparsers(title="commands")

        serve = commands.add_parser("serve", help="start the MCP server (default)")
        serve.add_argument("--transport", choices=TRANSPORTS, default=TRANSPORTS[0])
        serve.add_argument("--host", default=DEFAULT_HOST)
        serve.add_argument("--port", type=int, default=DEFAULT_PORT)
        serve.set_defaults(handler=self._serve)

        index = commands.add_parser("index", help="download the prebuilt index (git build if unavailable)")
        index.add_argument("--from-git", action="store_true", help="blobless clone + local build instead")
        index.set_defaults(handler=self._index)
        update = commands.add_parser("update", help="download the newest prebuilt index if it changed")
        update.add_argument("--from-git", action="store_true", help="git fetch + local rebuild instead")
        update.set_defaults(handler=self._update)
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

        services = ServiceProvider(self._settings, self._service, self._prepare_index)
        services.start()
        LeisServer(services).run(args.transport, args.host, args.port)

    def _index(self, args: argparse.Namespace) -> None:
        stats = self._builder().build() if args.from_git else self._prepare_index()
        self._print(asdict(stats))

    def _update(self, args: argparse.Namespace) -> None:
        if args.from_git:
            self._repository.clone()
            self._print(asdict(self._builder().build(self._repository.fetch_latest())))
            return
        snapshot = self._snapshot()
        manifest = snapshot.latest()
        if snapshot.installed_commit() == manifest["commit"]:
            self._print({"commit": manifest["commit"], "updated": False})
            return
        self._print(asdict(snapshot.download(manifest)))

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

    def _snapshot(self) -> IndexSnapshot:
        return IndexSnapshot(self._settings)

    def _prepare_index(self) -> IndexStats:
        """Downloads the prebuilt index; builds it from git when the download fails and git is installed."""
        try:
            return self._snapshot().download()
        except SnapshotError as exc:
            if shutil.which("git") is None:
                raise
            log.warning("Prebuilt index unavailable (%s); building it from git.", exc)
            return self._builder().build()

    @staticmethod
    def _print(payload: dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    Cli().run(argv)
