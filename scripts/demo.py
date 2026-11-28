"""A demo library: a believable developer's Downloads after a few days of flib.

    uv run python -m scripts.demo /tmp/flib-demo            # English
    uv run python -m scripts.demo /tmp/flib-demo-pt --pt-BR # Brazilian Portuguese
    uv run python -m scripts.demo /tmp/flib-demo --messy    # plus a cluttered folder to film
    uv run python -m scripts.demo /tmp/flib-demo --clean    # remove it

Everything lives in the folder given, which stands in for a home folder: its own
Downloads, configuration, index and logs. About 40 fictional files (bills and
invoices in USD and BRL with due dates, receipts, a contract, screenshots,
installers, study PDFs, spreadsheets, an e-mail, a set of photos the AI was not
sure about, a duplicate, one credential-looking file) arrive over five days and
are organized by backdated "daily" runs, with one fix by hand and one undo in
between. Three more arrive today and wait for a live ``flib run``.

Classification is offline and deterministic: the fake provider answers from the
table below (``ITEMS``), as a real model would, and the answers for today's files
are cached in the index, so the ``flib run`` you type later gets the same answers
from the configuration's plain ``provider: {name: fake}``. Nothing is sent
anywhere and no real tool is run: the Keychain, launchctl, the notifier, the menu
bar item, the OCR helper and ``open`` are pointed at paths that do not exist
while the demo is built, and every path in its configuration is absolute, inside
the folder. It refuses ``/``, your home folder and flib's real folders.

``--messy`` adds ``before/Downloads``: a folder of that name, outside the demo's
own Downloads and never read by flib, cluttered the way a Downloads folder is
before any of this (``MESSY``). It exists for one shot of a video: the mess.

Every name, company, amount and e-mail address here is invented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

from file_librarian.models import Classification, FileContext, Kind, Rules

MARKER = ".flib-demo"
"""The file that says a folder holds a demo library, the only kind ``--clean`` removes."""
Language = Literal["en", "pt-BR"]
NOWHERE = "/nonexistent/flib-demo"
"""Where every real system tool points while the demo is built: calling one fails."""


class DemoError(Exception):
    """The demo cannot be built (or removed) there; the message says why."""


# --- Where it may go ------------------------------------------------------------------


def protected_folders(home: Path) -> list[Path]:
    """Folders of the real account the demo never goes into, nor above."""
    support = home / "Library" / "Application Support"
    return [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        home / ".config" / "file-librarian",
        support / "file-librarian",
        support / "file-librarian-menu",
        home / "Library" / "LaunchAgents",
    ]


def check_target(target: Path, home: Path) -> Path:
    """``target``, resolved, if a demo may be built or removed there; else ``DemoError``.

    Refused: the file system's root and any folder just under it (``/tmp`` itself),
    ``home`` and every folder above it, and flib's real folders (Downloads, the
    configuration, the index, the LaunchAgents...), inside or above them, and the shared
    temporary folders themselves."""
    folder = target.expanduser().resolve()
    home = home.expanduser().resolve()
    shared = {Path(p).resolve() for p in ("/tmp", "/var/tmp", tempfile.gettempdir())}  # noqa: S108
    if folder == Path(folder.anchor) or len(folder.parts) < 3 or folder in shared:
        raise DemoError(f"refusing {folder}: give a new folder of its own, such as /tmp/flib-demo")
    if folder == home or folder in home.parents:
        raise DemoError(f"refusing {folder}: it is your home folder or holds it")
    for real in protected_folders(home):
        real = real.resolve()
        if folder == real or real in folder.parents or folder in real.parents:
            raise DemoError(f"refusing {folder}: it is, holds or is inside {real}")
    return folder


# --- The files ---------------------------------------------------------------------------


Writer = Callable[[Path], object]

CATEGORIES: dict[str, tuple[str, str, str, bool, str | None]] = {
    # key: (English, Portuguese, hint, rename, layout)
    "finance": (
        "Finance",
        "Financeiro",
        "invoices, bills, receipts, payslips, taxes",
        True,
        "{year}",
    ),
    "contracts": ("Contracts", "Contratos", "contracts, agreements, leases", True, None),
    "work": ("Work", "Trabalho", "work documents, notes, slides, spreadsheets, code", True, None),
    "study": ("Study", "Estudos", "books, papers, course notes, cheat sheets", True, None),
    "installers": (
        "Installers",
        "Instaladores",
        "applications, disk images and installer packages",
        False,
        None,
    ),
    "images": ("Images", "Imagens", "screenshots, photos, wallpapers", True, None),
    "other": ("Other", "Outros", "anything else", True, None),
}
HINTS_PT = {
    "finance": "faturas, contas, recibos, holerites, impostos",
    "contracts": "contratos, acordos, aditivos",
    "work": "documentos de trabalho, notas, slides, planilhas, código",
    "study": "livros, artigos, notas de curso, colas",
    "installers": "aplicativos, imagens de disco e pacotes de instalação",
    "images": "capturas de tela, fotos, papéis de parede",
    "other": "todo o resto",
}
UNCONFIGURED = {"en": "Recipes", "pt-BR": "Receitas"}
"""What the AI suggests for the recipe, a category the configuration does not have."""


@dataclass(frozen=True, slots=True)
class Answer:
    """What the scripted model answers for one file, in both languages."""

    category: str
    """A key of ``CATEGORIES``, or ``"unconfigured"``."""
    subject: tuple[str, str]
    description: tuple[str, str]
    tags: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    confidence: float = 0.93
    kind: Kind | None = None
    issuer: str | None = None
    amount: str | None = None
    currency: str | None = None
    due: int | None = None
    """Due date, in days from today."""
    dated: int | None = None
    """Document date, in days from today; a bill's period is that month."""
    keep_name: bool = False


