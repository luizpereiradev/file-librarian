# Primeiros passos

Do zero a uma execução diária em poucos minutos. Nada se move até você
mandar: a configuração testa o flib primeiro em *cópias* dos seus arquivos.

## Requisitos { #requirements }

- A versão principal atual do macOS (a única plataforma suportada)
- [uv](https://docs.astral.sh/uv/) 0.13 ou mais recente (`brew install uv`); ele
  instala o Python 3.11+ para você
- Um provedor de IA ([Provedores](providers.md) compara as opções):
    - uma chave de API da Anthropic, para o provedor padrão `anthropic`;
    - ou o [Claude Code](https://code.claude.com) logado na sua assinatura do
      Claude, para `claude-code` (sem chave);
    - ou o [Ollama](https://ollama.com) com um modelo baixado, para `ollama`
      (nada sai do seu Mac);
    - ou qualquer API compatível com a da OpenAI, para `openai-compat`.

Para experimentar o flib sem nenhum deles, use `provider: {name: fake, model: offline}`:
tudo é arquivado na última categoria, offline.

## Experimente numa biblioteca de demonstração { #try-it-on-a-demo-library }

Antes de apontar o flib para a sua pasta Downloads, você pode explorar uma
fictícia. Num clone do repositório:

```sh
uv sync
uv run python -m scripts.demo /tmp/flib-demo          # --pt-BR para português do Brasil
```

Ele monta, só naquela pasta, a pasta Downloads de um desenvolvedor depois de
alguns dias de flib: uns 40 arquivos inventados (contas e faturas em USD e
BRL, recibos, um contrato, capturas de tela, instaladores, PDFs de estudo,
fotos esperando na revisão, um duplicado, um arquivo de credencial deixado no
lugar), várias execuções diárias, uma correção e um desfazer. Funciona
offline, usa o provedor `fake` e nunca mexe na sua configuração, no seu
índice, no Keychain nem nas suas pastas. Ele mostra os comandos para
explorá-la, cada um restrito ao `HOME` da própria demonstração:

```sh
HOME="$DEMO_HOME" "$FLIB" status -c "$CFG"
HOME="$DEMO_HOME" "$FLIB" ui -c "$CFG"
```

`uv run python -m scripts.demo /tmp/flib-demo --clean` remove tudo.

## Instalação { #install }

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
./scripts/install.sh      # instala o `flib` com as versões travadas das dependências;
                          # pergunta se deve adicionar o servidor MCP opcional
flib init                 # algumas perguntas, um teste em cópias dos seus arquivos, a execução diária
```

Para atualizar, rode `git pull` e `./scripts/install.sh` de novo.

## Primeira execução: `flib init` { #first-run-flib-init }

O `flib init` pergunta o idioma, a pasta a organizar e para onde vão os
arquivos organizados, o provedor e, com `anthropic`, se deve ativar a
[visão](configuration.md#images-and-scanned-pdfs-vision) (padrão: não). Toda
pergunta tem um padrão, então apertar Enter até o fim já dá uma configuração
que funciona (`flib init --yes` aceita todos sem perguntar):

- **Idioma:** o sugerido, e o que o `flib init` fala até você responder, é o
  idioma da configuração que ele substituiria, quando há uma que ele consegue
  ler: substituí-la nunca troca o idioma das pastas e categorias da sua
  biblioteca só porque o idioma deste shell é outro. Sem configuração, é o
  idioma do shell (`LANG`): português dá `pt-BR`, qualquer outro dá `en`.
- **Provedor:** `claude-code` quando o Claude Code está instalado e logado
  (nada mais a configurar); depois `ollama` quando o Ollama está rodando neste
  Mac com um modelo baixado (só é oferecido nesse caso, e o teste confere se o
  modelo está lá); senão `anthropic`, e aí ele oferece guardar a sua chave de
  API no Keychain (digitada sem aparecer, nunca exibida). `openai-compat` é
  sempre oferecido: ele pede a URL do servidor e o modelo, e oferece guardar
  uma chave. O mesmo vale para `local`, a configuração totalmente local para a
  qual o `flib init --local` vai direto
  ([Nada sai deste Mac](privacy.md#nothing-leaves-this-mac)).
- **Configuração:** gravada em `~/.config/file-librarian/config.yaml` (ou
  `--config PATH`), legível só por você. Se já existir uma, ela é mantida a
  menos que você confirme a substituição (`--force` substitui sem perguntar).
- **Teste:** os seus itens mais recentes (30 por padrão, `--sample N`; `0`
  pula o teste) são *copiados* para uma pasta temporária e classificados lá,
  com um índice descartável. Você vê o que iria para onde, o que iria para a
  revisão e o custo estimado. Nada nas suas pastas se move, e o seu índice de
  verdade não é tocado; as cópias são apagadas no final. Itens ocultos,
  sensíveis, `never_send`, que estão só no iCloud ou grandes (mais de 20 MB)
  ficam de fora.
- **Execução diária:** ele oferece agendá-la, como faz o `flib schedule`, e
  pergunta se deve rodar diariamente (o padrão), quando chegarem arquivos
  ([modo watch](daily-use.md#or-as-files-arrive-watch-mode)) ou os dois.

Para configurar à mão:

```sh
mkdir -p ~/.config/file-librarian
cp config.example.yaml ~/.config/file-librarian/config.yaml
flib key set              # guarda a sua chave de API no Keychain do macOS (anthropic, openai-compat)
flib check-config         # valida a configuração e encontra a chave
```

Depois, a primeira execução, à mão:

```sh
flib simulate             # o plano para a sua pasta de verdade; nada se move
flib run                  # organiza agora
flib undo                 # mudou de ideia? desfaz a execução inteira
```

## Rodar todo dia { #run-it-every-day }

```sh
flib schedule                 # diariamente às schedule.time (12:30 por padrão); --at 21:00 para mudar
flib schedule --status        # quando roda e se está ativo
flib schedule --remove        # para de rodar
```

O `flib schedule` instala um agente do launchd, oferece mover para o Keychain
uma chave de API que esteja só no seu shell e depois confere se um processo
*agendado* consegue ler as suas pastas. O macOS pode bloquear o acesso de
processos em segundo plano à pasta Downloads; nesse caso, ele diz exatamente
qual programa adicionar em Ajustes do Sistema > Privacidade e Segurança >
Acesso Total ao Disco. Esse programa é o `flib-python`, a cópia do
interpretador Python que é só do flib, então o acesso que você dá vale só
para o flib, não para os seus outros projetos Python. Depois de atualizar o
Python ou o flib, o `./scripts/install.sh` o renova (`flib schedule
--refresh`); o macOS pode pedir o acesso de novo.

Se o Mac estiver dormindo no horário agendado, a execução acontece quando ele
acordar. Se rodar com o Mac bloqueado e pular arquivos que o macOS protege até
o desbloqueio, ele espera o desbloqueio e roda mais uma vez
([detalhes](troubleshooting.md#a-file-is-skipped-as-locked)). Cada execução
grava um log em `~/Library/Application Support/file-librarian/logs`,
terminando com o custo estimado do provedor.

Prefere rodar quando os arquivos chegam? Veja o [modo watch](daily-use.md#or-as-files-arrive-watch-mode).

## O item da barra de menus { #the-menu-bar-item }

```sh
flib menu            # põe o flib na barra de menus; ele abre no login
flib menu --remove   # tira de lá
```

![As cinco poses numa barra de menus clara e numa escura: ocupado,
vencimento, problema, atenção, dormindo](images/menu-bar.png)

O gato do ícone do flib fica na barra de menus, na pose que diz o que precisa
de você: carregando uma folha enquanto uma execução está em andamento, com a
pata levantada quando um documento vence em breve ou está vencido, arrepiado
quando o flib não consegue rodar (sem chave, sem acesso a uma pasta, um índice
que ele não consegue ler), sentado olhando para você quando há arquivos
esperando na revisão ou com falha, enrolado dormindo quando não há nada
pendente. Um numerozinho ao lado conta o motivo da pose. Ele acompanha o
visual claro ou escuro da barra de menus.

![O item da barra de menus com o menu aberto: a linha de estado, a linha do
agendamento, Abrir o flib, Revisão, Vencimentos, Organizar agora,
Configurações… e Sair](assets/screenshots/pt-BR/menu.webp){ width="420" }

O menu é curto: uma linha dizendo como estão as coisas ("2 arquivos para
revisar, 1 com falha", "Organizando…", "Nada pendente"), quando é a próxima
execução, **Abrir o flib** (a página Status da interface), **Revisão (n)** e
**Vencimentos (n)** quando há algo lá, **Organizar agora** (exatamente o que a
execução diária faz; com uma execução em andamento, ele avisa e não faz nada),
**Configurações…** e **Sair**. Se uma execução for interrompida à força (um
travamento, uma queda de energia), o menu diz "A última execução não terminou"
depois de três horas, ou antes, assim que você abrir o `flib status`, a
interface ou **Organizar agora**. Cada item que abre algo é um
`flib ui --open PAGE`; quando a interface já está aberta, ele vai para aquela
página nela.

O menu mostra só contagens, nunca um nome de arquivo: a barra de menus fica
visível para qualquer um olhando a sua tela, e no compartilhamento de tela. O
item só lê um pequeno arquivo de estado que o flib grava (quando uma execução
começa e termina, depois de uma revisão, correção, desfazer ou mudança de
vencimento, e quando o agendamento muda) e não roda nada além do próprio
flib, com argumentos fixos. É um app minúsculo que o `flib menu` monta no seu
Mac com ferramentas que vêm com o macOS (`osacompile`), dentro do ambiente do
próprio flib, como o notificador; nada é baixado e nada precisa do Xcode.
Depois de **Sair** (ou de um travamento), ele fica desligado até o próximo
login, ou até `flib menu`. O `flib status`, o `flib doctor` e a página
Configurações da interface dizem se ele está lá; o `./scripts/install.sh` o
remonta depois de uma atualização. Num Mac onde ele não pode ser montado, o
`flib menu` diz por quê, e todo o resto funciona como antes.

As ações que você faz numa interface aberta pelo menu, e o **Organizar agora**
sem uma execução diária, rodam em nome do app da barra de menus: o macOS pode
perguntar uma vez se "file-librarian" pode acessar a sua pasta Downloads. O
**Organizar agora** com uma execução diária instalada dispara essa execução,
que usa o acesso que você deu ao `flib-python`.

![Todas as poses em 1x e 2x, em barras de menus claras e escuras](images/menu-poses.png)

## Desinstalação { #uninstalling }

Rode primeiro `flib menu --remove`, e `flib schedule --remove`: o app da barra
de menus fica dentro do ambiente do flib, e o LaunchAgent dele fica em
`~/Library/LaunchAgents` até ser removido. Se ficar para trás, ele só falha
uma vez a cada login (nunca tenta de novo), mas é sujeira. Os seus arquivos
organizados ficam onde estão; o índice e os logs ficam em
`~/Library/Application Support/file-librarian`, a configuração em
`~/.config/file-librarian`.

Próximo: [Uso diário](daily-use.md).
