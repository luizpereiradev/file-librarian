"""The public evaluation corpus: about 150 synthetic downloads with gold labels.

Everything here is invented. Organisations are fictitious, there are no real
people, no real document numbers and nothing copied from a real file. The
corpus is written by code, so the repository holds no binary fixtures:
``items(seed)`` lists every item with its gold labels and ``build`` writes
them into an empty folder. The same ``seed`` always writes the same bytes
(amounts, a few dates and the document numbers in four names are drawn from
it; every other name is fixed), so two runs over the corpus score the same
files.

The items cover the formats flib reads (text, Markdown, CSV, Word, Excel,
PowerPoint, PDF with text, e-mail), files read by name only (images,
installers, video and audio, scans with no text layer), archives and folder
units, in English and Portuguese, across the default categories of both
locales, plus hard cases: generic names next to descriptive ones,
near-identical bills from one issuer in different months, ambiguous
documents, files that should go to review, and credential files that must
never be sent.

The gold labels of the default seed are also published as ``gold.json`` next
to this module, for reading and for other tools; a test keeps it equal to
what this module generates. After changing an item, write it again with
``python -m file_librarian.evals.corpus``.

Gold labels per item (``Gold``):

- ``categories``: per language, the categories that count as right. Empty
  means no category is right: the item is scored on going to review only.
- ``keep_name``: whether the original name should survive (``True``: it is
  descriptive already; ``False``: it is generic and should be replaced;
  ``None``: not scored, because flib never renames it, e.g. no readable content).
- ``review``: the item should go to the review folder.
- ``sensitive``: flib must withhold it (credential file name or a secret
  inside); it must never reach the provider.
- ``fields``: for later milestones (structured fields, #88): ``amount``,
  ``currency``, ``document_date``, ``due_date``, ``issuer``, ``period``, ``kind``.
"""

from __future__ import annotations

import json
import os
import plistlib
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from file_librarian.evals import documents as doc
from file_librarian.locales import LOCALES, Language

DEFAULT_SEED = 87
CORPUS_VERSION = "1"
"""Bump when items or gold labels change, so saved results say which corpus they scored."""
GOLD_PATH = Path(__file__).with_name("gold.json")
MTIME = 1_790_000_000  # 2026-09-21: fixed, so the corpus looks the same on every run
FIELD_NAMES = ("amount", "currency", "document_date", "due_date", "issuer", "period", "kind")
LANGUAGES: tuple[Language, ...] = ("en", "pt-BR")

Writer = Callable[[Path], object]


@dataclass(frozen=True, slots=True)
class Gold:
    categories: dict[Language, tuple[str, ...]]
    keep_name: bool | None = None
    review: bool = False
    sensitive: bool = False
    language: Language | None = None
    """The language of the document's text; ``None`` when it has none."""
    fields: dict[str, str] = field(default_factory=dict)
    cases: tuple[str, ...] = ()
    """Hard-case tags: ``generic-name``, ``near-duplicate``, ``ambiguous``, ``name-only``..."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "categories": {lang: list(self.categories[lang]) for lang in LANGUAGES},
            "keep_name": self.keep_name,
            "review": self.review,
            "sensitive": self.sensitive,
            "language": self.language,
            "fields": dict(self.fields),
            "cases": list(self.cases),
        }


@dataclass(frozen=True, slots=True)
class Item:
    path: str
    """The item's name in the input folder; also its id."""
    format: str
    gold: Gold
    write: Writer = field(repr=False, compare=False)


# --- Writers --------------------------------------------------------------------


def _pdf(text: str) -> Writer:
    return lambda p: doc.make_pdf(p, text)


def _scan(pages: int) -> Writer:
    """A PDF with no text layer; the page count keeps each one's content unique."""
    return lambda p: doc.make_blank_pdf(p, pages)


def _docx(*paragraphs: str) -> Writer:
    return lambda p: doc.make_docx(p, list(paragraphs))


def _pptx(*slides: str) -> Writer:
    return lambda p: doc.make_pptx(p, list(slides))


def _xlsx(sheets: list[str], strings: list[str]) -> Writer:
    return lambda p: doc.make_xlsx(p, sheets, strings)


def _text(text: str) -> Writer:
    return lambda p: p.write_text(text, encoding="utf-8")


def _bytes(data: bytes) -> Writer:
    return lambda p: p.write_bytes(data)


def _png(shade: int) -> Writer:
    return lambda p: doc.make_png(p, shade=shade)


def _eml(subject: str, sender: str, body: str, when: str) -> Writer:
    return lambda p: doc.make_eml(p, body, subject=subject, sender=sender, date=when)


def _zip(members: dict[str, bytes | str]) -> Writer:
    return lambda p: doc.write_zip(p, members)


def _folder(members: dict[str, bytes | str]) -> Writer:
    def write(root: Path) -> None:
        for name, data in members.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, str):
                target.write_text(data, encoding="utf-8")
            else:
                target.write_bytes(data)

    return write


def _app(name: str, identifier: str, version: str) -> Writer:
    info = {
        "CFBundleName": name,
        "CFBundleIdentifier": identifier,
        "CFBundleShortVersionString": version,
    }
    return _folder(
        {
            "Contents/Info.plist": plistlib.dumps(info, sort_keys=True),
            f"Contents/MacOS/{name.lower()}": b"\xcf\xfa\xed\xfe synthetic binary",
        }
    )