@dataclass(frozen=True, slots=True)
class Item:
    name: str
    write: Writer
    answer: Answer | None
    """``None``: the provider fails on it (one failed file in the demo)."""
    pt_name: str | None = None
    """Its name on a Mac set to Brazilian Portuguese, when that differs."""

    def name_in(self, language: Language) -> str:
        return self.pt_name if language == "pt-BR" and self.pt_name else self.name


def _day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


def _pdf(*lines: str) -> Writer:
    from file_librarian.evals.documents import make_pdf

    return lambda path: make_pdf(path, "\n".join(lines))


def _bill(title: str, issuer: str, amount: str, currency: str, due: int, dated: int) -> Writer:
    """A bill or invoice whose dates are relative to the day the demo is built."""

    def write(path: Path) -> object:
        from file_librarian.evals.documents import make_pdf

        text = "\n".join(
            [
                issuer,
                title,
                f"Issued: {_day(dated).isoformat()}",
                f"Amount due: {currency} {amount}",
                f"Due date: {_day(due).isoformat()}",
                "Thank you for your business.",
            ]
        )
        return make_pdf(path, text)

    return write


def _xlsx(sheets: list[str], cells: list[str]) -> Writer:
    from file_librarian.evals.documents import make_xlsx

    return lambda path: make_xlsx(path, sheets, cells)


def _docx(*paragraphs: str) -> Writer:
    from file_librarian.evals.documents import make_docx

    return lambda path: make_docx(path, list(paragraphs))


def _pptx(*slides: str) -> Writer:
    from file_librarian.evals.documents import make_pptx

    return lambda path: make_pptx(path, list(slides))


def _png(width: int, height: int, shade: int) -> Writer:
    from file_librarian.evals.documents import make_png

    return lambda path: make_png(path, width, height, shade)


def _raw(text: str) -> Writer:
    return lambda path: path.write_bytes(text.encode())


def _eml() -> Writer:
    from file_librarian.evals.documents import make_eml

    return lambda path: make_eml(
        path,
        "Your ticket for Byteconf 2026 is attached. Doors open at 9:00.",
        subject="Your Byteconf 2026 ticket",
        sender="Byteconf Tickets <tickets@byteconf.example>",
        to="dev@mail.example",
        attachments={"ticket.pdf": b"%PDF-1.4 demo ticket"},
    )


def _folder(files: dict[str, str]) -> Writer:
    def write(path: Path) -> object:
        path.mkdir()
        for name, text in files.items():
            (path / name).write_text(text, encoding="utf-8")
        return path

    return write


def _client_secret(path: Path) -> object:
    """An OAuth client file as a cloud console names it, with a value that is plainly
    fake and built here, so no secret-looking string sits in the repository."""
    fake = "-".join(["FAKE", "DEMO", hashlib.sha256(b"flib demo").hexdigest()[:20]])
    data = {"installed": {"client_id": "demo-app.apps.example", "client_secret": fake}}
    return path.write_text(json.dumps(data), encoding="utf-8")


def A(category: str, en: str, pt: str, desc_en: str, desc_pt: str, **kw: Any) -> Answer:
    return Answer(category, (en, pt), (desc_en, desc_pt), **kw)


def _screenshot(day: int, taken: str, shade: int) -> Item:
    """A screenshot, named the way macOS names them in each language."""
    when = _day(day).isoformat()
    return Item(
        f"Screenshot {when} at {taken}.png",
        _png(64, 40, shade),
        A(
            "images",
            "screenshot",
            "captura-de-tela",
            "A screenshot.",
            "Uma captura de tela.",
            tags=(("screenshot",), ("captura",)),
            confidence=0.9,
            keep_name=True,
        ),
        pt_name=f"Captura de Tela {when} às {taken}.png",
    )


