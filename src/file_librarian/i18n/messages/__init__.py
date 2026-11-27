"""The catalogue: every message of the interface and the menu bar item, per language.

One catalogue, kept in one module per part of the interface so each reads on its
own: ``common`` (the page frame, refusals, words shared by several pages),
``pages`` (the packaged pages: Status, session ended, the handoff), ``status``
(what ``status.js`` writes), ``views`` (Activity, Search, Due), ``review``
(Review and undo), ``settings`` and ``menu`` (the menu bar item). Each defines
``EN`` and ``PT_BR``, message id to text (``str``) or ``Plural``. An id is used in
one module only (checked here, at import); the tests check that every id is in
both languages with the same placeholders, and that every id is used.

Glossary (pt-BR), kept the same everywhere: Status, Revisão, Busca, Atividade,
Vencimentos, Configurações; Organizar agora, Desfazer, Mostrar no Finder, Abrir;
run: execução; library: biblioteca; input folder: pasta de entrada; index:
índice; cost cap: limite de custo; due: vencimento; overdue: vencido; mark done:
marcar como concluído; open again: reabrir; sensitive: sensível; set aside
(duplicates): separado; AI: IA; End session: Encerrar sessão. "flib", command
names, folder and category names from the configuration are never translated.
"""

from __future__ import annotations

from types import ModuleType

from file_librarian.i18n import Entry, Lang
from file_librarian.i18n.messages import common as common
from file_librarian.i18n.messages import menu as menu
from file_librarian.i18n.messages import pages as pages
from file_librarian.i18n.messages import review as review
from file_librarian.i18n.messages import settings as settings
from file_librarian.i18n.messages import status as status
from file_librarian.i18n.messages import views as views

MODULES: tuple[ModuleType, ...] = (common, pages, status, views, review, settings, menu)


def _merge(name: str) -> dict[str, Entry]:
    merged: dict[str, Entry] = {}
    for module in MODULES:
        part: dict[str, Entry] = getattr(module, name)
        twice = merged.keys() & part.keys()
        if twice:
            raise RuntimeError(f"message ids in two modules: {sorted(twice)}")
        merged |= part
    return merged


CATALOGUE: dict[Lang, dict[str, Entry]] = {"en": _merge("EN"), "pt-BR": _merge("PT_BR")}
