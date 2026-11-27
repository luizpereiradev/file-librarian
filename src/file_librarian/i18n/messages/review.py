"""Review, Accept all and Undo (``ui/review.py``), what the actions say (``ui/actions.py``)
and flib's core texts about fixes, undo and due dates (``i18n.core``); see
``file_librarian.i18n.messages``.

An outcome about one file is shown as the file's name followed by what happened
to it ("power bill.pdf is now in Finance."): those messages are written as what
follows the name, in both languages.
"""

from __future__ import annotations

from file_librarian.i18n import Entry, Plural

EN: dict[str, Entry] = {
    # what the actions say (ui.actions)
    "review.undo_this": "Undo this…",
    "review.stale": (
        "This file is no longer as the page showed it: it was moved, renamed or undone since, "
        "in another tab, from Terminal or by a run. Nothing was done; the page now shows how "
        "it is."
    ),
    "review.stale_undo": (
        "This is no longer as the Undo page showed it: something was moved or undone since, "
        "in another tab, from Terminal or by a run. Nothing was done. Open Undo again from "
        "Review or Activity to see how it is now."
    ),
    "review.stale_group": (
        "This group changed since the list was shown: a file was handled or added. Nothing "
        "was moved; check the list again."
    ),
    "review.not_written": (
        "The index could not be written. Anything already moved is recorded by the next flib "
        "command; see Activity."
    ),
    "review.old_index": (
        "The index is from another version of flib; the next `flib run` updates it."
    ),
    "review.sensitive": (
        "The interface does not act on sensitive files. `flib undo` in Terminal does, if you "
        "need it."
    ),
    "review.gone": "That file is not in the index any more.",
    "review.unreadable": "Cannot read the index; nothing was done.",
    "review.fix_failed": "It could not be moved; nothing was changed.",
    "review.bad_name": "The new name is not a plain file name (no '/', ':' or leading '.').",
    "review.warning": "{text}.",
    "review.numbered": "is its new name: {source} was taken in {folder}/.",
    "review.now_in": "is now in {folder}.",
    "review.undo_back_in": "Undo: put it back in {folder}…",
    "review.renamed": "is its new name; it stays in review.",
    "review.undo_rename": "Undo the rename…",
    "review.undo_these": Plural(
        one="Undo: put it back in review…", other="Undo: put these {count} back…"
    ),
    "review.accept_broken": Plural(
        one=(
            "{count} file moved to {target} and recorded; then the index could not be written, "
            "so the rest stopped there. See Activity: the next flib command records a move left "
            "half-done."
        ),
        other=(
            "{count} files moved to {target} and recorded; then the index could not be "
            "written, so the rest stopped there. See Activity: the next flib command records a "
            "move left half-done."
        ),
    ),
    "review.nothing_moved": "Nothing was moved.",
    "review.accept_moved": Plural(
        one="Moved {count} file to {target}, as one run.",
        other="Moved {count} files to {target}, as one run.",
    ),
    "review.accept_moved_left": Plural(
        one="Moved {count} file to {target}, as one run. {left}",
        other="Moved {count} files to {target}, as one run. {left}",
    ),
    "review.accept_left": Plural(
        one="{count} file could not be moved and stays in review.",
        other="{count} files could not be moved and stay in review.",
    ),
    "review.not_moved": "It could not be moved.",
    "review.not_moved_index": "Not moved: the index could not be written.",
    "due.marked_done": "is marked done: it leaves the Due list.",
    "due.reopened": "is open again: it is back on the Due list.",
    "due.done_again": "is marked done again.",
    "due.undo_reopen": "Undo: open it again…",
    "due.undo_mark_done": "Undo: mark it done again…",
    "undo.reason.folder_gone": "its original folder is gone",
    "undo.reason.taken": "something else has its original name now",
    "undo.reason.not_moved_back": "it could not be moved back",
    "undo.reason.changed": "it changed since it was moved (edited by hand?)",
    "undo.back_in": "is back in {folder}.",
    "undo.skipped": "skipped: {why}.",
    "undo.warning": "warning: {why}.",
    "undo.restored": Plural(one="{count} restored", other="{count} restored"),
    "undo.skipped_count": Plural(one="{count} skipped", other="{count} skipped"),
    "undo.reverted": Plural(
        one="{count} index change reverted", other="{count} index changes reverted"
    ),
    "undo.done_run": "Run {run} undone: {restored}, {skipped}.",
    "undo.done_run_reverted": "Run {run} undone: {restored}, {skipped}, {reverted}.",
    "undo.done_file": "Undone: {restored}, {skipped}.",
    "undo.done_file_reverted": "Undone: {restored}, {skipped}, {reverted}.",
    # the Review page
    "review.outcome_gone": (
        "What happened there is no longer kept (only the latest are, until the session ends). "
        "Reload the page from the menu to see how things are now."
    ),
    "review.reason.low_confidence": "Not sure",
    "review.reason.unknown_category": "Category not configured",
    "review.reason.category_removed": "Category removed",
    "review.reason.moved_by_hand": "Moved there by hand",
    "review.reason.none": "Filed before flib kept a reason",
    "review.group_suggested": "{reason}: suggested {suggested}",
    "review.explain.low_confidence": "The AI was not sure enough.",
    "review.explain.low_confidence_suggested": (
        "The AI was not sure enough; it suggested {suggested}."
    ),
    "review.explain.unknown_category": (
        "The AI chose a category that is not configured: {suggested}."
    ),
    "review.explain.category_removed": "Its category was removed from the configuration.",
    "review.explain.moved_by_hand": "It was moved to the review folder outside flib.",
    "review.explain.unknown": "Unknown (filed before flib recorded review reasons).",
    "review.lead": (
        "Files flib was not sure about wait in {folder}. Accept the suggestion, pick another "
        "category or a new name, or put a file back where it was. Every change is journaled; "
        "Undo reverts it."
    ),
    "review.nothing": "Nothing waits for review.",
    "review.count": Plural(one="{count} file in review", other="{count} files in review"),
    "review.group_chip": "{title} ({count})",
    "review.groups_label": "Groups",
    "review.more": Plural(
        one="And {count} more file; they show here once these are done.",
        other="And {count} more files; they show here once these are done.",
    ),
    "review.title": Plural(one="Review: {count} file", other="Review: {count} files"),
    "review.accept_all_link": Plural(
        one="Accept {target} for all {count}…", other="Accept {target} for all {count}…"
    ),
    "review.accept_some_link": "Accept {target} for {count} of the {present}…",
    "review.from": "from {folder}",
    "review.in_review": "in review ",  # its age unknown: as the page always said it
    "review.in_review_today": "in review since today",
    "review.in_review_days": Plural(
        one="in review for {count} day", other="in review for {count} days"
    ),
    "review.confidence": "confidence {value}",
    "review.reconcile": "`flib reconcile` in Terminal finds it.",
    "review.accept": "Accept: {target}",
    "review.accept_named": "Accept {target}: {name}",
    "review.keep": "Keep in review (only rename it)",
    "review.move_to": "Move to {category}",
    "review.other": "Another category or a new name",
    "review.category": "Category",
    "review.category_named": "Category for {name}",
    "review.new_name": "New name",
    "review.new_name_optional": "New name (optional)",
    "review.new_name_named": "New name for {name}",
    "review.move": "Move or rename as chosen",
    "review.move_named": "Move or rename as chosen: {name}",
    "review.put_back": "Put back where it was…",
    # Accept all
    "review.accept_all": "Accept all",
    "review.group_gone": "That group is not in review any more.",
    "review.accept_lead": Plural(
        one=(
            "{count} file goes to {target}, as one run: one Undo puts it back in review. A taken "
            "name gets the next free number; nothing is overwritten."
        ),
        other=(
            "These {count} files go to {target}, as one run: one Undo puts them all back in "
            "review. A taken name gets the next free number; nothing is overwritten."
        ),
    ),
    "review.accept_missing": Plural(
        one="{count} file of this group is missing on disk and left out.",
        other="{count} files of this group are missing on disk and left out.",
    ),
    "review.accept_capped": ("Only {count} at a time; the rest stay in review for the next round."),
    "review.accept_button": Plural(
        one="Move {count} file to {target}", other="Move {count} files to {target}"
    ),
    # Undo
    "undo.title": "Undo",
    "undo.back_to": "back to ",
    "undo.old_name_back": " (gets its old name back: {name})",
    "undo.moves_label": "Moves",
    "undo.run_none": Plural(
        one=(
            "Nothing of run {run} can be put back now: each of its {count} file is listed below "
            "with why."
        ),
        other=(
            "Nothing of run {run} can be put back now: each of its {count} files is listed "
            "below with why."
        ),
    ),
    "undo.run_lead": Plural(
        one=(
            "Undoing run {run} puts {count} file back where it was. Nothing is ever forced or "
            "overwritten."
        ),
        other=(
            "Undoing run {run} puts {count} files back where they were. Nothing is ever forced "
            "or overwritten."
        ),
    ),
    "undo.run_lead_changes": Plural(
        one=(
            "Undoing run {run} puts {count} file back where it was, and puts back what the run "
            "changed in the index. Nothing is ever forced or overwritten."
        ),
        other=(
            "Undoing run {run} puts {count} files back where they were, and puts back what the "
            "run changed in the index. Nothing is ever forced or overwritten."
        ),
    ),
    "undo.run_lead_skipped": Plural(
        one=(
            "Undoing run {run} puts {count} file back where it was; {skipped} skipped (see "
            "below). Nothing is ever forced or overwritten."
        ),
        other=(
            "Undoing run {run} puts {count} files back where they were; {skipped} skipped (see "
            "below). Nothing is ever forced or overwritten."
        ),
    ),
    "undo.run_lead_skipped_changes": Plural(
        one=(
            "Undoing run {run} puts {count} file back where it was; {skipped} skipped (see "
            "below), and puts back what the run changed in the index. Nothing is ever forced "
            "or overwritten."
        ),
        other=(
            "Undoing run {run} puts {count} files back where they were; {skipped} skipped (see "
            "below), and puts back what the run changed in the index. Nothing is ever forced "
            "or overwritten."
        ),
    ),
    "undo.run_skipped": Plural(one="{count} is", other="{count} are"),
    "undo.run_index_only": (
        "Undoing run {run} puts back what it changed in the index (nothing moves). Nothing is "
        "ever forced or overwritten."
    ),
    "undo.run_button": "Undo run {run}",
    "undo.is_open": "is open again.",
    "undo.settle_lead": "Undoing this puts the Due list back as it was:",
    "undo.open_it_again": "Open it again",
    "undo.undo_it": "Undo it",
    "undo.inputs_note": (
        "A file put back in an input folder stays there: the next runs leave it where you put it."
    ),
    "undo.file_blocked": "This file's last move cannot be undone now:",
    "undo.file_lead": "Undoing this file's last move puts it back:",
    "undo.file_button": "Undo this move",
    # flib's core (i18n.core): fix, undo and due, with "This file" in place of the path
    "core.fix.not_organized": "This file is not a file flib organized (see `flib search`).",
    "core.fix.status": "This file is {status}; only organized or review files can be fixed.",
    "core.fix.locked": (
        "This file is protected by macOS while the Mac is locked; unlock it and try again."
    ),
    "core.fix.changed": (
        "This file changed or moved since it was indexed; run `flib reconcile` first."
    ),
    "core.fix.same_name": "This file is already there with that name.",
    "core.fix.too_long": "The new name is longer than macOS allows (255 bytes).",
    "core.undo.not_moved": "This file is not a file flib moved.",
    "core.undo.no_move": "This file has no move to undo.",
    "core.undo.no_run": "No run has moved anything yet.",
    "core.undo.nothing_left": "Run {run} has nothing left to undo.",
    "core.due.status": "This file is {status}; only organized or review files have a due.",
    "core.due.no_due_date": "This file states no due date, so it is never listed as due.",
    "core.due.already_done": "This file is already marked as done.",
    "core.due.not_done": "This file is not marked as done.",
    "core.undo.gone": "no longer there (moved or deleted by hand)",
    "core.undo.moved_again": "moved again since, by run {run} (undo that first)",
    "core.undo.locked": "protected by macOS while the Mac is locked; unlock it and try again",
    "core.tag.not_set": "Finder tag {value} not set: {why}",
    "core.tag.not_removed": "Finder tag {value} not removed: {why}",
}