# fmt: off
DAY_1 = [
    Item(
        "invoice-INV-2041.pdf",
        _bill("Invoice INV-2041, cloud hosting", "Lumen Cloud Hosting", "48.00", "USD", 5, -8),
        A(
            "finance", "lumen-cloud-invoice", "fatura-lumen-cloud",
            "Monthly cloud hosting invoice from Lumen Cloud Hosting.",
            "Fatura mensal de hospedagem em nuvem da Lumen Cloud Hosting.",
            tags=(("invoice", "hosting"), ("fatura", "hospedagem")),
            kind="invoice", issuer="Lumen Cloud Hosting", amount="48.00", currency="USD",
            due=5, dated=-8,
        ),
    ),
    Item(
        "fatura-energia-nov.pdf",
        _bill("Conta de energia", "Companhia Eletrica Aurora", "287.43", "BRL", -3, -12),
        A(
            "finance", "aurora-electricity-bill", "conta-de-energia-aurora",
            "Electricity bill from Companhia Elétrica Aurora.",
            "Conta de energia elétrica da Companhia Elétrica Aurora.",
            tags=(("bill", "electricity"), ("conta", "energia")),
            kind="bill", issuer="Companhia Elétrica Aurora", amount="287.43", currency="BRL",
            due=-3, dated=-12,
        ),
    ),
    Item(
        "documento(3).pdf",
        _bill("Conta de agua", "Aguas do Vale", "96.20", "BRL", 2, -10),
        A(
            "finance", "water-bill", "conta-de-agua",
            "Water bill from Águas do Vale.",
            "Conta de água da Águas do Vale.",
            tags=(("bill", "water"), ("conta", "água")),
            kind="bill", issuer="Águas do Vale", amount="96.20", currency="BRL",
            due=2, dated=-10,
        ),
    ),
    Item(
        "scan001.pdf",
        _pdf("Keystone Gear", "Receipt", "Mechanical keyboard, 75%", "Total: USD 129.90", "Paid"),
        A(
            "finance", "keyboard-receipt", "recibo-teclado",
            "Receipt for a mechanical keyboard from Keystone Gear.",
            "Recibo de um teclado mecânico da Keystone Gear.",
            tags=(("receipt", "hardware"), ("recibo", "hardware")),
            kind="receipt", issuer="Keystone Gear", amount="129.90", currency="USD", dated=-7,
        ),
    ),
    Item(
        "contractor-agreement-signed.pdf",
        _pdf("Independent contractor agreement", "Northbeam Labs and the contractor",
             "Term: twelve months", "Signed by both parties"),
        A(
            "contracts", "northbeam-contractor-agreement", "contrato-prestacao-northbeam",
            "Signed independent contractor agreement with Northbeam Labs, twelve months.",
            "Contrato de prestação de serviços assinado com a Northbeam Labs, doze meses.",
            tags=(("contract", "signed"), ("contrato", "assinado")),
            kind="contract", issuer="Northbeam Labs", dated=-9,
        ),
    ),
    _screenshot(-7, "10.14.03", 10),
    _screenshot(-7, "16.52.40", 20),
    Item(
        "quillterm-1.8.0-arm64.dmg",
        _raw("demo placeholder: quillterm 1.8.0 disk image\n"),
        A(
            "installers", "quillterm", "quillterm",
            "Disk image of the QuillTerm terminal app, version 1.8.0 for Apple silicon.",
            "Imagem de disco do terminal QuillTerm, versão 1.8.0 para Apple silicon.",
            tags=(("installer", "terminal"), ("instalador", "terminal")), keep_name=True,
        ),
    ),
    Item(
        "rust-async-patterns.pdf",
        _pdf("Async patterns in Rust", "Futures, executors and cancellation", "Chapter 1"),
        A(
            "study", "rust-async-patterns", "padroes-async-em-rust",
            "Study notes on asynchronous programming patterns in Rust.",
            "Notas de estudo sobre padrões de programação assíncrona em Rust.",
            tags=(("rust", "async"), ("rust", "async")), kind="manual", keep_name=True,
        ),
    ),
    Item(
        "designing-data-systems-notes.pdf",
        _pdf("Designing data systems", "Replication, partitioning, consistency", "Notes"),
        A(
            "study", "designing-data-systems-notes", "notas-sistemas-de-dados",
            "Reading notes on designing data-intensive systems.",
            "Notas de leitura sobre o projeto de sistemas de dados.",
            tags=(("databases", "notes"), ("bancos de dados", "notas")), keep_name=True,
        ),
    ),
    Item(
        "budget-2026.xlsx",
        _xlsx(["Budget", "Subscriptions"], ["Rent", "Hosting", "Coffee", "Total"]),
        A(
            "finance", "household-budget", "orcamento-domestico",
            "Household budget spreadsheet for 2026, with subscriptions.",
            "Planilha de orçamento doméstico de 2026, com assinaturas.",
            tags=(("budget", "spreadsheet"), ("orçamento", "planilha")), dated=-30,
        ),
    ),
    Item(
        "sprint-metrics-q4.xlsx",
        _xlsx(["Velocity", "Bugs"], ["Sprint 41", "Sprint 42", "Story points", "Open bugs"]),
        A(
            "work", "sprint-metrics-q4", "metricas-sprint-q4",
            "Team sprint metrics for the fourth quarter: velocity and open bugs.",
            "Métricas de sprint do quarto trimestre: velocidade e bugs abertos.",
            tags=(("metrics", "sprint"), ("métricas", "sprint")), kind="report", keep_name=True,
        ),
    ),
    Item(
        "meeting-notes.docx",
        _docx("Architecture sync", "Decided: move the queue to the new cluster", "Owner: team"),
        A(
            "work", "architecture-sync-notes", "notas-reuniao-arquitetura",
            "Notes from an architecture meeting about moving the job queue.",
            "Notas de uma reunião de arquitetura sobre a mudança da fila de tarefas.",
            tags=(("meeting", "notes"), ("reunião", "notas")), dated=-7,
        ),
    ),
    Item(
        "slides-final.pdf",
        _pdf("Platform roadmap", "Q1 goals", "Migration plan", "Open questions"),
        A(
            "other", "slides", "slides",
            "A slide deck.", "Uma apresentação de slides.",
            confidence=0.74, keep_name=True,
        ),
    ),
]

