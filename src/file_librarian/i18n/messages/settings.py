"""The Settings page (``ui/settings.py``); see ``file_librarian.i18n.messages``.

Text from flib's core that the page passes on as a parameter stays as the core
wrote it: the configuration parser's reason for refusing a value (``{why}``) and
a YAML error (``{error}``), which the command line prints the same way.
"""

from __future__ import annotations

from file_librarian.i18n import Entry, Plural

EN: dict[str, Entry] = {
    # the page
    "settings.title": "Settings",
    "settings.lead": (
        "The essentials. Each change is checked as `flib check-config` checks it before it is "
        "saved, keeps a backup, and can be undone with Restore the previous settings. "
        "Everything else is in the configuration file."
    ),
    "settings.toc": "Sections",
    "settings.unusable": "The configuration file cannot be used: {problem}",
    "settings.section.schedule": "When to organize",
    "settings.section.provider": "Who classifies",
    "settings.section.reading": "What flib reads and sends",
    "settings.section.inputs": "Folders",
    "settings.section.categories": "Categories",
    "settings.section.costs": "Cost caps",
    "settings.section.reminders": "Notifications and what is due",
    "settings.section.language": "Language",
    "settings.section.file": "Everything else",
    "settings.save": "Save",
    "settings.back": "Back to Settings",
    "settings.command": "`{text}`",
    "settings.list.semicolon": "{first}; {rest}",
    "settings.list.comma": "{first}, {rest}",
    # outcomes
    "settings.stale": (
        "The configuration file changed since this page was shown: edited by hand, in another "
        "tab or by flib. Nothing was saved; the page now shows it as it is."
    ),
    "settings.no_path": (
        "This flib ui does not know its configuration file, so its settings can only be read "
        "here. Run `flib ui` from Terminal to change them."
    ),
    "settings.gone": (
        "What happened there is no longer kept (only the latest are, until the session ends). "
        "The page shows the settings as they are now."
    ),
    "settings.nothing": "Nothing changed: the settings were already like that.",
    "settings.other_database": (
        "The file now names another database than this flib ui uses. Nothing was saved; end "
        "the session and run `flib ui` again."
    ),
    "settings.other_schedule": (
        "The schedule uses another configuration file, {other}. Nothing was changed in "
        "launchd: tick “Use this file for the schedule” to move it here."
    ),
    "settings.saved": "Saved.",
    "settings.restore_link": "Restore the previous settings…",
    "settings.what_changed": "What changed",
    # what the parser names
    "settings.label.language": "Language",
    "settings.label.inputs": "Input folders",
    "settings.label.output_root": "Library folder",
    "settings.label.review_folder": "Review folder",
    "settings.label.duplicates_folder": "Duplicates folder",
    "settings.label.duplicates": "Duplicates",
    "settings.label.provider.name": "Who classifies",
    "settings.label.provider.model": "Model",
    "settings.label.vision.enabled": "Images to the model (vision)",
    "settings.label.ocr.enabled": "Text in images (OCR)",
    "settings.label.ocr.languages": "Text recognition languages",
    "settings.label.search.semantic.enabled": "Search by meaning",
    "settings.label.limits.max_cost_usd_per_run": "Cost cap per run",
    "settings.label.limits.max_cost_usd_per_day": "Daily cost cap",
    "settings.label.summary.notify": "Summary notification",
    "settings.label.due.remind": "Due reminders",
    "settings.label.due.remind_days_before": "Reminder, days before",
    "settings.label.due.kinds": "Kinds on the Due list",
    "settings.label.schedule.mode": "When to organize",
    "settings.label.schedule.time": "Daily run at",
    "settings.label.categories": "Categories",
    "settings.field.categories.name": "Category name",
    "settings.field.categories.hint": "Category hint",
    "settings.field.categories.layout": "Sub-folders",
    "settings.field.inputs.path": "Input folder",
    "settings.invalid.labelled": "{label} ({where}): {why}",
    "settings.invalid.labelled_only": "{label} ({where})",
    "settings.invalid.where": "{where}: {why}",
    # what changed
    "settings.entry.category": "Category {name}",
    "settings.entry.input": "Input folder {name}",
    "settings.entry.category_field": "Category {name} {field}",
    "settings.entry.input_field": "Input folder {name} {field}",
    "settings.change.added": "added",
    "settings.change.removed": "removed",
    "settings.change.renamed": "renamed to {name}",
    "settings.change.value": "{old} → {new}",
    "settings.change.language_default": "back to what the language brings",
    "settings.change.written": "written in the file, as they were",
    "settings.value.default": "the default",
    "settings.value.none": "none",
    "settings.value.on": "on",
    "settings.value.off": "off",
    "settings.address_in_file": "an address in the file",
    # who classifies
    "settings.provider.anthropic": "Anthropic API",
    "settings.provider.claude_code": "Your Claude subscription (Claude Code)",
    "settings.provider.ollama": "Ollama, on this Mac",
    "settings.provider.openai_compat": "OpenAI-compatible API at {host}",
    "settings.provider.fake": "The test provider (nothing is sent)",
    "settings.provider.choice": "{provider}: {model}",
    "settings.provider.field": "Provider and model",
    "settings.provider.each": "What each choice sends",
    "settings.provider.help": (
        "Another server or model, a key or the provider's other options are set in the file "
        "(`flib key set` for a key). `flib reprocess` classifies filed files again."
    ),
    "settings.leaves.anthropic": (
        "Each file's name, details and up to {chars} characters of its text go to Anthropic's API."
    ),
    "settings.leaves.claude_code": (
        "Each file's name, details and up to {chars} characters of its text go to Anthropic "
        "through your Claude subscription."
    ),
    "settings.leaves.remote": (
        "Each file's name, details and up to {chars} characters of its text go to {host}."
    ),
    "settings.leaves.local": "Nothing leaves this Mac: the model runs here.",
    "settings.leaves.none": "Nothing leaves this Mac.",
    "settings.key.anthropic": "Anthropic API key",
    "settings.key.openai_compat": "openai-compat API key",
    "settings.key.embeddings": "embeddings API key",
    "settings.key.not_set": (
        "{label}: not set. `flib key set` in Terminal saves it in the Keychain."
    ),
    "settings.key.set": "{label}: set ({where}).",
    "settings.key.where.environment": "in the environment",
    "settings.key.where.keychain": "in the Keychain",
    "settings.key.where.env_file": "in the file named by provider.env_file",
    # folders
    "settings.folder.downloads": "Downloads",
    "settings.folder.desktop": "Desktop",
    "settings.folder.downloads_note": "Everything at its top level.",
    "settings.folder.desktop_note": Plural(
        one=(
            "Only documents and images unchanged for {days} day; folders are left alone, as "
            "`flib init` sets it up."
        ),
        other=(
            "Only documents and images unchanged for {days} days; folders are left alone, as "
            "`flib init` sets it up."
        ),
        by="days",
    ),
    "settings.why.plain": "it is not a plain folder path",
    "settings.why.missing": "there is no such folder",
    "settings.why.link": "it is a link to somewhere else",
    "settings.why.not_folder": "it is not a folder",
    "settings.why.broad": "it is too broad",
    "settings.why.outside": "it is outside your home folder",
    "settings.why.hidden": "it is hidden",
    "settings.why.system": "it is a system folder",
    "settings.why.not_yours": "it is not your folder",
    "settings.why.library": "it is the library, or inside it",
    "settings.why.already": "it is an input folder already",
    "settings.inputs.cannot_add": "{folder}: cannot be added, {why}.",
    "settings.inputs.not_added": "{folder} was not added: {why}.",
    "settings.inputs.stop": "Stop organizing it…",
    "settings.inputs.stop_named": "Stop organizing {name}…",
    "settings.inputs.add": "Organize {folder} too",
    "settings.inputs.dup_move": "Set them aside in {folder}",
    "settings.inputs.dup_leave": "Leave them where they are",
    "settings.inputs.help": "flib reads the top level of each input folder only.",
    "settings.inputs.library": "Library",
    "settings.inputs.help_file": (
        "Another folder, the rules of a folder or the library's place are changed in the "
        "file; files already filed stay where they are."
    ),
    "settings.inputs.dup_legend": "Files with the same content as one already filed",
    "settings.inputs.last": "Not saved: flib needs at least one input folder.",
    "settings.rules.summary": "Its rules",
    "settings.rules.only": "only {patterns}",
    "settings.rules.ignore": "ignore {patterns}",
    "settings.rules.ignore_own": "ignore {patterns} ({own} of them its own)",
    "settings.rules.min_age": Plural(
        one="min_age_days {days}", other="min_age_days {days}", by="days"
    ),
    "settings.rules.folders.as_unit": "folders: as_unit",
    "settings.rules.folders.ignore": "folders: ignore",
    "settings.rules.duplicates.move": "duplicates: move",
    "settings.rules.duplicates.leave": "duplicates: leave",
    "settings.rules.fixed_renamed": (
        "fixed category {category} (no AI), renamed by naming.pattern"
    ),
    "settings.rules.fixed_kept": "fixed category {category} (no AI), name kept",
    "settings.rules.fixed_layout_renamed": (
        "fixed category {category} (no AI), layout {layout}, renamed by naming.pattern"
    ),
    "settings.rules.fixed_layout_kept": (
        "fixed category {category} (no AI), layout {layout}, name kept"
    ),
    "settings.rules.ai": "classified by the AI",
    # the schedule
    "settings.schedule.unreadable": "Launchd's state cannot be read now.",
    "settings.schedule.not_loaded": "The schedule is written but not loaded: save it again.",
    "settings.schedule.other": (
        "The installed schedule uses another configuration file, {other}. Saving here "
        "changes nothing in launchd unless you tick the box below: then it uses this file "
        "instead."
    ),
    "settings.schedule.takeover": "Use this file for the schedule",
    "settings.schedule.stale_watch": (
        "Watch mode watches other folders than the inputs: save it again."
    ),
    "settings.schedule.mode.off": "Off: only when you run it",
    "settings.schedule.mode.daily": "Every day at the time below",
    "settings.schedule.mode.watch": "As files arrive",
    "settings.schedule.mode.both": "As files arrive, and every day at the time below",
    "settings.schedule.legend": "Run flib",
    "settings.schedule.time": "Daily run at (24-hour)",
    "settings.schedule.save": "Save the schedule",
    "settings.schedule.help": (
        "A launchd agent runs flib, as `flib schedule` sets it up. If the Mac is asleep at "
        "the time, it runs on wake. As files arrive, the reminders and the periodic summary "
        "come only after a daily run."
    ),
    "settings.schedule.remove_failed": (
        "The schedule could not be removed; `flib schedule --remove` in Terminal says why."
    ),
    "settings.schedule.off": "The schedule is off: flib runs only when you run it.",
    "settings.schedule.bad_time": "Not saved: Daily run at needs a 24-hour time like 12:30.",
    "settings.schedule.refused": (
        "macOS's launchd did not take the schedule. {now} `flib schedule` in Terminal says why."
    ),
    "settings.schedule.refused_saved": (
        "macOS's launchd did not take the schedule (the settings were saved). {now} "
        "`flib schedule` in Terminal says why."
    ),
    "settings.menu.shown": "Menu bar: the cat is there. `flib menu --remove` takes it away.",
    "settings.menu.hidden": (
        "Menu bar: not shown. `flib menu` puts the cat there, with what needs you."
    ),
    "settings.now.unreadable": "launchd's state cannot be read now.",
    "settings.now.none": "Nothing is scheduled now.",
    "settings.now.loaded": "Loaded and running now: {what}.",
    "settings.now.written": "Written but not loaded, so not running: {what}.",
    "settings.now.what_at": "{mode} at {time}",
    "settings.mode.daily": "daily",
    "settings.mode.watch": "watch",
    "settings.mode.both": "both",
    "settings.scheduled.daily": (
        "Daily run scheduled at {time}. If the Mac is asleep then, it runs on wake."
    ),
    "settings.scheduled.watch": Plural(
        one=(
            "Watch mode on: flib runs when something arrives in the {count} input folder, "
            "once downloads finish."
        ),
        other=(
            "Watch mode on: flib runs when something arrives in the {count} input folders, "
            "once downloads finish."
        ),
    ),
    "settings.scheduled.both": Plural(
        one=(
            "Watch mode on: flib runs when something arrives in the {count} input folder, "
            "once downloads finish. And every day at {time}."
        ),
        other=(
            "Watch mode on: flib runs when something arrives in the {count} input folders, "
            "once downloads finish. And every day at {time}."
        ),
    ),
    "settings.note": "Note",
    "settings.notes.dev": (
        "flib runs from a development folder: the schedule depends on it. Install it with "
        "`./scripts/install.sh` and save the schedule again."
    ),
    "settings.notes.no_key": "No API key is set: the scheduled run fails until `flib key set`.",
    "settings.notes.env_key": (
        "The API key is only in this Terminal's environment, which the scheduled run cannot "
        "see: `flib key set` saves it in the Keychain."
    ),
    "settings.notes.claude": (
        "The scheduled run will not find Claude Code: `flib schedule` in Terminal says what "
        "to add to the file."
    ),
    "settings.notes.notifier": (
        "Notifications will show as Script Editor; `flib schedule --refresh` tries again."
    ),
    "settings.notes.doctor": "`flib doctor` checks that the scheduled run can read the folders.",
    "settings.watch.name": "Watch mode",
    "settings.watch.other": "not changed: the schedule uses another configuration file.",
    "settings.watch.failed": (
        "still watches the old folders: launchd did not take the change. "
        "`flib schedule --refresh` in Terminal tries again and says why."
    ),
    "settings.watch.done": "now watches these folders.",
    "settings.menu.name": "Menu bar",
    "settings.menu.done": "rebuilt in the new language.",
    "settings.menu.failed": (
        "still in the old language: `flib menu --refresh` in Terminal tries again and says why."
    ),
    # what flib reads and sends
    "settings.reading.this_mac": "this Mac",
    "settings.reading.vision": "Send images to the model (vision)",
    "settings.reading.vision_help": (
        "Images (made smaller, without their location) and the first pages of scanned PDFs "
        "go to the model with the file. Off, they are classified from their name and text."
    ),
    "settings.reading.vision_help_claude": (
        "Images (made smaller, without their location) and the first pages of scanned PDFs "
        "go to the model with the file. Off, they are classified from their name and text. "
        "It has no effect with your Claude subscription."
    ),
    "settings.reading.ocr": "Read text in images and scanned PDFs (OCR)",
    "settings.reading.ocr_help": (
        "On this Mac, with macOS's own text recognition; nothing is sent for it."
    ),
    "settings.reading.semantic": "Search by meaning",
    "settings.reading.semantic_help": (
        "Names, tags, descriptions and categories (never file content) go to {where} to be "
        "embedded. `flib search --reindex` embeds what is already filed."
    ),
    # categories
    "settings.layout.none": "All in the category's folder",
    "settings.layout.year": "By year",
    "settings.layout.year_month": "By year, then month",
    "settings.layout.issuer": "By issuer",
    "settings.layout.issuer_year": "By issuer, then year",
    "settings.layout.as_file": "As in the file: {layout}",
    "settings.hint.label": "Hint for the AI",
    "settings.hint.kept": (
        "It has line breaks or is long, so it is kept as it is here; change it in the "
        "configuration file."
    ),
    "settings.hint.field": "Hint for the AI (what belongs here)",
    "settings.category.subfolders": "sub-folders {layout}",
    "settings.category.names_kept": "names kept",
    "settings.category.change": "Change",
    "settings.category.name": "Name",
    "settings.category.layout": "Sub-folders",
    "settings.category.rename": "Rename files with the naming pattern",
    "settings.category.remove": "Remove this category",
    "settings.category.remove_named": "Remove the category {name}",
    "settings.category.no_hint": "no hint",
    "settings.category.add": "Add a category",
    "settings.category.new_name": "Name (a folder name)",
    "settings.category.new_hint": "Hint for the AI (optional)",
    "settings.category.add_button": "Add",
    "settings.category.help": (
        "Each one is a folder in the library. A hint tells the AI what belongs there. A "
        "category that holds files is renamed or removed with `flib reprocess`, which moves "
        "them; the page says how."
    ),
    "settings.category.needs_name": "Not saved: a category needs a name.",
    "settings.category.last": "Not saved: flib needs at least one category.",
    "settings.category.not_renamed": (
        "{name} was not renamed: {why}. Renaming it means moving them: change the name in the "
        "configuration file, then run {command} in Terminal."
    ),
    "settings.category.not_removed": (
        "{name} was not removed: {why}. Remove it in the configuration file, then run "
        "{command} in Terminal: it classifies those files again (`--to-review` sends them to "
        "review instead)."
    ),
    "settings.holds.input": "the input folder {path} files everything in it",
    "settings.holds.index": Plural(
        one="the index has {count} file in it", other="the index has {count} files in it"
    ),
    "settings.holds.not_empty": "its folder is not empty",
    "settings.holds.unreadable": "its folder cannot be read",
    "settings.holds.index_error": "Not saved: {problem}; nothing changed.",
    "settings.field.too_long": "Not saved: {label} is longer than {limit} characters.",
    "settings.field.invisible": (
        "Not saved: {label} has invisible characters, which are not taken."
    ),
    "settings.what.name": "the name",
    "settings.what.hint": "the hint",
    # cost caps
    "settings.costs.default": "Three times the cap per run",
    "settings.costs.none": "No daily cap",
    "settings.costs.amount": "This amount:",
    "settings.costs.help": (
        "What the provider's calls may cost, estimated by flib. Files left over wait for the "
        "next run. The daily cap applies to the scheduled runs."
    ),
    "settings.costs.per_run": "Per run, US$ (empty: no cap)",
    "settings.costs.per_day": "Per day",
    "settings.costs.day_amount": "Daily cap, US$",
    "settings.costs.no_prices": (
        "{provider} at a remote server has no prices (provider.price_input_per_mtok, "
        "price_output_per_mtok), so no cost cap can stop it; {limit}."
    ),
    "settings.costs.unlimited": "nothing limits them",
    "settings.costs.files_cap": Plural(
        one="automatic runs see at most {files} files a day (limits.max_files_per_day)",
        other="automatic runs see at most {files} files a day (limits.max_files_per_day)",
        by="files",
    ),
    "settings.costs.bad_amount": (
        "Not saved: {what} must be an amount in US$ above 0 and at most {max}, like 1.50."
    ),
    "settings.costs.ambiguous": (
        "Not saved: {what} “{typed}” could be read two ways. Type it without the comma for a "
        "whole number, or with a dot for decimals, like 1.50."
    ),
    "settings.costs.what_run": "the cap per run",
    "settings.costs.what_day": "the daily cap",
    # notifications and what is due
    "settings.reminders.notify": "Notify me with the periodic summary",
    "settings.reminders.notify_help": (
        "A macOS notification with counts only, never file names, when files have waited."
    ),
    "settings.reminders.remind": "Count documents coming due in the summary",
    "settings.reminders.days": "Days before the due date",
    "settings.reminders.kinds": "Kinds of document on the Due list",
    "settings.reminders.bad_days": (
        "Not saved: Days before the due date needs a number from 0 to 60."
    ),
    # language
    "settings.language.field": "Language of the interface, names and descriptions",
    "settings.language.help": (
        "It is the language of this interface and the menu bar, and of the names and "
        "descriptions flib writes. Folder and category names stay as they are: the page "
        "writes them in the file first if they came from the language."
    ),
    # everything else
    "settings.file.where": (
        "The configuration file: {path}. `config.example.yaml` in the repository lists every "
        "setting."
    ),
    "settings.file.check": (
        "`flib check-config` checks it, and `flib doctor` checks flib as a whole (folder "
        "access, the schedule, the key)."
    ),
    "settings.file.open": "Open it in a text editor",
    "settings.file.missing": "There is no configuration file there.",
    "settings.file.unreadable": "The configuration file cannot be read.",
    "settings.file.not_utf8": "The configuration file is not UTF-8 text.",
    "settings.file.cannot_change": "The configuration file cannot be changed here: {problem}",
    "settings.file.not_saved": "Not saved: {error}.",
    "settings.file.write_failed": ("The configuration file could not be written; nothing changed."),
    "settings.file.edit_failed": (
        "Not saved: {error}. Change it in the configuration file instead; nothing was written."
    ),
    "settings.file.invalid": "Not saved, nothing was written: {problem}.",
    "settings.file.not_plain": "The configuration file is not a plain file.",
    "settings.file.open_failed": "macOS could not open it.",
    "settings.file.opened": (
        "Opened in your text editor. Reload this page after you save it there; "
        "`flib check-config` checks it."
    ),
    # stop organizing a folder
    "settings.remove.title": "Stop organizing a folder",
    "settings.remove.short": "Stop organizing",
    "settings.remove.cannot": "That folder cannot be removed from here now.",
    "settings.remove.lead": (
        "flib stops taking files from {folder}. Nothing in it moves; what flib already filed "
        "stays in the library. Its entry, its rules and the comment above it leave the "
        "configuration file (a backup is kept)."
    ),
    "settings.remove.button": "Stop organizing it",
    # restore
    "settings.restore.title": "Restore the previous settings",
    "settings.restore.short": "Restore",
    "settings.restore.none": "There is no backup to restore: nothing was changed from here yet.",
    "settings.restore.unusable": "The latest backup cannot be used: {problem}",
    "settings.restore.lead": (
        "This puts back the configuration file as it was before the last change made here (a "
        "backup from {stamp}). The current file is backed up first, so this can be undone the "
        "same way. The schedule in launchd is not changed: save it again on the Settings page "
        "if needed."
    ),
    "settings.restore.warning": (
        "The whole file is put back: comments and changes made by hand since that backup are "
        "lost (the current file stays in a backup)."
    ),
    "settings.restore.what": "What changes",
    "settings.restore.same": "The backup has the same settings; only comments or layout differ.",
    "settings.restore.button": "Restore these settings",
    "settings.restore.earlier": "earlier",
    "settings.restore.changed": (
        "The backup changed since the page was shown; nothing was restored."
    ),
    "settings.restore.backup_unusable": "The backup cannot be used: {problem}",
    "settings.restore.done": "The previous settings are back.",
}

