# Privacidade e segurança

O flib lê seus arquivos pessoais todo dia, sem ninguém olhando. Esta página
diz exatamente o que ele lê, o que sai do seu Mac e para quem, o que nunca sai
e como ele mantém seus arquivos seguros. A
[arquitetura](https://luizpereiradev.github.io/file-librarian/architecture/) (em inglês)
traz o projeto completo.

## O que ele garante { #what-it-promises }

**A IA só decide. Quem move os arquivos é código determinístico.** O modelo
devolve dados (uma categoria, um nome, uma descrição); ele nunca recebe um
caminho sobre o qual possa agir. Uma categoria fora da sua lista, uma resposta
malformada ou uma confiança baixa nunca viram ação: o arquivo vai para a pasta
de revisão. Os comandos que organizam mantêm estas garantias:

- **Nada é apagado e nada é sobrescrito.** A única exceção: quando o destino
  está em outro volume (um disco externo), o item é copiado, a cópia é
  conferida com o hash do original e só então o original é removido;
  atributos estendidos (origem do download, tags do Finder), permissões e
  datas vão junto. Discos exFAT e FAT não conseguem renomear sem substituir,
  então o flib não move nada para eles.
- **Toda execução é registrada e pode ser desfeita**, exceto arquivos que
  você alterou ou moveu depois, que são pulados e listados.
- **Nada é baixado do iCloud.** Arquivos que existem só na nuvem, e pastas
  com qualquer arquivo assim dentro, são pulados e listados até você
  baixá-los. `flib reconcile` e `flib inspect` também não os leem.
- **Segredos ficam onde estão.** Arquivos com nomes de credenciais ou com
  segredos no conteúdo nunca são enviados à IA e nunca são movidos; padrões
  de `never_send` enviam só metadados.

## O que sai do seu Mac { #what-leaves-your-mac }

O índice, os logs e o relatório do `flib doctor` ficam no seu Mac. O que sai
dele depende do provedor e de dois recursos opcionais.

**Para cada arquivo classificado**, o provedor recebe:

- o nome, a extensão, o tamanho, a data do download e a URL de onde ele foi
  baixado;
- no máximo `provider.max_chars_per_file` caracteres do texto (4.000 por
  padrão) ou, no caso de uma pasta, os nomes de até 100 arquivos dentro dela.
  Num e-mail, esse texto começa com o assunto, a data, os nomes de
  remetentes e destinatários e os domínios dos endereços deles (nunca a parte
  antes do `@`), e os nomes dos anexos; o corpo vem em seguida, como está,
  como o texto de qualquer documento;
- para uma imagem ou um PDF sem camada de texto, o texto que o
  [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) reconheceu
  nele, como o texto de qualquer documento. O reconhecimento roda no seu Mac;
  o texto passa pela busca de segredos antes de qualquer envio;
- para uma imagem cujo texto foi reconhecido, o tamanho em pixels, quantas
  linhas de texto ela tem e quanto dela essas linhas ocupam;
- só com `vision.enabled` (desligado por padrão): para uma imagem, a própria
  imagem, reduzida a `vision.max_image_px` e sem os metadados, a menos que o
  OCR tenha concluído que é uma página de texto (aí vai só o texto); para um
  PDF sem camada de texto, as primeiras `vision.max_pdf_pages` páginas,
  quando o OCR encontrou pouco ou nenhum texto. A detecção de segredos lê
  texto, não pixels, então o que o OCR não conseguiu ler vai como está.
  Mantenha esses arquivos de fora com `never_send`;
- suas correções mais recentes (no máximo `provider.max_examples`): para
  cada uma, a extensão do arquivo corrigido, a descrição que o provedor
  escreveu para ele antes e a categoria ou o nome antes e depois. Nunca
  conteúdo de arquivo, e nunca de arquivos que casam com `never_send` ou
  marcados como sensíveis.

**Para onde vai, por provedor:**

| Provedor | Recebe o que está acima | Imagens (com vision) | Páginas de PDF escaneado (com vision) |
| --- | --- | --- | --- |
| `anthropic` | a API da Anthropic | sim | sim |
| `claude-code` | a Anthropic, pela CLI `claude` | nunca | nunca |
| `openai-compat` | o servidor em `provider.base_url` | só com `provider.vision: true` | nunca |
| `ollama` | o modelo neste Mac (nada sai dele), a menos que `base_url` aponte para outro lugar | só com `provider.vision: true` | nunca |

O reconhecimento de texto (OCR) não está nesta tabela porque nunca sai do seu
Mac: o macOS lê o texto localmente, e só esse texto segue adiante, como
descrito acima.

**Recursos opcionais:**

- A [busca por significado](daily-use.md#search-by-meaning-optional)
  (desligada por padrão) envia os nomes, as categorias, as tags e as
  descrições do índice (nunca conteúdo, nunca um arquivo sensível ou de
  `never_send`) e suas buscas por significado para
  `search.semantic.base_url`: por padrão, o Ollama neste Mac, então nada sai
  dele a menos que você aponte para outro lugar.
- O [servidor MCP](mcp.md) dá a um assistente só o que o `flib search`
  mostraria a você, nunca conteúdo de arquivo. O que o assistente faz com
  isso é com ele: um assistente na nuvem, como o Claude, envia isso ao
  provedor dele como qualquer outra coisa da conversa.

`flib check-config` e `flib doctor` dizem `fully local: yes`, ou listam o que
sai do Mac e para onde (o pedido de classificação com `anthropic`,
`claude-code` ou um servidor remoto; o texto do índice com embeddings
remotos).

## Arquivos sensíveis e `never_send` { #sensitive-files-and-never_send }

Dois tipos de arquivo são tratados de forma diferente, com qualquer provedor:

- **Que casam com `never_send`:** só os metadados acima são enviados, nunca o
  conteúdo. Adicione `"*.eml"` para que mensagens de e-mail enviem só os
  metadados.
- **Nomes de credenciais (`id_rsa`, `*.pem`…) ou segredos encontrados no
  conteúdo:** nada é enviado, o arquivo fica onde está e o índice registra só
  que ele é sensível, e por quê. Nomes que dizem guardar senhas ou material
  de recuperação também contam, comparados como palavras inteiras, sem
  diferenciar maiúsculas de minúsculas:
  `password(s)`, `passwd`, `2fa`, `senha(s)`, `contraseña(s)`,
  `mot(s) de passe`, `Passwort`/`Passwörter`, `recovery code(s)`,
  `backup code(s)`, `seed phrase`, `private key`, `recovery key`,
  `bitwarden export`, `logins.csv`, `client secret(s)`, `api key(s)`,
  `access token`, `refresh token`, `secret key`. Arquivos de cliente OAuth
  com o nome que os consoles de nuvem dão a eles (`client_secret_<id>.json`),
  `token.json`, `secrets.json`/`.yaml`, `kubeconfig`, `*.tfstate` e
  `*.tfvars` contam como nomes de credenciais, e um segredo sob uma chave
  entre aspas (`"client_secret": "…"`) é encontrado em qualquer lugar,
  inclusive num arquivo que é uma única linha longa de JSON.
  `1Password-8.10.dmg` ou um nome hexadecimal com `2fa` no meio não é pego,
  e instaladores (`.dmg`, `.pkg`, `.app`…) nunca são pegos só pelo nome. Um
  inofensivo "password reset guide.pdf" também fica onde está: renomeie-o, ou
  liste-o em `sensitive.allow_names`, e a próxima execução olha para ele como
  para qualquer arquivo (um arquivo cujo conteúdo guarda um segredo continua
  sensível, seja qual for o nome). `flib status --sensitive` lista todo
  arquivo deixado no lugar e por quê, só no seu terminal (nunca no
  `flib doctor`, numa notificação ou no servidor MCP).

Uma captura de tela de uma senha ou de uma chave de API também conta: o texto
que o [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) reconhece
numa imagem passa pela mesma busca de segredos antes de qualquer envio. A
interface nunca lista nem age sobre um arquivo sensível, e a busca os conta
sem dizer o nome.

## O que o flib lê { #what-flib-reads }

| Formato | O que é lido |
| --- | --- |
| PDF | a camada de texto (primeiras 20 páginas); num PDF escaneado, o texto que o [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) reconhece nas 3 primeiras páginas, no seu Mac |
| Word, Excel, PowerPoint (`.docx`, `.xlsx`, `.pptx` e as versões com macro) | o texto; numa planilha, os nomes das abas e o texto das células |
| Texto simples, Markdown, CSV, JSON, YAML, HTML, legendas e código-fonte | o texto |
| E-mail (`.eml`) | assunto, data, remetente e destinatários (só nomes e domínios), nomes dos anexos e o corpo (texto simples ou, se não houver, o texto do HTML) |
| Arquivos compactados (`.zip`, `.tar.gz` e outros tarballs) | a lista de nomes dentro; nada é descompactado |
| Pastas e pacotes `.app` | os nomes de até 100 arquivos dentro; o nome e a versão de um app |
| Imagens | o texto que o [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) reconhece nelas, no seu Mac; numa foto com pouco ou nenhum texto, nada, a menos que [vision](configuration.md#images-and-scanned-pdfs-vision) esteja ligado |
| Qualquer outra coisa (`.doc`, vídeos, instaladores...) | nada: classificado pelo nome e pelos metadados |

No máximo `provider.max_chars_per_file` caracteres (4.000 por padrão) do texto
são enviados. Um arquivo danificado ou protegido por senha também é
classificado pelo nome e pelos metadados. Para adicionar um formato, veja
[como adicionar um extrator](https://luizpereiradev.github.io/file-librarian/adding-an-extractor/) (em inglês).

## O que o índice guarda { #what-the-index-stores }

Para cada arquivo: o nome e o caminho originais e atuais, o tamanho, o hash do
conteúdo, a categoria, o status e o que a IA escreveu sobre ele (uma descrição
de 1 a 3 frases, algumas tags, a confiança e por que manteve ou trocou o
nome), além do diário de movimentações e correções por trás do `flib undo`.
Nunca o conteúdo do arquivo. Essa descrição é o que o `flib search`, o
`flib inspect` e as ferramentas MCP mostram, e o que a busca por significado
transforma em embeddings, por isso ela é mantida livre de identificadores
pessoais de duas formas:

- A IA é instruída a dizer o que um arquivo *é* sem copiar identificadores
  dele: nada de números de telefone, endereços de e-mail, números de
  documento, de identidade ou fiscais, números de conta bancária ou de
  cartão, chaves de pagamento, endereços, placas de carro ou nomes completos
  de pessoas físicas (nomes de organizações, produtos e figuras públicas são
  permitidos). O nome de arquivo proposto segue a mesma regra.
- Antes de guardar qualquer coisa, o flib troca o que ainda parece um
  identificador por um marcador: `(00) 90000-0000` vira `[phone]`,
  `user@example.com` vira `[email]`, e CPF, CNPJ, cartão, IBAN e outros
  números longos viram `[id number]`, `[card number]` ou `[account number]`
  (segredos viram `[secret]`). Datas, valores, números de versão e anos não
  são tocados. Num nome proposto, o identificador é removido. Nomes de
  pessoas e endereços não podem ser distinguidos de outras palavras por um
  padrão: para eles, vale só a instrução à IA.

`privacy.descriptions: verbatim` desliga a segunda etapa e guarda o que a IA
escreveu (a instrução continua). Descrições guardadas por uma versão anterior
podem ser limpas sem perguntar à IA de novo, sem custo:
`flib reprocess --scrub-descriptions` (`--simulate` para ver antes quais
arquivos). Ele também limpa o cache de classificação, remove os embeddings dos
arquivos que alterou (a próxima execução os gera de novo) e é desfeito pelo
`flib undo`; até você desfazer, o diário de desfazer no índice guarda o texto
antigo, que não aparece em lugar nenhum. O `flib doctor` nunca inclui
descrições nem tags.

O `flib status`, o item da barra de menus e a notificação periódica não dizem
o nome de nenhum arquivo, porque a barra de menus fica visível para qualquer
um que olhe sua tela e as notificações aparecem na tela de bloqueio.

## Nada sai deste Mac { #nothing-leaves-this-mac }

```sh
flib init --local
```

configura o flib para que nenhum arquivo, nome ou busca saia do seu Mac:

| O quê | Onde roda |
| --- | --- |
| Classificar arquivos | `gemma4:e4b` no [Ollama](https://ollama.com), neste Mac |
| Texto em imagens e PDFs escaneados | o reconhecimento de texto do próprio macOS (ligado por padrão) |
| Busca por significado | `embeddinggemma` no Ollama, neste Mac |
| O índice, os logs, o histórico de desfazer | a pasta do flib, como sempre |

`flib init --local` confere se o Ollama está instalado e rodando e, se não
estiver, diz como obtê-lo (`brew install ollama` e
`brew services start ollama`, ou o app em
[ollama.com](https://ollama.com/download)); o flib nunca o instala por você.
Depois ele oferece baixar os dois modelos (cerca de 10 GB; mostra os tamanhos
e pergunta antes), grava a configuração, faz o teste de sempre em cópias dos
seus arquivos recentes e diz que qualidade esperar. O `flib init` sem opções
oferece a mesma configuração como o provedor `local`.

**Que qualidade esperar.** Medido no corpus de teste sintético do flib
([qualidade](https://luizpereiradev.github.io/file-librarian/quality/#local-models-92) (em inglês)),
o `gemma4:e4b` coloca 92% dos itens numa categoria certa em inglês e 97% em
português, contra 99% e 100% do padrão hospedado (Claude Haiku), e manda para
revisão 7 dos 9 itens que precisam de uma pessoa, contra 9 de 9. Ele mantém
nomes descritivos com a mesma frequência (e também mantém mais nomes
genéricos), lê valores, moedas e vencimentos igualmente bem, e erra emissores
e datas de documentos com mais frequência. Ele processa cerca de 20 arquivos
por minuto num M3 Pro, e não custa nada. Espere corrigir mais no começo:
`flib simulate` antes da primeira execução, e uma olhada na pasta de revisão.

**Hardware.** Um Mac com Apple silicon e 16 GB de memória ou mais, e cerca de
10 GB de disco para os modelos. O modelo precisa de cerca de 9,5 GB depois de
carregado, e o número que o Ollama mostra cresce para cerca de 17 GB ao longo
de uma execução longa, antes de ele ser descarregado (10 minutos depois do
último arquivo); com 16 GB, o macOS vai usar swap durante uma primeira
execução grande. Dos quatro modelos medidos, o `qwen3.5:4b` (4 GB), menor, é
menos preciso em inglês (85%) e raramente manda algo para revisão; os maiores
não são precisos o bastante a mais para compensar serem de 2,5 a 3 vezes mais
lentos.

O vision fica desligado nesta configuração: nas imagens com texto do corpus,
ele ganhou um item em doze, com metade da velocidade. O reconhecimento de
texto as lê no lugar dele.

Ser local não relaxa mais nada: arquivos que casam com `never_send` continuam
enviando só os metadados, e arquivos com nomes de credenciais ou segredos
continuam nunca sendo enviados, mesmo com o modelo no seu Mac
([Ollama](providers.md#a-local-model-with-ollama-ollama)).

## Como seus arquivos são mantidos seguros { #how-your-files-are-kept-safe }

Em palavras simples, o que acontece quando o flib move um arquivo:

1. **Ele anota primeiro o que vai fazer.** Toda movimentação é registrada no
   diário do índice como *planejada* antes de acontecer, e marcada como
   *feita* depois. Se o Mac travar ou ficar sem energia no meio de uma
   execução, a próxima execução encontra as entradas inacabadas e as conclui
   ou deixa o arquivo onde estava; nada se perde no meio do caminho.
2. **Ele nunca substitui nada.** Um nome de destino já ocupado recebe o
   próximo número livre (`report (2).pdf`); a renomeação em si é atômica e se
   recusa a substituir um arquivo existente. As pastas que ele cria recusam
   links simbólicos no caminho, então um arquivo nunca pode ser levado para
   fora da sua pasta organizada.
3. **Ele confere antes de desfazer.** O `flib undo` percorre as
   movimentações de uma execução de trás para frente, confere o hash de cada
   arquivo e pula (e lista) qualquer arquivo que você editou desde então, ou
   cujo lugar antigo agora está ocupado.
4. **Entre volumes, ele copia, confere e só então remove.** Em outro disco, a
   cópia é gravada por completo, o SHA-256 dela é comparado com o do original
   e só então o original é removido; se algo for diferente, o original fica.
5. **O que ele não consegue ler, ele deixa.** Arquivos só no iCloud, arquivos
   que o Mac protege enquanto está bloqueado, pastas ilegíveis: pulados e
   listados, nunca forçados.

Os detalhes, incluindo cada caso de travamento, estão nas
[garantias de segurança](https://luizpereiradev.github.io/file-librarian/architecture/#safety-guarantees)
da arquitetura (em inglês).

## A interface (`flib ui`) { #the-interface-flib-ui }

O `flib ui` é um servidor web no seu Mac que só você deve conseguir usar,
pelo tempo em que você o usar. Ele foi feito para resistir a uma página web
aberta em qualquer navegador do Mac, a outra máquina na rede, a outras contas
no mesmo Mac, a outros programas escutando no loopback e a qualquer coisa que
leia logs, o histórico do shell ou a barra de endereços. Ele não consegue
resistir a um programa rodando como você, que já pode fazer tudo o que você
pode. O que ele faz, em palavras simples:

- **Só este Mac consegue acessá-lo.** Ele escuta apenas no endereço de
  loopback; nada mais na rede consegue se conectar. Ele termina depois de 10
  minutos sem uso (`ui.idle_minutes`), com **Encerrar sessão** ou com Ctrl-C.
- **Cada sessão tem um endereço próprio,**
  `http://flib-<random>.localhost:<port>/`, para que outros programas
  escutando no Mac nunca recebam o cookie dela, mesmo que uma página faça o
  seu navegador visitá-los (macOS, Safari, Chrome e Firefox resolvem nomes
  `.localhost` para o próprio Mac; num Mac que não faz isso, o `flib ui`
  avisa e serve em `127.0.0.1`, onde eles poderiam recebê-lo). Trate esse
  endereço como a própria sessão enquanto ela durar: ele fica na barra de
  endereços e no histórico, nunca numa linha de comando, e no terminal só no
  link do `--no-browser`.
- **A chave da sessão passa por um arquivo que só você pode ler,** nunca pela
  linha de comando, onde outras contas no Mac poderiam vê-la. O terminal diz
  "session started" quando ela é usada. Se a chave for usada uma segunda
  vez, por qualquer coisa sem a sua sessão, o `flib ui` termina e avisa você.
- **Suas buscas ficam fora do seu histórico.** A Busca e a exportação em CSV
  dela vão no formulário, nunca no endereço; **Abrir** e **Mostrar no
  Finder** enviam o número do arquivo no índice, nunca um caminho.
- **Abrir é só para documentos** (PDFs, imagens, texto, documentos de
  escritório modernos, mídia): qualquer coisa que possa rodar um programa ou
  levar a algum lugar, como um app, um script, um instalador, um atalho para
  a web, um `.doc`/`.xls`/`.ppt` antigo ou um arquivo configurado para abrir
  com um app próprio, recebe só **Mostrar no Finder**.
- **Nada é carregado da internet.** Ele usa só a biblioteca padrão do Python
  e arquivos que vêm com o flib, e as páginas funcionam sem JavaScript. Toda
  alteração passa pelo mesmo código, pela mesma trava e pelo mesmo diário da
  linha de comando, então o `flib undo` também a reverte.

`--no-browser` imprime a própria chave, num link: ela fica no histórico de
rolagem do terminal e, depois de aberta, no histórico do navegador, e qualquer
um que consiga ver sua tela ou esse histórico de rolagem poderia usá-la antes
de você. Ela funciona uma vez só, e um segundo uso encerra o servidor, mas
prefira o padrão, a menos que o navegador não possa ser aberto para você. A
[arquitetura](https://luizpereiradev.github.io/file-librarian/architecture/#the-interface-flib-ui) (em inglês)
tem os detalhes, com o modelo de ameaças numa tabela de cada ameaça, sua
defesa e o teste que a comprova: verificações de origem e CSRF, a content
security policy, limites de requisição, o que cada página lê.

## Relatórios de bug sem os seus dados { #bug-reports-without-your-data }

O `flib doctor` escreve um relatório para uma issue com a chave de API
totalmente omitida e com nomes de arquivos, pastas, endereços de e-mail, URLs
e qualquer coisa que pareça um segredo substituídos. Leia antes de anexar: a
remoção é cuidadosa, não infalível. A
[solução de problemas](troubleshooting.md#the-doctor-report) lista exatamente
o que é removido.