DAY_2 = [
    Item(
        "invoice-8812.pdf",
        _bill("Invoice 8812, design services", "Pixelforge Studio", "1850.00", "BRL", 20, -5),
        A(
            "finance", "pixelforge-invoice", "fatura-pixelforge",
            "Invoice for design services from Pixelforge Studio.",
            "Fatura de serviços de design da Pixelforge Studio.",
            tags=(("invoice", "design"), ("fatura", "design")),
            kind="invoice", issuer="Pixelforge Studio", amount="1850.00", currency="BRL",
            due=20, dated=-5,
        ),
    ),
    Item(
        "payment-receipt-oct.pdf",
        _pdf("Lumen Cloud Hosting", "Payment received", "Amount: USD 46.10", "Thank you"),
        A(
            "finance", "lumen-cloud-payment-receipt", "recibo-pagamento-lumen-cloud",
            "Payment receipt for last month's cloud hosting from Lumen Cloud Hosting.",
            "Recibo de pagamento da hospedagem do mês passado, da Lumen Cloud Hosting.",
            tags=(("receipt", "hosting"), ("recibo", "hospedagem")),
            kind="receipt", issuer="Lumen Cloud Hosting", amount="46.10", currency="USD",
            dated=-34,
        ),
    ),
    Item(
        "domain-renewal.pdf",
        _pdf("Namewise Registrar", "Domain renewal, one year", "Total: USD 12.00", "Paid"),
        A(
            "finance", "domain-renewal-receipt", "recibo-renovacao-dominio",
            "Receipt for a one-year domain renewal from Namewise Registrar.",
            "Recibo da renovação de um domínio por um ano, da Namewise Registrar.",
            tags=(("receipt", "domain"), ("recibo", "domínio")),
            kind="receipt", issuer="Namewise Registrar", amount="12.00", currency="USD",
            dated=-5,
        ),
    ),
    Item(
        "iptu-2026-parcela-11.pdf",
        _bill("Imposto predial, parcela 11", "Prefeitura de Vila Serena", "212.75", "BRL", 12, -6),
        A(
            "finance", "property-tax-installment-11", "iptu-parcela-11",
            "Property tax installment 11 of 2026 from the city of Vila Serena.",
            "Parcela 11 do IPTU de 2026 da Prefeitura de Vila Serena.",
            tags=(("tax", "property"), ("imposto", "iptu")),
            kind="tax", issuer="Prefeitura de Vila Serena", amount="212.75", currency="BRL",
            due=12, dated=-6,
        ),
    ),
    Item(
        "orbitdb-studio-2.4.1.dmg",
        _raw("demo placeholder: orbitdb studio 2.4.1 disk image\n"),
        A(
            "installers", "orbitdb-studio", "orbitdb-studio",
            "Disk image of OrbitDB Studio 2.4.1, a database client.",
            "Imagem de disco do OrbitDB Studio 2.4.1, um cliente de banco de dados.",
            tags=(("installer", "database"), ("instalador", "banco de dados")), keep_name=True,
        ),
    ),
    Item(
        "ml-for-engineers-ch3.pdf",
        _pdf("Machine learning for engineers", "Chapter 3: evaluation", "Precision and recall"),
        A(
            "study", "ml-for-engineers-chapter-3", "ml-para-engenheiros-cap-3",
            "Chapter 3 of a machine learning book for engineers, on evaluation.",
            "Capítulo 3 de um livro de aprendizado de máquina para engenheiros, sobre avaliação.",
            tags=(("machine learning", "book"), ("aprendizado de máquina", "livro")),
            keep_name=True,
        ),
    ),
    Item(
        "typescript-cheatsheet.pdf",
        _pdf("TypeScript cheat sheet", "Generics, narrowing, utility types"),
        A(
            "study", "typescript-cheatsheet", "cola-typescript",
            "A TypeScript cheat sheet: generics, narrowing and utility types.",
            "Uma cola de TypeScript: genéricos, narrowing e utility types.",
            tags=(("typescript", "cheat sheet"), ("typescript", "cola")), keep_name=True,
        ),
    ),
    Item(
        "Your Byteconf 2026 ticket.eml",
        _eml(),
        A(
            "other", "byteconf-ticket", "ingresso-byteconf",
            "E-mail with a ticket for the Byteconf 2026 developer conference.",
            "E-mail com o ingresso da conferência de desenvolvedores Byteconf 2026.",
            tags=(("ticket", "conference"), ("ingresso", "conferência")),
            kind="ticket", issuer="Byteconf", amount="199.00", currency="USD", dated=-4,
        ),
    ),
    Item(
        "sample-api-main",
        _folder(
            {
                "README.md": "# sample-api\nA tiny HTTP API used in a workshop.\n",
                "main.py": "print('hello from sample-api')\n",
                "pyproject.toml": '[project]\nname = "sample-api"\nversion = "0.1.0"\n',
            }
        ),
        A(
            "work", "sample-api", "sample-api",
            "Source folder of a small HTTP API project used in a workshop.",
            "Pasta com o código de um pequeno projeto de API HTTP usado num workshop.",
            tags=(("code", "python"), ("código", "python")), keep_name=True,
        ),
    ),
    Item(
        "wallpaper-mountains.png",
        _png(96, 54, 90),
        A(
            "images", "wallpaper", "papel-de-parede",
            "A desktop wallpaper.", "Um papel de parede.",
            tags=(("wallpaper",), ("papel de parede",)), confidence=0.88, keep_name=True,
        ),
    ),
]

