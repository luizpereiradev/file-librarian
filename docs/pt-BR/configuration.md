# Configuração

Tudo o que dá para ajustar fica em um único arquivo YAML,
`~/.config/file-librarian/config.yaml` (ou o caminho em `$FLIB_CONFIG`, ou
`--config PATH`). O `flib init` escreve uma versão curta; o
[`config.example.yaml`](#every-option) comentado, no fim desta página, lista
todas as opções com o valor padrão. Só `provider.model` é obrigatório; todo o
resto tem padrão. O `flib check-config` valida o arquivo, e um arquivo inválido
interrompe a execução antes de qualquer arquivo ser tocado. Chaves de API nunca
vão neste arquivo. A [página Configurações](daily-use.md#the-interface) da
interface altera o essencial para você, com as mesmas verificações.

## As opções que você mais deve querer mudar { #the-options-you-are-most-likely-to-change }

| Opção | Padrão | O que faz |
| --- | --- | --- |
| `language` | `en` | `en` ou `pt-BR`: o idioma dos nomes e descrições gerados, das pastas padrão (`Organized`/`_Review` ou `Organizado`/`_Revisar`) e das categorias, da interface web (`flib ui`), do item da barra de menus, das notificações e do que a linha de comando mostra para você ([Idioma](https://luizpereiradev.github.io/file-librarian/configuration/#language), em inglês) |
| `inputs` | `[~/Downloads]` | As pastas a manter organizadas; cada uma pode ter regras próprias ([Mais de uma caixa de entrada](#more-than-one-inbox)) |
| `output_root` | `~/Downloads/Organized` | Onde as pastas de categoria são criadas |
| `categories` | 5 em `en`, 15 em `pt-BR` | A lista fechada da qual a IA escolhe; cada `hint` orienta a escolha, `rename: false` mantém os nomes originais, `layout` distribui em subpastas ([Subpastas](#sub-folders-by-year-or-issuer)) |
| `never_send` | `*.pem`, `*.key`, `.env*` no exemplo | Padrões classificados só pelos metadados ([Arquivos sensíveis](privacy.md#sensitive-files-and-never_send)) |
| `limits.confidence_threshold` | `0.7` | Abaixo disso, o arquivo vai para revisão |
| `limits.max_cost_usd_per_run`, `max_cost_usd_per_day`, `max_files_per_run`, `max_files_per_day` | veja [Custos e limites](providers.md#costs-and-caps) | Quanto uma execução, e um dia de execuções automáticas, pode gastar e enviar |
| `naming.pattern` | `{date}_{subject}` | Nomes novos; veja [Nomes](#names) |
| `duplicates` | `move` | `leave` deixa as cópias onde estão ([Duplicados](#duplicates)) |
| `finder_tags.enabled` | `true` | Marca cada item arquivado com a categoria como tag no Finder ([Tags do Finder](#finder-tags)) |
| `ocr.enabled` | `true` | Lê o texto de imagens e PDFs escaneados no seu Mac ([OCR](#text-in-images-and-scanned-pdfs-ocr)) |
| `vision.enabled` | `false` | Deixa o modelo olhar imagens e PDFs escaneados ([Visão](#images-and-scanned-pdfs-vision)) |
| `schedule.time` | `12:30` | Quando a execução diária começa (aplicado pelo `flib schedule`) |
| `schedule.mode` | `daily` | `watch` roda conforme os arquivos chegam, `both` faz as duas coisas ([Modo watch](daily-use.md#or-as-files-arrive-watch-mode)) |
| `summary.notify` | `true` | A notificação periódica sobre arquivos esperando por você ([O que espera por você](daily-use.md#what-waits-for-you)) |
| `search.semantic.enabled` | `false` | [Busca por significado](daily-use.md#search-by-meaning-optional) |
| `mcp.read_only` | `false` | Oferece só as ferramentas de leitura no `flib mcp` ([MCP](mcp.md)) |
| `privacy.descriptions` | `redact` | `verbatim` guarda descrições e tags exatamente como a IA escreveu ([O que o índice guarda](privacy.md#what-the-index-stores)) |

## Tags do Finder { #finder-tags }

Cada item movido para uma pasta de categoria também recebe o nome da
categoria como tag do Finder, então a barra lateral do Finder e o Spotlight
(`tag:Finance`) também o encontram. Suas próprias tags são mantidas, e o
`flib undo` remove só a tag que o flib adicionou.

## Mais de uma caixa de entrada { #more-than-one-inbox }

`inputs` aceita caminhos simples, ou um mapeamento com regras só para aquela
pasta. A Mesa guarda trabalho em andamento, então seja conservador ali; uma
pasta de capturas de tela pode ir direto para uma categoria, sem nenhuma
chamada à IA:

```yaml
inputs:
  - ~/Downloads                       # tudo no nível de cima, como antes
  - path: ~/Desktop
    only: ["*.pdf", "*.png", "Screenshot*"]   # todo o resto fica intocado
    min_age_days: 7                   # só o que não mudou há uma semana
    folders: ignore
  - path: ~/Pictures/Screenshots     # uma pasta só para capturas de tela
    category: Screenshots             # uma categoria configurada; sem IA, sem custo
    layout: "{year}/{month}"
    only: ["Screenshot*.png", "Screen Recording*.mov"]
    folders: ignore
```

Uma categoria fixa arquiva tudo o que pega sem olhar o conteúdo, então dê a
ela um `only` (o `flib check-config` avisa quando não há).

As opções são `only`, `ignore` (somado à lista global), `min_age_days`, uma
`category` fixa (com `rename` e `layout`), `folders` e `duplicates`. O
`flib check-config` mostra cada entrada com as regras que ela acaba tendo. As
entradas não podem se sobrepor nem ficar dentro de `output_root`. O
`flib init` oferece a Mesa (documentos e imagens intocados há uma semana,
pastas deixadas de lado) e a sua pasta de capturas de tela quando ela não
guarda mais nada; as duas vêm com "não" como padrão. Uma pasta de entrada que
não pode ser lida é pulada com um aviso; as outras continuam sendo
organizadas.
Cada pasta é uma permissão separada do macOS para a execução diária, que o
`flib schedule` verifica uma por uma. Os detalhes, inclusive como a idade é
contada, estão
[no guia](https://luizpereiradev.github.io/file-librarian/guide/#more-than-one-inbox) (em inglês).

## Nomes { #names }

Um arquivo só é renomeado quando o modelo leu o conteúdo dele (texto, texto
[reconhecido por OCR](#text-in-images-and-scanned-pdfs-ocr), ou a imagem com a
visão ligada), a categoria permite renomear, e o modelo tem pelo menos
`naming.rename_confidence_threshold` de certeza (0.8 por padrão). Vídeos,
instaladores, arquivos compactados, pastas e fotos com a visão desligada
mantêm o nome. Um nome genérico (`scan001.pdf`, `IMG_2041.jpg`) é
substituído; um descritivo mantém suas palavras:

```text
Study guide - 11 tweaks for coding agents.pdf  ->  2026-11-16_study-guide-11-tweaks-for-coding-agents.pdf
scan001.pdf                                    ->  2026-10-05_electricity-bill.pdf
```

É o flib, e não o modelo, que monta o nome final a partir de
`naming.pattern` (`{date}_{subject}` por padrão);
[o guia](https://luizpereiradev.github.io/file-librarian/guide/#names) (em inglês)
traz as regras.

## Subpastas por ano ou emissor { #sub-folders-by-year-or-issuer }

Uma categoria é uma pasta única, sem subpastas, a menos que você dê a ela um
`layout`:

```yaml
categories:
  - name: Finance
    layout: "{year}"            # Finance/2026/…
  - name: Contracts
    layout: "{issuer}"          # Contracts/Acme/…
```

`{year}` e `{month}` vêm da data do documento (senão da data do download,
senão do dia em que foi arquivado); `{issuer}` é a organização de onde o
documento vem, com o nome que a IA dá a ela (o prompt pede uma organização,
não uma pessoa física; o próprio flib só descarta um nome que contenha um
telefone ou número de documento). A mesma empresa cai na mesma pasta:
`Acme Inc.`, `ACME` e `Acme Ltda` vão para a pasta que já existe, escrita como
foi da primeira vez (na pasta de cada ano, com `{year}/{issuer}`), enquanto
nomes diferentes ficam separados. Sem emissor, o arquivo para na pasta mais
funda conhecida (`Finance/2026/` para `{year}/{issuer}`). Para levar o que já
está organizado para um novo layout, sem a IA:

```sh
flib reprocess --apply-layout --simulate   # mostra o que seria movido
flib reprocess --apply-layout              # pergunta, depois move; flib undo desfaz
```

Arquivos que você mesmo moveu ficam onde você os colocou. Os detalhes estão
[no guia](https://luizpereiradev.github.io/file-librarian/guide/#sub-folders-inside-a-category) (em inglês).

## Duplicados { #duplicates }

Um arquivo com o mesmo conteúdo de um que o flib já arquivou (o segundo
`report (1).pdf`, ou um novo download de algo que está em `Finance`) é movido
para `_Duplicates` (`_Duplicados` em pt-BR) dentro da pasta organizada, com o
próprio nome. Ele nunca é apagado e não é enviado à IA, então não custa nada:

```text
  dup     report (1).pdf  ->  _Duplicates/report (1).pdf  (same content as Finance/report.pdf)
```

O `flib undo` devolve os duplicados como qualquer outra movimentação. O
`flib search` os deixa de fora, a menos que você adicione `--duplicates`.
Esvazie a pasta quando quiser: o flib nunca faz isso. `duplicates: leave`
desliga esse comportamento, e `duplicates_folder` renomeia a pasta.
[O guia](https://luizpereiradev.github.io/file-librarian/guide/#duplicates) (em inglês)
cobre os casos-limite, inclusive o que a primeira execução faz com cópias que
versões anteriores deixaram em Downloads.

## Texto em imagens e PDFs escaneados (OCR) { #text-in-images-and-scanned-pdfs-ocr }

Capturas de tela, recibos fotografados e PDFs escaneados não têm camada de
texto. O flib lê o texto deles no seu Mac com o reconhecimento de texto do
próprio macOS (o framework Vision, executado pelo `osascript`, que todo Mac
tem): nada é instalado, nada é enviado a lugar nenhum para isso, e funciona na
execução agendada. O texto reconhecido passa então a ser o texto do arquivo,
como o de qualquer documento: ele é verificado em busca de segredos (uma
captura de tela de uma senha ou de uma chave de API torna o arquivo sensível,
e nada é enviado), `never_send` e `provider.max_chars_per_file` se aplicam, e
o arquivo pode ser renomeado. Todo provedor se beneficia, inclusive os que só
leem texto.

```yaml
ocr:
  enabled: true              # ligado por padrão
  languages: [pt-BR, en-US]  # padrão vem de `language`: en dá [en-US]
  max_pages: 3               # primeiras páginas lidas de um PDF sem camada de texto (1-20)
  min_chars: 40              # menos letras e dígitos que isso: uma foto, não um documento
```

Uma imagem com menos texto que `min_chars` (uma foto) é tratada como antes:
com a [visão](#images-and-scanned-pdfs-vision) ligada o modelo a vê; caso
contrário, ela é classificada pelo nome e pelos metadados. O que acontece com
uma imagem com texto suficiente depende de ela ser ou não um documento:

- um PDF escaneado, ou uma imagem que é claramente um documento (pelo menos
  10 linhas e 300 letras e dígitos, cobrindo pelo menos um quinto da imagem:
  uma página escaneada ou fotografada), envia só o texto, mesmo com a visão
  ligada: é mais barato e passou pela verificação de segredos;
- qualquer outra imagem com palavras (um folheto, um pôster, um meme, uma
  captura de tela de conversa, uma foto com legenda, um cupom fiscal sobre a
  mesa) envia a imagem *e* o texto quando a visão está ligada: a imagem diz
  ao modelo o que é o arquivo, o texto o ajuda a ler as letras miúdas. Com a
  visão desligada, ou com um provedor que não lê imagens, o texto vai
  sozinho, mas o modelo é avisado de que ele foi encontrado em uma imagem,
  com o tamanho da imagem, quantas linhas e quanto dela é texto, para que um
  folheto seja julgado como folheto e não como um documento quebrado.

O reconhecimento leva cerca de um quarto de segundo por imagem ou página; o
primeiro em um Mac pode levar meio minuto enquanto o macOS prepara o modelo
dele. O `flib check-config` diz se o OCR funciona nesta máquina, e o
`flib inspect FILE` diz se o texto de um arquivo veio do OCR.

## Imagens e PDFs escaneados (visão) { #images-and-scanned-pdfs-vision }

Fotos não têm texto, então por padrão são classificadas só pelo nome e pelos
metadados, e mantêm o nome. Ligue a visão para deixar o modelo olhar para
elas:

```yaml
vision:
  enabled: true      # desligado por padrão
  max_image_px: 1568 # lado maior da imagem enviada, em pixels (256-2576)
  max_pdf_pages: 3   # primeiras páginas enviadas de um PDF sem camada de texto (1-20)
```

Imagens (`.jpg`, `.png`, `.heic`, `.webp`, `.gif`, `.tiff`, `.bmp`) são
convertidas para JPEG e reduzidas pelo `sips` do próprio macOS (nunca
ampliadas), e os metadados delas (EXIF, posição de GPS, dados da câmera) são
removidos antes do envio. Um PDF cujas páginas não têm texto tem as primeiras
`max_pdf_pages` páginas recortadas em um novo PDF, que é enviado como
documento. Como o modelo então vê o conteúdo, esses arquivos podem ser
renomeados como qualquer documento (`IMG_4512.HEIC` vira, por exemplo,
`2026-11-02_beach-at-sunset.HEIC`). Uma imagem com palavras vai junto com o
texto que o [OCR](#text-in-images-and-scanned-pdfs-ocr) encontrou nela, a
menos que seja claramente um documento, que vai só como texto. Se uma imagem
não puder ser convertida, ou o provedor não ler imagens, o arquivo é
classificado pelo texto reconhecido, se houver, ou pelos metadados, como com a
visão desligada.

O que cada provedor lê: `anthropic` lê imagens e PDFs escaneados; `ollama` e
`openai-compat` leem só imagens, e só com `provider.vision: true` (defina isso
quando o modelo for um modelo com visão); `claude-code` não lê nenhum dos
dois. Respostas dadas com visão ficam em cache separadas das respostas sem
ela, então ligar ou desligar a visão nunca reaproveita o outro tipo.

No modelo padrão, uma foto ou captura de tela custa cerca de US$ 0,0005 e um
escaneamento de três páginas cerca de US$ 0,001; veja
[Custos e limites](providers.md#costs-and-caps).

## Todas as opções { #every-option }

O exemplo comentado, como está no repositório: todas as opções, com o padrão
e o que cada uma faz. Os comentários do arquivo estão em inglês.

```yaml
--8<-- "config.example.yaml"
```