PT_BR: dict[str, Entry] = {
    "settings.title": "Configurações",
    "settings.lead": (
        "O essencial. Cada alteração é conferida como o `flib check-config` confere antes de "
        "ser salva, guarda um backup e pode ser desfeita com Restaurar as configurações "
        "anteriores. Todo o resto fica no arquivo de configuração."
    ),
    "settings.toc": "Seções",
    "settings.unusable": "O arquivo de configuração não pode ser usado: {problem}",
    "settings.section.schedule": "Quando organizar",
    "settings.section.provider": "Quem classifica",
    "settings.section.reading": "O que o flib lê e envia",
    "settings.section.inputs": "Pastas",
    "settings.section.categories": "Categorias",
    "settings.section.costs": "Limites de custo",
    "settings.section.reminders": "Notificações e vencimentos",
    "settings.section.language": "Idioma",
    "settings.section.file": "Todo o resto",
    "settings.save": "Salvar",
    "settings.back": "Voltar para Configurações",
    "settings.command": "`{text}`",
    "settings.list.semicolon": "{first}; {rest}",
    "settings.list.comma": "{first}, {rest}",
    "settings.stale": (
        "O arquivo de configuração mudou desde que esta página foi aberta: foi editado à mão, "
        "em outra aba ou pelo flib. Nada foi salvo; a página agora mostra como ele está."
    ),
    "settings.no_path": (
        "Este flib ui não sabe qual é o seu arquivo de configuração, então aqui as "
        "configurações só podem ser lidas. Rode `flib ui` no Terminal para alterá-las."
    ),
    "settings.gone": (
        "O que aconteceu ali não está mais guardado (só as últimas ações ficam, até a sessão "
        "terminar). A página mostra as configurações como estão agora."
    ),
    "settings.nothing": "Nada mudou: as configurações já estavam assim.",
    "settings.other_database": (
        "O arquivo agora aponta para outro banco de dados, diferente do que este flib ui usa. "
        "Nada foi salvo; encerre a sessão e rode `flib ui` de novo."
    ),
    "settings.other_schedule": (
        "O agendamento usa outro arquivo de configuração, {other}. Nada foi alterado no "
        "launchd: marque “Usar este arquivo para o agendamento” para trazê-lo para cá."
    ),
    "settings.saved": "Salvo.",
    "settings.restore_link": "Restaurar as configurações anteriores…",
    "settings.what_changed": "O que mudou",
    "settings.label.language": "Idioma",
    "settings.label.inputs": "Pastas de entrada",
    "settings.label.output_root": "Pasta da biblioteca",
    "settings.label.review_folder": "Pasta de revisão",
    "settings.label.duplicates_folder": "Pasta de duplicados",
    "settings.label.duplicates": "Duplicados",
    "settings.label.provider.name": "Quem classifica",
    "settings.label.provider.model": "Modelo",
    "settings.label.vision.enabled": "Imagens para o modelo (visão)",
    "settings.label.ocr.enabled": "Texto em imagens (OCR)",
    "settings.label.ocr.languages": "Idiomas do reconhecimento de texto",
    "settings.label.search.semantic.enabled": "Busca por significado",
    "settings.label.limits.max_cost_usd_per_run": "Limite de custo por execução",
    "settings.label.limits.max_cost_usd_per_day": "Limite de custo diário",
    "settings.label.summary.notify": "Notificação de resumo",
    "settings.label.due.remind": "Lembretes de vencimento",
    "settings.label.due.remind_days_before": "Lembrete, dias antes",
    "settings.label.due.kinds": "Tipos na lista de Vencimentos",
    "settings.label.schedule.mode": "Quando organizar",
    "settings.label.schedule.time": "Execução diária às",
    "settings.label.categories": "Categorias",
    "settings.field.categories.name": "Nome da categoria",
    "settings.field.categories.hint": "Dica da categoria",
    "settings.field.categories.layout": "Subpastas",
    "settings.field.inputs.path": "Pasta de entrada",
    "settings.invalid.labelled": "{label} ({where}): {why}",
    "settings.invalid.labelled_only": "{label} ({where})",
    "settings.invalid.where": "{where}: {why}",
    "settings.entry.category": "Categoria {name}",
    "settings.entry.input": "Pasta de entrada {name}",
    "settings.entry.category_field": "Categoria {name}, {field}",
    "settings.entry.input_field": "Pasta de entrada {name}, {field}",
    "settings.change.added": "adicionada",
    "settings.change.removed": "removida",
    "settings.change.renamed": "renomeada para {name}",
    "settings.change.value": "{old} → {new}",
    "settings.change.language_default": "de volta ao padrão do idioma",
    "settings.change.written": "escritas no arquivo, como estavam",
    "settings.value.default": "o padrão",
    "settings.value.none": "nenhum",
    "settings.value.on": "ligado",
    "settings.value.off": "desligado",
    "settings.address_in_file": "um endereço no arquivo",
    "settings.provider.anthropic": "API da Anthropic",
    "settings.provider.claude_code": "Sua assinatura do Claude (Claude Code)",
    "settings.provider.ollama": "Ollama, neste Mac",
    "settings.provider.openai_compat": "API compatível com OpenAI em {host}",
    "settings.provider.fake": "O provedor de teste (nada é enviado)",
    "settings.provider.choice": "{provider}: {model}",
    "settings.provider.field": "Provedor e modelo",
    "settings.provider.each": "O que cada opção envia",
    "settings.provider.help": (
        "Outro servidor ou modelo, uma chave ou as outras opções do provedor ficam no arquivo "
        "(`flib key set` para a chave). `flib reprocess` classifica de novo os arquivos já "
        "organizados."
    ),
    "settings.leaves.anthropic": (
        "O nome, os detalhes e até {chars} caracteres do texto de cada arquivo vão para a API "
        "da Anthropic."
    ),
    "settings.leaves.claude_code": (
        "O nome, os detalhes e até {chars} caracteres do texto de cada arquivo vão para a "
        "Anthropic pela sua assinatura do Claude."
    ),
    "settings.leaves.remote": (
        "O nome, os detalhes e até {chars} caracteres do texto de cada arquivo vão para {host}."
    ),
    "settings.leaves.local": "Nada sai deste Mac: o modelo roda aqui.",
    "settings.leaves.none": "Nada sai deste Mac.",
    "settings.key.anthropic": "Chave de API da Anthropic",
    "settings.key.openai_compat": "Chave de API do openai-compat",
    "settings.key.embeddings": "Chave de API dos embeddings",
    "settings.key.not_set": (
        "{label}: não definida. `flib key set` no Terminal salva a chave no Keychain."
    ),
    "settings.key.set": "{label}: definida ({where}).",
    "settings.key.where.environment": "no ambiente",
    "settings.key.where.keychain": "no Keychain",
    "settings.key.where.env_file": "no arquivo indicado em provider.env_file",
    "settings.folder.downloads": "Downloads",
    "settings.folder.desktop": "Mesa",
    "settings.folder.downloads_note": "Tudo o que estiver no primeiro nível.",
    "settings.folder.desktop_note": Plural(
        zero="Só documentos e imagens; as pastas ficam como estão, como o `flib init` configura.",
        one=(
            "Só documentos e imagens sem alteração há {days} dia; as pastas ficam como estão, "
            "como o `flib init` configura."
        ),
        other=(
            "Só documentos e imagens sem alteração há {days} dias; as pastas ficam como estão, "
            "como o `flib init` configura."
        ),
        by="days",
    ),
    "settings.why.plain": "não é um caminho de pasta simples",
    "settings.why.missing": "essa pasta não existe",
    "settings.why.link": "é um link para outro lugar",
    "settings.why.not_folder": "não é uma pasta",
    "settings.why.broad": "é abrangente demais",
    "settings.why.outside": "fica fora da sua pasta pessoal",
    "settings.why.hidden": "está oculta",
    "settings.why.system": "é uma pasta do sistema",
    "settings.why.not_yours": "não é uma pasta sua",
    "settings.why.library": "é a biblioteca, ou fica dentro dela",
    "settings.why.already": "já é uma pasta de entrada",
    "settings.inputs.cannot_add": "{folder}: não pode ser adicionada, {why}.",
    "settings.inputs.not_added": "{folder} não foi adicionada: {why}.",
    "settings.inputs.stop": "Parar de organizar…",
    "settings.inputs.stop_named": "Parar de organizar {name}…",
    "settings.inputs.add": "Organizar {folder} também",
    "settings.inputs.dup_move": "Separar em {folder}",
    "settings.inputs.dup_leave": "Deixar onde estão",
    "settings.inputs.help": "O flib lê só o primeiro nível de cada pasta de entrada.",
    "settings.inputs.library": "Biblioteca",
    "settings.inputs.help_file": (
        "Outra pasta, as regras de uma pasta ou o lugar da biblioteca se alteram no arquivo; "
        "os arquivos já organizados ficam onde estão."
    ),
    "settings.inputs.dup_legend": "Arquivos com o mesmo conteúdo de um já organizado",
    "settings.inputs.last": "Não salvo: o flib precisa de pelo menos uma pasta de entrada.",
    "settings.rules.summary": "Regras",
    "settings.rules.only": "só {patterns}",
    "settings.rules.ignore": "ignora {patterns}",
    "settings.rules.ignore_own": "ignora {patterns} ({own} só desta pasta)",
    "settings.rules.min_age": Plural(
        zero="de qualquer idade",
        one="só o que não muda há {days} dia",
        other="só o que não muda há {days} dias",
        by="days",
    ),
    "settings.rules.folders.as_unit": "subpastas: tratadas como um item só",
    "settings.rules.folders.ignore": "subpastas: ignoradas",
    "settings.rules.duplicates.move": "duplicados: separados",
    "settings.rules.duplicates.leave": "duplicados: deixados no lugar",
    "settings.rules.fixed_renamed": (
        "categoria fixa {category} (sem IA), renomeados pelo naming.pattern"
    ),
    "settings.rules.fixed_kept": "categoria fixa {category} (sem IA), nomes mantidos",
    "settings.rules.fixed_layout_renamed": (
        "categoria fixa {category} (sem IA), subpastas {layout}, renomeados pelo naming.pattern"
    ),
    "settings.rules.fixed_layout_kept": (
        "categoria fixa {category} (sem IA), subpastas {layout}, nomes mantidos"
    ),
    "settings.rules.ai": "classificados pela IA",
    "settings.schedule.unreadable": "Não foi possível ler o estado do launchd agora.",
    "settings.schedule.not_loaded": (
        "O agendamento está gravado, mas não carregado: salve de novo."
    ),
    "settings.schedule.other": (
        "O agendamento instalado usa outro arquivo de configuração, {other}. Salvar aqui não "
        "muda nada no launchd, a menos que você marque a opção abaixo: aí ele passa a usar "
        "este arquivo."
    ),
    "settings.schedule.takeover": "Usar este arquivo para o agendamento",
    "settings.schedule.stale_watch": (
        "O modo de observação está de olho em pastas diferentes das pastas de entrada: salve "
        "de novo."
    ),
    "settings.schedule.mode.off": "Desligado: só quando você rodar",
    "settings.schedule.mode.daily": "Todo dia, no horário abaixo",
    "settings.schedule.mode.watch": "Quando chegarem arquivos",
    "settings.schedule.mode.both": "Quando chegarem arquivos, e todo dia no horário abaixo",
    "settings.schedule.legend": "Rodar o flib",
    "settings.schedule.time": "Execução diária às (24 horas)",
    "settings.schedule.save": "Salvar o agendamento",
    "settings.schedule.help": (
        "Um agente do launchd roda o flib, como o `flib schedule` configura. Se o Mac estiver "
        "dormindo no horário, ele roda quando o Mac acordar. No modo “quando chegarem "
        "arquivos”, os lembretes e o resumo periódico só vêm depois de uma execução diária."
    ),
    "settings.schedule.remove_failed": (
        "Não foi possível remover o agendamento; `flib schedule --remove` no Terminal diz por quê."
    ),
    "settings.schedule.off": "O agendamento está desligado: o flib só roda quando você rodar.",
    "settings.schedule.bad_time": (
        "Não salvo: o horário da execução diária precisa estar no formato de 24 horas, como 12:30."
    ),
    "settings.schedule.refused": (
        "O launchd do macOS não aceitou o agendamento. {now} `flib schedule` no Terminal diz "
        "por quê."
    ),
    "settings.schedule.refused_saved": (
        "O launchd do macOS não aceitou o agendamento (as configurações foram salvas). {now} "
        "`flib schedule` no Terminal diz por quê."
    ),
    "settings.menu.shown": (
        "Barra de menus: o gato está lá. `flib menu --remove` remove o gato de lá."
    ),
    "settings.menu.hidden": (
        "Barra de menus: não aparece. `flib menu` coloca o gato lá, com o que precisa de você."
    ),
    "settings.now.unreadable": "Não foi possível ler o estado do launchd agora.",
    "settings.now.none": "Nada está agendado agora.",
    "settings.now.loaded": "Carregado e rodando agora: {what}.",
    "settings.now.written": "Gravado, mas não carregado, então não está rodando: {what}.",
    "settings.now.what_at": "{mode} às {time}",
    "settings.mode.daily": "diário",
    "settings.mode.watch": "observação",
    "settings.mode.both": "diário e observação",
    "settings.scheduled.daily": (
        "Execução diária agendada para as {time}. Se o Mac estiver dormindo nessa hora, ela "
        "roda quando ele acordar."
    ),
    "settings.scheduled.watch": Plural(
        zero="Modo de observação ligado, mas não há nenhuma pasta de entrada para observar.",
        one=(
            "Modo de observação ligado: o flib roda quando algo chega em {count} pasta de "
            "entrada, depois que os downloads terminam."
        ),
        other=(
            "Modo de observação ligado: o flib roda quando algo chega nas {count} pastas de "
            "entrada, depois que os downloads terminam."
        ),
    ),
    "settings.scheduled.both": Plural(
        zero=(
            "Modo de observação ligado, mas não há nenhuma pasta de entrada para observar. E todo "
            "dia às {time}."
        ),
        one=(
            "Modo de observação ligado: o flib roda quando algo chega em {count} pasta de "
            "entrada, depois que os downloads terminam. E todo dia às {time}."
        ),
        other=(
            "Modo de observação ligado: o flib roda quando algo chega nas {count} pastas de "
            "entrada, depois que os downloads terminam. E todo dia às {time}."
        ),
    ),
    "settings.note": "Observação",
    "settings.notes.dev": (
        "O flib está rodando de uma pasta de desenvolvimento: o agendamento depende dela. "
        "Instale com `./scripts/install.sh` e salve o agendamento de novo."
    ),
    "settings.notes.no_key": (
        "Nenhuma chave de API definida: a execução agendada vai falhar até você rodar "
        "`flib key set`."
    ),
    "settings.notes.env_key": (
        "A chave de API está só no ambiente deste Terminal, que a execução agendada não "
        "enxerga: `flib key set` salva a chave no Keychain."
    ),
    "settings.notes.claude": (
        "A execução agendada não vai encontrar o Claude Code: `flib schedule` no Terminal diz "
        "o que acrescentar ao arquivo."
    ),
    "settings.notes.notifier": (
        "As notificações vão aparecer como Editor de Scripts; `flib schedule --refresh` tenta "
        "de novo."
    ),
    "settings.notes.doctor": (
        "`flib doctor` confere se a execução agendada consegue ler as pastas."
    ),
    "settings.watch.name": "Modo de observação",
    "settings.watch.other": "não mudou: o agendamento usa outro arquivo de configuração.",
    "settings.watch.failed": (
        "continua de olho nas pastas antigas: o launchd não aceitou a mudança. "
        "`flib schedule --refresh` no Terminal tenta de novo e diz por quê."
    ),
    "settings.watch.done": "agora observa estas pastas.",
    "settings.menu.name": "Barra de menus",
    "settings.menu.done": "refeita no novo idioma.",
    "settings.menu.failed": (
        "ainda no idioma anterior: `flib menu --refresh` no Terminal tenta de novo e diz por quê."
    ),
    "settings.reading.this_mac": "este Mac",
    "settings.reading.vision": "Enviar imagens para o modelo (visão)",
    "settings.reading.vision_help": (
        "Imagens (reduzidas, sem a localização) e as primeiras páginas de PDFs digitalizados "
        "vão para o modelo junto com o arquivo. Desligado, eles são classificados pelo nome e "
        "pelo texto."
    ),
    "settings.reading.vision_help_claude": (
        "Imagens (reduzidas, sem a localização) e as primeiras páginas de PDFs digitalizados "
        "vão para o modelo junto com o arquivo. Desligado, eles são classificados pelo nome e "
        "pelo texto. Não tem efeito com a sua assinatura do Claude."
    ),
    "settings.reading.ocr": "Ler texto em imagens e PDFs digitalizados (OCR)",
    "settings.reading.ocr_help": (
        "Neste Mac, com o reconhecimento de texto do próprio macOS; nada é enviado para isso."
    ),
    "settings.reading.semantic": "Busca por significado",
    "settings.reading.semantic_help": (
        "Nomes, tags, descrições e categorias (nunca o conteúdo dos arquivos) vão para {where} "
        "para gerar os embeddings. `flib search --reindex` gera os embeddings do que já está "
        "organizado."
    ),
    "settings.layout.none": "Tudo na pasta da categoria",
    "settings.layout.year": "Por ano",
    "settings.layout.year_month": "Por ano, depois mês",
    "settings.layout.issuer": "Por emissor",
    "settings.layout.issuer_year": "Por emissor, depois ano",
    "settings.layout.as_file": "Como está no arquivo: {layout}",
    "settings.hint.label": "Dica para a IA",
    "settings.hint.kept": (
        "Ela tem quebras de linha ou é longa, então fica como está aqui; altere no arquivo de "
        "configuração."
    ),
    "settings.hint.field": "Dica para a IA (o que vai aqui)",
    "settings.category.subfolders": "subpastas {layout}",
    "settings.category.names_kept": "nomes mantidos",
    "settings.category.change": "Alterar",
    "settings.category.name": "Nome",
    "settings.category.layout": "Subpastas",
    "settings.category.rename": "Renomear arquivos com o padrão de nomes",
    "settings.category.remove": "Remover esta categoria",
    "settings.category.remove_named": "Remover a categoria {name}",
    "settings.category.no_hint": "sem dica",
    "settings.category.add": "Adicionar uma categoria",
    "settings.category.new_name": "Nome (um nome de pasta)",
    "settings.category.new_hint": "Dica para a IA (opcional)",
    "settings.category.add_button": "Adicionar",
    "settings.category.help": (
        "Cada uma é uma pasta na biblioteca. A dica diz à IA o que vai ali. Uma categoria com "
        "arquivos é renomeada ou removida com `flib reprocess`, que move os arquivos; a "
        "página explica como."
    ),
    "settings.category.needs_name": "Não salvo: uma categoria precisa de um nome.",
    "settings.category.last": "Não salvo: o flib precisa de pelo menos uma categoria.",
    "settings.category.not_renamed": (
        "{name} não foi renomeada: {why}. Renomear significa mover esses arquivos: troque o "
        "nome no arquivo de configuração e depois rode {command} no Terminal."
    ),
    "settings.category.not_removed": (
        "{name} não foi removida: {why}. Remova a categoria no arquivo de configuração e "
        "depois rode {command} no Terminal: ele classifica esses arquivos de novo "
        "(`--to-review` manda os arquivos para a revisão)."
    ),
    "settings.holds.input": "a pasta de entrada {path} manda tudo para ela",
    "settings.holds.index": Plural(
        zero="o índice não tem nenhum arquivo nela",
        one="o índice tem {count} arquivo nela",
        other="o índice tem {count} arquivos nela",
    ),
    "settings.holds.not_empty": "a pasta dela não está vazia",
    "settings.holds.unreadable": "não foi possível ler a pasta dela",
    "settings.holds.index_error": "Não salvo: {problem}; nada mudou.",
    "settings.field.too_long": "Não salvo: {label} tem mais de {limit} caracteres.",
    "settings.field.invisible": (
        "Não salvo: {label} tem caracteres invisíveis, que não são aceitos."
    ),
    "settings.what.name": "o nome",
    "settings.what.hint": "a dica",
    "settings.costs.default": "Três vezes o limite por execução",
    "settings.costs.none": "Sem limite diário",
    "settings.costs.amount": "Este valor:",
    "settings.costs.help": (
        "Quanto as chamadas ao provedor podem custar, pela estimativa do flib. Os arquivos que "
        "sobrarem esperam a próxima execução. O limite diário vale para as execuções "
        "agendadas."
    ),
    "settings.costs.per_run": "Por execução, US$ (vazio: sem limite)",
    "settings.costs.per_day": "Por dia",
    "settings.costs.day_amount": "Limite diário, US$",
    "settings.costs.no_prices": (
        "{provider} num servidor remoto não tem preços (provider.price_input_per_mtok, "
        "price_output_per_mtok), então nenhum limite de custo consegue pará-lo; {limit}."
    ),
    "settings.costs.unlimited": "nada limita as execuções automáticas",
    "settings.costs.files_cap": Plural(
        zero="as execuções automáticas não veem nenhum arquivo por dia (limits.max_files_per_day)",
        one=(
            "as execuções automáticas veem no máximo {files} arquivo por dia "
            "(limits.max_files_per_day)"
        ),
        other=(
            "as execuções automáticas veem no máximo {files} arquivos por dia "
            "(limits.max_files_per_day)"
        ),
        by="files",
    ),
    "settings.costs.bad_amount": (
        "Não salvo: {what} precisa ser um valor em US$ acima de 0 e de no máximo {max}, como 1,50."
    ),
    "settings.costs.ambiguous": (
        "Não salvo: {what} “{typed}” pode ser lido de dois jeitos. Digite sem o ponto para "
        "um número inteiro, ou com vírgula para os centavos, como 1,50."
    ),
    "settings.costs.what_run": "o limite por execução",
    "settings.costs.what_day": "o limite diário",
    "settings.reminders.notify": "Me avisar com o resumo periódico",
    "settings.reminders.notify_help": (
        "Uma notificação do macOS só com contagens, nunca nomes de arquivos, quando houver "
        "arquivos esperando."
    ),
    "settings.reminders.remind": "Contar no resumo os documentos que vão vencer",
    "settings.reminders.days": "Dias antes do vencimento",
    "settings.reminders.kinds": "Tipos de documento na lista de Vencimentos",
    "settings.reminders.bad_days": (
        "Não salvo: o número de dias antes do vencimento precisa ser de 0 a 60."
    ),
    "settings.language.field": "Idioma da interface, dos nomes e das descrições",
    "settings.language.help": (
        "É o idioma desta interface e da barra de menus, e dos nomes e descrições que o flib "
        "escreve. Os nomes de pastas e categorias continuam como estão: se vieram do idioma, a "
        "página os escreve no arquivo antes."
    ),
    "settings.file.where": (
        "O arquivo de configuração: {path}. O `config.example.yaml` do repositório lista "
        "todas as opções."
    ),
    "settings.file.check": (
        "O `flib check-config` confere o arquivo, e o `flib doctor` confere o flib como um "
        "todo (acesso às pastas, o agendamento, a chave)."
    ),
    "settings.file.open": "Abrir num editor de texto",
    "settings.file.missing": "Não há arquivo de configuração nesse lugar.",
    "settings.file.unreadable": "Não foi possível ler o arquivo de configuração.",
    "settings.file.not_utf8": "O arquivo de configuração não é texto UTF-8.",
    "settings.file.cannot_change": (
        "O arquivo de configuração não pode ser alterado aqui: {problem}"
    ),
    "settings.file.not_saved": "Não salvo: {error}.",
    "settings.file.write_failed": (
        "Não foi possível gravar o arquivo de configuração; nada mudou."
    ),
    "settings.file.edit_failed": (
        "Não salvo: {error}. Faça essa alteração no arquivo de configuração; nada foi gravado."
    ),
    "settings.file.invalid": "Não salvo, nada foi gravado: {problem}.",
    "settings.file.not_plain": "O arquivo de configuração não é um arquivo comum.",
    "settings.file.open_failed": "O macOS não conseguiu abri-lo.",
    "settings.file.opened": (
        "Aberto no seu editor de texto. Recarregue esta página depois de salvar lá; o "
        "`flib check-config` confere o arquivo."
    ),
    "settings.remove.title": "Parar de organizar uma pasta",
    "settings.remove.short": "Parar de organizar",
    "settings.remove.cannot": "Essa pasta não pode ser removida daqui agora.",
    "settings.remove.lead": (
        "O flib deixa de pegar arquivos de {folder}. Nada lá dentro é movido; o que o flib já "
        "organizou fica na biblioteca. A entrada da pasta, as regras dela e o comentário acima "
        "saem do arquivo de configuração (um backup é guardado)."
    ),
    "settings.remove.button": "Parar de organizar",
    "settings.restore.title": "Restaurar as configurações anteriores",
    "settings.restore.short": "Restaurar",
    "settings.restore.none": ("Não há backup para restaurar: nada foi alterado por aqui ainda."),
    "settings.restore.unusable": "O backup mais recente não pode ser usado: {problem}",
    "settings.restore.lead": (
        "Isto volta o arquivo de configuração para como estava antes da última alteração "
        "feita aqui (um backup de {stamp}). Antes, o arquivo atual vira um backup, então dá "
        "para desfazer do mesmo jeito. O agendamento no launchd não muda: salve de novo na "
        "página Configurações, se precisar."
    ),
    "settings.restore.warning": (
        "O arquivo inteiro volta: comentários e alterações feitas à mão desde esse backup se "
        "perdem (o arquivo atual fica guardado num backup)."
    ),
    "settings.restore.what": "O que muda",
    "settings.restore.same": (
        "O backup tem as mesmas configurações; só mudam comentários ou a formatação."
    ),
    "settings.restore.button": "Restaurar estas configurações",
    "settings.restore.earlier": "antes",
    "settings.restore.changed": (
        "O backup mudou desde que a página foi aberta; nada foi restaurado."
    ),
    "settings.restore.backup_unusable": "O backup não pode ser usado: {problem}",
    "settings.restore.done": "As configurações anteriores estão de volta.",
}