def _binary(tag: str, size: int = 2048) -> Writer:
    """Opaque bytes for formats flib reads by name only (installers, media)."""
    seed = tag.encode()
    return _bytes((b"\0" + seed) * (size // (len(seed) + 1)))


# --- Labels ---------------------------------------------------------------------

FIN, CON, INS, IMG, OTH = "Finance", "Contracts", "Installers", "Images", "Other"
FI, IR, CT, PD, SA = "Financeiro", "Impostos", "Contratos", "Documentos pessoais", "Saúde"
TR, ES, VI, CO, CV = "Trabalho", "Estudos", "Viagens", "Compras", "Casa e veículos"
IM, VA, IN, CD, OU = "Imagens", "Vídeos e áudio", "Instaladores", "Código e dados", "Outros"


def _cats(spec: str) -> tuple[str, ...]:
    return tuple(c for c in spec.split("|") if c)


class _Corpus:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 - reproducible, not secret
        self.items: list[Item] = []

    def add(
        self,
        path: str,
        writer: Writer,
        en: str,
        pt: str,
        *,
        keep: bool | None = None,
        review: bool = False,
        sensitive: bool = False,
        lang: Language | None = None,
        fields: dict[str, str] | None = None,
        cases: tuple[str, ...] = (),
        fmt: str | None = None,
    ) -> None:
        gold = Gold(
            categories={"en": _cats(en), "pt-BR": _cats(pt)},
            keep_name=keep,
            review=review,
            sensitive=sensitive,
            language=lang,
            fields=dict(fields or {}),
            cases=cases,
        )
        suffix = Path(path).suffix.lower().lstrip(".")
        self.items.append(Item(path, fmt or suffix or "none", gold, writer))

    def money(self, low: int, high: int) -> float:
        return self.rng.randint(low * 100, high * 100) / 100

    def number(self, digits: int) -> str:
        return "".join(str(self.rng.randint(0, 9)) for _ in range(digits))


def brl(value: float) -> str:
    whole, cents = f"{value:,.2f}".split(".")
    return f"R$ {whole.replace(',', '.')},{cents}"


def usd(value: float) -> str:
    return f"${value:,.2f}"


def dmy(day: date) -> str:
    return day.strftime("%d/%m/%Y")


MONTHS_PT = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _bill_fields(
    value: float, currency: str, issued: date, due: date, issuer: str, kind: str
) -> dict[str, str]:
    return {
        "amount": f"{value:.2f}",
        "currency": currency,
        "document_date": issued.isoformat(),
        "due_date": due.isoformat(),
        "issuer": issuer,
        "period": f"{issued.year}-{issued.month - 1:02d}" if issued.month > 1 else "",
        "kind": kind,
    }


# --- The items ------------------------------------------------------------------


def _finance(c: _Corpus) -> None:
    # Three bills from one issuer, in three months, under generic names.
    issuer = "Northwind Power & Light"
    for i, month in enumerate((8, 9, 10)):
        value = c.money(80, 220)
        issued = date(2026, month + 1, 3)
        due = issued + timedelta(days=17)
        name = "bill.pdf" if i == 0 else f"bill ({i}).pdf"
        text = (
            f"{issuer}\nElectricity bill - {date(2026, month, 1):%B %Y}\n"
            f"Billing period: {date(2026, month, 1):%b} 1 to month end\n"
            f"Issue date: {issued:%B %d, %Y}\nAmount due: {usd(value)}\n"
            f"Pay by: {due:%B %d, %Y}\nUsage: {c.rng.randint(250, 600)} kWh"
        )
        c.add(
            name,
            _pdf(text),
            FIN,
            FI,
            keep=False,
            lang="en",
            fields=_bill_fields(value, "USD", issued, due, issuer, "utility bill"),
            cases=("generic-name", "near-duplicate"),
        )
    issuer = "Companhia Elétrica Vale Azul"
    for i, month in enumerate((8, 9, 10)):
        value = c.money(120, 420)
        issued = date(2026, month + 1, 5)
        due = issued + timedelta(days=15)
        name = "documento.pdf" if i == 0 else f"documento({i}).pdf"
        text = (
            f"{issuer}\nConta de energia elétrica - {MONTHS_PT[month - 1]} de 2026\n"
            f"Referência: {month:02d}/2026\nEmissão: {dmy(issued)}\n"
            f"Vencimento: {dmy(due)}\nTotal a pagar: {brl(value)}\n"
            f"Consumo: {c.rng.randint(150, 450)} kWh"
        )
        c.add(
            name,
            _pdf(text),
            FIN,
            FI,
            keep=False,
            lang="pt-BR",
            fields=_bill_fields(value, "BRL", issued, due, issuer, "utility bill"),
            cases=("generic-name", "near-duplicate"),
        )
    # Water: one descriptive name, one generic, same issuer.
    issuer = "Água Clara Saneamento"
    for name, month, keep in (("2026-09_conta-agua.pdf", 9, True), ("2via.pdf", 10, False)):
        value = c.money(60, 180)
        issued = date(2026, month + 1, 8)
        due = issued + timedelta(days=12)
        text = (
            f"{issuer}\nSegunda via da conta de água e esgoto\n"
            f"Mês de referência: {MONTHS_PT[month - 1]}/2026\nEmissão: {dmy(issued)}\n"
            f"Vencimento: {dmy(due)}\nValor: {brl(value)}"
        )
        cases = () if keep else ("generic-name",)
        c.add(
            name,
            _pdf(text),
            FIN,
            FI,
            keep=keep,
            lang="pt-BR",
            fields=_bill_fields(value, "BRL", issued, due, issuer, "utility bill"),
            cases=cases,
        )
    # Telecom.
    issuer = "Sabiá Telecom"
    value = c.money(90, 160)
    issued, due = date(2026, 10, 2), date(2026, 10, 15)
    c.add(
        f"boleto_{c.number(8)}.pdf",
        _pdf(
            f"{issuer}\nBoleto - Internet fibra 500 Mega\nCompetência: setembro/2026\n"
            f"Vencimento: {dmy(due)}\nValor do documento: {brl(value)}\n"
            "Pagável em qualquer banco até o vencimento"
        ),
        FIN,
        FI,
        keep=False,
        lang="pt-BR",
        fields=_bill_fields(value, "BRL", issued, due, issuer, "telecom bill"),
        cases=("generic-name",),
    )
    issuer = "Fabrikam Mobile"
    value = c.money(40, 95)
    issued, due = date(2026, 10, 1), date(2026, 10, 21)
    c.add(
        f"invoice_{c.number(6)}.pdf",
        _pdf(
            f"{issuer}\nInvoice\nInvoice date: {issued:%B %d, %Y}\n"
            f"Unlimited plan, September 2026\nTotal: {usd(value)}\nDue: {due:%B %d, %Y}"
        ),
        FIN,
        FI,
        keep=False,
        lang="en",
        fields=_bill_fields(value, "USD", issued, due, issuer, "telecom bill"),
        cases=("generic-name",),
    )
    # Bank statements.
    issuer = "Contoso Bank"
    for name, month in (("statement_2026-09.pdf", 9), ("Statement-Oct-2026.pdf", 10)):
        balance = c.money(1000, 9000)
        issued = date(2026, month + 1, 1)
        c.add(
            name,
            _pdf(
                f"{issuer}\nChecking account statement\n"
                f"Period: {date(2026, month, 1):%B %Y}\nOpening balance: {usd(balance)}\n"
                f"Card purchase  GROCERY MART  -{usd(c.money(20, 200))}\n"
                f"Direct deposit  PAYROLL  +{usd(c.money(1500, 4000))}"
            ),
            FIN,
            FI,
            keep=True,
            lang="en",
            fields={
                "document_date": issued.isoformat(),
                "issuer": issuer,
                "period": f"2026-{month:02d}",
                "kind": "bank statement",
                "currency": "USD",
            },
        )
    issuer = "Banco Ipê Roxo"
    c.add(
        "extrato_out_2026.pdf",
        _pdf(
            f"{issuer}\nExtrato de conta corrente\nPeríodo: 01/10/2026 a 31/10/2026\n"
            f"Saldo anterior: {brl(c.money(500, 6000))}\n"
            f"PIX recebido  {brl(c.money(100, 900))}\nPagamento de boleto  -{brl(c.money(50, 400))}"
        ),
        FIN,
        FI,
        keep=True,
        lang="pt-BR",
        fields={
            "document_date": "2026-11-01",
            "issuer": issuer,
            "period": "2026-10",
            "kind": "bank statement",
            "currency": "BRL",
        },
    )
    value = c.money(1500, 4800)
    c.add(
        "fatura-cartao-setembro.pdf",
        _pdf(
            f"{issuer} - Cartão de crédito\nFatura de setembro de 2026\n"
            f"Fechamento: 28/09/2026\nVencimento: 05/10/2026\nTotal da fatura: {brl(value)}\n"
            "Pagamento mínimo: 15% do total"
        ),
        FIN,
        FI,
        keep=True,
        lang="pt-BR",
        fields=_bill_fields(
            value, "BRL", date(2026, 9, 28), date(2026, 10, 5), issuer, "card statement"
        )
        | {"period": "2026-09"},
    )
    value = c.money(50, 900)
    c.add(
        "comprovante.pdf",
        _pdf(
            f"{issuer}\nComprovante de transferência Pix\nData: 14/10/2026 09:41\n"
            f"Valor: {brl(value)}\nDestino: Imobiliária Jacarandá Ltda\n"
            "Descrição: aluguel outubro"
        ),
        FIN,
        f"{FI}|{CV}",
        keep=False,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "document_date": "2026-10-14",
            "issuer": issuer,
            "kind": "payment receipt",
        },
        cases=("generic-name", "ambiguous"),
    )
    c.add(
        "transactions-export.csv",
        _text(
            "date,description,amount\n"
            + "".join(
                f"2026-10-{d:02d},{shop},-{c.money(5, 120):.2f}\n"
                for d, shop in (
                    (2, "COFFEE CORNER"),
                    (5, "GROCERY MART"),
                    (9, "BOOKSHOP"),
                    (14, "PHARMACY PLUS"),
                    (21, "FUEL STATION"),
                )
            )
        ),
        FIN,
        f"{FI}|{CD}",
        keep=None,
        lang="en",
        fields={
            "issuer": "Contoso Bank",
            "period": "2026-10",
            "kind": "bank statement",
            "currency": "USD",
        },
        cases=("ambiguous",),
    )
    c.add(
        "household-budget-2026.xlsx",
        _xlsx(
            ["2026"],
            [
                "Household budget 2026",
                "Rent",
                "Groceries",
                "Utilities",
                "Savings",
                f"{c.money(900, 1500):.2f}",
                f"{c.money(300, 600):.2f}",
            ],
        ),
        FIN,
        f"{FI}|{CD}",
        keep=True,
        lang="en",
    )
    c.add(
        "orcamento-casa.xlsx",
        _xlsx(
            ["Outubro", "Novembro"],
            ["Orçamento doméstico", "Aluguel", "Mercado", "Energia", "Internet", "Reserva"],
        ),
        FIN,
        f"{FI}|{CV}|{CD}",
        keep=True,
        lang="pt-BR",
    )
    value = c.money(15, 80)
    c.add(
        "receipt.eml",
        _eml(
            "Your Adventure Works order receipt",
            "Adventure Works Store <orders@adventure-works.example>",
            f"Thanks for your order.\nOrder date: October 12, 2026\n1 x Hiking socks\n"
            f"1 x Water bottle\nTotal charged: {usd(value)}\nShipping: free",
            "Mon, 12 Oct 2026 18:20:00 -0400",
        ),
        FIN,
        f"{CO}|{FI}",
        keep=False,
        lang="en",
        fields={
            "amount": f"{value:.2f}",
            "currency": "USD",
            "document_date": "2026-10-12",
            "issuer": "Adventure Works Store",
            "kind": "receipt",
        },
        cases=("generic-name",),
    )
    c.add(
        "alerta-compra.eml",
        _eml(
            "Compra aprovada no seu cartão",
            "Banco Ipê Roxo <avisos@ipe-roxo.example>",
            f"Compra aprovada: {brl(c.money(30, 300))} em LOJA TUCANO ONLINE em 18/10/2026.\n"
            "Se não reconhece esta compra, fale com a central.",
            "Sun, 18 Oct 2026 20:02:00 -0300",
        ),
        FIN,
        f"{FI}|{CO}",
        keep=None,
        lang="pt-BR",
        cases=("ambiguous",),
    )


def _taxes(c: _Corpus) -> None:
    c.add(
        "recibo-irpf-2026.pdf",
        _pdf(
            "Receita Federal do Brasil\nRecibo de entrega da Declaração de Ajuste Anual\n"
            "Exercício 2026 - Ano-calendário 2025\nImposto a restituir: "
            f"{brl(c.money(200, 3000))}\nDeclaração entregue em 28/04/2026"
        ),
        FIN,
        IR,
        keep=True,
        lang="pt-BR",
        fields={
            "document_date": "2026-04-28",
            "issuer": "Receita Federal",
            "period": "2025",
            "kind": "tax return receipt",
        },
    )
    value = c.money(150, 1200)
    c.add(
        "DARF-carne-leao-setembro.pdf",
        _pdf(
            "Documento de Arrecadação de Receitas Federais - DARF\nCarnê-Leão\n"
            f"Período de apuração: 09/2026\nVencimento: 30/10/2026\nValor total: {brl(value)}"
        ),
        FIN,
        IR,
        keep=True,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "due_date": "2026-10-30",
            "issuer": "Receita Federal",
            "period": "2026-09",
            "kind": "tax payment",
        },
    )
    c.add(
        "informe-rendimentos-2025.pdf",
        _pdf(
            "Banco Ipê Roxo\nInforme de rendimentos financeiros - ano-calendário 2025\n"
            f"Rendimentos de aplicações: {brl(c.money(100, 5000))}\n"
            "Saldo em 31/12/2025 e 31/12/2024\nPara uso na declaração de imposto de renda"
        ),
        FIN,
        IR,
        keep=True,
        lang="pt-BR",
        fields={"issuer": "Banco Ipê Roxo", "period": "2025", "kind": "income statement"},
    )
    c.add(
        "informe.pdf",
        _pdf(
            "Fonte pagadora: Andorinha Tecnologia Ltda\n"
            "Comprovante de rendimentos pagos e de imposto sobre a renda retido na fonte\n"
            f"Ano-calendário 2025\nRendimentos tributáveis: {brl(c.money(40000, 120000))}"
        ),
        FIN,
        IR,
        keep=False,
        lang="pt-BR",
        fields={
            "issuer": "Andorinha Tecnologia Ltda",
            "period": "2025",
            "kind": "income statement",
        },
        cases=("generic-name",),
    )
    value = c.money(200, 600)
    c.add(
        "recibo-consulta.pdf",
        _pdf(
            "Clínica Jatobá\nRecibo\nRecebemos a quantia de "
            f"{brl(value)} referente a consulta médica em 03/09/2026.\n"
            "Documento válido para dedução no imposto de renda."
        ),
        f"{FIN}|{OTH}",
        f"{IR}|{SA}",
        keep=None,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "document_date": "2026-09-03",
            "issuer": "Clínica Jatobá",
            "kind": "medical receipt",
        },
        cases=("ambiguous",),
    )
    c.add(
        "2025 Tax Return Summary.pdf",
        _pdf(
            "Form 1040 summary - tax year 2025\nPrepared for filing\n"
            f"Adjusted gross income: {usd(c.money(40000, 90000))}\n"
            f"Refund: {usd(c.money(100, 2000))}"
        ),
        FIN,
        IR,
        keep=True,
        lang="en",
        fields={"period": "2025", "kind": "tax return"},
    )
    value = c.money(800, 4000)
    c.add(
        "IPTU 2026 carne.pdf",
        _pdf(
            "Prefeitura Municipal\nIPTU 2026 - Imposto Predial e Territorial Urbano\n"
            f"Valor anual: {brl(value)}\nCota única com desconto até 10/02/2026"
        ),
        FIN,
        f"{CV}|{IR}",
        keep=True,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "due_date": "2026-02-10",
            "period": "2026",
            "kind": "property tax",
        },
        cases=("ambiguous",),
    )