DAY_3 = [
    *[
        Item(
            f"IMG_{2041 + i}.jpg",
            _png(48 + i, 64, 140 + i),
            A(
                "work", "whiteboard-photo", "foto-do-quadro",
                "A photo, possibly of a whiteboard.", "Uma foto, talvez de um quadro branco.",
                tags=(("photo",), ("foto",)), confidence=0.55, keep_name=True,
            ),
        )
        for i in range(4)
    ],
    Item(
        "invoice-INV-2041 (1).pdf",
        _bill("Invoice INV-2041, cloud hosting", "Lumen Cloud Hosting", "48.00", "USD", 5, -8),
        None,  # never asked: the same content as invoice-INV-2041.pdf, a duplicate
    ),
    Item(
        "lease-addendum.pdf",
        _pdf("Lease addendum", "Rent adjustment and term extension", "Signed"),
        A(
            "contracts", "lease-addendum", "aditivo-contrato-aluguel",
            "Addendum to an apartment lease: rent adjustment and a longer term.",
            "Aditivo ao contrato de aluguel: reajuste e prazo estendido.",
            tags=(("lease", "contract"), ("aluguel", "contrato")), kind="contract", dated=-3,
        ),
    ),
    Item(
        "sourdough.pdf",
        _pdf("Sourdough bread", "Flour, water, salt, starter", "Bake at 240 C"),
        A(
            "unconfigured", "sourdough-recipe", "receita-pao-de-fermentacao-natural",
            "A recipe for sourdough bread.", "Uma receita de pão de fermentação natural.",
            tags=(("recipe", "bread"), ("receita", "pão")), confidence=0.9,
        ),
    ),
    Item(
        "internet-bill.pdf",
        _bill("Internet, fibre 500 Mb", "Fibra Rapida Telecom", "119.90", "BRL", 9, -4),
        A(
            "finance", "fibra-rapida-internet-bill", "conta-internet-fibra-rapida",
            "Fibre internet bill from Fibra Rápida Telecom.",
            "Conta de internet por fibra da Fibra Rápida Telecom.",
            tags=(("bill", "internet"), ("conta", "internet")),
            kind="bill", issuer="Fibra Rápida Telecom", amount="119.90", currency="BRL",
            due=9, dated=-4,
        ),
    ),
    Item(
        "quarterly-review-draft.pptx",
        _pptx("Quarterly review", "Uptime 99.95%", "Next quarter"),
        A(
            "work", "quarterly-review-draft", "revisao-trimestral-rascunho",
            "Draft slides for the team's quarterly review: uptime and next steps.",
            "Rascunho dos slides da revisão trimestral do time: disponibilidade e próximos passos.",
            tags=(("slides", "review"), ("slides", "revisão")), kind="report", keep_name=True,
        ),
    ),
]

DAY_4 = [
    Item("client_secret_demo.json", _client_secret, None),  # left in place as sensitive
    Item(
        "holerite-outubro.pdf",
        _pdf("Northbeam Labs", "Payslip", "Net pay: BRL 9,400.00", "Reference month: last month"),
        A(
            "finance", "northbeam-payslip", "holerite-northbeam",
            "Payslip from Northbeam Labs for last month.",
            "Holerite da Northbeam Labs referente ao mês passado.",
            tags=(("payslip", "salary"), ("holerite", "salário")),
            kind="payslip", issuer="Northbeam Labs", amount="9400.00", currency="BRL", dated=-28,
        ),
    ),
    Item(
        "events-export.csv",
        _raw("timestamp,event,count\n2026-01-01T00:00:00Z,signup,3\n"),
        None,  # the provider fails on it: one failed file
    ),
    _screenshot(-2, "09.03.11", 30),
    Item(
        "sql-indexing-guide.pdf",
        _pdf("A practical guide to SQL indexes", "B-trees, covering indexes, EXPLAIN"),
        A(
            "study", "sql-indexing-guide", "guia-indices-sql",
            "A practical guide to SQL indexes and reading query plans.",
            "Um guia prático de índices em SQL e de leitura de planos de consulta.",
            tags=(("sql", "databases"), ("sql", "bancos de dados")), keep_name=True,
        ),
    ),
]

TODAY = [
    Item(
        "scan_0007.pdf",
        _pdf("Farmacia Central", "Receipt", "Total: BRL 54.90", "Paid by card"),
        A(
            "finance", "pharmacy-receipt", "recibo-farmacia",
            "Receipt from Farmácia Central.", "Recibo da Farmácia Central.",
            tags=(("receipt", "pharmacy"), ("recibo", "farmácia")),
            kind="receipt", issuer="Farmácia Central", amount="54.90", currency="BRL", dated=0,
        ),
    ),
    Item(
        "container-networking-notes.pdf",
        _pdf("Container networking", "Bridges, overlays, service meshes", "Notes"),
        A(
            "study", "container-networking-notes", "notas-redes-de-containers",
            "Study notes on container networking: bridges, overlays and meshes.",
            "Notas de estudo sobre redes de containers: bridges, overlays e meshes.",
            tags=(("networking", "containers"), ("redes", "containers")), keep_name=True,
        ),
    ),
    Item(
        "untitled.docx",
        _docx("Statement of work", "Northbeam Labs", "Scope: API migration", "Six weeks"),
        A(
            "contracts", "northbeam-statement-of-work", "escopo-de-trabalho-northbeam",
            "Statement of work with Northbeam Labs for an API migration, six weeks.",
            "Escopo de trabalho com a Northbeam Labs para uma migração de API, seis semanas.",
            tags=(("contract", "scope"), ("contrato", "escopo")), kind="contract", dated=0,
        ),
    ),
]
# fmt: on

