# Video script (for maintainers)

The presentation video: seven scenes in about 75 seconds, for developers on
macOS, in English and in Brazilian Portuguese. It opens with the problem, not
with how flib is built. Everything on screen comes from the
[demo library](getting-started.md#try-it-on-a-demo-library): every file, name
and amount is invented.

The [demo walkthrough](demo-walkthrough.md) is the longer, purely technical
sequence. This script keeps fewer steps so that a voice can keep up with the
screen.

## The scenes

| Time | On screen | Narration (English) | Narration (Portuguese) |
| --- | --- | --- | --- |
| 0–6 s | The cluttered `before/Downloads` folder in Finder. Caption: "Where's that bill?" | Your Downloads folder. You know the bill is in there somewhere. | Sua pasta Downloads. Você sabe que o boleto está aí em algum lugar. |
| 6–18 s | Terminal: `flib run`. Highlight `scan_0007.pdf` becoming a pharmacy receipt in Finance | flib reads each new file, picks a category and a real name, and files it. The AI only decides. Plain code does the moving. | O flib lê cada arquivo novo, escolhe a categoria e um nome de verdade, e arquiva. A IA só decide. Quem move é código comum. |
| 18–28 s | `flib search --kind bill,invoice --sum --by issuer` | And it remembers what it read. Ask how much you paid, to whom, in which currency. | E ele lembra do que leu. Pergunte quanto você pagou, para quem, em qual moeda. |
| 28–36 s | `flib due`, then the menu bar image with the cat's paw raised | It knows what's due this week. The cat in your menu bar raises a paw when something needs you. | Ele sabe o que vence esta semana. O gato na barra de menus levanta a pata quando algo precisa de você. |
| 36–52 s | Browser, Review: **Accept Work for all 4…**, confirm; then **Undo**, confirm | When it isn't sure, it asks. One click accepts the whole group. One click undoes it. Nothing is deleted, nothing is overwritten. | Quando não tem certeza, ele pergunta. Um clique aceita o grupo inteiro. Um clique desfaz. Nada é apagado, nada é sobrescrito. |
| 52–64 s | `flib status`, the line about the file left in place as sensitive. Caption: "never sent". Cut to the site's Privacy page | Credentials never leave your Mac. flib spots them and leaves them alone. Want nothing to leave at all? Run it fully local with Ollama. | Credenciais nunca saem do seu Mac. O flib as reconhece e não toca nelas. Quer que nada saia? Rode tudo local, com Ollama. |
| 64–75 s | The three install commands, then the site and repository addresses with the icon | Open source, for macOS. Three commands to install. Link below. | Código aberto, para macOS. Três comandos para instalar. Link abaixo. |

The English narration is about 150 words: an unhurried pace for 75 seconds.

Left out on purpose: the architecture, the four providers and the
configuration. Someone interested follows the link; someone who did not see
their own folder in the first scene would not have stayed for SQLite.

## Recording

Before each take:

```sh
uv sync
uv run python -m scripts.demo /tmp/flib-demo --messy
```

Paste the three lines it prints (`DEMO_HOME=…`, `FLIB=…`, `CFG=…`) into the
terminal you will record, and this one, so that the commands on screen are
short:

```sh
flib() { HOME="$DEMO_HOME" "$FLIB" "$@" -c "$CFG"; }
```

- **The first scene** films the folder `--messy` adds, `before/Downloads`
  (the last line the script prints opens it in Finder). Its window is titled
  "Downloads" and it holds forty files named the way downloads arrive. flib
  never reads it: it sits outside the demo's own Downloads, so the counts of
  the later scenes do not change.
- **One take per scene**, not one long recording. The demo is rebuilt
  identical each time, so any scene can be redone.
- **The Portuguese version**: build with `--pt-BR` into another folder, and
  search for `fatura` where the English take searches for `invoice`.
- **The menu bar scene**: use `docs/assets/screenshots/en/menu.webp`, or the
  real item on your own Mac.
- **A large terminal font**, 18 pt or more, in a narrow window. A terminal
  nobody can read on a phone is the usual mistake.

!!! warning "Never install the menu bar item or a schedule from the demo"
    `flib menu` and `flib schedule` install LaunchAgents whose labels are the
    same as your real ones.

When done:

```sh
uv run python -m scripts.demo /tmp/flib-demo --clean
unset -f flib
```

## Two weak spots

- **The first scene carries the video.** If it does not make the viewer think
  "that is my folder", the rest does not matter. Hold it long enough to read
  three or four of the names.
- **The sixth scene is the most important and the least visual**: one line of
  text in a terminal saying a file stayed where it was. The caption does the
  work there.