def _contracts(c: _Corpus) -> None:
    c.add(
        "lease_agreement_signed.docx",
        _docx(
            "Residential Lease Agreement",
            "This agreement is made between Proseware Realty (Landlord) and the Tenant.",
            f"Monthly rent: {usd(c.money(1200, 2500))}. Term: 12 months from November 1, 2026.",
            "Signed by both parties.",
        ),
        CON,
        f"{CT}|{CV}",
        keep=True,
        lang="en",
        fields={"issuer": "Proseware Realty", "document_date": "2026-10-20", "kind": "lease"},
    )
    c.add(
        "NDA - Litware.docx",
        _docx(
            "Mutual Non-Disclosure Agreement",
            "Between Litware Inc. and the Consultant.",
            "Confidential information shall not be disclosed for three years.",
        ),
        CON,
        f"{CT}|{TR}",
        keep=True,
        lang="en",
        fields={"issuer": "Litware Inc.", "kind": "nda"},
    )
    c.add(
        "contract.docx",
        _docx(
            "Freelance Services Agreement",
            "Client: Tailspin Toys. Contractor agrees to deliver a website redesign.",
            f"Fee: {usd(c.money(3000, 9000))}, paid in two installments.",
        ),
        CON,
        f"{CT}|{TR}",
        keep=False,
        lang="en",
        fields={"issuer": "Tailspin Toys", "kind": "services agreement"},
        cases=("generic-name",),
    )
    c.add(
        "contrato-aluguel-apto.docx",
        _docx(
            "Contrato de locação residencial",
            "Locador: Imobiliária Jacarandá Ltda. Locatário: conforme qualificação anexa.",
            f"Aluguel mensal de {brl(c.money(1500, 4000))}, prazo de 30 meses.",
            "Foro da comarca da capital.",
        ),
        CON,
        f"{CT}|{CV}",
        keep=True,
        lang="pt-BR",
        fields={"issuer": "Imobiliária Jacarandá Ltda", "kind": "lease"},
    )
    c.add(
        "aditivo contratual.pdf",
        _pdf(
            "Primeiro termo aditivo ao contrato de prestação de serviços\n"
            "Contratante: Andorinha Tecnologia Ltda\nO prazo fica prorrogado até 31/12/2027."
        ),
        CON,
        CT,
        keep=True,
        lang="pt-BR",
        fields={"kind": "contract amendment"},
    )
    c.add(
        "procuracao.pdf",
        _pdf(
            "Procuração particular\nPelo presente instrumento, o outorgante nomeia seu "
            "procurador para representá-lo junto ao cartório de registro de imóveis."
        ),
        f"{CON}|{OTH}",
        f"{CT}|{PD}",
        keep=True,
        lang="pt-BR",
        fields={"kind": "power of attorney"},
    )
    c.add(
        "proposta-comercial-v3.docx",
        _docx(
            "Proposta comercial",
            "Andorinha Tecnologia apresenta proposta para desenvolvimento de aplicativo.",
            f"Investimento: {brl(c.money(20000, 80000))}. Validade: 30 dias.",
        ),
        f"{CON}|{OTH}",
        f"{CT}|{TR}",
        keep=True,
        lang="pt-BR",
        cases=("ambiguous",),
    )
    c.add(
        "distrato.pdf",
        _pdf(
            "Instrumento particular de distrato\nAs partes resolvem encerrar o contrato de "
            "prestação de serviços firmado em 2024, sem pendências financeiras."
        ),
        CON,
        CT,
        keep=True,
        lang="pt-BR",
        fields={"kind": "termination agreement"},
    )
    c.add(
        "Re_ minuta do contrato.eml",
        _eml(
            "Re: minuta do contrato",
            "Jurídico Pau-Brasil <juridico@pau-brasil.example>",
            "Segue a minuta revisada do contrato de fornecimento. Ajustamos a cláusula de "
            "multa e o prazo de vigência. Aguardamos a assinatura.",
            "Tue, 06 Oct 2026 11:30:00 -0300",
        ),
        f"{CON}|{OTH}",
        f"{CT}|{TR}",
        keep=True,
        lang="pt-BR",
        cases=("ambiguous",),
    )
    c.add(
        "terms-of-service.pdf",
        _pdf(
            "Wingtip Cloud - Terms of Service\nBy using the service you agree to these terms.\n"
            "Last updated September 2026."
        ),
        f"{CON}|{OTH}",
        f"{CT}|{OU}",
        keep=True,
        lang="en",
    )


