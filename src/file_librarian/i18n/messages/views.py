"""Activity, Search and Due (``ui/views.py``), and flib's core texts these pages show
(``i18n.core``); see ``file_librarian.i18n.messages``."""

from __future__ import annotations

from file_librarian.i18n import Entry, Plural

EN: dict[str, Entry] = {
    # shared by these pages
    "views.window": "Window",
    "views.total": "Total",
    "views.files": "Files",
    "views.was": "was {name}",
    "views.more": "and {count} more",
    # Activity
    "activity.days": Plural(one="{count} day", other="{count} days"),
    "activity.lead": "What each run did, newest first.",
    "activity.none": Plural(
        one="No run in the last {count} day.", other="No run in the last {count} days."
    ),
    "activity.capped": "Only the newest {count} runs are shown; choose fewer days.",
    "activity.day_runs": Plural(one="{count} run, {cost}", other="{count} runs, {cost}"),
    "activity.day_runs_unpriced": Plural(
        one="{count} run, {cost} (and {unpriced} without a cost)",
        other="{count} runs, {cost} (and {unpriced} without a cost)",
    ),
    "activity.runs_without_cost": Plural(one="{count} run", other="{count} runs"),
    "activity.trigger.schedule": "daily run",
    "activity.trigger.manual": "by hand",
    "activity.trigger.mcp": "from an assistant",
    "activity.trigger.reprocess": "reprocess",
    "activity.trigger.watch": "on arrival (watch)",
    "activity.run_counts": "{organized}, {review}, {duplicates}, {failed}; {cost}.",
    "activity.organized": Plural(one="{count} organized", other="{count} organized"),
    "activity.to_review": Plural(one="{count} to review", other="{count} to review"),
    "activity.duplicates": Plural(
        one="{count} duplicate set aside", other="{count} duplicates set aside"
    ),
    "activity.failed": Plural(one="{count} failed", other="{count} failed"),
    "activity.unfinished": "It did not finish (still running, or stopped).",
    "activity.sensitive": Plural(
        one="{count} sensitive file left in place, unread (not listed here).",
        other="{count} sensitive files left in place, unread (not listed here).",
    ),
    "activity.settled": Plural(
        one="{count} document marked done or open again.",
        other="{count} documents marked done or open again.",
    ),
    "activity.index_changes": Plural(
        one="{count} change to the index.", other="{count} changes to the index."
    ),
    "activity.undo_run": "Undo this run…",
    "activity.undo_run_named": "Undo run {run}",
    "activity.run_number": "run {run}",
    "activity.catch_up": "Run after the Mac was unlocked.",
    "activity.locked": Plural(
        one="{count} file protected while the Mac is locked skipped.",
        other="{count} files protected while the Mac is locked skipped.",
    ),
    "activity.locked_waited": Plural(
        one="{count} file protected while the Mac is locked skipped; {waited}.",
        other="{count} files protected while the Mac is locked skipped; {waited}.",
    ),
    "activity.waited.waiting": "it waited for the Mac to be unlocked",
    "activity.waited.unlocked": "it waited for the unlock, then ran once more",
    "activity.waited.timed_out": "it waited for the unlock in vain; the next run gets them",
    "activity.waited.stopped": "its wait for the unlock was stopped; the next run gets them",
    "activity.waited.elsewhere": "an earlier run was already waiting for the unlock",
    "activity.fixed_by_hand": "fixed by hand",
    "activity.undone": "undone",
    "activity.op.planned": "planned",
    "activity.op.failed": "failed",
    "activity.op.skipped": "skipped",
    "activity.undo_move": "Undo…",
    "activity.undo_move_named": "Undo: {name}",
    "activity.to": "to ",
    # Search
    "search.lead": (
        "Words in a name, a description or a tag, and filters. Accents and case are ignored."
    ),
    "search.nothing": "Give words to search for, a filter, or both.",
    "search.gone": (
        "That search is no longer kept (only the latest ones are, until the session ends). "
        "Search again."
    ),
    "search.title_matches": Plural(one="Search: {count} match", other="Search: {count} matches"),
    "search.matches": Plural(one="{count} match", other="{count} matches"),
    "search.matches_capped": Plural(
        one="{count} match (the most asked for)", other="{count} matches (the most asked for)"
    ),
    "search.more_filters": "More filters",
    "search.label.ext": "Extension",
    "search.label.since": "Processed from",
    "search.label.until": "Processed until",
    "search.label.period": "Period",
    "search.label.period_field": "Period (year or month)",
    "search.label.issuer": "Issuer",
    "search.label.min_amount": "At least",
    "search.label.max_amount": "At most",
    "search.label.currency": "Currency",
    "search.label.due_after": "Due after",
    "search.label.due_before": "Due before",
    "search.label.by": "Totals by",
    "search.label.limit": "Most results",
    "search.label.duplicates": "Also duplicates set aside",
    "search.label.semantic": "Also by meaning (semantic search)",
    "search.label.category": "Category",
    "search.label.kind": "Kind of document",
    "search.by_currency_only": "currency only",
    "search.group.issuer": "issuer",
    "search.group.month": "month",
    "search.group.year": "year",
    "search.group.kind": "kind",
    "search.group.category": "category",
    "search.heading.issuer": "Issuer",
    "search.heading.month": "Month",
    "search.heading.year": "Year",
    "search.heading.kind": "Kind",
    "search.heading.category": "Category",
    "search.words": "Words",
    "search.placeholder": "What you remember about the file",
    "search.button": "Search",
    "search.export": "Export CSV",
    "search.filters": "Filters",
    "search.filters_used": "Filters ({count} in use)",
    "search.sensitive": Plural(
        one=(
            "{count} sensitive file also matched; they are never shown here (`flib search` in "
            "Terminal lists them)."
        ),
        other=(
            "{count} sensitive files also matched; they are never shown here (`flib search` in "
            "Terminal lists them)."
        ),
    ),
    "search.no_close": "No close matches; try other words, or search by words only.",
    "search.no_matches": "No matches.",
    "search.matches_label": "Matches",
    "search.results_label": "Results",
    "search.not_filed": "not filed: back in {folder}",
    "search.state.skipped": "skipped",
    "search.state.missing": "missing",
    "search.state.failed": "failed",
    "search.state.sensitive": "sensitive",
    "search.duplicate_of": "duplicate of {path}",
    "search.field.due": "due {date}",
    "search.field.period": "for {period}",
    "search.field.dated": "dated {date}",
    "search.tags_label": "Tags",
    "search.totals": "Totals",
    "search.totals_note": "Over every match by words (never duplicates), not only those listed.",
    "search.totals_note_semantic": (
        "Over every match by words (never duplicates), not only those listed. Totals match the "
        "words only, not by meaning."
    ),
    "search.no_amounts": "No amounts among the matches.",
    "search.no_group": "(none)",
    "search.by_caption": "By {group}",
    "search.per_currency": "Per currency",
    "search.without_amount": Plural(
        one="{count} matching file states no amount; not counted.",
        other="{count} matching files state no amount; not counted.",
    ),
    "search.csv_cut": (
        "# cut: more than {count} matches; only the first {count} are here. Narrow the search."
    ),
    # Due
    "due.within.7d": "7 days",
    "due.within.2w": "2 weeks",
    "due.within.1m": "1 month",
    "due.within.3m": "3 months",
    "due.overdue_chip": "Overdue",
    "due.hide_done": "Hide those marked done",
    "due.show_all": "Also those marked done, and other kinds",
    "due.lead": (
        "Bills and other documents with a due date the AI read in them; nothing is guessed."
    ),
    "due.span_overdue": "Past due, however long ago (today is {today}).",
    "due.span": Plural(
        one="Overdue since {start} ({count} day back) and due up to {end}; today is {today}.",
        other="Overdue since {start} ({count} days back) and due up to {end}; today is {today}.",
    ),
    "due.nothing": "Nothing due in this window.",
    "due.totals": "Total open, per currency",
    "due.without_amount": Plural(
        one="{count} document states no amount; not counted.",
        other="{count} documents state no amount; not counted.",
    ),
    "due.hidden": Plural(
        one="{count} document marked done or of another kind not shown.",
        other="{count} documents marked done or of another kind not shown.",
    ),
    "due.reopen": "Open again",
    "due.mark_done": "Mark done",
    "due.reopen_named": "Open again: {name}",
    "due.mark_done_named": "Mark done: {name}",
    "due.done": "done",
    "due.done_tag": "done (Finder tag)",
    "due.overdue": "overdue",
    "due.col.due": "Due",
    "due.col.when": "When",
    "due.col.amount": "Amount",
    "due.col.issuer": "Issuer",
    "due.col.kind": "Kind",
    "due.col.file": "File",
    "due.col.actions": "Actions",
    "due.today": "today",
    "due.tomorrow": "tomorrow",
    "due.yesterday": "yesterday",
    "due.in_days": Plural(one="in {count} day", other="in {count} days"),
    "due.days_ago": Plural(one="{count} day ago", other="{count} days ago"),
    # Open and Show in Finder
    "open.no_file": "No file was named.",
    "open.not_indexed": "That file is not in the index.",
    "open.not_openable": "That file is not one the interface opens.",
    "open.outside": "That file is not in the library or an input folder.",
    "open.gone": "That file is no longer where the index says.",
    "open.not_a_document": (
        "Only documents are opened from here; this one could run a program or go somewhere. "
        "Use Show in Finder instead."
    ),
    "open.failed": "macOS could not open it.",
    # flib's core (i18n.core): search's filter errors, named as the form names its fields
    "core.search.unknown_category": "Unknown category {value} (known: {known})",
    "core.search.bad_extension": "Bad extension {value}: give it like pdf or .pdf",
    "core.search.since_after_until": "Processed from {since} is after Processed until {until}",
    "core.search.issuer": "Issuer {value} names no organisation",
    "core.search.unknown_kind": "Unknown kind {value} (kinds: {kinds})",
    "core.search.min_over_max": "At least {low} is more than At most {high}",
    "core.search.currency": "Currency {value} is not a currency code like USD or BRL",
    "core.search.period": "Period {value} is not a year or month like 2026 or 2026-09",
    "core.search.amount": "{field} {value} is not an amount like 100 or 99.90",
    "core.search.date_form": "{field} {value} is not a date like 2026-01-31",
    "core.search.date_invalid": "{field} {value} is not a valid date",
    "core.search.outdated": Plural(
        one=(
            "{count} file has no up-to-date embedding yet and may be found by words only; the "
            "next run or `flib search --reindex` embeds it"
        ),
        other=(
            "{count} files have no up-to-date embedding yet and may be found by words only; "
            "the next run or `flib search --reindex` embeds them"
        ),
    ),
    "core.search.semantic_off": (
        "Semantic search is off (search.semantic.enabled: false); searched by words only"
    ),
    "core.search.no_embeddings": (
        "No file has an embedding from {model} yet; run `flib search --reindex`. Searched by "
        "words only"
    ),
    "core.search.semantic_unavailable": (
        "Semantic search unavailable ({why}); searched by words only"
    ),
    "core.index.older": "The index is from an older flib; the next `flib run` updates it",
    "core.index.newer": (
        "The index (schema {schema}) is newer than this flib; upgrade file-librarian"
    ),
}

