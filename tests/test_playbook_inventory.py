"""Deterministic checks for the synthetic Ansible syntax-check inventory."""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml

from tests.helpers import REPO_ROOT


PLAYBOOK_DIR = REPO_ROOT / "playbooks"
SYNTAX_INVENTORY = REPO_ROOT / "tests" / "fixtures" / "syntax-inventory.yml"
SPECIAL_HOST_PATTERNS = frozenset({"all", "localhost", "ungrouped"})
JINJA_MARKERS = ("{{", "}}", "{%", "%}", "{#", "#}")
LITERAL_GROUP = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _fixture_group_names() -> set[str]:
    """Return explicit and Ansible-provided group names from the fixture."""
    inventory = yaml.safe_load(SYNTAX_INVENTORY.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise AssertionError("syntax inventory must be a YAML mapping")

    group_names = {"all", "ungrouped"}

    def visit_group(name: str, group: object) -> None:
        group_names.add(name)
        if not isinstance(group, dict):
            return
        children = group.get("children", {})
        if not isinstance(children, dict):
            raise AssertionError(f"children for inventory group {name!r} must be a mapping")
        for child_name, child_group in children.items():
            if not isinstance(child_name, str):
                raise AssertionError("inventory group names must be strings")
            visit_group(child_name, child_group)

    for group_name, group in inventory.items():
        if not isinstance(group_name, str):
            raise AssertionError("inventory group names must be strings")
        visit_group(group_name, group)
    return group_names


def _playbook_plays(path: pathlib.Path) -> list[dict[object, object]]:
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    plays: list[dict[object, object]] = []
    for document in documents:
        if not isinstance(document, list):
            raise AssertionError(f"{path} must contain a top-level play list")
        for play in document:
            if not isinstance(play, dict):
                raise AssertionError(f"{path} contains a non-mapping play")
            plays.append(play)
    return plays


def _host_expression_kind(hosts: object) -> tuple[str, str]:
    if not isinstance(hosts, str) or not hosts.strip():
        raise AssertionError("each play must have a non-empty string hosts expression")
    expression = hosts.strip()
    if expression in SPECIAL_HOST_PATTERNS:
        return "special", expression
    if any(marker in expression for marker in JINJA_MARKERS):
        return "dynamic", expression
    if not LITERAL_GROUP.fullmatch(expression):
        return "unsupported", expression
    return "literal-group", expression


class PlaybookInventoryTests(unittest.TestCase):
    def test_literal_playbook_groups_exist_in_syntax_inventory(self) -> None:
        group_names = _fixture_group_names()
        playbooks = sorted(
            path
            for pattern in ("*.yml", "*.yaml")
            for path in PLAYBOOK_DIR.rglob(pattern)
            if path.is_file()
        )
        self.assertTrue(playbooks, "expected at least one playbook")

        for path in playbooks:
            for play_number, play in enumerate(_playbook_plays(path), start=1):
                self.assertIn("hosts", play, f"{path} play {play_number} has no hosts expression")
                kind, expression = _host_expression_kind(play["hosts"])
                if kind == "special":
                    continue
                if kind == "dynamic":
                    self.fail(
                        f"{path} play {play_number} uses dynamic/Jinja-derived hosts "
                        f"expression {expression!r}; add explicit handling here"
                    )
                if kind == "unsupported":
                    self.fail(
                        f"{path} play {play_number} uses unsupported non-literal hosts "
                        f"expression {expression!r}; add explicit handling here"
                    )
                self.assertIn(
                    expression,
                    group_names,
                    f"{path} play {play_number} hosts group {expression!r} "
                    "is absent from the syntax inventory",
                )


if __name__ == "__main__":
    unittest.main()
