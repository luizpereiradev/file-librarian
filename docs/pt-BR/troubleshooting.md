# Solução de problemas

Comece pelo `flib check-config` (a configuração e a chave de API) e pelo
`flib schedule --status` (a execução diária). O `flib status` mostra o que a
última execução pulou, e o log de cada execução em
`~/Library/Application Support/file-librarian/logs` diz por quê. Para relatar
um bug, anexe um [relatório do `flib doctor`](#the-doctor-report): ele deixa de
fora sua chave, os nomes de arquivos e as pastas.

As linhas de log e as mensagens de erro citadas abaixo estão em inglês, como o
flib as escreve.

## Um arquivo é pulado como "unreadable" { #a-file-is-skipped-as-unreadable }

O flib não conseguiu olhar o arquivo, então o deixou onde está e vai tentar de
novo na próxima execução. O resumo e o log da execução dizem o que falhou: a
etapa (`stat` lê os detalhes do arquivo, `read` o conteúdo) e o erro que o
macOS devolveu, por exemplo:

```text
  skip    photo.jpeg  (unreadable: EPERM Operation not permitted, on read)
```

- `EPERM` ou `EACCES` na execução diária: o macOS impede a execução agendada
  de acessar esse arquivo. Dê Acesso Total ao Disco ao `flib-python`, como
  descrito em [Rodar todo dia](getting-started.md#run-it-every-day), e depois
  rode `flib schedule` de novo para verificar. Uma execução agendada que
  esbarra nisso termina o log com uma dica dizendo isso. Numa execução manual,
  verifique as permissões do arquivo (`ls -l`).
- `EDEADLK`, `EAGAIN`, `EINTR`, `ETIMEDOUT`, `EBUSY` ou `ESTALE`: outro
  programa (um cliente de sincronização, um antivírus, o Spotlight) estava
  usando o arquivo. O flib já tentou mais uma vez depois de meio segundo; a
  próxima execução normalmente consegue ler.
- Qualquer outra coisa, ou o mesmo arquivo falhando todo dia: rode
  `flib doctor` e abra uma issue com o relatório.

## Um arquivo é pulado como "locked" { #a-file-is-skipped-as-locked }

```text
  skip    photo.jpeg  (protected by macOS while the Mac is locked)
```

A Proteção de Dados do macOS dá a cada arquivo uma classe de proteção. A
maioria dos arquivos pode ser lida a qualquer momento depois que você fez login
uma vez desde que o Mac foi ligado. Alguns apps feitos para iOS e executados no
Mac, como alguns apps de mensagens, salvam arquivos com uma classe mais
rígida: o macOS se recusa a abri-los enquanto a tela está bloqueada, mesmo
para o dono. Não há nada errado com o arquivo nem com as permissões do flib,
então o Acesso Total ao Disco não ajuda aqui.

Quando a execução diária começa com o Mac bloqueado, o flib pula esses
arquivos como `locked` (nenhuma tentativa falha é contada) e então espera você
desbloquear o Mac: ele verifica uma vez por minuto, dormindo entre uma
verificação e outra, por até `schedule.wait_for_unlock_hours` (8 por padrão,
no máximo 23; `0` desliga a espera), e então roda mais uma vez para o que
sobrou. O log da execução e o `schedule.out.log` dizem quando a espera começa
e como terminou. Enquanto espera, ele não segura nenhuma trava, então o
`flib run` manual funciona normalmente; se houver uma execução em andamento
quando o Mac for desbloqueado, a espera simplesmente termina. O
`flib schedule --remove` (ou sair da sessão) a interrompe de forma limpa. Uma
execução agendada que encontra uma anterior ainda esperando não espera também:
a que está esperando já vai rodar mais uma vez para tudo depois do desbloqueio.

O `flib status` conta esses arquivos na última execução, e o resumo periódico
os menciona. Para arquivá-los na hora, desbloqueie o Mac e rode `flib run`. O
flib nunca muda a classe de proteção de um arquivo: um arquivo movido para a
biblioteca a mantém, então `flib reconcile`, `flib undo`, `flib fix` e
`flib reprocess` tratam um arquivo desses que não conseguem ler com o Mac
bloqueado como "não dá para saber agora" (nunca alterado, nunca ausente) e
pedem que você tente de novo depois do desbloqueio.

## O modo watch não arquiva um download { #watch-mode-does-not-file-a-download }

Primeiro confira o `flib schedule --status`: o modo precisa ser `watch` ou
`both` e o agente de watch precisa estar ativo. Depois olhe o `watch.out.log`
em `~/Library/Application Support/file-librarian/logs`:

- **Nada.** Uma partida que não encontra nada novo não escreve nada, de
  propósito. O arquivo pode ainda estar baixando ou mudando (ele é arquivado
  quando se estabiliza), ser recente demais para o `min_age_days` da entrada,
  estar ignorado, ser sensível, ou ser um arquivo que o flib já tentou: o
  `flib simulate` mostra por que ele é pulado. Um arquivo colocado em uma
  *subpasta* de uma entrada não dispara o flib, porque o macOS observa só o
  nível de cima. Se você mudou `inputs` depois de instalar o agente, ele ainda
  observa as pastas antigas: o `flib status` avisa, e o
  `flib schedule --refresh` corrige.
- **"N item(s) are still downloading or changing (no change for 10 min, or 6
  h passed)".** O download ficou parado por mais tempo que
  `schedule.watch_settle_minutes` (um download que continua crescendo é
  esperado, por até 6 horas). A partida de reserva do agente de watch (a cada
  `schedule.watch_fallback_minutes`, 30) o pega quando ele termina, assim como
  a próxima mudança na pasta e a execução diária.
- **"Not running: another flib process ... is running".** Uma execução
  (manual, a diária, um `flib reprocess`...) segurou a trava por mais de 10
  minutos; ela ou a próxima mudança cuida do arquivo.
- **"Automatic runs reached today's cost cap".** O
  `limits.max_cost_usd_per_day` (ou, para um provedor com preços
  desconhecidos, o `limits.max_files_per_day`) foi atingido; os arquivos
  esperam até amanhã, ou rode `flib run` agora. Aumente o limite ou defina-o
  como `null` para desligá-lo. O `flib status` mostra quantos itens esperam e
  por quê ("Watch: N files wait in the inputs (daily cap reached)" ou
  "(provider failing)"); os itens que ficaram para trás são tentados mais uma
  vez por uma execução de watch no dia seguinte, e uma chegada nova sempre
  passa na frente deles.
- **Acesso negado.** O agente de watch roda como `flib-python`, igual à
  execução diária: o `flib schedule` verifica o acesso às pastas para ele.

Ele roda no máximo uma vez a cada `schedule.watch_min_interval_minutes` (5 por
padrão), e só pega um item depois que ele ficou sem mudar por
`schedule.watch_quiet_seconds` (5 minutos), então um arquivo pode levar cerca
de dez minutos para ser arquivado. Um arquivo com data no futuro (um
compartilhamento com o relógio adiantado) é pego depois de ficar igual por esse
tempo; uma execução diária o pega na segunda olhada. O `watch.out.log` é
cortado em cerca de 1 MB; o log próprio de cada execução de watch é um
`watch-run-*.log` ao lado dele.

## O macOS pergunta sobre a Mesa ou Documentos { #macos-asks-about-the-desktop-or-documents }

A Mesa, Documentos e Downloads são protegidos, cada um, por uma permissão de
privacidade própria do macOS (Ajustes do Sistema > Privacidade e Segurança >
Arquivos e Pastas). Quando você adiciona um deles a `inputs`:

- **Manualmente no Terminal**, o primeiro `flib run` ou `flib simulate` que o
  lê faz o macOS perguntar se o *Terminal* (ou o seu app de terminal) pode
  acessar aquela pasta. Permita; essa resposta vale só para o terminal.
- **A execução diária** não é o seu terminal: ela roda a cópia do Python do
  próprio flib (`flib-python`), que nunca mostra um pedido. O `flib schedule`
  verifica cada entrada como a execução diária a vê e mostra uma linha por
  pasta, `ok` ou `denied by macOS privacy settings`. Dê Acesso Total ao Disco
  ao `flib-python` (o caminho é mostrado; o Acesso Total ao Disco cobre a
  Mesa, Documentos e Downloads de uma vez), e depois rode `flib schedule` de
  novo até cada linha dizer `ok`.
- Uma pasta que a execução não consegue listar (negada, apagada, um disco
  desmontado) é pulada: a execução organiza as outras entradas, mostra
  `skip    <folder>  (input folder could not be read...: EPERM ..., on list)`
  e um aviso, e o `flib status` a conta. A execução só para com erro quando
  nenhuma entrada pode ser lida.

Um arquivo na Mesa pulado como `too recent (N days left)` não é um problema
de permissão: a entrada dele tem `min_age_days`, e o arquivo ainda não está ali
há tanto tempo
([Mais de uma caixa de entrada](https://luizpereiradev.github.io/file-librarian/guide/#more-than-one-inbox), em inglês).

## Imagens e PDFs escaneados são classificados só pelo nome { #images-and-scanned-pdfs-are-classified-from-their-name-only }

O flib lê o texto de capturas de tela, documentos fotografados e PDFs
escaneados no seu Mac com o reconhecimento de texto do macOS (OCR). Quando um
documento ainda assim acaba classificado só pelo nome:

- O `flib inspect FILE` diz o que uma execução leria dele: `text: N
  characters, recognised by OCR on this Mac`, ou `none sent` e por quê
  (`OCR failed: ...`, `no readable text`, `sensitive`).
- O `flib check-config` mostra `ocr: on, on this Mac (...)`, ou um aviso
  quando o OCR não pode rodar nesta máquina, com o motivo. O OCR usa
  `/usr/bin/osascript` e o framework Vision, que todo macOS suportado tem;
  nada precisa ser instalado. `ocr.enabled: false` o desliga.
- Uma foto com menos de `ocr.min_chars` letras e dígitos (40) não é tratada
  como documento: com `vision.enabled` o modelo a vê; caso contrário, ela é
  classificada pelo nome. Diminua `ocr.min_chars` para documentos com muito
  pouco texto.
- Um aviso de que o macOS não consegue reconhecer um idioma significa que um
  código em `ocr.languages` não é um que o Vision conhece; o aviso lista os
  que ele conhece. Os outros continuam sendo usados.
- O primeiro reconhecimento em um Mac pode levar cerca de meio minuto enquanto
  o macOS prepara o modelo dele; cada um depois disso leva uma fração de
  segundo. Um arquivo é abandonado depois de dois minutos e tratado como se o
  OCR estivesse desligado.
- Um arquivo cujo texto reconhecido contém uma senha ou uma chave é marcado
  como sensível de propósito: nada dele é enviado, e ele fica onde está.

## Nenhuma notificação aparece { #no-notification-appears }

A notificação do resumo periódico vem do app notificador do flib,
"file-librarian", que o `flib schedule` monta no ambiente do próprio flib. Na
primeira vez em que ele notifica, o macOS pergunta se deve permitir (o
`flib schedule` envia uma notificação na hora para isso, "Summaries will show
here"); até você escolher Permitir, as notificações vão silenciosamente para a
Central de Notificações. Para mudar a resposta, abra Ajustes do Sistema >
Notificações > file-librarian e ative Permitir Notificações, com o estilo
Faixas ou Alertas. Um modo de Foco ou o "Não Perturbe" o silencia como
qualquer app, a menos que você permita o file-librarian naquele Foco.

O `flib status` (e o `flib doctor`) dizem qual notificador está em uso:

- `Notifications: from the "file-librarian" app`: o app está montado e
  atualizado.
- `Notifications: through osascript, shown as Script Editor (...)`: a
  alternativa de reserva, com o motivo. O app ainda não foi montado, está
  desatualizado depois de uma atualização (um ícone ou script novo), ou uma
  ferramenta necessária para montá-lo (`osacompile`, `codesign`, `sips`,
  `iconutil`, todas parte do macOS) está faltando ou falhou; o
  `flib schedule --refresh` o monta de novo. Se abrir o app falhar, aquela
  notificação também cai no `osascript`. As notificações do Editor de Scripts
  são permitidas separadamente, sob o nome dele, nos mesmos ajustes.

O macOS não confia em apps para notificações quando eles ficam numa pasta
temporária (`/tmp`, `~/Library/Caches`); o ambiente do flib criado pelo
`./scripts/install.sh` não está em nenhuma das duas. Uma notificação que o
macOS descarta não pode ser detectada, então não é reenviada. Aconteça o que
acontecer com ela, o log da execução e o `flib status` sempre têm o mesmo
resumo.

A notificação também é pulada de propósito: ela só vem depois de uma
execução *agendada*, só quando um arquivo esperou `summary.min_age_days` (2 por
padrão), e no máximo uma vez a cada `summary.every_days` (7 por padrão).

## O relatório do doctor { #the-doctor-report }

Para relatar um bug, rode `flib doctor`. Ele escreve um arquivo,
`doctor-<date>-<time>.txt`, e mostra o caminho. O arquivo traz as versões do
flib, do Python e do macOS, como o flib foi instalado, se o extra de MCP está
presente, a configuração (validada), de onde vem a chave de API, o estado do
agendamento, a versão do esquema e as contagens do banco de dados, o que o
`flib status` mostra, as últimas execuções com o custo delas e os últimos
cinco logs de execução (`--logs N` para mais). Ele é feito para ser postado em
uma issue pública, então, antes de ser escrito:

- a chave de API fica totalmente de fora, nem os últimos caracteres; só a
  palavra que diz de onde ela vem (`environment`, `keychain`, `env_file`,
  `none`);
- as pastas viram `<home>`, `<input-1>`, `<output>` ou `<data>` (a pasta do
  próprio flib), e outros nomes de pasta viram `<dir-N>`; os nomes das
  categorias e a pasta de revisão ficam, já que vêm da configuração;
- os nomes de arquivo viram `<file-N>`, mantendo só a extensão
  (`<file-3>.pdf`), com o mesmo número para o mesmo nome, para que um arquivo
  possa ser acompanhado pelos logs;
- qualquer coisa que pareça um segredo, endereços de e-mail, URLs, texto entre
  aspas, o nome da sua conta, dicas de categoria e padrões de
  `ignore`/`never_send` que nomeiam algo também são substituídos.

Leia o arquivo antes de anexá-lo: a remoção de dados é cuidadosa, não
infalível. `--stdout` mostra o relatório em vez de gravá-lo, `--output PATH`
(`-o`) o grava em outro lugar, e `--format markdown` o deixa pronto para colar
em uma issue.

O relatório vai para a pasta de logs do flib
(`~/Library/Application Support/file-librarian/logs`), acessível só ao dono,
que guarda os dez últimos, e não para a Mesa: o flib não escreve em nenhum
lugar novo, e em muitos Macs a Mesa é sincronizada com o iCloud, o que
enviaria um arquivo que você ainda não leu. Um `open -R` no caminho mostrado
o exibe no Finder, pronto para arrastar para a issue.

## Para desenvolvedores e CI { #for-developers-and-ci }

- **Os testes nunca tocam no seu Mac.** `uv run pytest` roda offline, em
  paralelo, com um `HOME` temporário, um Keychain falso, `launchctl`,
  `osascript`, `sips`, `open` e `claude` falsos, e sem rede nem DNS
  ([Contribuir](project.md#contributing)). Um teste que parece precisar de uma
  ferramenta real recebe, em vez disso, um marcador opcional: `volumes`,
  `ocr`, `notifier`, `menu`, `browser`, rodados um de cada vez, por exemplo
  `FLIB_TEST_BROWSER=1 uv run pytest -m browser -n 0` (precisa do Chrome ou do
  Chromium; `FLIB_TEST_BROWSER_PATH` aponta para outro).
- **Um teste só falha em paralelo.** Rode-o sozinho com `-n 0`; um teste que
  escreve fora de `tmp_path` ou do `HOME` falso, ou que conta com uma porta
  que encontrou livre, é a causa de costume.
- **O `coverage report` diz "No data to report".** Rode
  `uv run coverage combine` antes: cada worker escreve o próprio arquivo de
  dados.
- **O `uv sync` recusa uma versão nova.** De propósito: versões com menos de 7
  dias são ignoradas (`exclude-newer` no `pyproject.toml`). Espere, ou veja
  [Urgent security fixes](https://github.com/luizpereiradev/file-librarian/blob/main/SECURITY.md#urgent-security-fixes) (em inglês).
- **CI.** A única verificação obrigatória é `CI passed`: lint, formatação,
  tipos e a suíte com cobertura no Python mais antigo e no mais novo, mais as
  etapas opcionais de macOS no mais novo, e o job `docs`, que monta este site
  nos dois idiomas em modo estrito: um link ou âncora quebrados, ou uma página
  faltando na navegação, fazem ele falhar (`uv run --group docs python -m
  scripts.docs build` mostra o mesmo localmente). Um texto de `--help` alterado
  faz a suíte de testes falhar até a [referência da linha de comando](cli.md)
  ser gerada de novo (`uv run python -m scripts.docs_cli`, com o texto em
  português em `scripts/cli_pt_BR.json`).
- **Experimente o flib sem os seus arquivos.** `uv run python -m scripts.demo
  /tmp/flib-demo` monta uma biblioteca fictícia offline
  ([biblioteca de demonstração](getting-started.md#try-it-on-a-demo-library)).
