"""Supported languages and the defaults each one brings.

``language`` in the configuration picks the language of generated names and
descriptions and the default names of the folders flib creates. Anything set
explicitly in the configuration wins over these defaults. The tool's own
messages stay in English.

To add a language, add an entry to ``LOCALES`` and a test in
``tests/unit/test_config.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Language = Literal["en", "pt-BR"]
DEFAULT_LANGUAGE: Language = "en"


@dataclass(frozen=True, slots=True)
class DefaultCategory:
    name: str
    hint: str | None = None
    rename: bool = True

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "hint": self.hint, "rename": self.rename}


@dataclass(frozen=True, slots=True)
class Locale:
    language: Language
    display_name: str
    output_folder: str
    review_folder: str
    categories: tuple[DefaultCategory, ...]


LOCALES: dict[Language, Locale] = {
    "en": Locale(
        language="en",
        display_name="English",
        output_folder="Organized",
        review_folder="_Review",
        categories=(
            DefaultCategory("Finance", "invoices, bills, bank statements, receipts, tax documents"),
            DefaultCategory("Contracts", "contracts, terms, signed proposals"),
            DefaultCategory(
                "Installers", "applications, disk images and installer packages", rename=False
            ),
            DefaultCategory("Images"),
            DefaultCategory("Other"),
        ),
    ),
    "pt-BR": Locale(
        language="pt-BR",
        display_name="Português (Brasil)",
        output_folder="Organizado",
        review_folder="_Revisar",
        categories=(
            DefaultCategory("Financeiro", "boletos, notas fiscais, extratos, comprovantes"),
            DefaultCategory("Contratos", "contratos, termos, propostas assinadas"),
            DefaultCategory(
                "Instaladores",
                "aplicativos, imagens de disco e pacotes de instalação",
                rename=False,
            ),
            DefaultCategory("Imagens"),
            DefaultCategory("Outros"),
        ),
    ),
}
