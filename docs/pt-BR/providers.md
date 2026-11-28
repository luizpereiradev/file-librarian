# Provedores

`provider.name` escolhe quem classifica. Todos recebem o mesmo prompt e a
mesma validação: uma categoria fora da sua lista faz o arquivo falhar.
[Privacidade e segurança](privacy.md#what-leaves-your-mac) diz o que cada um
recebe.

| Provedor | Roda em | Chave | Custo | Lê imagens | Lê PDFs escaneados |
| --- | --- | --- | --- | --- | --- |
| `anthropic` (padrão) | a API da Anthropic | chave de API | por token, com limite | com vision | com vision |
| `claude-code` | sua assinatura do Claude, pelo `claude` | nenhuma | o uso do seu plano | não | não |
| `ollama` | um modelo neste Mac | nenhuma | nada | com `provider.vision` | não |
| `openai-compat` | qualquer servidor compatível com OpenAI | opcional | por token, aos preços que você informar | com `provider.vision` | não |

## Anthropic (`anthropic`, o padrão) { #anthropic-anthropic-the-default }

```yaml
provider:
  name: anthropic
  model: claude-haiku-5-5    # rápido e barato; claude-sonnet-5-5 ou claude-opus-5-5 para arquivos mais difíceis
```

`provider.api_key_source: auto` (o padrão) pega a chave do primeiro lugar
onde encontrar uma:

1. `ANTHROPIC_API_KEY` no ambiente;
2. o Keychain do macOS (`flib key set`; `flib key status` mostra qual está em
   uso);
3. o arquivo `.env` indicado em `provider.env_file`, se você definir um.

Execuções agendadas não veem o ambiente do seu shell, então o Keychain é a
opção mais fácil para elas. Defina `api_key_source` como `environment`,
`keychain` ou `env_file` para usar só um lugar. A chave nunca vai no arquivo
de configuração.

Cada provedor guarda a própria chave: `flib key set --provider openai-compat`
guarda a chave do `openai-compat` numa entrada separada do Keychain, então ela
nunca substitui a da Anthropic (`flib key set`, `status` e `delete` agem sobre
a chave do provedor configurado quando `--provider` não é informado).

## Sua assinatura do Claude (`claude-code`) { #your-claude-subscription-claude-code }

Se você já usa o Claude Code, o flib pode classificar por ele em vez da API,
sem chave de API:

```yaml
provider:
  name: claude-code
  model: haiku               # qualquer nome que `claude --model` aceite: haiku, sonnet, claude-sonnet-5-5...
```

`flib check-config` mostra qual `claude` ele encontrou e se há login feito
(se não houver, rode `claude` uma vez e entre com `/login`). Cada arquivo é
uma chamada `claude -p` com todas as ferramentas, servidores MCP, hooks,
skills e CLAUDE.md desligados, numa pasta temporária vazia, com um schema de
saída estruturada; só o mesmo prompt que o provedor `anthropic` envia sai do
seu Mac. `ANTHROPIC_API_KEY` e outros segredos do seu ambiente não são
repassados a ele.

Prós e contras:

- ele usa os limites de uso da sua assinatura; atingir um deles para a
  execução, e os arquivos restantes esperam a próxima;
- cada arquivo inicia um novo processo `claude`, então é mais lento que a API
  (alguns segundos por arquivo);
- `limits.max_cost_usd_per_run` se aplica ao custo que o Claude Code informa,
  uma estimativa pelos preços de tabela da API. Numa assinatura nada é
  cobrado por chamada, mas o valor ainda limita quanto do seu plano uma
  execução usa;
- `claude -p` aceita só texto, então `vision.enabled` não tem efeito com este
  provedor: PDFs escaneados e capturas de tela são classificados pelo texto
  que o [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr)
  reconhece no seu Mac, e fotos, pelos metadados.

Execuções agendadas têm um PATH mínimo. O flib também procura nas pastas de
instalação usuais (`~/.local/bin`, `~/.claude/local`, `/opt/homebrew/bin`,
`/usr/local/bin`); se o seu estiver em outro lugar, o `flib schedule` diz para
você definir `provider.command` com o caminho que ele encontrou no seu shell.

## Um modelo local com Ollama (`ollama`) { #a-local-model-with-ollama-ollama }

Com o [Ollama](https://ollama.com) rodando no seu Mac, os arquivos são
classificados por um modelo na sua própria máquina: nada sai dela, e não há
chave nem conta a pagar.

```yaml
provider:
  name: ollama
  model: gemma4:e4b          # qualquer modelo que você tenha baixado: `ollama pull gemma4:e4b`
  # base_url: http://localhost:11434/v1   # o padrão
  # timeout_seconds: 300     # o padrão para ollama: o primeiro arquivo também carrega o modelo
```

`flib init --local` configura isso para você, com o modelo que mediu melhor
num Mac de 16 GB ([Nada sai deste Mac](privacy.md#nothing-leaves-this-mac)).
`flib check-config` pede ao Ollama a lista de modelos (nenhum conteúdo de
arquivo é enviado) e avisa se ele não está rodando (`ollama serve`) ou se o
modelo não foi baixado. Modelos locais erram mais que o Claude e mandam pouco
para revisão: experimente `flib simulate` antes.

Para este provedor, o flib fala com a API própria do Ollama (`/api/chat`, ao
lado do `base_url` compatível com OpenAI), porque só ela aceita o que modelos
pequenos precisam: um contexto do tamanho do pedido (o padrão do Ollama
cortaria as instruções de um prompt longo), temperatura 0, raciocínio
(thinking) desligado e o JSON schema da resposta quando o modelo dá suporte.
Os modelos que não dão (as builds que o Ollama baixa por padrão no Apple
silicon respondem "structured output is unavailable") recebem o schema nas
instruções. Um JSON quase válido é consertado, e uma resposta com uma
categoria fora da lista é pedida mais uma vez; uma segunda resposta ruim faz
o arquivo falhar, como com qualquer provedor.

O que ser local muda, exatamente: os pedidos não são espaçados por
`provider.requests_per_minute`, e pedidos de loopback ignoram qualquer proxy
HTTP. Nada mais é relaxado: arquivos que casam com `never_send` continuam
enviando só os metadados, e arquivos com nomes de credenciais ou segredos
continuam nunca sendo enviados, mesmo com o modelo no seu Mac. Se você
apontar `base_url` para outra máquina, o flib a trata como remota (com limite
de taxa) e o `flib check-config` avisa que conteúdo de arquivo sai do seu Mac.

## Qualquer API compatível com OpenAI (`openai-compat`) { #any-openai-compatible-api-openai-compat }

Qualquer servidor que fale a API Chat Completions da OpenAI funciona: OpenAI,
OpenRouter, Groq, vLLM, LM Studio, o servidor do llama.cpp e outros.

```yaml
provider:
  name: openai-compat
  base_url: https://api.openai.com/v1   # obrigatório: a parte antes de /chat/completions
  model: gpt-5-mini
  price_input_per_mtok: 0.25            # US$ por milhão de tokens, da tabela de preços do seu provedor
  price_output_per_mtok: 2.00
  # api_key_env: OPENAI_API_KEY         # padrão: FLIB_OPENAI_COMPAT_API_KEY
  # vision: true                        # o modelo lê imagens (também exige vision.enabled)
```

- **Chave:** opcional (um servidor local normalmente não precisa). Ela vem da
  variável de ambiente `FLIB_OPENAI_COMPAT_API_KEY`, ou da que
  `provider.api_key_env` indicar, depois do Keychain
  (`flib key set --provider openai-compat`), depois de `provider.env_file`.
  `OPENAI_API_KEY` não é lida a menos que você a indique em `api_key_env`,
  então uma chave que você definiu para outras ferramentas nunca vai para um
  servidor com o qual você não a associou.
- **Custo:** o flib não tem como saber os preços do seu servidor. Com
  `price_input_per_mtok` e `price_output_per_mtok` deixados em 0, todo
  arquivo conta como grátis e **`limits.max_cost_usd_per_run` não consegue
  parar uma execução**; o `flib check-config` avisa sobre isso para um
  servidor remoto, e o custo da execução é mostrado e registrado como
  desconhecido (um servidor neste Mac conta como grátis).
  `limits.max_files_per_run` ainda limita quantos arquivos uma execução
  envia.
- **Respostas:** o flib pede saída estruturada com um JSON schema. Um
  servidor que recusa isso recebe o pedido de novo no modo JSON simples, com
  o schema nas instruções, e o flib continua usando o modo JSON pelo resto da
  execução.
- **Imagens:** enviadas só com `provider.vision: true` e `vision.enabled`.
  PDFs escaneados nunca são enviados a este provedor; eles são classificados
  pelo texto que o
  [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) reconhece no
  seu Mac.
- **Para onde os dados vão:** só para `base_url`. Redirecionamentos são
  recusados, nomes de usuário ou senhas na URL são rejeitados (use o
  Keychain), e o `flib check-config` avisa sobre `http://` para outra
  máquina.

## Custos e limites { #costs-and-caps }

Os custos são estimativas, registradas por execução (`flib run`,
`flib reprocess`; um `flib fix` não custa nada). O log de cada execução
termina com o custo estimado do provedor, e o `flib status` mostra o da última
execução e o do mês.

| Limite | Padrão | O que faz |
| --- | --- | --- |
| `limits.max_cost_usd_per_run` | `1.00` | Limite de custo estimado por execução; o resto espera a próxima execução (`null` = sem limite) |
| `limits.max_cost_usd_per_day` | 3 × o limite por execução | Limite de custo para as execuções automáticas do dia (daily e watch); execuções manuais não são limitadas por ele (`null` = sem limite) |
| `limits.max_files_per_run` | `200` | Arquivos enviados por execução |
| `limits.max_files_per_day` | 5 × `max_files_per_run` | Só para um provedor remoto sem preços: itens que as execuções automáticas do dia podem ver (`null` = sem limite) |

Uma execução cujos preços de provedor o flib não conhece (um servidor
`openai-compat` sem `price_input_per_mtok`/`price_output_per_mtok`) fica sem
custo registrado, em vez de custo zero; o `flib status` e a ferramenta MCP
`get_stats` contam essas execuções à parte. Duplicados e arquivos de uma
entrada com categoria fixa nunca são enviados, então não custam nada.

No modelo padrão, uma foto ou captura de tela custa cerca de US$ 0,0005 com
vision ligado, e um escaneamento de três páginas, cerca de US$ 0,001; então
cabem aproximadamente 2.000 fotos no limite padrão de US$ 1;
[o guia](https://luizpereiradev.github.io/file-librarian/guide/#what-vision-costs) (em inglês)
mostra a estimativa.

## Medindo a qualidade { #measuring-quality }

`flib eval` passa um corpus sintético de cerca de 150 downloads, em inglês e
português, pelo pipeline real numa pasta temporária (com qualquer provedor,
inclusive um modelo local do Ollama) e informa a precisão das categorias, a
taxa de revisão, com que frequência nomes descritivos são mantidos, a
velocidade e o custo. Ele nunca lê sua configuração nem toca na sua
biblioteca; com um provedor pago, mostra o custo estimado (alguns centavos de
dólar com o modelo padrão) e pergunta antes. `--compare` coloca duas
execuções lado a lado, para dois provedores ou duas versões de prompt.
[Medindo a qualidade](https://luizpereiradev.github.io/file-librarian/quality/) (em inglês)
tem as opções, o que cada número significa e as referências atuais.

Para adicionar um provedor, veja
[como adicionar um provedor](https://luizpereiradev.github.io/file-librarian/adding-a-provider/) (em inglês).
