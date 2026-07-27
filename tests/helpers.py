"""Helpers for importing extensionless managed Python programs."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys
from types import ModuleType


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_program(name: str, relative_path: str) -> ModuleType:
    """Load an extensionless Python program as an importable module."""
    path = REPO_ROOT / relative_path
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError(f"Unable to load {relative_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module