def _personal(c: _Corpus) -> None:
    c.add(
        "certidao-nascimento.pdf",
        _pdf(
            "República Federativa do Brasil\nRegistro Civil das Pessoas Naturais\n"
            "Certidão de nascimento - segunda via\nCartório do 2º Ofício"
        ),
        OTH,
        PD,
        keep=True,
        lang="pt-BR",
        fields={"kind": "birth certificate"},
    )
    c.add(
        "comprovante-residencia.pdf",
        _pdf(
            "Companhia Elétrica Vale Azul\nDeclaração para comprovação de endereço\n"
            "Emitida em 02/10/2026 a pedido do titular da unidade consumidora."
        ),
        f"{OTH}|{FIN}",
        f"{PD}|{FI}",
        keep=True,
        lang="pt-BR",
        cases=("ambiguous",),
    )
    c.add(
        "diploma-graduacao.pdf",
        _pdf(
            "Universidade Federal do Cerrado\nDiploma\nConferido o grau de Bacharel em "
            "Ciência da Computação. Colação de grau em 15/12/2020."
        ),
        OTH,
        f"{PD}|{ES}",
        keep=True,
        lang="pt-BR",
        fields={"kind": "diploma"},
    )
    c.add(
        "titulo-eleitor.pdf",
        _pdf("Justiça Eleitoral\nTítulo de eleitor digital\nZona 001 Seção 0123"),
        OTH,
        PD,
        keep=True,
        lang="pt-BR",
    )
    c.add("RG-frente.jpg", _png(10), f"{IMG}|{OTH}", f"{PD}|{IM}", keep=None, cases=("name-only",))
    c.add("CNH-digital.pdf", _scan(1), OTH, PD, keep=None, cases=("name-only", "scan"))
    c.add("passport-scan.pdf", _scan(2), OTH, f"{PD}|{VI}", keep=None, cases=("name-only", "scan"))
    c.add(
        "historico-escolar.pdf",
        _pdf("Escola Técnica Andorinha\nHistórico escolar\nCurso técnico em informática, 2015"),
        OTH,
        f"{PD}|{ES}",
        keep=True,
        lang="pt-BR",
    )


