"""The packaged pages (``ui/pages/``: Status's frame, session ended, ended after a
theft, not started yet, open elsewhere, opening) and the handoff file
(``ui.server.HANDOFF``); see ``i18n.messages``."""

from __future__ import annotations

from file_librarian.i18n import Entry

EN: dict[str, Entry] = {
    # ended.html
    "ended.title": "Session ended",
    "ended.heading": "This session has ended",
    "ended.lead": (
        "Each link from `flib ui` works once, and the interface closes when it is not in use. "
        "You can close this tab."
    ),
    "ended.again": "For a new session, run `flib ui` in Terminal.",
    # stolen.html: the launch link came back from a client without the session (#127)
    "stolen.title": "Session ended for safety",
    "stolen.heading": "This session was ended because its link was opened twice",
    "stolen.lead": (
        "The link `flib ui` opened works once. It was used again, by a browser or program "
        "that did not have the session, so someone else on this Mac may have seen it: "
        "flib ui dropped the session and is closing. Nothing was moved or changed."
    ),
    "stolen.again": (
        "Run `flib ui` in Terminal for a new session. If you opened the link again yourself "
        "(in another browser, or a copy of it), that was all; if not, check what else runs "
        "on this Mac."
    ),
    # waiting.html: reached before the launch link was opened (#127)
    "waiting.title": "Not opened yet",
    "waiting.heading": "This session has not started yet",
    "waiting.lead": (
        "flib ui is waiting for its link to be opened: the page it opened in your browser "
        "when it started, or the link it printed in Terminal (`flib ui --no-browser`)."
    ),
    "waiting.again": (
        "Use that page or link; it works once. If it is gone, press Ctrl-C where `flib ui` "
        "runs and run it again."
    ),
    # elsewhere.html
    "elsewhere.title": "Open in another window",
    "elsewhere.heading": "flib ui is open in another window",
    "elsewhere.lead": (
        "The session that `flib ui` started belongs to the browser or window that opened its "
        "link first; this one does not have it."
    ),
    "elsewhere.move": (
        "Use that window. To move here, choose End session there (or press Ctrl-C where "
        "`flib ui` runs), then run `flib ui` again."
    ),
    # opening.html
    "opening.title": "Opening",
    "opening.heading": "Session started",
    "opening.continue": "Continue to file-librarian",
    "open.heading": "Opening file-librarian",
    # the handoff file
    "handoff.title": "Opening file-librarian",
    "handoff.opening": "Opening file-librarian…",
    "handoff.no_script": (
        "This page needs JavaScript. Run `flib ui --no-browser` for a link instead."
    ),
    "handoff.done": "file-librarian opened in this browser. You can close this tab.",
    # status.html
    "status.title": "Status",
    "status.loading": "Loading…",
    "status.no_script": (
        "This page shows its counts with JavaScript. `flib status` in Terminal shows the same."
    ),
    "status.review": "Review",
    "status.go_review": "Go through them",
    "status.failed": "Failed",
    "status.due": "Due",
    "status.left": "Left in place",
    "status.sensitive_note": "Sensitive files, never moved or sent anywhere.",
    "status.duplicates": "Duplicates set aside",
    "status.nothing_deleted": "Nothing is deleted.",
    "status.runs": "Runs",
    "status.last_run": "Last run",
    "status.next_run": "Next run",
    "status.today": "Today",
    "status.month": "This month",
    "status.none_yet": "none yet",
    "status.none_scheduled": "none scheduled",
    "status.refresh": "Refresh",
    "status.footer": (
        "Counts only: this page shows no file names. It is served on this Mac only "
        "(loopback), while `flib ui` runs."
    ),
}

PT_BR: dict[str, Entry] = {
    "ended.title": "Sessão encerrada",
    "ended.heading": "Esta sessão terminou",
    "ended.lead": (
        "Cada link do `flib ui` funciona uma vez só, e a interface fecha quando não está em "
        "uso. Você já pode fechar esta aba."
    ),
    "ended.again": "Para uma nova sessão, rode `flib ui` no Terminal.",
    "stolen.title": "Sessão encerrada por segurança",
    "stolen.heading": "Esta sessão foi encerrada porque o link dela foi aberto duas vezes",
    "stolen.lead": (
        "O link que o `flib ui` abriu funciona uma vez só. Ele foi usado de novo, por um "
        "navegador ou programa que não tinha a sessão; então outra pessoa ou programa neste Mac "
        "pode ter visto o link, e o flib ui encerrou a sessão e está fechando. Nada foi movido "
        "nem alterado."
    ),
    "stolen.again": (
        "Rode `flib ui` no Terminal para uma nova sessão. Se foi você quem abriu o link de "
        "novo (em outro navegador, ou uma cópia dele), foi só isso; se não, verifique o que "
        "mais está rodando neste Mac."
    ),
    "waiting.title": "Ainda não aberto",
    "waiting.heading": "Esta sessão ainda não começou",
    "waiting.lead": (
        "O flib ui está esperando que o link dele seja aberto: a página que ele abriu no seu "
        "navegador ao iniciar, ou o link que mostrou no Terminal (`flib ui --no-browser`)."
    ),
    "waiting.again": (
        "Use essa página ou esse link; ele funciona uma vez só. Se não estiver mais lá, "
        "pressione Ctrl-C onde o `flib ui` está rodando e rode de novo."
    ),
    "elsewhere.title": "Aberto em outra janela",
    "elsewhere.heading": "O flib ui está aberto em outra janela",
    "elsewhere.lead": (
        "A sessão que o `flib ui` iniciou pertence ao navegador ou à janela que abriu o link "
        "primeiro; esta aqui não tem a sessão."
    ),
    "elsewhere.move": (
        "Use aquela janela. Para trazer a sessão para cá, escolha Encerrar sessão lá (ou "
        "pressione Ctrl-C onde o `flib ui` está rodando) e rode `flib ui` de novo."
    ),
    "opening.title": "Abrindo",
    "opening.heading": "Sessão iniciada",
    "opening.continue": "Continuar para o file-librarian",
    "open.heading": "Abrindo o file-librarian",
    "handoff.title": "Abrindo o file-librarian",
    "handoff.opening": "Abrindo o file-librarian…",
    "handoff.no_script": (
        "Esta página precisa de JavaScript. Rode `flib ui --no-browser` para receber um link."
    ),
    "handoff.done": "O file-librarian abriu neste navegador. Você já pode fechar esta aba.",
    "status.title": "Status",
    "status.loading": "Carregando…",
    "status.no_script": (
        "Esta página mostra as contagens com JavaScript. `flib status` no Terminal mostra o mesmo."
    ),
    "status.review": "Revisão",
    "status.go_review": "Revisar agora",
    "status.failed": "Com falha",
    "status.due": "Vencimentos",
    "status.left": "Deixados no lugar",
    "status.sensitive_note": "Arquivos sensíveis, nunca movidos nem enviados a lugar nenhum.",
    "status.duplicates": "Duplicados separados",
    "status.nothing_deleted": "Nada é apagado.",
    "status.runs": "Execuções",
    "status.last_run": "Última execução",
    "status.next_run": "Próxima execução",
    "status.today": "Hoje",
    "status.month": "Este mês",
    "status.none_yet": "nenhuma ainda",
    "status.none_scheduled": "nenhuma agendada",
    "status.refresh": "Atualizar",
    "status.footer": (
        "Só contagens: esta página não mostra nomes de arquivos. Ela é servida só por este Mac "
        "(loopback), enquanto o `flib ui` estiver rodando."
    ),
}
