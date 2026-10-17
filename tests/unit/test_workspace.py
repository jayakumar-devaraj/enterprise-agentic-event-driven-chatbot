"""Smoke test: every workspace package is importable from the synced environment."""

import importlib

import pytest


@pytest.mark.parametrize("module", ["chat_common", "gateway", "worker", "seed"])
def test_workspace_package_imports(module: str) -> None:
    """Each workspace member resolves to a real package with a docstring."""
    mod = importlib.import_module(module)
    assert mod.__doc__
