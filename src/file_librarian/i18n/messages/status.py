"""What ``ui/static/status.js`` writes on the Status page; see ``i18n.messages``.

The page carries these (``SCRIPT_IDS``, in the language of the request) in a
``<meta>`` element, with how to show numbers, dates and plurals
(``formats.for_scripts``): the script fills in placeholders and picks plural
forms as the server does, and puts text in with ``textContent`` only.
"""

from __future__ import annotations

from file_librarian.i18n import Entry, Lang, Plural, entry

EN: dict[str, Entry] = {
    "js.waiting": Plural(one="{count} file waits for you.", other="{count} files wait for you."),
    "js.nothing_waits": "Nothing waits for you.",
    "js.nothing_to_review": "Nothing to review.",
    "js.oldest_today": "The oldest since today.",
    "js.oldest_days": Plural(
        one="The oldest for {count} day.", other="The oldest for {count} days."
    ),
    "js.reason_line": "{reason}: {parts}",
    "js.suggested_count": "{suggested} {count}",
    "js.no_suggestion": "no suggestion {count}",
    "js.failed_retried": "{count}: {error} (tried again next run)",
    "js.failed_given_up": "{count}: {error} (given up)",
    "js.due_note": "{overdue} overdue, {upcoming} in the next {days}.",
    "js.days": Plural(one="{count} day", other="{count} days"),
    "js.due_after_run": "After the next run.",
    "js.sensitive_note": Plural(
        one="Sensitive file, never moved or sent anywhere.",
        other="Sensitive files, never moved or sent anywhere.",
    ),
    "js.last_run": "{when}, {trigger}: {organized}, {review}, {failed}; {cost}",
    "js.none_yet": "None yet",
    "js.trigger.schedule": "daily run",
    "js.trigger.manual": "by hand",
    "js.trigger.mcp": "from an assistant",
    "js.trigger.watch": "on arrival",
    "js.trigger.reprocess": "reprocess",
    "js.not_scheduled": "None scheduled (`flib schedule` sets one up)",
    "js.watching": "As files arrive (watch mode)",
    "js.watch_not_loaded": "Watch mode installed but not loaded",
    "js.next_and_watch": "{when}, and as files arrive",
    "js.not_loaded": "Installed but not loaded (run `flib schedule` again)",
    "js.today": "Automatic runs: {cost}",
    "js.today_cap": "Automatic runs: {cost} (daily cap {cap})",
    "js.today_cap_reached": "Automatic runs: {cost} (daily cap {cap}), cap reached",
    "js.month_unpriced": "{cost} (plus {runs} without a cost)",
    "js.runs": Plural(one="{count} run", other="{count} runs"),
    "js.unreadable": "Could not read the status: {error}",
}

PT_BR: dict[str, Entry] = {
    "js.waiting": Plural(
        one="{count} arquivo espera por você.", other="{count} arquivos esperam por você."
    ),
    "js.nothing_waits": "Nada espera por você.",
    "js.nothing_to_review": "Nada para revisar.",
    "js.oldest_today": "O mais antigo chegou hoje.",
    "js.oldest_days": Plural(
        one="O mais antigo espera há {count} dia.", other="O mais antigo espera há {count} dias."
    ),
    "js.reason_line": "{reason}: {parts}",
    "js.suggested_count": "{suggested} {count}",
    "js.no_suggestion": "sem sugestão {count}",
    "js.failed_retried": "{count}: {error} (nova tentativa na próxima execução)",
    "js.failed_given_up": "{count}: {error} (sem nova tentativa)",
    "js.due_note": "Vencidos: {overdue}; a vencer nos próximos {days}: {upcoming}.",
    "js.days": Plural(one="{count} dia", other="{count} dias"),
    "js.due_after_run": "Depois da próxima execução.",
    "js.sensitive_note": Plural(
        one="Arquivo sensível, nunca movido nem enviado a lugar nenhum.",
        other="Arquivos sensíveis, nunca movidos nem enviados a lugar nenhum.",
    ),
    "js.last_run": "{when}, {trigger}: {organized}, {review}, {failed}; {cost}",
    "js.none_yet": "Nenhuma ainda",
    "js.trigger.schedule": "execução diária",
    "js.trigger.manual": "à mão",
    "js.trigger.mcp": "por um assistente",
    "js.trigger.watch": "na chegada",
    "js.trigger.reprocess": "reprocessamento",
    "js.not_scheduled": "Nenhuma agendada (`flib schedule` agenda uma)",
    "js.watching": "Quando chegarem arquivos (modo de observação)",
    "js.watch_not_loaded": "Modo de observação instalado, mas não carregado",
    "js.next_and_watch": "{when}, e quando chegarem arquivos",
    "js.not_loaded": "Instalado, mas não carregado (rode `flib schedule` de novo)",
    "js.today": "Execuções automáticas: {cost}",
    "js.today_cap": "Execuções automáticas: {cost} (limite diário de {cap})",
    "js.today_cap_reached": (
        "Execuções automáticas: {cost} (limite diário de {cap}), limite atingido"
    ),
    "js.month_unpriced": "{cost} (mais {runs} sem custo registrado)",
    "js.runs": Plural(one="{count} execução", other="{count} execuções"),
    "js.unreadable": "Não foi possível ler o status: {error}",
}

SCRIPT_IDS: tuple[str, ...] = (
    *EN,
    "common.no_index",
    "common.no_cost",
    "common.no_cost_recorded",
    "review.reason.low_confidence",
    "review.reason.unknown_category",
    "review.reason.category_removed",
    "review.reason.moved_by_hand",
    "review.reason.none",
    "activity.organized",
    "activity.to_review",
    "activity.failed",
)
"""Every message ``status.js`` uses, its own and a few shared with other pages."""


def bundle(lang: Lang) -> dict[str, object]:
    """What the Status page hands its script: the messages in ``lang`` and the formats."""
    from file_librarian.i18n import formats

    messages: dict[str, object] = {}
    for msg_id in SCRIPT_IDS:
        found = entry(lang, msg_id)
        messages[msg_id] = (
            found
            if isinstance(found, str)
            else {"one": found.one, "other": found.other, "by": found.by}
        )
    return {"formats": formats.for_scripts(lang), "m": messages}
