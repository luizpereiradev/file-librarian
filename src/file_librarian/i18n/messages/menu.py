"""The menu bar item (``menubar``, ``menubar_state``); see ``i18n.messages``.

The menu's fixed entries are put in the applet when it is built (``menubar.script``),
in the configuration's language; a change of language rebuilds it (the Settings
page asks, and ``flib menu --refresh`` / ``flib schedule --refresh`` find it out of
date). The state line and the next-run line are written by flib in ``state.json``
in the language of the moment. Counts only, never a file name.
"""

from __future__ import annotations

from file_librarian.i18n import Entry, Plural

EN: dict[str, Entry] = {
    # the applet's fixed entries
    "menu.open_status": "Open flib",
    "menu.review": "Review",
    "menu.due": "Due",
    "menu.organize": "Organize now",
    "menu.settings": "Settings…",
    "menu.quit": "Quit",
    "menu.default_line": "No word from flib yet",
    "menu.stale_line": "The last run did not finish (`flib status`)",
    # state.json's line and next
    "menu.organizing": "Organizing…",
    "menu.unreadable": "Cannot read flib's index (`flib doctor`)",
    "menu.problem.run_failed": "flib could not run (`flib doctor` says why)",
    "menu.problem.no_access": "macOS denied a folder (`flib doctor`)",
    "menu.to_review": Plural(one="{count} file to review", other="{count} files to review"),
    "menu.failed": Plural(one="{count} failed", other="{count} failed"),
    "menu.overdue": Plural(one="{count} overdue", other="{count} overdue"),
    "menu.due_soon": Plural(one="{count} due soon", other="{count} due soon"),
    "menu.nothing": "Nothing pending",
    "menu.not_scheduled": "Not scheduled",
    "menu.not_active": "Schedule not active (`flib schedule`)",
    "menu.on_arrival": "Runs as files arrive",
    "menu.next_and_watch": "Next run {when}, and as files arrive",
    "menu.next": "Next run {when}",
}

PT_BR: dict[str, Entry] = {
    "menu.open_status": "Abrir o flib",
    "menu.review": "Revisão",
    "menu.due": "Vencimentos",
    "menu.organize": "Organizar agora",
    "menu.settings": "Configurações…",
    "menu.quit": "Sair",
    "menu.default_line": "Ainda sem notícias do flib",
    "menu.stale_line": "A última execução não terminou (`flib status`)",
    "menu.organizing": "Organizando…",
    "menu.unreadable": "Não foi possível ler o índice do flib (`flib doctor`)",
    "menu.problem.run_failed": "O flib não conseguiu rodar (`flib doctor` diz por quê)",
    "menu.problem.no_access": "O macOS negou acesso a uma pasta (`flib doctor`)",
    "menu.to_review": Plural(
        one="{count} arquivo para revisar", other="{count} arquivos para revisar"
    ),
    "menu.failed": Plural(one="{count} com falha", other="{count} com falha"),
    "menu.overdue": Plural(one="{count} vencido", other="{count} vencidos"),
    "menu.due_soon": Plural(one="{count} vence em breve", other="{count} vencem em breve"),
    "menu.nothing": "Nada pendente",
    "menu.not_scheduled": "Sem agendamento",
    "menu.not_active": "Agendamento inativo (`flib schedule`)",
    "menu.on_arrival": "Roda quando chegam arquivos",
    "menu.next_and_watch": "Próxima execução {when}, e quando chegarem arquivos",
    "menu.next": "Próxima execução {when}",
}