def _health(c: _Corpus) -> None:
    c.add(
        "resultado-exame-sangue.pdf",
        _pdf(
            "Laboratório Ceriba\nHemograma completo\nColeta: 22/09/2026\n"
            "Hemoglobina: 14,1 g/dL (referência 13,5 a 17,5)\nLeucócitos: 6.200 /mm3"
        ),
        OTH,
        SA,
        keep=True,
        lang="pt-BR",
        fields={
            "document_date": "2026-09-22",
            "issuer": "Laboratório Ceriba",
            "kind": "lab result",
        },
    )
    c.add(
        "receita.pdf",
        _pdf(
            "Clínica Jatobá\nReceituário\nUso oral: amoxicilina 500 mg, 1 cápsula de 8 em 8 "
            "horas por 7 dias.\n03/09/2026"
        ),
        OTH,
        SA,
        keep=False,
        lang="pt-BR",
        fields={"document_date": "2026-09-03", "issuer": "Clínica Jatobá", "kind": "prescription"},
        cases=("generic-name", "ambiguous"),
    )
    c.add(
        "atestado.pdf",
        _pdf(
            "Clínica Jatobá\nAtestado médico\nAtesto para os devidos fins que o paciente "
            "necessita de 2 dias de afastamento a partir de 03/09/2026."
        ),
        OTH,
        f"{SA}|{TR}",
        keep=True,
        lang="pt-BR",
        fields={"kind": "medical certificate"},
    )
    c.add(
        "laudo-ressonancia-joelho.pdf",
        _pdf(
            "Centro de Imagem Ipê\nRessonância magnética do joelho direito\n"
            "Impressão: discreto derrame articular."
        ),
        OTH,
        SA,
        keep=True,
        lang="pt-BR",
        fields={"kind": "imaging report"},
    )
    c.add(
        "lab_results_2026-10.pdf",
        _pdf(
            "Litware Health Labs\nLipid panel\nCollected: October 4, 2026\n"
            "Total cholesterol: 182 mg/dL\nHDL: 55 mg/dL"
        ),
        OTH,
        SA,
        keep=True,
        lang="en",
        fields={
            "document_date": "2026-10-04",
            "issuer": "Litware Health Labs",
            "kind": "lab result",
        },
    )
    value = c.money(100, 900)
    c.add(
        "reembolso-plano-saude.pdf",
        _pdf(
            f"Plano de Saúde Bem-te-vi\nDemonstrativo de reembolso\nValor reembolsado: "
            f"{brl(value)}\nCrédito em conta em 16/10/2026"
        ),
        f"{FIN}|{OTH}",
        f"{SA}|{FI}",
        keep=True,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "document_date": "2026-10-16",
            "issuer": "Plano de Saúde Bem-te-vi",
            "kind": "reimbursement",
        },
        cases=("ambiguous",),
    )
    value = c.money(80, 400)
    c.add(
        "Litware Health invoice.pdf",
        _pdf(
            f"Litware Health Clinic\nPatient invoice\nOffice visit, October 7, 2026\n"
            f"Amount due: {usd(value)}\nDue by: November 6, 2026"
        ),
        FIN,
        f"{SA}|{FI}",
        keep=True,
        lang="en",
        fields={
            "amount": f"{value:.2f}",
            "currency": "USD",
            "document_date": "2026-10-07",
            "due_date": "2026-11-06",
            "issuer": "Litware Health Clinic",
            "kind": "medical bill",
        },
        cases=("ambiguous",),
    )
    c.add(
        "carteira-vacinacao.jpg",
        _png(20),
        f"{IMG}|{OTH}",
        f"{SA}|{IM}|{PD}",
        keep=None,
        cases=("name-only",),
    )


def _work(c: _Corpus) -> None:
    value = c.money(4000, 12000)
    c.add(
        "holerite-setembro-2026.pdf",
        _pdf(
            f"Andorinha Tecnologia Ltda\nDemonstrativo de pagamento - setembro/2026\n"
            f"Salário base: {brl(value)}\nINSS e IRRF descontados conforme tabela\n"
            f"Líquido a receber: {brl(value * 0.78)}"
        ),
        f"{FIN}|{OTH}",
        f"{TR}|{FI}",
        keep=True,
        lang="pt-BR",
        fields={
            "amount": f"{value * 0.78:.2f}",
            "currency": "BRL",
            "issuer": "Andorinha Tecnologia Ltda",
            "period": "2026-09",
            "kind": "payslip",
        },
    )
    value = c.money(3000, 8000)
    c.add(
        "payslip_oct.pdf",
        _pdf(
            f"Tailspin Toys\nEarnings statement\nPay period: October 1-31, 2026\n"
            f"Gross pay: {usd(value)}\nNet pay: {usd(value * 0.72)}"
        ),
        f"{FIN}|{OTH}",
        f"{TR}|{FI}",
        keep=True,
        lang="en",
        fields={
            "amount": f"{value * 0.72:.2f}",
            "currency": "USD",
            "issuer": "Tailspin Toys",
            "period": "2026-10",
            "kind": "payslip",
        },
    )
    c.add(
        "Q3 report.pptx",
        _pptx(
            "Q3 2026 business review",
            "Revenue grew 12% quarter over quarter",
            "Next steps: hiring plan for Q4",
        ),
        OTH,
        TR,
        keep=True,
        lang="en",
    )
    c.add(
        "relatorio-trimestral.docx",
        _docx(
            "Relatório trimestral de atividades",
            "Terceiro trimestre de 2026",
            "Entregas concluídas: migração do sistema de pagamentos.",
        ),
        OTH,
        TR,
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "curriculo-2026.docx",
        _docx(
            "Currículo",
            "Desenvolvedor de software com 8 anos de experiência",
            "Experiência: Andorinha Tecnologia (2019-atual)",
            "Formação: Ciência da Computação",
        ),
        OTH,
        f"{TR}|{PD}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "resume_final_v2.docx",
        _docx(
            "Resume",
            "Product designer",
            "Experience: Litware Inc. 2021-2026",
            "Skills: research, prototyping",
        ),
        OTH,
        f"{TR}|{PD}",
        keep=True,
        lang="en",
    )
    c.add(
        "apresentacao-cliente.pptx",
        _pptx("Proposta de parceria", "Cliente: Loja Tucano Online", "Cronograma e investimento"),
        OTH,
        f"{TR}|{CT}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "planilha-horas.xlsx",
        _xlsx(["Outubro"], ["Controle de horas", "Projeto", "Horas", "Cliente Tucano", "32"]),
        OTH,
        f"{TR}|{CD}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "meeting-notes.md",
        _text(
            "# Weekly sync, October 14\n\n- Ship the onboarding redesign\n"
            "- Decide on the analytics vendor\n- Next sync: October 21\n"
        ),
        OTH,
        f"{TR}|{OU}",
        keep=True,
        lang="en",
    )
    c.add(
        "ata-reuniao-condominio.txt",
        _text(
            "Ata da assembleia geral ordinária do Condomínio Residencial Flamboyant\n"
            "Pauta: aprovação de contas e reforma da fachada.\nAprovada por maioria."
        ),
        OTH,
        f"{CV}|{OU}",
        keep=True,
        lang="pt-BR",
        cases=("ambiguous",),
    )
    c.add(
        "onboarding-checklist.txt",
        _text("New hire checklist\n- Laptop setup\n- Security training\n- Meet the team"),
        OTH,
        TR,
        keep=True,
        lang="en",
    )
    c.add(
        "Re_ aprovacao ferias.eml",
        _eml(
            "Re: aprovação de férias",
            "RH Andorinha <rh@andorinha.example>",
            "Suas férias de 10/12 a 24/12 foram aprovadas. Bom descanso!",
            "Thu, 15 Oct 2026 09:10:00 -0300",
        ),
        OTH,
        TR,
        keep=True,
        lang="pt-BR",
    )


def _studies(c: _Corpus) -> None:
    c.add(
        "artigo-aprendizado-de-maquina.pdf",
        _pdf(
            "Resumo\nEste artigo compara métodos de classificação de documentos com modelos "
            "de linguagem.\nPalavras-chave: aprendizado de máquina, classificação"
        ),
        OTH,
        ES,
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "2310.01234v2.pdf",
        _pdf(
            "Abstract\nWe study retrieval-augmented classification of personal documents.\n"
            "1 Introduction\nPersonal file collections grow quickly."
        ),
        OTH,
        ES,
        keep=False,
        lang="en",
        cases=("generic-name",),
    )
    c.add(
        "apostila-calculo-1.pdf",
        _pdf("Apostila de Cálculo I\nCapítulo 1: Limites\nCapítulo 2: Derivadas"),
        OTH,
        ES,
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "aula03.pptx",
        _pptx("Aula 3 - Estruturas de dados", "Listas, pilhas e filas", "Exercícios para casa"),
        OTH,
        ES,
        keep=None,
        lang="pt-BR",
    )
    c.add(
        "certificado-curso-python.pdf",
        _pdf(
            "Escola Técnica Andorinha\nCertificado de conclusão\nCurso: Python para análise "
            "de dados - 40 horas\nConcluído em 30/08/2026"
        ),
        OTH,
        f"{ES}|{PD}",
        keep=True,
        lang="pt-BR",
        fields={"kind": "course certificate"},
    )
    c.add(
        "lecture-notes-week2.md",
        _text(
            "# Linear algebra, week 2\n\nVector spaces, bases and dimension.\n"
            "Homework: exercises 2.1 to 2.9.\n"
        ),
        OTH,
        ES,
        keep=True,
        lang="en",
    )
    c.add(
        "ebook-introducao-estatistica.pdf",
        _pdf(
            "Introdução à Estatística\nSumário\n1. Estatística descritiva\n"
            "2. Probabilidade\n3. Inferência"
        ),
        OTH,
        ES,
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "tcc-versao-final",
        _folder(
            {
                "capitulos/01-introducao.md": "# Introdução\nEste trabalho...",
                "capitulos/02-metodologia.md": "# Metodologia\n",
                "referencias.bib": "@article{exemplo2025, title={Um estudo}}",
                "tcc.tex": "\\documentclass{article}",
            }
        ),
        OTH,
        ES,
        keep=None,
        fmt="folder",
        cases=("unit",),
    )


