from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from file_librarian import config as config_module
from file_librarian.config import (
    INACTIVE_OPTIONS,
    ConfigError,
    inactive_options_set,
    load_config,
    parse_config,
)
from file_librarian.locales import LOCALES
from tests._helpers import REPO_ROOT


def minimal() -> dict[str, Any]:
    return {
        "inputs": ["~/Downloads"],
        "output_root": "~/Downloads/Organized",
        "provider": {"name": "fake", "model": "fixed"},
        "categories": [{"name": "Finance"}, {"name": "Other"}],
    }


def test_example_config_is_valid(fake_home: Path) -> None:
    cfg = load_config(REPO_ROOT / "config.example.yaml")
    assert cfg.inputs == [fake_home / "Downloads"]
    assert cfg.output_root == fake_home / "Downloads" / "Organized"
    assert cfg.category("Installers") is not None
    assert cfg.category("Installers").rename is False  # type: ignore[union-attr]
    assert cfg.database.is_relative_to(fake_home / "Library")


def test_only_provider_is_required(fake_home: Path) -> None:
    cfg = parse_config({"provider": {"name": "fake", "model": "fixed"}})
    assert cfg.language == "en"
    assert cfg.inputs == [fake_home / "Downloads"]
    assert cfg.output_root == fake_home / "Downloads" / "Organized"
    assert cfg.review_folder == "_Review"
    assert [c.name for c in cfg.categories][:2] == ["Finance", "Contracts"]


def test_portuguese_defaults(fake_home: Path) -> None:
    cfg = parse_config({"language": "pt-BR", "provider": {"name": "fake", "model": "fixed"}})
    assert cfg.output_root == fake_home / "Downloads" / "Organizado"
    assert cfg.review_folder == "_Revisar"
    assert cfg.category("Instaladores") is not None
    assert cfg.category("Instaladores").rename is False  # type: ignore[union-attr]


def test_explicit_values_win_over_language_defaults() -> None:
    raw = minimal() | {"language": "pt-BR", "review_folder": "_Conferir"}
    cfg = parse_config(raw)
    assert cfg.review_folder == "_Conferir"
    assert [c.name for c in cfg.categories] == ["Finance", "Other"]


@pytest.mark.parametrize("language", sorted(LOCALES))
def test_every_language_has_valid_defaults(language: str) -> None:
    cfg = parse_config({"language": language, "provider": {"name": "fake", "model": "x"}})
    assert cfg.review_folder not in {c.name for c in cfg.categories}


def test_defaults_fill_optional_sections() -> None:
    cfg = parse_config(minimal())
    assert cfg.review_folder == "_Review"
    assert cfg.limits.confidence_threshold == 0.7
    assert cfg.folders == "as_unit"
    assert cfg.mcp.read_only is False


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"categories": []}, "categories"),
        ({"categories": [{"name": "A"}, {"name": "A"}]}, "same folder"),
        ({"categories": [{"name": "Finance"}, {"name": "finance"}]}, "same folder"),
        ({"categories": [{"name": "Finanças"}, {"name": "Financ\u0327as"}]}, "same folder"),
        ({"categories": [{"name": "a\x00b"}]}, "control"),
        ({"categories": [{"name": "x\ny"}]}, "control"),
        ({"categories": [{"name": ".hidden"}]}, "start with '.'"),
        ({"review_folder": "../../../etc"}, "plain folder name"),
        ({"review_folder": "/abs/review"}, "plain folder name"),
        ({"review_folder": "_Review", "categories": [{"name": "_review"}]}, "same folder"),
        ({"naming": {"pattern": "../../{subject}"}}, "file name, not a path"),
        ({"naming": {"pattern": "{subject}/{date}"}}, "file name, not a path"),
        ({"naming": {"pattern": "{subject}_{bogus}"}}, "unknown placeholder"),
        ({"naming": {"pattern": "{subject!r}"}}, "no format spec"),
        ({"inputs": ["~/Downloads", "/"]}, "too broad"),
        ({"inputs": ["~"]}, "too broad"),
        ({"inputs": ["~/Downloads", "~/Downloads/sub"]}, "overlap"),
        ({"output_root": "~"}, "inside output_root"),
        ({"output_root": "relative/dir"}, "absolute path"),
        ({"database": "~/Downloads/library.db"}, "database must be outside"),
        ({"database": "~/Downloads/Organized/lib.db"}, "database must be outside"),
        ({"provider": {"name": "nope", "model": "x"}}, "unknown provider"),
        ({"categories": [{"name": "../etc"}]}, "plain folder name"),
        ({"review_folder": "Finance"}, "same folder"),
        ({"output_root": "~/Downloads"}, "inside output_root"),
        ({"limits": {"confidence_threshold": 1.5}}, "confidence_threshold"),
        ({"naming": {"pattern": "{date}"}}, "{subject}"),
        ({"folders": "flatten"}, "folders"),
        ({"language": "fr"}, "language"),
        ({"provider": {"model": "x", "tempreature": 0}}, "tempreature"),
    ],
)
def test_invalid_config_is_rejected(patch: dict[str, Any], message: str) -> None:
    raw = minimal() | patch
    with pytest.raises(ConfigError, match=None) as excinfo:
        parse_config(raw)
    assert message in str(excinfo.value)


def test_missing_file_explains_how_to_start(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.example\.yaml"):
        load_config(tmp_path / "nope.yaml")


def test_broken_yaml_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("inputs: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_env_var_selects_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(minimal()), encoding="utf-8")
    monkeypatch.setenv("FLIB_CONFIG", str(path))
    assert load_config().provider.name == "fake"


def test_portuguese_default_categories_are_complete() -> None:
    cfg = parse_config({"language": "pt-BR", "provider": {"name": "fake", "model": "x"}})
    names = [c.name for c in cfg.categories]
    assert len(names) == 15
    assert names[-1] == "Outros"  # the catch-all stays last
    assert {"Impostos", "Saúde", "Documentos pessoais", "Código e dados"} <= set(names)
    assert all(c.hint for c in cfg.categories)
    assert [c.name for c in cfg.categories if not c.rename] == ["Instaladores"]


def test_inactive_options_name_real_fields() -> None:
    # A typo in INACTIVE_OPTIONS raises here instead of in check-config, and every
    # inactive option defaults to "off", so an untouched config gets no warning.
    assert all(milestone for milestone in INACTIVE_OPTIONS.values())
    assert inactive_options_set(parse_config(minimal())) == []


def test_inactive_options_warn_only_when_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_module, "INACTIVE_OPTIONS", {"limits.stable_seconds": "M9", "folders": "M8"}
    )
    assert inactive_options_set(parse_config(minimal())) == []
    changed = parse_config(minimal() | {"limits": {"stable_seconds": 5}, "folders": "ignore"})
    assert inactive_options_set(changed) == [("folders", "M8"), ("limits.stable_seconds", "M9")]


def test_inactive_options_default_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "INACTIVE_OPTIONS", {"ignore": "M9"})
    assert inactive_options_set(parse_config(minimal())) == []
    assert inactive_options_set(parse_config(minimal() | {"ignore": ["*.tmp"]})) == [
        ("ignore", "M9")
    ]