PT_BR: dict[str, Entry] = {
    "review.undo_this": "Desfazer isto…",
    "review.stale": (
        "Este arquivo não está mais como a página mostrava: ele foi movido, renomeado ou "
        "desfeito depois disso, em outra aba, pelo Terminal ou por uma execução. Nada foi "
        "feito; a página agora mostra como ele está."
    ),
    "review.stale_undo": (
        "Isto não está mais como a página Desfazer mostrava: algo foi movido ou desfeito "
        "depois disso, em outra aba, pelo Terminal ou por uma execução. Nada foi feito. Abra "
        "Desfazer de novo pela Revisão ou pela Atividade para ver como está agora."
    ),
    "review.stale_group": (
        "Este grupo mudou desde que a lista foi mostrada: um arquivo foi resolvido ou entrou "
        "no grupo. Nada foi movido; confira a lista de novo."
    ),
    "review.not_written": (
        "Não foi possível gravar no índice. O que já foi movido fica registrado no próximo "
        "comando do flib; veja a Atividade."
    ),
    "review.old_index": (
        "O índice é de outra versão do flib; a próxima execução de `flib run` o atualiza."
    ),
    "review.sensitive": (
        "A interface não mexe em arquivos sensíveis. Se você precisar, `flib undo` no "
        "Terminal faz isso."
    ),
    "review.gone": "Esse arquivo não está mais no índice.",
    "review.unreadable": "Não foi possível ler o índice; nada foi feito.",
    "review.fix_failed": "Não foi possível mover o arquivo; nada foi alterado.",
    "review.bad_name": (
        "O novo nome não é um nome de arquivo simples (sem '/', ':' nem '.' no começo)."
    ),
    "review.warning": "{text}.",
    "review.numbered": "é o novo nome: {source} já existia em {folder}/.",
    "review.now_in": "agora está em {folder}.",
    "review.undo_back_in": "Desfazer: devolver para {folder}…",
    "review.renamed": "é o novo nome; o arquivo continua em revisão.",
    "review.undo_rename": "Desfazer a renomeação…",
    "review.undo_these": Plural(
        zero="Desfazer…",
        one="Desfazer: devolver para a revisão…",
        other="Desfazer: devolver estes {count}…",
    ),
    "review.accept_broken": Plural(
        zero=(
            "Nenhum arquivo foi movido para {target}: não foi possível gravar no índice, então "
            "tudo parou aí. Veja a Atividade."
        ),
        one=(
            "{count} arquivo foi movido para {target} e registrado; depois não foi possível "
            "gravar no índice, então o resto parou aí. Veja a Atividade: o próximo comando do "
            "flib registra uma movimentação que ficou pela metade."
        ),
        other=(
            "{count} arquivos foram movidos para {target} e registrados; depois não foi "
            "possível gravar no índice, então o resto parou aí. Veja a Atividade: o próximo "
            "comando do flib registra uma movimentação que ficou pela metade."
        ),
    ),
    "review.nothing_moved": "Nada foi movido.",
    "review.accept_moved": Plural(
        zero="Nenhum arquivo foi movido para {target}.",
        one="{count} arquivo movido para {target}, numa só execução.",
        other="{count} arquivos movidos para {target}, numa só execução.",
    ),
    "review.accept_moved_left": Plural(
        zero="Nenhum arquivo foi movido para {target}. {left}",
        one="{count} arquivo movido para {target}, numa só execução. {left}",
        other="{count} arquivos movidos para {target}, numa só execução. {left}",
    ),
    "review.accept_left": Plural(
        zero="Nenhum arquivo ficou para trás.",
        one="{count} arquivo não pôde ser movido e continua em revisão.",
        other="{count} arquivos não puderam ser movidos e continuam em revisão.",
    ),
    "review.not_moved": "Não foi possível mover o arquivo.",
    "review.not_moved_index": "Não movido: não foi possível gravar no índice.",
    "due.marked_done": "foi marcado como concluído: sai da lista de Vencimentos.",
    "due.reopened": "foi reaberto: está de volta na lista de Vencimentos.",
    "due.done_again": "está marcado como concluído de novo.",
    "due.undo_reopen": "Desfazer: reabrir…",
    "due.undo_mark_done": "Desfazer: marcar como concluído de novo…",
    "undo.reason.folder_gone": "a pasta de origem não existe mais",
    "undo.reason.taken": "outro arquivo tem o nome original agora",
    "undo.reason.not_moved_back": "não foi possível devolvê-lo",
    "undo.reason.changed": "ele mudou depois de ser movido (editado à mão?)",
    "undo.back_in": "está de volta em {folder}.",
    "undo.skipped": "ignorado: {why}.",
    "undo.warning": "aviso: {why}.",
    "undo.restored": Plural(
        zero="nenhum devolvido", one="{count} devolvido", other="{count} devolvidos"
    ),
    "undo.skipped_count": Plural(
        zero="nenhum ignorado", one="{count} ignorado", other="{count} ignorados"
    ),
    "undo.reverted": Plural(
        zero="nenhuma alteração no índice desfeita",
        one="{count} alteração no índice desfeita",
        other="{count} alterações no índice desfeitas",
    ),
    "undo.done_run": "Execução {run} desfeita: {restored}, {skipped}.",
    "undo.done_run_reverted": "Execução {run} desfeita: {restored}, {skipped}, {reverted}.",
    "undo.done_file": "Desfeito: {restored}, {skipped}.",
    "undo.done_file_reverted": "Desfeito: {restored}, {skipped}, {reverted}.",
    "review.outcome_gone": (
        "O que aconteceu ali não está mais guardado (só os mais recentes ficam, até a sessão "
        "terminar). Abra a página de novo pelo menu para ver como as coisas estão agora."
    ),
    "review.reason.low_confidence": "Sem certeza",
    "review.reason.unknown_category": "Categoria não configurada",
    "review.reason.category_removed": "Categoria removida",
    "review.reason.moved_by_hand": "Movido para lá à mão",
    "review.reason.none": "Arquivado antes de o flib guardar um motivo",
    "review.group_suggested": "{reason}: sugestão {suggested}",
    "review.explain.low_confidence": "A IA não teve certeza suficiente.",
    "review.explain.low_confidence_suggested": (
        "A IA não teve certeza suficiente; ela sugeriu {suggested}."
    ),
    "review.explain.unknown_category": (
        "A IA escolheu uma categoria que não está configurada: {suggested}."
    ),
    "review.explain.category_removed": "A categoria dele foi removida da configuração.",
    "review.explain.moved_by_hand": "Ele foi movido para a pasta de revisão fora do flib.",
    "review.explain.unknown": "Desconhecido (arquivado antes de o flib guardar motivos).",
    "review.lead": (
        "Os arquivos sobre os quais o flib ficou em dúvida esperam em {folder}. Aceite a "
        "sugestão, escolha outra categoria ou um novo nome, ou devolva o arquivo para onde ele "
        "estava. Toda alteração fica registrada; Desfazer volta atrás."
    ),
    "review.nothing": "Nada esperando revisão.",
    "review.count": Plural(
        zero="Nenhum arquivo em revisão",
        one="{count} arquivo em revisão",
        other="{count} arquivos em revisão",
    ),
    "review.group_chip": "{title} ({count})",
    "review.groups_label": "Grupos",
    "review.more": Plural(
        zero="Nenhum arquivo a mais.",
        one="E mais {count} arquivo; ele aparece aqui quando estes forem resolvidos.",
        other="E mais {count} arquivos; eles aparecem aqui quando estes forem resolvidos.",
    ),
    "review.title": Plural(
        zero="Revisão: nenhum arquivo",
        one="Revisão: {count} arquivo",
        other="Revisão: {count} arquivos",
    ),
    "review.accept_all_link": Plural(
        zero="Aceitar {target} para nenhum arquivo…",
        one="Aceitar {target} para {count} arquivo…",
        other="Aceitar {target} para os {count}…",
    ),
    "review.accept_some_link": "Aceitar {target} para {count} dos {present}…",
    "review.from": "veio de {folder}",
    "review.in_review": "em revisão",
    "review.in_review_today": "em revisão desde hoje",
    "review.in_review_days": Plural(
        zero="em revisão desde hoje",
        one="em revisão há {count} dia",
        other="em revisão há {count} dias",
    ),
    "review.confidence": "confiança {value}",
    "review.reconcile": "`flib reconcile` no Terminal encontra o arquivo.",
    "review.accept": "Aceitar: {target}",
    "review.accept_named": "Aceitar {target}: {name}",
    "review.keep": "Manter em revisão (só renomear)",
    "review.move_to": "Mover para {category}",
    "review.other": "Outra categoria ou um novo nome",
    "review.category": "Categoria",
    "review.category_named": "Categoria de {name}",
    "review.new_name": "Novo nome",
    "review.new_name_optional": "Novo nome (opcional)",
    "review.new_name_named": "Novo nome para {name}",
    "review.move": "Mover ou renomear como escolhido",
    "review.move_named": "Mover ou renomear como escolhido: {name}",
    "review.put_back": "Devolver para onde estava…",
    "review.accept_all": "Aceitar todos",
    "review.group_gone": "Esse grupo não está mais em revisão.",
    "review.accept_lead": Plural(
        zero="Nenhum arquivo vai para {target}.",
        one=(
            "{count} arquivo vai para {target}, numa só execução: um Desfazer o devolve para a "
            "revisão. Se o nome já existir, ele ganha o próximo número livre; nada é "
            "sobrescrito."
        ),
        other=(
            "Estes {count} arquivos vão para {target}, numa só execução: um Desfazer devolve "
            "todos para a revisão. Se um nome já existir, ele ganha o próximo número livre; "
            "nada é sobrescrito."
        ),
    ),
    "review.accept_missing": Plural(
        zero="Nenhum arquivo deste grupo sumiu do disco.",
        one="{count} arquivo deste grupo sumiu do disco e ficou de fora.",
        other="{count} arquivos deste grupo sumiram do disco e ficaram de fora.",
    ),
    "review.accept_capped": (
        "No máximo {count} por vez; o resto continua em revisão para a próxima rodada."
    ),
    "review.accept_button": Plural(
        zero="Nada para mover para {target}",
        one="Mover {count} arquivo para {target}",
        other="Mover {count} arquivos para {target}",
    ),
    "undo.title": "Desfazer",
    "undo.back_to": "de volta para ",
    "undo.old_name_back": " (recupera o nome antigo: {name})",
    "undo.moves_label": "Movimentações",
    "undo.run_none": Plural(
        zero="Nada da execução {run} pode ser devolvido agora.",
        one=(
            "Nada da execução {run} pode ser devolvido agora: {count} arquivo dela aparece "
            "abaixo, com o motivo."
        ),
        other=(
            "Nada da execução {run} pode ser devolvido agora: cada um dos {count} arquivos dela "
            "aparece abaixo, com o motivo."
        ),
    ),
    "undo.run_lead": Plural(
        zero=(
            "Desfazer a execução {run} não devolve nenhum arquivo. Nada é forçado nem sobrescrito."
        ),
        one=(
            "Desfazer a execução {run} devolve {count} arquivo para onde ele estava. Nada é "
            "forçado nem sobrescrito."
        ),
        other=(
            "Desfazer a execução {run} devolve {count} arquivos para onde eles estavam. Nada é "
            "forçado nem sobrescrito."
        ),
    ),
    "undo.run_lead_changes": Plural(
        zero=(
            "Desfazer a execução {run} não devolve nenhum arquivo, mas desfaz o que a execução "
            "mudou no índice. Nada é forçado nem sobrescrito."
        ),
        one=(
            "Desfazer a execução {run} devolve {count} arquivo para onde ele estava e desfaz o "
            "que a execução mudou no índice. Nada é forçado nem sobrescrito."
        ),
        other=(
            "Desfazer a execução {run} devolve {count} arquivos para onde eles estavam e desfaz "
            "o que a execução mudou no índice. Nada é forçado nem sobrescrito."
        ),
    ),
    "undo.run_lead_skipped": Plural(
        zero=(
            "Desfazer a execução {run} não devolve nenhum arquivo; {skipped} (veja abaixo). Nada é "
            "forçado nem sobrescrito."
        ),
        one=(
            "Desfazer a execução {run} devolve {count} arquivo para onde ele estava; {skipped} "
            "(veja abaixo). Nada é forçado nem sobrescrito."
        ),
        other=(
            "Desfazer a execução {run} devolve {count} arquivos para onde eles estavam; "
            "{skipped} (veja abaixo). Nada é forçado nem sobrescrito."
        ),
    ),
    "undo.run_lead_skipped_changes": Plural(
        zero=(
            "Desfazer a execução {run} não devolve nenhum arquivo; {skipped} (veja abaixo); e "
            "desfaz o que a execução mudou no índice. Nada é forçado nem sobrescrito."
        ),
        one=(
            "Desfazer a execução {run} devolve {count} arquivo para onde ele estava; {skipped} "
            "(veja abaixo); e desfaz o que a execução mudou no índice. Nada é forçado nem "
            "sobrescrito."
        ),
        other=(
            "Desfazer a execução {run} devolve {count} arquivos para onde eles estavam; "
            "{skipped} (veja abaixo); e desfaz o que a execução mudou no índice. Nada é forçado "
            "nem sobrescrito."
        ),
    ),
    "undo.run_skipped": Plural(
        zero="nenhum fica de fora", one="{count} fica de fora", other="{count} ficam de fora"
    ),
    "undo.run_index_only": (
        "Desfazer a execução {run} desfaz o que ela mudou no índice (nada é movido). Nada é "
        "forçado nem sobrescrito."
    ),
    "undo.run_button": "Desfazer a execução {run}",
    "undo.is_open": "está reaberto.",
    "undo.settle_lead": "Desfazer isto deixa a lista de Vencimentos como estava:",
    "undo.open_it_again": "Reabrir",
    "undo.undo_it": "Desfazer",
    "undo.inputs_note": (
        "Um arquivo devolvido para uma pasta de entrada fica lá: as próximas execuções o "
        "deixam onde você colocou."
    ),
    "undo.file_blocked": "A última movimentação deste arquivo não pode ser desfeita agora:",
    "undo.file_lead": "Desfazer a última movimentação deste arquivo o devolve:",
    "undo.file_button": "Desfazer esta movimentação",
    "core.fix.not_organized": ("Este arquivo não foi organizado pelo flib (veja `flib search`)."),
    "core.fix.status": (
        "Este arquivo está como {status}; só arquivos organizados ou em revisão podem ser "
        "corrigidos."
    ),
    "core.fix.locked": (
        "Este arquivo está protegido pelo macOS enquanto o Mac está bloqueado; desbloqueie e "
        "tente de novo."
    ),
    "core.fix.changed": (
        "Este arquivo mudou ou foi movido depois de indexado; rode `flib reconcile` antes."
    ),
    "core.fix.same_name": "Este arquivo já está lá com esse nome.",
    "core.fix.too_long": "O novo nome é mais longo do que o macOS permite (255 bytes).",
    "core.undo.not_moved": "Este arquivo não foi movido pelo flib.",
    "core.undo.no_move": "Este arquivo não tem movimentação para desfazer.",
    "core.undo.no_run": "Nenhuma execução moveu nada ainda.",
    "core.undo.nothing_left": "A execução {run} não tem mais nada para desfazer.",
    "core.due.status": (
        "Este arquivo está como {status}; só arquivos organizados ou em revisão têm vencimento."
    ),
    "core.due.no_due_date": (
        "Este arquivo não informa data de vencimento, então nunca aparece nos Vencimentos."
    ),
    "core.due.already_done": "Este arquivo já está marcado como concluído.",
    "core.due.not_done": "Este arquivo não está marcado como concluído.",
    "core.undo.gone": "não está mais lá (movido ou apagado à mão)",
    "core.undo.moved_again": "foi movido de novo depois, pela execução {run} (desfaça essa antes)",
    "core.undo.locked": (
        "protegido pelo macOS enquanto o Mac está bloqueado; desbloqueie e tente de novo"
    ),
    "core.tag.not_set": "Tag do Finder {value} não aplicada: {why}",
    "core.tag.not_removed": "Tag do Finder {value} não removida: {why}",
}