def _travel(c: _Corpus) -> None:
    c.add(
        "boarding-pass.pdf",
        _pdf(
            "Tailspin Airways\nBoarding pass\nFlight TS 482  GRU to LIS\n"
            "Date: November 28, 2026  Boarding 21:15  Seat 23C"
        ),
        OTH,
        VI,
        keep=True,
        lang="en",
        fields={
            "document_date": "2026-11-28",
            "issuer": "Tailspin Airways",
            "kind": "boarding pass",
        },
    )
    c.add(
        "e-ticket.pdf",
        _pdf(
            f"Garça Linhas Aéreas\nBilhete eletrônico\nLocalizador: {c.number(6)}\n"
            "São Paulo (GRU) - Recife (REC), 12/12/2026\n"
            f"Tarifa total: {brl(c.money(400, 1600))}"
        ),
        f"{OTH}|{FIN}",
        VI,
        keep=False,
        lang="pt-BR",
        fields={"issuer": "Garça Linhas Aéreas", "kind": "flight ticket"},
        cases=("generic-name",),
    )
    c.add(
        "reserva-hotel.eml",
        _eml(
            "Confirmação da sua reserva",
            "Hotel Sabiá Praia <reservas@sabia-praia.example>",
            "Reserva confirmada: 3 noites, check-in 12/12/2026, check-out 15/12/2026.\n"
            f"Valor total: {brl(c.money(900, 2500))}",
            "Fri, 02 Oct 2026 14:00:00 -0300",
        ),
        OTH,
        VI,
        keep=True,
        lang="pt-BR",
        fields={"kind": "hotel booking"},
    )
    c.add(
        "itinerario-lisboa.docx",
        _docx("Roteiro Lisboa", "Dia 1: Alfama e Castelo", "Dia 2: Belém", "Dia 3: Sintra"),
        OTH,
        VI,
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "seguro-viagem-europa.pdf",
        _pdf(
            "Seguradora Quero-Quero\nApólice de seguro viagem\nDestino: Europa\n"
            "Vigência: 27/11/2026 a 10/12/2026\nCobertura médica: EUR 30.000"
        ),
        f"{OTH}|{CON}",
        VI,
        keep=True,
        lang="pt-BR",
        fields={"kind": "travel insurance"},
    )
    c.add(
        "aluguel-carro-reserva.pdf",
        _pdf(
            "Locadora Tamanduá\nReserva de veículo\nRetirada: Aeroporto de Recife, 12/12/2026\n"
            "Devolução: 15/12/2026\nCategoria econômica com ar-condicionado"
        ),
        OTH,
        VI,
        keep=True,
        lang="pt-BR",
        cases=("ambiguous",),
    )
    c.add(
        "hotel-confirmation.eml",
        _eml(
            "Your stay at Wingtip Suites",
            "Wingtip Suites <stay@wingtip.example>",
            "Confirmation for 2 nights, check-in November 29, 2026.\nTotal: "
            f"{usd(c.money(200, 700))}",
            "Sat, 10 Oct 2026 08:00:00 +0000",
        ),
        OTH,
        VI,
        keep=True,
        lang="en",
        fields={"kind": "hotel booking"},
    )
    c.add(
        "visa-application-form.pdf",
        _pdf(
            "Application for a short-stay visa\nSection 1: personal details\n"
            "Section 2: travel plans and accommodation"
        ),
        OTH,
        f"{VI}|{PD}",
        keep=True,
        lang="en",
    )


def _shopping(c: _Corpus) -> None:
    value = c.money(80, 600)
    c.add(
        f"pedido-{c.number(7)}.eml",
        _eml(
            "Seu pedido foi enviado",
            "Loja Tucano Online <pedidos@tucano.example>",
            f"Pedido enviado!\n1 x Fone de ouvido sem fio\nTotal: {brl(value)}\n"
            "Previsão de entrega: 20/10/2026",
            "Wed, 14 Oct 2026 16:45:00 -0300",
        ),
        f"{FIN}|{OTH}",
        f"{CO}|{FI}",
        keep=False,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "document_date": "2026-10-14",
            "issuer": "Loja Tucano Online",
            "kind": "order",
        },
        cases=("generic-name",),
    )
    c.add(
        "termo-garantia-geladeira.pdf",
        _pdf(
            "Eletrodomésticos Arara\nTermo de garantia\nRefrigerador frost free 400 L\n"
            "Garantia de 12 meses a partir da data da nota fiscal."
        ),
        OTH,
        f"{CO}|{CV}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "manual-cafeteira.pdf",
        _pdf(
            "Manual de instruções\nCafeteira elétrica Arara CF-20\nLeia antes de usar.\n"
            "Limpeza e descalcificação"
        ),
        OTH,
        CO,
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "user-guide.pdf",
        _pdf("Adventure Works TrailCam 2\nUser guide\nCharging the battery\nMounting the camera"),
        OTH,
        CO,
        keep=False,
        lang="en",
        cases=("generic-name",),
    )
    value = c.money(100, 2000)
    c.add(
        f"NFe{c.number(10)}.pdf",
        _pdf(
            f"DANFE - Documento auxiliar da nota fiscal eletrônica\n"
            f"Emitente: Eletrodomésticos Arara Ltda\nValor total da nota: {brl(value)}\n"
            "Data de emissão: 11/10/2026"
        ),
        FIN,
        f"{FI}|{CO}",
        keep=False,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "document_date": "2026-10-11",
            "issuer": "Eletrodomésticos Arara Ltda",
            "kind": "invoice",
        },
        cases=("generic-name", "ambiguous"),
    )
    c.add(
        "order-confirmation.eml",
        _eml(
            "Order confirmed",
            "Adventure Works Store <orders@adventure-works.example>",
            f"Order confirmed: 1 x Camping stove, {usd(c.money(40, 150))}.\n"
            "Estimated delivery: October 30, 2026",
            "Thu, 22 Oct 2026 12:00:00 -0400",
        ),
        f"{FIN}|{OTH}",
        f"{CO}|{FI}",
        keep=True,
        lang="en",
        fields={"kind": "order"},
    )
    c.add(
        "warranty-registration.txt",
        _text(
            "Product: Contoso Air Fryer XL\nPurchased: September 2026\n"
            "Warranty: 2 years, registered online."
        ),
        OTH,
        CO,
        keep=True,
        lang="en",
    )