FIXED = "slides-final.pdf"
"""Filed in Other on day 1; corrected by hand to Work on day 2."""
UNDONE = "wallpaper-mountains.png"
"""Filed on day 2, put back in Downloads with undo on day 4."""
FAILED = "events-export.csv"
SENSITIVE = "client_secret_demo.json"
ITEMS = [*DAY_1, *DAY_2, *DAY_3, *DAY_4, *TODAY]


# --- The scripted provider ---------------------------------------------------------------


def classification(answer: Answer, language: Language) -> Classification:
    pt = 1 if language == "pt-BR" else 0
    if answer.category == "unconfigured":
        category = UNCONFIGURED[language]
    else:
        category = category_name(answer.category, language)
    dated = _day(answer.dated) if answer.dated is not None else None
    period = (
        dated.strftime("%Y-%m")
        if dated is not None and answer.kind in ("bill", "invoice", "payslip", "tax")
        else None
    )
    return Classification(
        category=category,
        suggested_name=answer.subject[pt],
        description=answer.description[pt],
        tags=list(answer.tags[pt]),
        confidence=answer.confidence,
        document_date=dated,
        name_choice="original" if answer.keep_name else "proposed",
        name_choice_reason=(
            ("The original name already says what it is.", "O nome original já diz o que é.")[pt]
            if answer.keep_name
            else ("The original name says nothing.", "O nome original não diz nada.")[pt]
        ),
        issuer=answer.issuer,
        amount=answer.amount,
        currency=answer.currency,
        due_date=_day(answer.due) if answer.due is not None else None,
        period=period,
        kind=answer.kind,
    )


class ScriptedProvider:
    """The fake provider, answering from ``ITEMS``; each answer is billed as a small
    hosted model would bill it, so costs look like real ones."""

    name = "fake"  # the configuration's provider: the cache serves `flib run` later
    local = True
    attachments: frozenset[str] = frozenset()

    def __init__(self, language: Language) -> None:
        from file_librarian.providers.base import Usage

        self._answers = {i.name_in(language): i.answer for i in ITEMS}
        self._language = language
        self.last_usage = Usage()

    def cost_usd(self, usage: Any) -> float:
        # US$ 1 per million input tokens and 5 per million output tokens.
        return float(usage.input_tokens * 1e-6 + usage.output_tokens * 5e-6)

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        from file_librarian.providers.base import ProviderError, Usage

        self.last_usage = Usage(700 + 9 * len(context.path.name), 150, ["demo"])
        answer = self._answers.get(context.path.name)
        if answer is None:
            raise ProviderError("the provider did not answer in time")
        return classification(answer, self._language)


# --- The mess, for one shot ---------------------------------------------------------------

# Names as downloads really arrive: numbered copies, scanner and camera counters,
# exports and "final" versions. Nothing here says what the file is; that is the point.
MESSY = (
    "documento(3).pdf", "documento(4).pdf", "scan_0003.pdf", "scan_0004.pdf",
    "scan_0005.pdf", "untitled.docx", "untitled (1).docx", "Document1.docx",
    "IMG_2041.jpg", "IMG_2042.jpg", "IMG_2043.jpg", "IMG_2044.jpg",
    "Screenshot 2026-10-02 at 09.14.33.png", "Screenshot 2026-10-02 at 09.14.41.png",
    "Screenshot 2026-10-19 at 17.02.10.png", "download.pdf", "download (1).pdf",
    "download (2).pdf", "export.csv", "export (1).csv", "report_final.xlsx",
    "report_final_v2.xlsx", "report_final_v2 (1).xlsx", "invoice.pdf", "invoice (1).pdf",
    "boleto.pdf", "boleto (1).pdf", "comprovante.pdf", "file.zip", "archive.zip",
    "presentation-new.pptx", "notes.txt", "notes (1).txt", "installer.dmg",
    "setup-latest.dmg", "2f9c1e7a.pdf", "0b41d2c8.pdf", "attachment.eml",
    "contract_signed_FINAL.pdf", "contract_signed_FINAL (1).pdf",
)  # fmt: skip


def messy(demo: Demo) -> Path:
    """A cluttered folder named Downloads, beside the demo's home: for filming only.

    flib never reads it (it is outside every folder of the demo's configuration);
    the files are a few bytes each and their dates are spread over three months.
    """
    folder = demo.before
    folder.mkdir(parents=True)
    now = datetime.now().astimezone()
    for n, name in enumerate(MESSY):
        path = folder / name
        path.write_bytes(b"A placeholder for the flib demo: nothing to read here.\n")
        stamp = (now - timedelta(days=(n * 7) % 90, minutes=n * 13)).timestamp()
        os.utime(path, (stamp, stamp))
    return folder


