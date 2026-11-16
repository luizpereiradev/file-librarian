"""M0 smoke test: the example config loads and its database opens where it says."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_librarian.config import load_config
from file_librarian.db import Store
from tests._helpers import REPO_ROOT


@pytest.mark.integration
def test_example_config_opens_its_database(fake_home: Path) -> None:
    cfg = load_config(REPO_ROOT / "config.example.yaml")
    with Store.open(cfg.database) as store:
        assert store.schema_version >= 1
    assert cfg.database.exists()
    assert cfg.database.is_relative_to(fake_home)