def _home(c: _Corpus) -> None:
    value = c.money(400, 1200)
    c.add(
        "boleto-condominio-outubro.pdf",
        _pdf(
            f"Condomínio Residencial Flamboyant\nTaxa condominial - outubro/2026\n"
            f"Vencimento: 10/10/2026\nValor: {brl(value)}"
        ),
        FIN,
        f"{CV}|{FI}",
        keep=True,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "due_date": "2026-10-10",
            "issuer": "Condomínio Residencial Flamboyant",
            "period": "2026-10",
            "kind": "condo fee",
        },
        cases=("ambiguous",),
    )
    c.add(
        "escritura-imovel.pdf",
        _pdf(
            "Escritura pública de compra e venda\nImóvel: apartamento residencial\n"
            "Lavrada no 3º Tabelionato de Notas"
        ),
        CON,
        f"{CV}|{CT}",
        keep=True,
        lang="pt-BR",
    )
    value = c.money(600, 3000)
    c.add(
        "IPVA-2026.pdf",
        _pdf(
            f"Secretaria da Fazenda\nIPVA 2026\nVeículo: automóvel de passeio\n"
            f"Valor em cota única: {brl(value)}"
        ),
        FIN,
        f"{CV}|{IR}|{FI}",
        keep=True,
        lang="pt-BR",
        fields={
            "amount": f"{value:.2f}",
            "currency": "BRL",
            "period": "2026",
            "kind": "vehicle tax",
        },
    )
    value = c.money(130, 300)
    c.add(
        "notificacao.pdf",
        _pdf(
            f"Departamento de Trânsito\nNotificação de autuação\nInfração: excesso de "
            f"velocidade até 20%\nValor da multa: {brl(value)}\nPrazo para recurso: 30 dias"
        ),
        f"{OTH}|{FIN}",
        f"{CV}|{FI}",
        keep=False,
        lang="pt-BR",
        fields={"amount": f"{value:.2f}", "currency": "BRL", "kind": "traffic fine"},
        cases=("generic-name",),
    )
    c.add(
        "laudo-vistoria-apartamento.pdf",
        _pdf(
            "Laudo de vistoria de entrada\nImóvel: apartamento 42\nSala: pintura em bom "
            "estado\nCozinha: torneira com pequeno vazamento"
        ),
        f"{CON}|{OTH}",
        f"{CV}|{CT}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "home-insurance-policy.pdf",
        _pdf(
            "Wingtip Insurance\nHomeowners policy declarations\nPolicy period: "
            "November 1, 2026 to November 1, 2027\nDwelling coverage"
        ),
        f"{CON}|{FIN}",
        f"{CV}|{CT}",
        keep=True,
        lang="en",
        fields={"kind": "insurance policy"},
    )
    c.add(
        "car-service-invoice.pdf",
        _pdf(
            f"Proseware Auto Service\nInvoice\nOil change and brake inspection\n"
            f"Date: October 9, 2026\nTotal: {usd(c.money(90, 450))}"
        ),
        FIN,
        f"{CV}|{FI}",
        keep=True,
        lang="en",
        fields={"kind": "invoice"},
    )
    c.add(
        "proposta.pdf",
        _pdf(
            f"Marcenaria Ipê Amarelo\nOrçamento para armários de cozinha sob medida\n"
            f"Valor: {brl(c.money(5000, 15000))}\nPrazo de entrega: 45 dias\n"
            "Validade: 15 dias"
        ),
        f"{CON}|{FIN}|{OTH}",
        f"{CV}|{CT}|{CO}",
        keep=False,
        lang="pt-BR",
        cases=("generic-name", "ambiguous"),
    )


def _images(c: _Corpus) -> None:
    for i, name in enumerate(
        (
            "IMG_4821.jpg",
            "IMG_4822.jpg",
            "IMG_20260914_101522.jpg",
            "DSC_0042.JPG",
            "Screenshot 2026-10-03 at 14.22.10.png",
            "Captura de Tela 2026-09-12 às 10.15.33.png",
            "wallpaper-montanhas.jpg",
            "logo-final.png",
            "foto-perfil.heic",
            "WhatsApp Image 2026-10-05 at 19.03.11.jpeg",
            "diagram-architecture.png",
        )
    ):
        c.add(name, _png(40 + i), IMG, IM, keep=None, cases=("name-only",))
    c.add(
        "Fotos Lisboa 2026",
        _folder({f"IMG_{9000 + i}.jpg": bytes([i]) * 64 for i in range(6)}),
        IMG,
        f"{IM}|{VI}",
        keep=None,
        fmt="folder",
        cases=("unit",),
    )


def _media(c: _Corpus) -> None:
    for name in (
        "gravacao-reuniao-2026-10-14.mp4",
        "podcast-episodio-42.mp3",
        "screen-recording.mov",
        "voice memo.m4a",
        "aniversario-vovo.mp4",
    ):
        c.add(name, _binary(name), OTH, VA, keep=None, cases=("name-only",))


def _installers(c: _Corpus) -> None:
    for name in (
        "Notely-4.2.1.dmg",
        "PrinterDriver_3.1.pkg",
        "Install Fieldnote.pkg",
        "ZapWrite-universal.dmg",
        "atualizacao-firmware-roteador.zip",
    ):
        writer = (
            _zip({"firmware/roteador-v2.bin": b"\0firmware", "LEIA-ME.txt": "Atualização"})
            if name.endswith(".zip")
            else _binary(name, 4096)
        )
        en = f"{INS}|{OTH}" if name.endswith(".zip") else INS
        c.add(
            name,
            writer,
            en,
            f"{IN}|{OU}" if name.endswith(".zip") else IN,
            keep=None,
            cases=("name-only",) if not name.endswith(".zip") else (),
        )
    c.add(
        "Clipwise.app",
        _app("Clipwise", "example.clipwise", "2.3.0"),
        INS,
        IN,
        keep=None,
        fmt="app",
        cases=("unit",),
    )


def _code(c: _Corpus) -> None:
    c.add(
        "script_backup.py",
        _text("import shutil\n\n\ndef backup(src, dst):\n    shutil.copytree(src, dst)\n"),
        OTH,
        CD,
        keep=True,
    )
    c.add(
        "export-tarefas.json",
        _text(
            '[{"tarefa": "pagar condomínio", "feito": true}, '
            '{"tarefa": "renovar seguro", "feito": false}]'
        ),
        OTH,
        f"{CD}|{OU}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "dados-vendas-q3.csv",
        _text(
            "mes;produto;quantidade;receita\n07;Camiseta;120;3600\n08;Caneca;80;1600\n"
            "09;Boné;45;1350\n"
        ),
        OTH,
        f"{CD}|{TR}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "schema.sql",
        _text("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);\n"),
        OTH,
        CD,
        keep=True,
    )
    c.add(
        "analysis.ipynb",
        _text(
            json.dumps(
                {
                    "cells": [{"cell_type": "markdown", "source": ["# Sales analysis"]}],
                    "nbformat": 4,
                    "nbformat_minor": 5,
                }
            )
        ),
        OTH,
        CD,
        keep=True,
        lang="en",
    )
    c.add(
        "site-portfolio",
        _folder(
            {
                "index.html": "<h1>Portfolio</h1>",
                "css/style.css": "body{margin:0}",
                "js/app.js": "console.log('hi')",
            }
        ),
        OTH,
        CD,
        keep=None,
        fmt="folder",
        cases=("unit",),
    )
    c.add(
        "backup-notas-2026.zip",
        _zip({"notas/2026-01.md": "# Janeiro", "notas/2026-02.md": "# Fevereiro"}),
        OTH,
        f"{CD}|{OU}",
        keep=True,
    )
    c.add(
        "config.yaml",
        _text("theme: dark\nfont_size: 14\nplugins:\n  - spell\n  - outline\n"),
        OTH,
        CD,
        keep=True,
    )
    c.add(
        "dotfiles-main.zip",
        _zip({"dotfiles-main/zshrc": "export EDITOR=vim", "dotfiles-main/README.md": "# dots"}),
        OTH,
        CD,
        keep=True,
    )


