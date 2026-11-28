# Uso diário

```sh
flib simulate             # o plano para a sua pasta de verdade; nada se move
flib run                  # organiza agora
flib undo                 # desfaz a última execução (--run ID para outra, --file PATH para um arquivo)
flib search "power bill"  # encontra um arquivo de novo
flib status               # o que espera por você
flib due                  # contas que vencem esta semana, e o que está vencido
flib ui                   # o mesmo, no navegador
```

Os arquivos sobre os quais a IA não tem certeza vão para a pasta de revisão
(`_Review` dentro de `~/Downloads/Organized` por padrão; `_Revisar` numa
configuração em português) para você decidir.

## O que espera por você { #what-waits-for-you }

Quando o flib passa a rodar sozinho, os arquivos sobre os quais ele ficou em
dúvida esperam na pasta de revisão, e você só os vê se for olhar. O
`flib status` resume tudo sem citar nenhum arquivo:

```text
--8<-- "pt-BR/status.txt"
```

(A linha de comando ainda fala inglês; a interface já fala português.)

Ele só lê (sem lock, então funciona enquanto uma execução está em andamento, e
antes da primeira); `--json` dá o mesmo para scripts.

Você não precisa se lembrar de rodá-lo: depois de uma execução *agendada*,
quando um arquivo está esperando na revisão ou com falha há dois dias ou mais, o
flib escreve um resumo de uma linha no log da execução e mostra uma notificação
do macOS, como "3 files wait for review, 1 failed. Run `flib status` for
details." Ele faz isso no máximo uma vez por semana, e nunca depois de um
`flib run` que você mesmo iniciou. A notificação traz só contagens, nunca um
nome de arquivo, porque as notificações aparecem na tela de bloqueio. Ajuste em
`summary:` na configuração: `notify: false` mantém só a linha do log;
`every_days` e `min_age_days` definem a frequência e quanto ele espera.