PT_BR: dict[str, Entry] = {
    "views.window": "Período",
    "views.total": "Total",
    "views.files": "Arquivos",
    "views.was": "antes: {name}",
    "views.more": "e mais {count}",
    # Atividade
    "activity.days": Plural(one="{count} dia", other="{count} dias"),
    "activity.lead": "O que cada execução fez, da mais recente para a mais antiga.",
    "activity.none": Plural(
        one="Nenhuma execução no último {count} dia.",
        other="Nenhuma execução nos últimos {count} dias.",
    ),
    "activity.capped": (
        "Só as {count} execuções mais recentes aparecem; escolha um período menor."
    ),
    "activity.day_runs": Plural(one="{count} execução, {cost}", other="{count} execuções, {cost}"),
    "activity.day_runs_unpriced": Plural(
        one="{count} execução, {cost} (e {unpriced} sem custo registrado)",
        other="{count} execuções, {cost} (e {unpriced} sem custo registrado)",
    ),
    "activity.runs_without_cost": Plural(one="{count} execução", other="{count} execuções"),
    "activity.trigger.schedule": "execução diária",
    "activity.trigger.manual": "à mão",
    "activity.trigger.mcp": "por um assistente",
    "activity.trigger.reprocess": "reclassificação",
    "activity.trigger.watch": "na chegada (observação)",
    "activity.run_counts": "{organized}, {review}, {duplicates}, {failed}; {cost}.",
    "activity.organized": Plural(one="{count} organizado", other="{count} organizados"),
    "activity.to_review": Plural(one="{count} para revisar", other="{count} para revisar"),
    "activity.duplicates": Plural(
        one="{count} duplicado separado", other="{count} duplicados separados"
    ),
    "activity.failed": Plural(one="{count} com falha", other="{count} com falha"),
    "activity.unfinished": "Ela não terminou (ainda em andamento, ou interrompida).",
    "activity.sensitive": Plural(
        one="{count} arquivo sensível ficou onde estava, sem ser lido (não aparece aqui).",
        other="{count} arquivos sensíveis ficaram onde estavam, sem ser lidos (não aparecem aqui).",
    ),
    "activity.settled": Plural(
        one="{count} documento marcado como concluído ou reaberto.",
        other="{count} documentos marcados como concluídos ou reabertos.",
    ),
    "activity.index_changes": Plural(
        one="{count} alteração no índice.", other="{count} alterações no índice."
    ),
    "activity.undo_run": "Desfazer esta execução…",
    "activity.undo_run_named": "Desfazer a execução {run}",
    "activity.run_number": "execução {run}",
    "activity.catch_up": "Execução feita depois que o Mac foi desbloqueado.",
    "activity.locked": Plural(
        one="{count} arquivo protegido enquanto o Mac está bloqueado foi pulado.",
        other="{count} arquivos protegidos enquanto o Mac está bloqueado foram pulados.",
    ),
    "activity.locked_waited": Plural(
        one="{count} arquivo protegido enquanto o Mac está bloqueado foi pulado; {waited}.",
        other="{count} arquivos protegidos enquanto o Mac está bloqueado foram pulados; {waited}.",
    ),
    "activity.waited.waiting": "ela esperou o Mac ser desbloqueado",
    "activity.waited.unlocked": "ela esperou o desbloqueio e rodou mais uma vez",
    "activity.waited.timed_out": (
        "ela esperou o desbloqueio, sem sucesso; a próxima execução cuida deles"
    ),
    "activity.waited.stopped": (
        "a espera pelo desbloqueio foi interrompida; a próxima execução cuida deles"
    ),
    "activity.waited.elsewhere": "uma execução anterior já esperava o desbloqueio",
    "activity.fixed_by_hand": "corrigido à mão",
    "activity.undone": "desfeito",
    "activity.op.planned": "planejado",
    "activity.op.failed": "com falha",
    "activity.op.skipped": "pulado",
    "activity.undo_move": "Desfazer…",
    "activity.undo_move_named": "Desfazer: {name}",
    "activity.to": "para ",
    # Busca
    "search.lead": (
        "Palavras do nome, da descrição ou de uma tag, e filtros. Acentos e maiúsculas não "
        "fazem diferença."
    ),
    "search.nothing": "Digite palavras para buscar, escolha um filtro, ou os dois.",
    "search.gone": (
        "Essa busca não está mais guardada (só as mais recentes ficam, até a sessão terminar). "
        "Busque de novo."
    ),
    "search.title_matches": Plural(
        one="Busca: {count} resultado", other="Busca: {count} resultados"
    ),
    "search.matches": Plural(one="{count} resultado", other="{count} resultados"),
    "search.matches_capped": Plural(
        one="{count} resultado (o máximo pedido)", other="{count} resultados (o máximo pedido)"
    ),
    "search.more_filters": "Mais filtros",
    "search.label.ext": "Extensão",
    "search.label.since": "Processado a partir de",
    "search.label.until": "Processado até",
    "search.label.period": "Período",
    "search.label.period_field": "Período (ano ou mês)",
    "search.label.issuer": "Emissor",
    "search.label.min_amount": "Valor mínimo",
    "search.label.max_amount": "Valor máximo",
    "search.label.currency": "Moeda",
    "search.label.due_after": "Vence depois de",
    "search.label.due_before": "Vence antes de",
    "search.label.by": "Totais por",
    "search.label.limit": "Máximo de resultados",
    "search.label.duplicates": "Incluir duplicados separados",
    "search.label.semantic": "Buscar também pelo sentido (busca semântica)",
    "search.label.category": "Categoria",
    "search.label.kind": "Tipo de documento",
    "search.by_currency_only": "só moeda",
    "search.group.issuer": "emissor",
    "search.group.month": "mês",
    "search.group.year": "ano",
    "search.group.kind": "tipo",
    "search.group.category": "categoria",
    "search.heading.issuer": "Emissor",
    "search.heading.month": "Mês",
    "search.heading.year": "Ano",
    "search.heading.kind": "Tipo",
    "search.heading.category": "Categoria",
    "search.words": "Palavras",
    "search.placeholder": "O que você lembra do arquivo",
    "search.button": "Buscar",
    "search.export": "Exportar CSV",
    "search.filters": "Filtros",
    "search.filters_used": "Filtros ({count} em uso)",
    "search.sensitive": Plural(
        one=(
            "{count} arquivo sensível também foi encontrado; arquivos sensíveis nunca aparecem "
            "aqui (`flib search` no Terminal mostra)."
        ),
        other=(
            "{count} arquivos sensíveis também foram encontrados; eles nunca aparecem aqui "
            "(`flib search` no Terminal mostra)."
        ),
    ),
    "search.no_close": (
        "Nenhum resultado próximo; tente outras palavras, ou busque só pelas palavras."
    ),
    "search.no_matches": "Nenhum resultado.",
    "search.matches_label": "Resultados",
    "search.results_label": "Resultados da busca",
    "search.not_filed": "não arquivado: de volta em {folder}",
    "search.state.skipped": "pulado",
    "search.state.missing": "sumiu",
    "search.state.failed": "com falha",
    "search.state.sensitive": "sensível",
    "search.duplicate_of": "duplicado de {path}",
    "search.field.due": "vence em {date}",
    "search.field.period": "referente a {period}",
    "search.field.dated": "de {date}",
    "search.tags_label": "Tags",
    "search.totals": "Totais",
    "search.totals_note": (
        "Somando todos os resultados por palavras (nunca duplicados), não só os listados."
    ),
    "search.totals_note_semantic": (
        "Somando todos os resultados por palavras (nunca duplicados), não só os listados. Os "
        "totais seguem só as palavras, não o sentido."
    ),
    "search.no_amounts": "Nenhum valor entre os resultados.",
    "search.no_group": "(nenhum)",
    "search.by_caption": "Por {group}",
    "search.per_currency": "Por moeda",
    "search.without_amount": Plural(
        one="{count} arquivo encontrado não informa valor; fica fora da conta.",
        other="{count} arquivos encontrados não informam valor; ficam fora da conta.",
    ),
    "search.csv_cut": (
        "# cortado: mais de {count} resultados; só os primeiros {count} estão aqui. Refine a busca."
    ),
    # Vencimentos
    "due.within.7d": "7 dias",
    "due.within.2w": "2 semanas",
    "due.within.1m": "1 mês",
    "due.within.3m": "3 meses",
    "due.overdue_chip": "Vencidos",
    "due.hide_done": "Esconder os concluídos",
    "due.show_all": "Mostrar também os concluídos e outros tipos",
    "due.lead": (
        "Contas e outros documentos com uma data de vencimento que a IA leu neles; nada é "
        "adivinhado."
    ),
    "due.span_overdue": "Vencidos, não importa há quanto tempo (hoje é {today}).",
    "due.span": Plural(
        one="Vencidos desde {start} ({count} dia atrás) e a vencer até {end}; hoje é {today}.",
        other="Vencidos desde {start} ({count} dias atrás) e a vencer até {end}; hoje é {today}.",
    ),
    "due.nothing": "Nada vence neste período.",
    "due.totals": "Total em aberto, por moeda",
    "due.without_amount": Plural(
        one="{count} documento não informa valor; fica fora da conta.",
        other="{count} documentos não informam valor; ficam fora da conta.",
    ),
    "due.hidden": Plural(
        one="{count} documento concluído ou de outro tipo não aparece.",
        other="{count} documentos concluídos ou de outros tipos não aparecem.",
    ),
    "due.reopen": "Reabrir",
    "due.mark_done": "Marcar como concluído",
    "due.reopen_named": "Reabrir: {name}",
    "due.mark_done_named": "Marcar como concluído: {name}",
    "due.done": "concluído",
    "due.done_tag": "concluído (tag do Finder)",
    "due.overdue": "vencido",
    "due.col.due": "Vencimento",
    "due.col.when": "Quando",
    "due.col.amount": "Valor",
    "due.col.issuer": "Emissor",
    "due.col.kind": "Tipo",
    "due.col.file": "Arquivo",
    "due.col.actions": "Ações",
    "due.today": "hoje",
    "due.tomorrow": "amanhã",
    "due.yesterday": "ontem",
    "due.in_days": Plural(one="em {count} dia", other="em {count} dias"),
    "due.days_ago": Plural(one="há {count} dia", other="há {count} dias"),
    # Abrir e Mostrar no Finder
    "open.no_file": "Nenhum arquivo foi indicado.",
    "open.not_indexed": "Esse arquivo não está no índice.",
    "open.not_openable": "Esse arquivo não é um dos que a interface abre.",
    "open.outside": "Esse arquivo não está na biblioteca nem numa pasta de entrada.",
    "open.gone": "Esse arquivo não está mais onde o índice diz.",
    "open.not_a_document": (
        "Daqui só se abrem documentos; este poderia rodar um programa ou abrir um endereço. "
        "Use Mostrar no Finder."
    ),
    "open.failed": "O macOS não conseguiu abrir.",
    # o núcleo do flib (i18n.core)
    "core.search.unknown_category": "Categoria desconhecida: {value} (existentes: {known})",
    "core.search.bad_extension": "Extensão inválida: {value}; escreva como pdf ou .pdf",
    "core.search.since_after_until": (
        "“Processado a partir de” ({since}) é depois de “Processado até” ({until})"
    ),
    "core.search.issuer": "Emissor {value} não identifica nenhuma organização",
    "core.search.unknown_kind": "Tipo desconhecido: {value} (tipos: {kinds})",
    "core.search.min_over_max": "O valor mínimo ({low}) é maior que o valor máximo ({high})",
    "core.search.currency": "Moeda {value} não é um código de moeda como USD ou BRL",
    "core.search.period": "Período {value} não é um ano ou mês como 2026 ou 2026-09",
    "core.search.amount": "{field}: {value} não é um valor como 100 ou 99,90",
    "core.search.date_form": "{field}: {value} não é uma data como 2026-01-31",
    "core.search.date_invalid": "{field}: {value} não é uma data válida",
    "core.search.outdated": Plural(
        one=(
            "{count} arquivo ainda não tem um embedding atualizado e pode ser encontrado só "
            "pelas palavras; a próxima execução ou `flib search --reindex` resolve isso"
        ),
        other=(
            "{count} arquivos ainda não têm um embedding atualizado e podem ser encontrados só "
            "pelas palavras; a próxima execução ou `flib search --reindex` resolve isso"
        ),
    ),
    "core.search.semantic_off": (
        "A busca semântica está desligada (search.semantic.enabled: false); busquei só pelas "
        "palavras"
    ),
    "core.search.no_embeddings": (
        "Nenhum arquivo tem embedding de {model} ainda; rode `flib search --reindex`. Busquei "
        "só pelas palavras"
    ),
    "core.search.semantic_unavailable": (
        "A busca semântica não está disponível ({why}); busquei só pelas palavras"
    ),
    "core.index.older": (
        "O índice é de uma versão anterior do flib; a próxima execução de `flib run` o atualiza"
    ),
    "core.index.newer": (
        "O índice (esquema {schema}) é de uma versão mais nova do flib; atualize o file-librarian"
    ),
}