# --- Building it -------------------------------------------------------------------------


@dataclass(slots=True)
class Demo:
    root: Path
    language: Language
    home: Path = field(init=False)
    downloads: Path = field(init=False)
    output: Path = field(init=False)
    config_path: Path = field(init=False)
    database: Path = field(init=False)
    before: Path = field(init=False)

    def __post_init__(self) -> None:
        self.home = self.root / "home"
        self.before = self.root / "before" / "Downloads"
        self.downloads = self.home / "Downloads"
        organized = "Organizado" if self.language == "pt-BR" else "Organized"
        self.output = self.downloads / organized
        self.config_path = self.home / ".config" / "file-librarian" / "config.yaml"
        self.database = (
            self.home / "Library" / "Application Support" / "file-librarian" / "library.db"
        )


def config_text(demo: Demo) -> str:
    pt = demo.language == "pt-BR"
    lines = [
        "# A demo library built by scripts/demo.py: fictional files, the offline fake",
        "# provider. Every path is absolute and inside the demo's folder.",
        f"language: {demo.language}",
        "inputs:",
        f"  - {json.dumps(str(demo.downloads))}",
        f"output_root: {json.dumps(str(demo.output))}",
        f"database: {json.dumps(str(demo.database))}",
        "provider:",
        "  name: fake      # offline; today's answers are cached in the demo's index",
        "  model: demo",
        "categories:",
    ]
    for key, (en, pt_name, hint, rename, layout) in CATEGORIES.items():
        lines.append(f"  - name: {json.dumps(pt_name if pt else en, ensure_ascii=False)}")
        lines.append(f"    hint: {json.dumps(HINTS_PT[key] if pt else hint, ensure_ascii=False)}")
        if not rename:
            lines.append("    rename: false")
        if layout:
            lines.append(f"    layout: {json.dumps(layout)}")
    lines += [
        'never_send: ["*.pem", "*.key", ".env*"]',
        "schedule:",
        '  time: "12:30"',
        "",
    ]
    return "\n".join(lines)


@contextmanager
def sandboxed(home: Path) -> Iterator[None]:
    """``HOME`` is the demo's home, and every real tool flib could start points
    nowhere, until the block ends (then everything is put back as it was)."""
    from file_librarian import (
        keychain,
        menubar_state,
        notifier,
        onboarding,
        protection,
        scheduler,
        status,
    )
    from file_librarian.extractors import ocr, visual
    from file_librarian.ui import server

    patches: list[tuple[Any, str, Any]] = [
        (keychain, "SECURITY", NOWHERE),
        (scheduler, "LAUNCHCTL", NOWHERE),
        (status, "OSASCRIPT", NOWHERE),
        (ocr, "OSASCRIPT", NOWHERE),
        (visual, "SIPS", NOWHERE),
        (onboarding, "DEFAULTS", NOWHERE),
        (server, "OPEN", NOWHERE),
        (protection, "IOREG", NOWHERE),
        (protection, "screen_locked", lambda: False),
        (menubar_state, "POST", lambda _name: False),
        *[(notifier, n, NOWHERE) for n in ("OSACOMPILE", "CODESIGN", "SIPS", "ICONUTIL", "OPEN")],
    ]
    saved = [(module, name, getattr(module, name)) for module, name, _ in patches]
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    for module, name, value in patches:
        setattr(module, name, value)
    ocr.availability.cache_clear()
    try:
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)
        ocr.availability.cache_clear()
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


@contextmanager
def clock(when: datetime) -> Iterator[None]:
    """The index records ``when`` as the time of everything done in the block."""
    from file_librarian.db import store

    real = store._now
    stamp = when.astimezone(UTC).isoformat(timespec="seconds")
    setattr(store, "_now", lambda: stamp)  # noqa: B010 - a module function, swapped for a while
    try:
        yield
    finally:
        setattr(store, "_now", real)  # noqa: B010


def at(days: int, hour: int, minute: int) -> datetime:
    return datetime.combine(_day(days), time(hour, minute)).astimezone()


def arrive(demo: Demo, items: list[Item], when: datetime) -> None:
    """Drop ``items`` into Downloads as if downloaded over the hour before ``when``."""
    for n, item in enumerate(items):
        path = demo.downloads / item.name_in(demo.language)
        item.write(path)
        stamp = (when - timedelta(minutes=60 - n)).timestamp()
        targets = [path, *path.rglob("*")] if path.is_dir() else [path]
        for target in targets:
            os.utime(target, (stamp, stamp))


