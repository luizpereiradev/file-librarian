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
            DefaultCategory(
                "Financeiro",
                "boletos, faturas e contas de consumo (luz, água, internet, telefone), "
                "extratos bancários e de cartão, comprovantes de pagamento, Pix e "
                "transferência, notas fiscais",
            ),
            DefaultCategory(
                "Impostos",
                "declaração e recibo do Imposto de Renda, DARF, informes de rendimentos, recibos "
                "médicos e de educação para dedução, documentos da Receita Federal",
            ),
            DefaultCategory(
                "Contratos",
                "contratos, aditivos, termos assinados, propostas comerciais, procurações, "
                "distratos",
            ),
            DefaultCategory(
                "Documentos pessoais",
                "RG, CPF, CNH, passaporte, certidões, título de eleitor, comprovante de "
                "residência, diplomas e históricos escolares",
            ),
            DefaultCategory(
                "Saúde",
                "exames, laudos, receitas, atestados, carteira de vacinação, guias, boletos e "
                "reembolsos de plano de saúde",
            ),
            DefaultCategory(
                "Trabalho",
                "holerites, informes e documentos de RH, currículos, relatórios, apresentações e "
                "planilhas profissionais",
            ),
            DefaultCategory(
                "Estudos",
                "artigos, livros, apostilas, slides de aula, trabalhos acadêmicos, certificados de "
                "cursos",
            ),
            DefaultCategory(
                "Viagens",
                "passagens, cartões de embarque, reservas de hotel e carro, roteiros, seguro "
                "viagem, vistos",
            ),
            DefaultCategory(
                "Compras",
                "pedidos e recibos de lojas online, termos de garantia, manuais de produtos",
            ),
            DefaultCategory(
                "Casa e veículos",
                "aluguel e condomínio, IPTU, escrituras e documentos de imóveis, IPVA, multas e "
                "documentos do carro",
            ),
            DefaultCategory(
                "Imagens", "fotos, capturas de tela, ilustrações, logotipos, wallpapers"
            ),
            DefaultCategory("Vídeos e áudio", "vídeos, músicas, podcasts, gravações de reunião"),
            DefaultCategory(
                "Instaladores",
                "aplicativos, imagens de disco (.dmg), pacotes de instalação (.pkg), atualizações "
                "de software",
                rename=False,
            ),
            DefaultCategory(
                "Código e dados",
                "projetos de software, scripts, planilhas e exportações de dados (CSV, JSON), "
                "bancos de dados, backups",
            ),
            DefaultCategory(
                "Outros", "qualquer coisa que não se encaixe claramente nas categorias acima"
            ),
        ),
    ),
}
