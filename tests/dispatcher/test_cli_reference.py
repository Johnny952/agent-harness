"""`docs/cli.md` lists exactly what `cli.build_parser()` builds (ADR 334).

Names and exit codes only: units, defaults and prose are not compared.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from dispatcher import cli

DOC = Path(__file__).resolve().parents[2] / "docs" / "cli.md"

_FIRST_SPAN = re.compile(r"`([^`]+)`")


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("the parser has no subcommands")


def _arguments(parser: argparse.ArgumentParser) -> set[str]:
    names: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.option_strings:
            names.add(next(o for o in action.option_strings if o.startswith("--")))
        else:
            names.add(action.metavar or action.dest)
    return names


def _table_names(block: str) -> set[str]:
    """The first word of the first backticked span of each row after the header."""
    rows = [line for line in block.splitlines() if line.startswith("|")]
    names: set[str] = set()
    for row in rows[2:]:
        cell = row.split("|")[1]
        span = _FIRST_SPAN.search(cell)
        if span:
            names.add(span.group(1).split()[0])
    return names


def _section(text: str, heading: str) -> str:
    """Text under `heading` up to the next heading of the same or a higher level."""
    level = heading.split(" ")[0]
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line == heading:
            inside = True
            continue
        if inside and re.match(rf"^#{{1,{len(level)}}} ", line):
            break
        if inside:
            out.append(line)
    assert inside, f"{DOC.name} has no {heading!r}"
    return "\n".join(out)


def _documented_commands(text: str) -> dict[str, set[str]]:
    commands = _section(text, "## Commands")
    found: dict[str, set[str]] = {}
    name = None
    block: list[str] = []
    for line in commands.splitlines() + ["### "]:
        if line.startswith("### "):
            if name is not None:
                found[name] = _table_names("\n".join(block))
            match = _FIRST_SPAN.search(line)
            name = match.group(1) if match else None
            block = []
        else:
            block.append(line)
    return found


def _differences(text: str) -> list[str]:
    parser = cli.build_parser()
    problems: list[str] = []
    in_parser = _subparsers(parser)
    in_doc = _documented_commands(text)
    for name in sorted(in_parser.keys() - in_doc.keys()):
        problems.append(f"subcommand {name}: in the parser, not in {DOC.name}")
    for name in sorted(in_doc.keys() - in_parser.keys()):
        problems.append(f"subcommand {name}: in {DOC.name}, not in the parser")
    for name in sorted(in_parser.keys() & in_doc.keys()):
        real = _arguments(in_parser[name])
        for arg in sorted(real - in_doc[name]):
            problems.append(f"{name} {arg}: in the parser, not in {DOC.name}")
        for arg in sorted(in_doc[name] - real):
            problems.append(f"{name} {arg}: in {DOC.name}, not in the parser")

    real_global = _arguments(parser) - {"command"}
    doc_global = _table_names(_section(text, "## Global options"))
    for arg in sorted(real_global - doc_global):
        problems.append(f"global {arg}: in the parser, not in {DOC.name}")
    for arg in sorted(doc_global - real_global):
        problems.append(f"global {arg}: in {DOC.name}, not in the parser")

    codes = {
        0, 2, cli.EXIT_BLOCKED, cli.EXIT_HELD, cli.EXIT_SPLIT,
        cli.EXIT_NOT_PUSHED, cli.EXIT_LIMIT,
    }
    doc_codes = {int(n) for n in _table_names(_section(text, "## Exit codes"))}
    for code in sorted(codes - doc_codes):
        problems.append(f"exit code {code}: in cli.py, not in {DOC.name}")
    for code in sorted(doc_codes - codes):
        problems.append(f"exit code {code}: in {DOC.name}, not in cli.py")
    return problems


def test_the_reference_matches_the_parser():
    assert _differences(DOC.read_text()) == []


def test_the_check_catches_a_missing_option():
    text = DOC.read_text()
    cut = "\n".join(line for line in text.splitlines() if not line.startswith("| `--parallel`"))
    assert any("run-queue --parallel: in the parser" in p for p in _differences(cut))


def test_the_check_catches_a_missing_command():
    text = DOC.read_text().replace("### `worker`", "### `gone`")
    problems = _differences(text)
    assert any("subcommand worker: in the parser" in p for p in problems)
    assert any("subcommand gone: in docs" in p or "subcommand gone: in cli.md" in p for p in problems)


def test_the_check_catches_an_option_the_parser_lacks():
    text = DOC.read_text().replace("| `--yes` |", "| `--yes` | flag | off | x |\n| `--bogus` |", 1)
    assert any("run-queue --bogus: in cli.md, not in the parser" in p for p in _differences(text))