A notificação vem de "file-librarian", com ícone próprio: o `flib schedule`
monta um pequeno app notificador para ela dentro do ambiente do próprio flib
(nunca em /Applications), com ferramentas que já vêm no macOS, e envia uma
primeira notificação para que o macOS pergunte nessa hora se você permite.
Permita (ou depois, em Ajustes do Sistema > Notificações > file-librarian). Se
o app não puder ser montado, as notificações chegam assim mesmo, mostradas como
Editor de Scripts; o `flib status` diz qual está em uso. Se a notificação nunca
aparecer, veja [Solução de problemas](troubleshooting.md#no-notification-appears).

## A interface { #the-interface }

```sh
flib ui                  # abre o navegador padrão
flib ui --open review    # começa em outra página: status, review, search, activity, due, settings
flib ui --no-browser     # mostra um link de uso único em vez de abrir
flib ui --port 8765      # uma porta fixa (padrão: uma livre)
```

O `flib ui` abre uma pequena interface no navegador, clara ou escura como o seu
Mac. Ela lê o mesmo índice que a linha de comando e só existe enquanto você a
usa: termina depois de 10 minutos sem uso (`ui.idle_minutes`), com
**Encerrar sessão** ou com Ctrl-C. Roda uma de cada vez: um segundo
`flib ui --open PAGE` abre aquela página na que já está rodando (no navegador
onde ela roda) e sai. Como ela mantém a sessão só com você está em
[Privacidade e segurança](privacy.md#the-interface-flib-ui).

![A página Status: 5 arquivos em revisão, 1 com falha, 3 vencimentos, 1 arquivo
sensível deixado no lugar, 1 duplicado separado, a última execução e o custo
deste mês](assets/screenshots/pt-BR/status.webp)

- **Status**: as contagens do `flib status`.
- **Revisão**: os arquivos esperando na pasta de revisão, agrupados pelo motivo
  de estarem lá e pelo que a IA sugeriu. Para cada um: **Aceitar** a sugestão,
  escolher **outra categoria** ou um **novo nome** (o mesmo que o `flib fix`,
  então o flib aprende com isso), **Devolver para onde estava** (o `flib undo`
  daquele arquivo: de volta em Downloads, onde as próximas execuções o deixam),
  e **Abrir** ou **Mostrar no Finder** para olhar antes. Um grupo com sugestão
  tem **Aceitar todos**, que lista os arquivos antes de movê-los (no máximo 100
  por vez, nunca um que falte no disco), como uma execução só, que um único
  Desfazer reverte.
- **Desfazer**: depois de cada ação, e para cada execução e cada movimentação
  em Atividade. Mostra o que vai ser devolvido, e para onde, e o que vai ser
  pulado e por quê (movido de novo por uma execução posterior, editado desde
  então, o lugar antigo ocupado), antes de fazer qualquer coisa; recusa o que o
  `flib undo` recusa, com o mesmo motivo. Desfazer uma correção também faz o
  flib esquecer o que aprendeu com ela.
- **Busca**: o `flib search` com seus filtros (chips de categoria e de tipo, as
  datas, o emissor, os valores, o vencimento, o período), **Abrir** e
  **Mostrar no Finder** para cada resultado, totais por moeda como o
  `flib search --sum` soma, e **Exportar CSV**. Arquivos sensíveis nunca são
  listados ali; são contados.
- **Atividade**: as execuções dos últimos 7, 30 ou 90 dias, por dia: o que cada
  uma moveu para onde, duplicados separados, arquivos sensíveis deixados no
  lugar (contados, nunca nomeados), uma espera pelo desbloqueio do Mac, e o
  custo.
- **Vencimentos**: o `flib due`, com sua janela (de 7 dias a 3 meses, ou
  vencidos) e totais por moeda, e **Marcar como concluído** / **Reabrir**
  (`flib fix --done` / `--not-done`).
- **Configurações**: o essencial, sem abrir o arquivo. Quando organizar
  (desligado, todo dia num horário, conforme os arquivos chegam, ou os dois: o
  `flib schedule` a partir da página), quem classifica (uma escolha entre os
  provedores que o arquivo já permite, com o que cada um envia; a chave de API
  só aparece como definida ou não definida), visão, reconhecimento de texto e
  busca por significado (cada um diz o que sai do Mac), Downloads e a Mesa como
  pastas de entrada (a Mesa com as regras cuidadosas do `flib init`),
  duplicados, categorias (adicionar, renomear, remover, dica, subpastas), os
  limites de custo, a notificação de resumo e os vencimentos, e o idioma (que
  também é o da interface: inglês ou português do Brasil, a partir da próxima
  página). Cada alteração é verificada como o `flib check-config` verifica
  **antes** de ser gravada; um valor recusado aparece com o campo e o motivo, e
  nada é gravado. Só as chaves que você alterou são mexidas: seus comentários e
  a ordem do arquivo ficam. O arquivo antigo é guardado como backup ao lado
  dele (`config.yaml.backup-<time>Z`, os 10 mais recentes), e
  **Restaurar as configurações anteriores** o coloca de volta depois de mostrar
  o que muda. Uma categoria que tem arquivos é renomeada ou removida com
  `flib reprocess --rename-category` / `--remove-category`, que os movem: a
  página diz isso em vez de fazer. Todo o resto (outra pasta ou servidor,
  regras de pasta, o lugar da biblioteca) fica no arquivo; a página diz o que
  é, abre o arquivo num editor de texto e indica o `flib check-config` e o
  `flib doctor`. Se o arquivo mudou desde que a página foi mostrada (editado à
  mão, outra aba), nada é salvo e a página o mostra como ele está.

As páginas funcionam sem JavaScript. Uma busca vai no formulário, não no
endereço, e Voltar e Avançar funcionam: nunca repetem uma ação. Toda alteração
passa pelo mesmo código, lock e journal da linha de comando, então o
`flib undo` também a reverte. Se uma execução está em andamento, a ação avisa e
não faz nada; se um arquivo mudou desde que a página foi mostrada (movido,
desfeito, tratado em outra aba), ela é recusada, nunca executada. Nada é
sobrescrito: um nome já ocupado recebe o próximo número livre. Arquivos
sensíveis nunca são listados nem tocados.

## Revisão { #review }

![A página Revisão: quatro fotos agrupadas sob uma sugestão, Trabalho, com
Aceitar Trabalho para os 4; uma receita cuja categoria sugerida, Receitas, não
está configurada](assets/screenshots/pt-BR/review.webp)

Cada arquivo em revisão diz por que está lá: a confiança da IA ficou abaixo de
`limits.confidence_threshold` (com a categoria que ela sugeriu), a IA escolheu
uma categoria que você não configurou, a categoria dele foi removida
(`flib reprocess --remove-category --to-review`), ou você mesmo o moveu para a
pasta de revisão. Decida na interface, ou no terminal com
[`flib fix`](#teaching-it).

## Busca { #search }

![A página Busca: três documentos encontrados para "fatura", totais por moeda,
cada um com tipo, emissor, valor, vencimento e
período](assets/screenshots/pt-BR/search.webp)

Toda palavra da consulta precisa casar com um nome, a descrição ou uma tag;
acentos são ignorados e as palavras casam como prefixos. Os filtros estreitam
os resultados e podem ser combinados entre si e com uma consulta, ou usados sem
ela; nesse caso os resultados vêm do mais novo para o mais antigo:

- `--category NAME`: sem diferenciar maiúsculas. Repita a opção ou separe os
  nomes com vírgulas para casar com qualquer um deles
  (`--category finance,contracts`).
- `--ext EXT`: com ou sem o ponto (`pdf` ou `.pdf`). Repita a opção ou use
  vírgulas para várias (`--ext jpg,png`).
- `--since DATE` / `--until DATE`: `YYYY-MM-DD`, os dois dias incluídos, no seu
  horário local. A data é quando o flib processou o arquivo pela última vez
  (arquivou, ou mandou para revisão), não a data escrita no documento (essa é
  filtrada por `--period`, veja [abaixo](#amounts-dates-and-issuers)), nem a
  data de modificação do arquivo.
- `--limit N`: no máximo esse número de resultados (20 por padrão, até 500).

```sh
flib search "power bill" --category finance --since 2026-01-01
flib search --ext pdf --until 2025-12-31 --json
```

Uma categoria desconhecida, uma extensão ou data malformada, ou `--since`
depois de `--until` param com uma mensagem dizendo o que está errado.

```text
--8<-- "pt-BR/search.txt"
```

### Valores, datas e emissores { #amounts-dates-and-issuers }

Quando o flib arquiva um documento, a IA também anota o que o próprio documento
declara, e deixa de fora o que ele não declara (ela é instruída a nunca
adivinhar):

- `kind`: um de `bill`, `receipt`, `invoice`, `statement`, `contract`,
  `payslip`, `tax`, `ticket`, `policy`, `report`, `form`, `letter`,
  `manual`, `other`;
- `issuer`: a organização de onde ele vem (nunca uma pessoa física);
- `amount` e `currency`: o total ou o valor devido (não um saldo nem qualquer
  outro número), com o código ISO 4217;
- `document_date`, `due_date` e `period`: o mês ou o ano a que ele se refere
  (o mês de referência de uma conta, o ano fiscal).

Os números são lidos em qualquer formato (`1.234,56`, `1,234.56`, `R$`, `€`);
um valor que o flib não consegue ler é descartado, nunca adivinhado, e números
de documento, de conta e de identificação nunca são guardados. Os resultados
mostram os campos numa linha
(`bill · Power Co · USD 103.91 · due 2026-09-20 · for 2026-08`), e estes
filtros se combinam com os outros e com uma consulta:

- `--issuer NAME`: maiúsculas, acentos, pontuação e formas jurídicas (`Inc`,
  `Ltd`, `S.A.`) não importam, então `--issuer "power co"` encontra
  `POWER CO, Inc.`;
- `--kind KIND`: repita a opção ou use vírgulas para qualquer um de vários;
- `--min-amount N` / `--max-amount N`, os dois incluídos; `--currency CODE`;
- `--due-before DATE` / `--due-after DATE`, o próprio dia excluído;
- `--period 2026` ou `--period 2026-09`: o período a que o documento se
  refere, ou, na falta dele, o mês da data do documento.

`--sum` soma os valores de todos os resultados em vez de listá-los, por moeda
(duas moedas nunca são somadas juntas), e `--by issuer`, `month`, `year`,
`kind` ou `category` agrupa os totais. `--sum` sem palavras nem filtro soma
todos os documentos arquivados:

```sh
flib search --sum --by kind                             # tudo, por tipo de documento
flib search --issuer "Power Co" --period 2026 --sum     # quanto paguei à Power Co em 2026?
flib search --kind bill --period 2026 --sum --by month --json
flib search --kind bill --due-after 2026-11-20          # contas ainda a pagar
```

```text
--8<-- "pt-BR/search-sum.txt"
```

Arquivos arquivados antes de o flib guardar esses campos não têm nenhum: o
`flib reprocess --fill-fields` pergunta à IA só sobre esses arquivos, depois de
mostrar o custo, grava os campos no índice (nada se move, categorias e nomes
ficam) e o `flib undo` o reverte. Com `--sum`, uma consulta casa só por
palavras, não por significado. Mais no
[guia](https://luizpereiradev.github.io/file-librarian/guide/#amounts-dates-and-issuers)
(em inglês).

### Busca por significado (opcional) { #search-by-meaning-optional }

A busca por palavras só encontra "electricity invoice" se essas palavras
estiverem no índice. A busca por significado também encontra arquivos pelo
sentido: `flib search "power bill" --semantic` encontra um arquivo descrito
como "electricity invoice". Cada arquivo recebe um *embedding*, uma lista de
números que coloca textos de sentido parecido perto uns dos outros, e a
consulta também recebe um. Os resultados das duas buscas são mesclados
(reciprocal rank fusion), então correspondências exatas de palavras continuam
vindo primeiro, e os filtros acima valem para as duas. Vem desligada por
padrão. Para ligá-la com o [Ollama](https://ollama.com) neste Mac:

1. `ollama pull embeddinggemma` (cerca de 600 MB; multilíngue).
2. Na configuração:

    ```yaml
    search:
      semantic:
        enabled: true
    ```

3. `flib search --reindex` para gerar o embedding dos arquivos já indexados.
   Depois disso, todo `flib run` gera o dos arquivos que ele adiciona ou altera.

Aí busque com `--semantic`, ou defina `search.semantic.default: true` para
torná-la o padrão (`--no-semantic` então busca só por palavras, numa busca). A
ferramenta MCP `search_files` aceita a mesma escolha na flag `semantic`:
`true`, `false` (só palavras, mesmo quando o padrão é por significado) ou sem
valor (o padrão). Com `--json`, e nos resultados do MCP, cada arquivo diz como
foi encontrado (`source`: `text`, `semantic` ou `both`) e, quando tem
embedding, sua `similarity` com a consulta.

Uma busca por significado sempre tem um arquivo "mais próximo", mesmo quando
nada tem relação, então os arquivos encontrados **só** por significado precisam
ter pelo menos `search.semantic.min_similarity` de similaridade com a consulta
(cosseno, de 0 a 1; 0.25 por padrão). Um arquivo que casa com as palavras
sempre fica, por menor que seja a similaridade. Quando esse piso não deixa
nada, a busca diz "No close matches; try other words or --no-semantic".

O piso certo depende do modelo. O 0.25 foi medido para o `embeddinggemma` num
corpus sintético de 60 arquivos em português e inglês e 32 consultas: manteve
43 de 45 pares relacionados (os dois perdidos eram frouxos, como "credit card
statement" para um extrato bancário, em 0.20) e descartou 98% dos não
relacionados, deixando em média cerca de um arquivo sem relação por consulta em
vez de 18. Para outro modelo, busque com `--json` algumas coisas que você sabe
que estão lá, olhe a `similarity` dos arquivos que você queria e dos que não
queria, e defina `min_similarity` entre elas; `0` desliga o piso.

O `embeddinggemma` posiciona melhor uma consulta e um documento quando sabe
qual é qual: o flib coloca `task: search result | query: ` antes de uma
consulta e `title: none | text: ` antes do texto de um arquivo, como pede o
model card dele. Sem esses prefixos, arquivos sem relação tiveram notas quase
tão altas quanto os relacionados (mediana 0.22, e 5% acima de 0.34) e nenhum
piso conseguia separá-los. Outros modelos não recebem prefixo, a menos que
`search.semantic.query_prefix` e `document_prefix` digam (`""` desliga um
deles). Mudar o prefixo de documento gera de novo o embedding da biblioteca na
próxima execução.

O que vira embedding é só o que o índice já guarda sobre um arquivo: os nomes,
a categoria, as tags e a descrição da IA, nunca o conteúdo. Um arquivo
sensível, ou que case com `never_send`, nunca vira embedding. Qualquer servidor
com um endpoint `/embeddings` compatível com a OpenAI funciona
(`search.semantic.base_url` e `model`). A chave opcional vem de
`FLIB_EMBEDDINGS_API_KEY`, ou da variável indicada por
`search.semantic.api_key_env`, ou de `flib key set --provider embeddings`;
nunca da chave do provedor de classificação. Com um servidor remoto, esse texto
do índice e toda consulta por significado saem do seu Mac, o
`flib check-config` avisa disso, e o custo não é contado: confira os preços do
seu servidor. A Anthropic não tem endpoint de embeddings, então não pode ser
usada aqui.

Se o servidor de embeddings não puder ser alcançado, ou nenhum arquivo tiver
embedding ainda, a busca avisa numa linha e busca por palavras. Uma execução
nunca falha por causa de embeddings: o que não virou embedding é tentado de
novo na próxima execução. Mudar `search.semantic.model` gera de novo o
embedding da biblioteca. Os vetores ficam no mesmo banco de dados e são
buscados por força bruta: cerca de 0,1 s para 10.000 arquivos e 0,6 s para
50.000.

## Vencimentos { #what-is-due }

O `flib due` responde "o que vence esta semana?" a partir dos vencimentos que a
IA leu nos seus documentos:

```text
--8<-- "pt-BR/due.txt"
```

![A página Vencimentos: uma conta vencida e duas a vencer esta semana, com
totais por moeda e Marcar como concluído](assets/screenshots/pt-BR/due.webp)

Contas, faturas e impostos (`due.kinds`) vencidos há até 30 dias e a vencer
nos próximos 7 dias; `--within 2w` olha mais adiante, `--overdue` lista tudo o
que já venceu, `--issuer` e `--kind` estreitam a lista, `--json` é para
scripts. Um documento sem vencimento declarado nunca aparece. Depois de pagar
um, `flib fix PATH --done` o tira da lista (o `flib undo` reverte, `--all`
ainda o mostra), e `due.done_tag: Paid` deixa uma tag do Finder fazer o mesmo.
O `flib status` acrescenta uma linha ("Due: 3 documents within 7 days, 1
overdue"), e o resumo periódico avisa uma vez por documento quando um vence
dentro de 3 dias; detalhes no
[guia](https://luizpereiradev.github.io/file-librarian/guide/#what-is-due)
(em inglês).

## Atividade e desfazer { #activity-and-undo }

![A página Atividade: as execuções dos últimos sete dias, por dia, o que cada
uma moveu para onde, uma correção à mão, uma movimentação desfeita, e o
custo](assets/screenshots/pt-BR/activity.webp)

Toda execução é registrada e pode ser desfeita, exceto os arquivos que você
alterou ou moveu depois, que são pulados e listados:

```sh
flib undo                     # a última execução
flib undo --run 12            # outra execução (o número dela está na página Atividade)
flib undo --file PATH         # a última operação sobre um arquivo
```

O `flib undo` devolve os duplicados como qualquer outra movimentação, remove só
a tag do Finder que o flib adicionou, e reverte correções, reprocessamentos e o
que um assistente fez pelo [MCP](mcp.md). A página **Atividade** da interface
oferece o mesmo **Desfazer** para cada execução e cada movimentação, e mostra o
que vai ser devolvido, e para onde, antes de fazer.
[Privacidade e segurança](privacy.md#how-your-files-are-kept-safe) explica como
o journal torna isso confiável.

## Ensinando o flib { #teaching-it }

Quando um arquivo vai parar no lugar errado, corrija:

```sh
flib fix ~/Downloads/Organized/Other/scan.pdf --category Finance
flib fix ~/Downloads/Organized/_Review/document.pdf --category Contracts --name lease-2026
```

O arquivo se move, o índice, a busca e a tag do Finder acompanham, e o
`flib undo` o reverte como uma execução. Mover ou renomear um arquivo à mão
também funciona: rode `flib reconcile` e o índice se atualiza. Os dois tipos de
correção são enviados como exemplos nas próximas requisições, para que o
classificador pare de repetir o erro (`provider.max_examples`, 10 por padrão;
`0` não envia nenhum).

As categorias são uma lista fechada, e a `hint` de cada categoria na
configuração é a principal alavanca para corrigir classificações erradas.
Depois de mudar categorias, dicas, regras de nome ou o modelo, o
`flib reprocess` arquiva de novo o que já está organizado:

```sh
flib reprocess --simulate          # classifica e mostra o que seria movido; não move nada
flib reprocess                     # pergunta antes de enviar, e de novo antes de mover
flib reprocess --rename-category Docs Documents   # depois de renomear uma categoria na configuração
flib reprocess --remove-category Misc             # depois de remover uma
```

Ele pergunta antes de enviar qualquer coisa (com uma estimativa de custo) e
antes de mover qualquer coisa, mantém suas correções e nomes de arquivo, a
menos que você diga o contrário, e o `flib undo` desfaz o reprocessamento
inteiro. Os detalhes estão no
[guia](https://luizpereiradev.github.io/file-librarian/guide/#changing-your-rules)
(em inglês).

## Ou conforme os arquivos chegam (modo watch) { #or-as-files-arrive-watch-mode }

```sh
flib schedule --mode watch    # roda alguns minutos depois que algo chega numa pasta de entrada
flib schedule --mode both     # watch, com a execução diária como rede de segurança
```

No modo watch, o macOS inicia o flib quando uma pasta de entrada muda, no
máximo uma vez a cada `schedule.watch_min_interval_minutes` (5). Ele só pega um
item depois que este ficou sem mudar por `schedule.watch_quiet_seconds` (5
minutos, porque cópias lentas e torrents fazem pausas), espera downloads em
andamento (`.crdownload`, `.part`, uma cópia do Finder...) enquanto continuarem
mudando, e então roda uma vez para tudo o que chegou: dez arquivos de uma vez
são uma execução. Quando não há nada novo pronto (por exemplo, logo depois das
próprias movimentações do flib), ele sai na hora, sem execução e sem chamada à
IA. O macOS observa só o nível de cima de cada pasta de entrada, que é tudo o
que o flib organiza de qualquer forma, e o flib também confere a cada
`schedule.watch_fallback_minutes` (30) se algo passou despercebido. As
execuções automáticas (diária e watch) também respeitam
`limits.max_cost_usd_per_day` (três vezes o limite por execução, por padrão);
quando o custo de hoje chega a ele, não enviam mais nada à IA até a
meia-noite, e o `flib status` avisa. O resumo periódico e os avisos de
vencimento só vêm depois da execução diária, então `both` é o modo a escolher
se você os quer. Defina `schedule.mode` na configuração para tornar o modo o
padrão do `flib schedule`; `--refresh` e `--at` mantêm o modo instalado e
`--remove` para os dois. Detalhes, e o que o flib não consegue detectar:
[o guia](https://luizpereiradev.github.io/file-librarian/guide/#watch-mode-organizing-files-as-they-arrive)
(em inglês).