def _other(c: _Corpus) -> None:
    c.add(
        "receita-lasanha.pdf",
        _pdf(
            "Lasanha à bolonhesa\nIngredientes: massa, molho de tomate, carne moída, "
            "queijo\nModo de preparo: monte em camadas e asse por 40 minutos."
        ),
        OTH,
        OU,
        keep=True,
        lang="pt-BR",
        cases=("ambiguous",),
    )
    c.add(
        "shopping-list.txt",
        _text("milk\neggs\nbread\ncoffee\napples"),
        OTH,
        f"{OU}|{CO}",
        keep=True,
        lang="en",
    )
    c.add(
        "newsletter-outubro.eml",
        _eml(
            "Novidades de outubro",
            "Clube do Livro Sabiá <news@clube-sabia.example>",
            "Confira os lançamentos do mês e os encontros do clube de leitura.",
            "Thu, 01 Oct 2026 07:00:00 -0300",
        ),
        OTH,
        f"{OU}|{ES}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "wedding-invitation.pdf",
        _pdf(
            "You are invited\nto celebrate the wedding of our friends\n"
            "Saturday, December 5, 2026, 4 pm\nGarden Hall"
        ),
        OTH,
        OU,
        keep=True,
        lang="en",
    )
    c.add(
        "playlist-viagem.txt",
        _text("Músicas para a viagem\n1. Samba de verão\n2. Águas de março\n3. Trem das onze"),
        OTH,
        f"{OU}|{VI}|{VA}",
        keep=True,
        lang="pt-BR",
    )
    c.add(
        "ideias.md",
        _text("# Ideias\n\n- aprender marcenaria\n- organizar fotos antigas\n- plantar temperos\n"),
        OTH,
        OU,
        keep=True,
        lang="pt-BR",
    )


def _review(c: _Corpus) -> None:
    """Items with too little to go on: the right outcome is the review folder."""
    for pages, name in enumerate(("scan001.pdf", "scan002.pdf", "Scan 3.pdf"), start=3):
        c.add(
            name,
            _scan(pages),
            OTH,
            OU,
            keep=None,
            review=True,
            cases=("generic-name", "scan", "review"),
        )
    c.add(
        "document(4).pdf",
        _pdf("Página 1 de 1"),
        OTH,
        OU,
        keep=None,
        review=True,
        cases=("generic-name", "review"),
    )
    c.add(
        "untitled.txt",
        _text("ok"),
        OTH,
        OU,
        keep=None,
        review=True,
        cases=("generic-name", "review"),
    )
    c.add(
        "arquivo",
        _bytes(bytes(range(256)) * 4),
        OTH,
        OU,
        keep=None,
        review=True,
        cases=("generic-name", "review"),
    )
    c.add(
        "download.bin",
        _bytes(b"\x07\x00\x13" * 300),
        OTH,
        OU,
        keep=None,
        review=True,
        cases=("generic-name", "review"),
    )
    c.add(
        "temp.docx",
        _docx("Lorem ipsum dolor sit amet", "consectetur adipiscing elit"),
        OTH,
        OU,
        keep=None,
        review=True,
        cases=("generic-name", "review"),
    )
    c.add(
        "final_FINAL.pdf",
        _pdf("Versão 3\nVer comentários na página 2"),
        OTH,
        OU,
        keep=None,
        review=True,
        cases=("generic-name", "review"),
    )
    c.add("IMG_0001.HEIC", _png(99), IMG, IM, keep=None, cases=("name-only",))


def _sensitive(c: _Corpus) -> None:
    """Credential files: withheld by name or by a secret inside; never sent."""
    key_id = "AKIA" + "EVALSYNTHETIC0001"[:16]
    private_key = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nbm90LWEtcmVhbC1rZXk=\n"
    for name, writer in (
        ("credentials.json", _text('{"note": "synthetic placeholder"}')),
        ("service-account-staging.json", _text('{"type": "service_account"}')),
        ("cofre-senhas.kdbx", _binary("kdbx")),
        ("office-vpn.ovpn", _text("client\nremote vpn.example 1194\n")),
        ("deploy-key.pem", _text("synthetic placeholder, not a deploy key\n")),
        ("id_ed25519", _text("synthetic placeholder, not an SSH key\n")),
        ("notas-servidor.txt", _text(f"servidor de testes\naws key: {key_id}\n")),
        ("chave-acesso.txt", _text(private_key)),
        ("db-settings.ini", _text("[db]\ndb_password = " + "synthetic-only-1234\n")),
    ):
        c.add(name, writer, "", "", sensitive=True, cases=("sensitive",))
    c.add(
        "projeto-api",
        _folder({"main.py": "print('api')\n", ".env": "TOKEN=synthetic\n"}),
        "",
        "",
        sensitive=True,
        fmt="folder",
        cases=("sensitive", "unit"),
    )


GROUPS: tuple[Callable[[_Corpus], None], ...] = (
    _finance,
    _taxes,
    _contracts,
    _personal,
    _health,
    _work,
    _studies,
    _travel,
    _shopping,
    _home,
    _images,
    _media,
    _installers,
    _code,
    _other,
    _review,
    _sensitive,
)


def items(seed: int = DEFAULT_SEED) -> list[Item]:
    """Every corpus item with its gold labels, in a fixed order."""
    corpus = _Corpus(seed)
    for group in GROUPS:
        group(corpus)
    return corpus.items


def select(all_items: list[Item], limit: int | None, seed: int = DEFAULT_SEED) -> list[Item]:
    """A fixed sample of ``limit`` items, spread over the corpus; all of them without a limit.

    The sample always keeps the sensitive items, so a short run still checks
    that none is sent.
    """
    if limit is None or limit >= len(all_items):
        return list(all_items)
    sensitive = [i for i in all_items if i.gold.sensitive]
    rest = [i for i in all_items if not i.gold.sensitive]
    random.Random(seed).shuffle(rest)  # noqa: S311 - reproducible, not secret
    chosen = {i.path for i in sensitive[: max(1, limit // 10)]}
    chosen |= {i.path for i in rest[: max(0, limit - len(chosen))]}
    return [i for i in all_items if i.path in chosen]


def build(root: Path, chosen: list[Item]) -> None:
    """Write ``chosen`` into ``root`` (an empty folder), all with the same modification time."""
    for item in chosen:
        target = root / item.path
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"corpus item escapes the folder: {item.path}")
        item.write(target)
    for path in [*root.rglob("*"), root]:
        os.utime(path, (MTIME, MTIME), follow_symlinks=False)


def gold_document(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """The gold labels, as published in ``gold.json``."""
    categories = {lang: [c.name for c in LOCALES[lang].categories] for lang in LANGUAGES}
    return {
        "corpus_version": CORPUS_VERSION,
        "seed": seed,
        "categories": categories,
        "fields": list(FIELD_NAMES),
        "items": [{"path": i.path, "format": i.format, **i.gold.as_dict()} for i in items(seed)],
    }


def gold_json(seed: int = DEFAULT_SEED) -> str:
    return json.dumps(gold_document(seed), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":  # pragma: no cover - maintainer tool
    GOLD_PATH.write_text(gold_json(), encoding="utf-8")
    print(f"wrote {GOLD_PATH}")
