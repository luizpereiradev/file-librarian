"""The page frame, refusals and words several pages share; see ``i18n.messages``."""

from __future__ import annotations

from file_librarian.i18n import Entry

EN: dict[str, Entry] = {
    # the frame (ui.markup)
    "nav.label": "Pages",
    "nav.status": "Status",
    "nav.review": "Review",
    "nav.search": "Search",
    "nav.activity": "Activity",
    "nav.due": "Due",
    "nav.settings": "Settings",
    "frame.skip": "Skip to content",
    "frame.end_session": "End session",
    "frame.footer": (
        "Served on this Mac only (loopback), while `flib ui` runs. Names and descriptions come "
        "from your files and the AI: they are shown as text only."
    ),
    # refusals as pages (ui.server, ui.views.problem_page)
    "problem.title.400": "Bad Request",
    "problem.title.403": "Forbidden",
    "problem.title.404": "Not Found",
    "problem.title.502": "Bad Gateway",
    "problem.title.503": "Service Unavailable",
    "problem.title.other": "Error {code}",
    "problem.bad_request": (
        "This address or form asks for something the page does not offer. Go back, or open "
        "the page from the menu."
    ),
    "problem.not_found": "There is no such page here. Use the menu above.",
    "problem.stale_form": (
        "This form is from an earlier page or an earlier flib ui session. Reload the page and "
        "try again."
    ),
    "problem.stale_session": (
        "This page is from an earlier flib ui session, or that session has ended. Run "
        "`flib ui` in Terminal for a new one."
    ),
    # shared by several pages
    "common.open": "Open",
    "common.reveal": "Show in Finder",
    "common.open_named": "Open: {name}",
    "common.reveal_named": "Show in Finder: {name}",
    "common.cancel": "Cancel",
    "common.back_to_review": "Back to Review",
    "common.missing": "missing: moved by hand?",
    "common.in_review": "in review",
    "common.no_index": "No run yet: the index is created by the first `flib run`.",
    "common.unreadable": "Cannot read the index",
    "common.no_cost": "no cost",
    "common.no_cost_recorded": "no cost recorded",
    "common.busy": (
        "A flib run, or another change, is going on now. Nothing was done; try again when it "
        "finishes."
    ),
    # kinds of document (models.Kind): the code is the value, this its label
    "kind.bill": "bill",
    "kind.receipt": "receipt",
    "kind.invoice": "invoice",
    "kind.statement": "statement",
    "kind.contract": "contract",
    "kind.payslip": "payslip",
    "kind.tax": "tax",
    "kind.ticket": "ticket",
    "kind.policy": "policy",
    "kind.report": "report",
    "kind.form": "form",
    "kind.letter": "letter",
    "kind.manual": "manual",
    "kind.other": "other",
}

PT_BR: dict[str, Entry] = {
    "nav.label": "Páginas",
    "nav.status": "Status",
    "nav.review": "Revisão",
    "nav.search": "Busca",
    "nav.activity": "Atividade",
    "nav.due": "Vencimentos",
    "nav.settings": "Configurações",
    "frame.skip": "Pular para o conteúdo",
    "frame.end_session": "Encerrar sessão",
    "frame.footer": (
        "Servido só por este Mac (loopback), enquanto o `flib ui` estiver rodando. Nomes e "
        "descrições vêm dos seus arquivos e da IA: aparecem aqui só como texto."
    ),
    "problem.title.400": "Pedido inválido",
    "problem.title.403": "Acesso negado",
    "problem.title.404": "Página não encontrada",
    "problem.title.502": "Não foi possível abrir",
    "problem.title.503": "Indisponível no momento",
    "problem.title.other": "Erro {code}",
    "problem.bad_request": (
        "Este endereço ou formulário pede algo que a página não oferece. Volte, ou abra a "
        "página pelo menu."
    ),
    "problem.not_found": "Esta página não existe. Use o menu acima.",
    "problem.stale_form": (
        "Este formulário é de uma página anterior ou de uma sessão anterior do flib ui. "
        "Recarregue a página e tente de novo."
    ),
    "problem.stale_session": (
        "Esta página é de uma sessão anterior do flib ui, ou essa sessão terminou. Rode "
        "`flib ui` no Terminal para começar outra."
    ),
    "common.open": "Abrir",
    "common.reveal": "Mostrar no Finder",
    "common.open_named": "Abrir: {name}",
    "common.reveal_named": "Mostrar no Finder: {name}",
    "common.cancel": "Cancelar",
    "common.back_to_review": "Voltar para a Revisão",
    "common.missing": "sumiu: movido à mão?",
    "common.in_review": "em revisão",
    "common.no_index": (
        "Nenhuma execução ainda: o índice é criado pela primeira execução de `flib run`."
    ),
    "common.unreadable": "Não foi possível ler o índice",
    "common.no_cost": "sem custo",
    "common.no_cost_recorded": "custo não registrado",
    "common.busy": (
        "Uma execução do flib, ou outra alteração, está em andamento. Nada foi feito; tente "
        "de novo quando ela terminar."
    ),
    # kinds of document (models.Kind): the code is the value, this its label
    "kind.bill": "conta",
    "kind.receipt": "recibo",
    "kind.invoice": "fatura",
    "kind.statement": "extrato",
    "kind.contract": "contrato",
    "kind.payslip": "holerite",
    "kind.tax": "imposto",
    "kind.ticket": "bilhete",
    "kind.policy": "apólice",
    "kind.report": "relatório",
    "kind.form": "formulário",
    "kind.letter": "carta",
    "kind.manual": "manual",
    "kind.other": "outro",
}
