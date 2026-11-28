# Providers

`provider.name` picks who classifies. All of them get the same prompt and
the same validation: a category outside your list fails the file.
[Privacy and safety](privacy.md#what-leaves-your-mac) says what each one
receives.

| Provider | Runs | Key | Cost | Reads images | Reads scanned PDFs |
| --- | --- | --- | --- | --- | --- |
| `anthropic` (default) | Anthropic's API | API key | per token, capped | with vision | with vision |
| `claude-code` | your Claude subscription, through `claude` | none | your plan's usage | no | no |
| `ollama` | a model on this Mac | none | nothing | with `provider.vision` | no |
| `openai-compat` | any OpenAI-compatible server | optional | per token, at the prices you give | with `provider.vision` | no |

## Anthropic (`anthropic`, the default)

```yaml
provider:
  name: anthropic
  model: claude-haiku-5-5    # fast and cheap; claude-sonnet-5-5 or claude-opus-5-5 for harder files
```

`provider.api_key_source: auto` (the default) takes the key from the first
place it finds one:

1. `ANTHROPIC_API_KEY` in the environment;
2. the macOS Keychain (`flib key set`; `flib key status` shows which is used);
3. the `.env` file named by `provider.env_file`, if you set one.

Scheduled runs don't see your shell's environment, so the Keychain is the
easiest choice for them. Set `api_key_source` to `environment`, `keychain` or
`env_file` to use only one place. The key never goes in the configuration
file.

Each provider keeps its own key: `flib key set --provider openai-compat`
stores the `openai-compat` key in a separate Keychain entry, so it never
replaces the Anthropic one (`flib key set`, `status` and `delete` act on the
configured provider's key when `--provider` is not given).

## Your Claude subscription (`claude-code`)

If you already use Claude Code, flib can classify through it instead of the
API, with no API key:

```yaml
provider:
  name: claude-code
  model: haiku               # any name `claude --model` takes: haiku, sonnet, claude-sonnet-5-5...
```

`flib check-config` shows which `claude` it found and whether it is logged in
(if not, run `claude` once and sign in with `/login`). Each file is one
`claude -p` call with every tool, MCP server, hook, skill and CLAUDE.md turned
off, in an empty temporary folder, with a structured-output schema; only the
same prompt the `anthropic` provider sends leaves your Mac. `ANTHROPIC_API_KEY`
and other secrets in your environment are not passed to it.

Trade-offs:

- it uses your subscription's usage limits; reaching one stops the run, and
  the remaining files wait for the next one;
- each file starts a new `claude` process, so it is slower than the API
  (a few seconds per file);
- `limits.max_cost_usd_per_run` applies to the cost Claude Code reports, an
  estimate at API list prices. On a subscription nothing is charged per call,
  but the figure still caps how much of your plan a run uses;
- `claude -p` takes text only, so `vision.enabled` has no effect with this
  provider: scanned PDFs and screenshots are classified from the text
  [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) recognises on your Mac, and
  photos from their metadata.

Scheduled runs have a minimal PATH. flib also looks in the usual install
folders (`~/.local/bin`, `~/.claude/local`, `/opt/homebrew/bin`,
`/usr/local/bin`); if yours is elsewhere, `flib schedule` tells you to set
`provider.command` to the path it found in your shell.

## A local model with Ollama (`ollama`)

With [Ollama](https://ollama.com) running on your Mac, files are classified by
a model on your own machine: nothing leaves it, and there is no key and no bill.

```yaml
provider:
  name: ollama
  model: gemma4:e4b          # any model you have pulled: `ollama pull gemma4:e4b`
  # base_url: http://localhost:11434/v1   # the default
  # timeout_seconds: 300     # the default for ollama: the first file also loads the model
```

`flib init --local` sets this up for you, with the model measured best on a
16 GB Mac ([Nothing leaves this Mac](privacy.md#nothing-leaves-this-mac)). `flib
check-config` asks Ollama for its model list (no file content is sent) and
tells you if it is not running (`ollama serve`) or the model is not pulled.
Local models make more mistakes than Claude, and send too little to review:
try `flib simulate` first.

flib talks to Ollama's own API for this provider (`/api/chat`, next to the
OpenAI-compatible `base_url`), because only it takes what small models need:
a context sized to the request (Ollama's default would cut the instructions
off a long prompt), temperature 0, thinking off, and the answer's JSON schema
where the model supports it. Models that do not (the builds Ollama pulls by
default on Apple silicon answer "structured output is unavailable") get the
schema in the instructions instead. Near-valid JSON is repaired, and an
answer with a category outside the list is asked for once more; a second bad
answer fails the file, as with any provider.

What being local changes, precisely: requests are not spaced by
`provider.requests_per_minute`, and loopback requests skip any HTTP proxy.
Nothing else is relaxed: files matching `never_send` still send only their
metadata, and files with credential names or secrets in them are still never
sent at all, even though the model is on your Mac. If you point `base_url` at
another machine, flib treats it as remote (with rate limiting) and
`flib check-config` warns that file content leaves your Mac.

## Any OpenAI-compatible API (`openai-compat`)

Any server that speaks the OpenAI Chat Completions API works: OpenAI,
OpenRouter, Groq, vLLM, LM Studio, llama.cpp's server and others.

```yaml
provider:
  name: openai-compat
  base_url: https://api.openai.com/v1   # required: the part before /chat/completions
  model: gpt-5-mini
  price_input_per_mtok: 0.25            # US$ per million tokens, from your provider's price list
  price_output_per_mtok: 2.00
  # api_key_env: OPENAI_API_KEY         # default: FLIB_OPENAI_COMPAT_API_KEY
  # vision: true                        # the model reads images (also needs vision.enabled)
```

- **Key:** optional (a local server usually needs none). It comes from the
  environment variable `FLIB_OPENAI_COMPAT_API_KEY`, or the one
  `provider.api_key_env` names, then the Keychain (`flib key set --provider
  openai-compat`), then `provider.env_file`. `OPENAI_API_KEY` is not read
  unless you name it in `api_key_env`, so a key you set for other tools never
  goes to a server you did not pair it with.
- **Cost:** flib cannot know your server's prices. With
  `price_input_per_mtok` and `price_output_per_mtok` left at 0, every file
  counts as free and **`limits.max_cost_usd_per_run` cannot stop a run**;
  `flib check-config` warns about it for a remote server, and the run's cost
  is shown and recorded as unknown (a server on this Mac counts as free).
  `limits.max_files_per_run` still caps how many files a run sends.
- **Answers:** flib asks for structured output with a JSON schema. A server
  that rejects that is asked again with plain JSON mode and the schema in the
  instructions, and flib keeps using JSON mode for the rest of the run.
- **Images:** sent only with `provider.vision: true` and `vision.enabled`.
  Scanned PDFs are never sent to this provider; they are classified from the
  text [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) recognises on your Mac.
- **Where data goes:** only to `base_url`. Redirects are refused, user names
  or passwords in the URL are rejected (use the Keychain), and `flib
  check-config` warns about `http://` to another machine.

## Costs and caps

Costs are estimates, recorded per run (`flib run`, `flib reprocess`; a `flib
fix` costs nothing). Each run's log ends with its estimated provider cost, and
`flib status` shows the last run's and this month's.

| Cap | Default | What it does |
| --- | --- | --- |
| `limits.max_cost_usd_per_run` | `1.00` | Estimated cost cap per run; the rest waits for the next run (`null` = no cap) |
| `limits.max_cost_usd_per_day` | 3 × the per-run cap | Cost cap for the day's automatic runs (daily and watch); runs by hand are not limited by it (`null` = no cap) |
| `limits.max_files_per_run` | `200` | Files sent per run |
| `limits.max_files_per_day` | 5 × `max_files_per_run` | For a remote provider without prices only: items the day's automatic runs may see (`null` = no limit) |

A run whose provider prices flib does not know (an `openai-compat` server
without `price_input_per_mtok`/`price_output_per_mtok`) has no recorded cost
rather than a cost of zero; `flib status` and the MCP tool `get_stats` count
those runs apart. Duplicates and files of an input with a fixed category are
never sent, so they cost nothing.

On the default model a photo or screenshot costs about US$0.0005 with vision
on and a three-page scan about US$0.001, so roughly 2,000 photos fit in the
default US$1 cap; [the guide](guide.md#what-vision-costs) shows the estimate.

## Measuring quality

`flib eval` runs a synthetic corpus of about 150 downloads, in English and
Portuguese, through the real pipeline in a temporary folder (with any
provider, a local Ollama model included) and reports
category accuracy, the review rate, how often descriptive names are kept,
speed and cost. It never reads your configuration or touches your library;
with a paid provider it shows the estimated cost (a few US cents with the
default model) and asks first. `--compare` puts two runs side by side, for
two providers or two prompt versions. [Measuring quality](quality.md) has
the options, what each number means and the current baselines.

To add a provider, see [adding a provider](adding-a-provider.md).