def build(
    root: Path,
    language: Language = "en",
    *,
    home: Path | None = None,
    with_mess: bool = False,
) -> Demo:
    """Build the demo library in ``root`` (refused where ``check_target`` refuses);
    a demo already there is replaced. ``home``: the real home folder to stay out of.
    ``with_mess``: also the cluttered folder of ``messy``."""
    from file_librarian import pipeline
    from file_librarian.config import load_config
    from file_librarian.db.store import Store
    from file_librarian.fix import fix_file
    from file_librarian.models import RunTrigger
    from file_librarian.undo import undo_file

    real_home = home if home is not None else Path.home()
    root = check_target(root, real_home)
    if root.exists() and any(root.iterdir()):
        if not (root / MARKER).is_file():
            raise DemoError(f"refusing {root}: it is not empty and holds no demo library")
        shutil.rmtree(root)
    demo = Demo(root, language)
    demo.downloads.mkdir(parents=True)
    (root / MARKER).write_text(
        "A flib demo library (scripts/demo.py); remove it with --clean.\n", encoding="utf-8"
    )
    demo.config_path.parent.mkdir(parents=True)
    demo.config_path.write_text(config_text(demo), encoding="utf-8")
    demo.config_path.chmod(0o600)
    provider = ScriptedProvider(language)
    with sandboxed(demo.home):
        config = load_config(demo.config_path)
        schedule = RunTrigger.SCHEDULE
        for days, items in ((-6, DAY_1), (-4, DAY_2), (-2, DAY_3), (-1, DAY_4)):
            when = at(days, 12, 30)
            arrive(demo, items, when)
            with clock(when):
                pipeline.run(
                    config, provider, simulate=False, trigger=schedule, now=when.timestamp()
                )
            if days == -4:
                # The slide deck went to Other; it belongs in Work.
                with clock(at(-3, 18, 10)), Store.open(config.database) as store:
                    row = next(r for r in store.all_files() if r.original_name == FIXED)
                    fix_file(config, store, Path(row.current_path), category=work(language))
            if days == -1:
                # The wallpaper was fine where it was: put it back.
                with clock(at(-1, 19, 45)), Store.open(config.database) as store:
                    row = next(r for r in store.all_files() if r.original_name == UNDONE)
                    undo_file(store, Path(row.current_path), config=config)
        # Today's downloads wait for a live `flib run`; their answers are cached now.
        now = datetime.now().astimezone()
        arrive(demo, TODAY, now - timedelta(minutes=30))
        pipeline.run(config, provider, simulate=True, now=now.timestamp())
    if with_mess:
        messy(demo)
    return demo


def category_name(key: str, language: Language) -> str:
    english, portuguese, *_ = CATEGORIES[key]
    return portuguese if language == "pt-BR" else english


def work(language: Language) -> str:
    return category_name("work", language)


def clean(root: Path, *, home: Path | None = None) -> bool:
    """Remove a demo library; ``False`` when there is nothing there. Only a folder
    holding the demo's marker is removed."""
    root = check_target(root, home if home is not None else Path.home())
    if not root.exists():
        return False
    if not (root / MARKER).is_file():
        raise DemoError(f"refusing {root}: it holds no demo library ({MARKER} is missing)")
    shutil.rmtree(root)
    return True


def flib_command() -> str:
    """The ``flib`` of this environment, which is the one to try the demo with."""
    beside = Path(sys.executable).with_name("flib")
    return str(beside) if beside.exists() else "flib"


def variables(demo: Demo) -> list[str]:
    """Shell lines that name the demo's home, its ``flib`` and its configuration."""
    return [
        f"DEMO_HOME={shlex.quote(str(demo.home))}",
        f"FLIB={shlex.quote(flib_command())}",
        'CFG="$DEMO_HOME/.config/file-librarian/config.yaml"',
    ]


def commands(demo: Demo) -> list[tuple[str, str]]:
    """(what it shows, the command) after ``variables``: every command runs with the
    demo's ``HOME`` and configuration, never yours."""
    query = "fatura" if demo.language == "pt-BR" else "invoice"
    flib = 'HOME="$DEMO_HOME" "$FLIB"'
    return [
        ("what waits for you", f'{flib} status -c "$CFG"'),
        ("what is due this week, and what is overdue", f'{flib} due -c "$CFG"'),
        ("search, then add up amounts per issuer", f'{flib} search {query} -c "$CFG"'),
        ("", f'{flib} search --kind bill,invoice --sum --by issuer -c "$CFG"'),
        ("today's three downloads: the plan, then the run", f'{flib} simulate -c "$CFG"'),
        ("", f'{flib} run -c "$CFG"'),
        ("the interface in your browser (Ctrl-C ends it)", f'{flib} ui -c "$CFG"'),
        ("remove the demo", f"uv run python -m scripts.demo {shlex.quote(str(demo.root))} --clean"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.demo",
        description="Build a demo flib library in a throwaway folder (offline, fictional).",
    )
    parser.add_argument("folder", type=Path, help="a new folder for it, e.g. /tmp/flib-demo")
    parser.add_argument("--pt-BR", dest="pt", action="store_true", help="Brazilian Portuguese")
    parser.add_argument(
        "--messy",
        action="store_true",
        help="also a cluttered before/Downloads to film; flib never reads it",
    )
    parser.add_argument("--clean", action="store_true", help="remove the demo in FOLDER")
    args = parser.parse_args(argv)
    try:
        if args.clean:
            removed = clean(args.folder)
            print("Removed." if removed else "Nothing there.")
            return 0
        demo = build(args.folder, "pt-BR" if args.pt else "en", with_mess=args.messy)
    except DemoError as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 2
    print(f"Demo library ({demo.language}) in {demo.root}")
    print("Paste these lines into a shell first; they change nothing outside the demo:\n")
    print("\n".join(variables(demo)))
    for what, command in commands(demo):
        print(f"\n# {what}" if what else "", command, sep="\n" if what else "")
    if args.messy:
        print("\n# the cluttered folder to film first (flib never reads it)")
        print(f"open {shlex.quote(str(demo.before))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
