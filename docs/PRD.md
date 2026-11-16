# PRD — file-librarian

> Snapshot of 2026-11-16. This is the only document in the repository written in Portuguese.

## Resumo

O **file-librarian** é uma ferramenta de linha de comando open source para macOS que roda uma vez por dia e mantém organizadas as pastas que o usuário indicar, começando pela Downloads. Para cada arquivo novo, ela lê o conteúdo, dá um nome descritivo, move para uma pasta por categoria e grava uma descrição em um banco SQLite local com busca em texto completo.

O problema que resolve: a pasta Downloads acumula arquivos com nomes inúteis (`documento(3).pdf`, `IMG_4821.png`) e, depois de alguns meses, não dá mais para saber o que importa. Os projetos existentes organizam, ou renomeiam, ou indexam; nenhum faz as três coisas para qualquer tipo de arquivo.

O princípio central do projeto: **a IA só decide, quem mexe nos arquivos é código determinístico**. Nada é apagado, nada é sobrescrito e toda operação pode ser desfeita.

Decisões já tomadas: Python 3.11+, SQLite, Claude como provedor padrão de IA, suporte apenas à versão atual do macOS e `flib` como comando. O nome não cita Downloads de propósito, para o projeto poder cobrir outras pastas no futuro.

**Idioma.** O código, os comentários, as mensagens da ferramenta e toda a documentação do repositório ficam em inglês. Este PRD é o único documento em português.

**Idioma do usuário.** A opção `language` da configuração (por enquanto `en` e `pt-BR`) define o idioma dos nomes e descrições gerados e os nomes padrão das pastas e categorias: `Organized`, `_Review` e `Finance` em inglês; `Organizado`, `_Revisar` e `Financeiro` em português. O que o usuário configurar explicitamente prevalece. Os exemplos deste PRD usam `pt-BR`.

## Objetivos e não-objetivos

### Objetivos

- **Execução desassistida:** a pasta se mantém organizada com uma execução diária agendada, sem ninguém aprovar nada.
- **Todo arquivo com contexto:** cada arquivo processado termina com nome legível, categoria e descrição pesquisável.
- **Busca rápida:** qualquer arquivo processado é encontrado por uma consulta em linguagem simples, mesmo que o usuário não lembre o nome.
- **Reversibilidade:** qualquer execução, ou qualquer arquivo individual, pode voltar ao estado anterior, desde que o arquivo não tenha sido alterado depois.
- **Regras sem código:** categorias, padrão de nomes e exclusões ficam em um arquivo de configuração.

### Não-objetivos da v1

- Não é um gerenciador de documentos: sem interface web e sem acervo próprio. Os arquivos continuam no sistema de arquivos do usuário.
- Não apaga arquivos, nem duplicatas.
- Não sincroniza com nuvem nem entre máquinas.
- Não organiza o disco inteiro: o escopo são as pastas de entrada configuradas.
- Não tem interface gráfica.
- Não suporta Windows nem Linux. O projeto é só para macOS, e contribuições para outros sistemas não serão aceitas na v1.
- Não grava comentários do Finder nem integra com o Spotlight ou com lançadores. A integração com lançadores fica para depois e usará a saída JSON da busca.

## Usuários e casos de uso

O projeto atende dois públicos: quem usa a ferramenta e quem contribui com o código.

| Público | Quem é | O que precisa |
| --- | --- | --- |
| Usuário | Pessoa técnica que baixa de tudo (documentos, instaladores, imagens, planilhas) e não quer manter a pasta na mão | Instalar em poucos minutos, configurar uma vez e esquecer |
| Contribuidor | Desenvolvedor que quer adicionar um provedor de IA, um extrator de formato ou corrigir um bug | Entender a arquitetura em uma leitura, rodar os testes localmente e saber onde cada mudança entra |

Casos de uso principais:

1. **Organização diária.** O agendador dispara a ferramenta; os arquivos novos são renomeados, movidos e indexados; o usuário não vê nada além de um resumo no log.
2. **Busca.** O usuário roda `flib search "contrato de aluguel"` e recebe o caminho atual, o nome original e a descrição dos arquivos que batem.
3. **Primeira execução em pasta bagunçada.** O usuário roda `flib simulate`, revisa o plano e só então executa.
4. **Correção.** Um arquivo foi parar na pasta errada; o usuário roda `flib undo` para aquele arquivo ou ajusta as regras.
5. **Revisão.** Arquivos em que a IA teve baixa confiança ficam em `_Revisar`, e o usuário decide o destino.

## Requisitos funcionais

São 40 requisitos, 24 deles P0 (obrigatórios para o MVP). Os IDs servem para referenciar em issues e pull requests e não são renumerados: os requisitos RF-23 em diante vieram da análise de projetos existentes. P1 entra antes da v1.0; P2 é desejável.

| ID | Etapa | Requisito | Prioridade |
| --- | --- | --- | --- |
| RF-01 | Varredura | Varre as pastas de entrada configuradas e identifica arquivos ainda não processados, usando o hash do conteúdo | P0 |
| RF-02 | Varredura | Ignora downloads em andamento (`.crdownload`, `.part`) e só processa arquivos cujo tamanho e data de modificação ficaram estáveis entre duas checagens | P0 |
| RF-03 | Varredura | Ignora padrões configurados (glob), arquivos de sistema (`.DS_Store`, `.localized`, `._*`) e as pastas que a própria ferramenta gerencia | P0 |
| RF-04 | Extração | Extrai texto de PDF, DOCX, XLSX, PPTX, texto puro, Markdown, CSV e código-fonte | P0 |
| RF-05 | Extração | Para imagens, envia a imagem a um modelo com visão | P1 |
| RF-06 | Extração | Para arquivos sem conteúdo legível (instaladores, vídeos, compactados), usa nome, extensão, tamanho e metadados; em compactados, a lista de arquivos internos | P0 |
| RF-07 | Classificação | A IA devolve, em JSON validado por esquema: categoria, nome sugerido, descrição de 1 a 3 frases, tags e confiança | P0 |
| RF-08 | Classificação | A categoria só pode ser uma das definidas na configuração; resposta fora da lista é rejeitada | P0 |
| RF-09 | Classificação | Confiança abaixo do limiar configurado envia o arquivo para `_Revisar`, sem renomear | P0 |
| RF-10 | Execução | Renomeia segundo um padrão configurável, preserva a extensão e resolve colisões com sufixo numérico | P0 |
| RF-11 | Execução | Move para a pasta da categoria e nunca sobrescreve um arquivo existente | P0 |
| RF-12 | Índice | Grava cada arquivo no banco: nome original, nome novo, caminhos, hash, categoria, descrição, tags e data | P0 |
| RF-13 | Busca | `flib search <query>` faz busca em texto completo sobre nomes, descrição e tags | P0 |
| RF-14 | Busca | Filtros por categoria, período e extensão | P1 |
| RF-15 | Busca | Busca semântica opcional por embeddings | P2 |
| RF-16 | Segurança | `flib simulate` mostra o plano completo sem alterar nada | P0 |
| RF-17 | Segurança | `flib undo` reverte a última execução ou um arquivo específico. Confere o hash antes de reverter e pula, com aviso, arquivos alterados depois ou cujo caminho original foi ocupado | P0 |
| RF-18 | Operação | Gera log por execução e um resumo final: processados, enviados para revisão, erros | P0 |
| RF-19 | Operação | `flib schedule` instala a execução diária como agente do launchd e testa o acesso à pasta Downloads | P1 |
| RF-20 | Operação | `flib reconcile` detecta arquivos movidos ou apagados à mão e atualiza o índice | P1 |
| RF-21 | Operação | Detecta duplicatas por hash e move para `_Duplicados`, sem apagar | P2 |
| RF-22 | Busca | Servidor MCP local que permite a assistentes de IA buscar e consultar os arquivos indexados, recategorizar um arquivo e desfazer operações | P1 |
| RF-23 | Varredura | Trata subpastas, aplicativos `.app` e pacotes como uma unidade: move inteiros ou ignora, nunca reorganiza o conteúdo interno | P0 |
| RF-24 | Varredura | Detecta arquivos que existem só no iCloud e os pula, sem disparar o download | P1 |
| RF-25 | Extração | Lê o endereço de origem do download gravado pelo macOS no arquivo e o usa como sinal de classificação | P0 |
| RF-26 | Extração | Para PDFs escaneados sem texto, envia as primeiras páginas como imagem ao modelo com visão, com limite de páginas | P1 |
| RF-27 | Extração | Arquivos protegidos por senha, corrompidos ou em formato legado (`.doc`, `.xls`, `.ppt`) são classificados só por metadados; uma falha de extração nunca interrompe a execução | P0 |
| RF-28 | Classificação | Detecta segredos pelo conteúdo antes do envio (chaves privadas, tokens, credenciais). Arquivo sensível não vai ao provedor, não recebe descrição e fica marcado como `sensitive` | P0 |
| RF-29 | Classificação | Correções do usuário (recategorizar, renomear) são guardadas e reutilizadas como exemplos nas classificações seguintes | P1 |
| RF-30 | Classificação | Chamadas ao provedor têm limite de requisições por minuto, novas tentativas com espera crescente e timeout. Após N falhas, o arquivo recebe o status `failed`, gravado no banco, e deixa de ser reenviado | P0 |
| RF-31 | Execução | Política de renomeação por categoria (por exemplo, instaladores e compactados mantêm o nome original) e limiar de confiança próprio para o nome | P0 |
| RF-32 | Execução | A data usada no nome é a do documento, quando encontrada com confiança e lida na ordem dia/mês/ano por padrão; senão, a data do download | P0 |
| RF-33 | Execução | Em movimentação entre volumes, copia, confere o hash da cópia e só então remove a origem | P1 |
| RF-34 | Operação | Trava de instância única: execução agendada, execução manual e ferramentas de escrita do MCP nunca alteram arquivos ou banco ao mesmo tempo | P0 |
| RF-35 | Busca | `flib search --json` devolve os resultados em formato estruturado, para uso por outros programas | P0 |
| RF-36 | Operação | Aplica a categoria como etiqueta do Finder, a partir de um conjunto fechado; pode ser desligado na configuração | P1 |
| RF-37 | Operação | `flib reprocess` reclassifica arquivos depois de uma mudança de regras; renomear ou remover uma categoria exige migração explícita | P1 |
| RF-38 | Operação | `flib init` conduz a primeira execução sobre uma amostra de 20 a 50 arquivos copiados para uma pasta temporária | P1 |
| RF-39 | Operação | `flib doctor` reúne logs com nomes e caminhos omitidos, prontos para anexar a uma issue | P1 |
| RF-40 | Operação | Resumo periódico do que está parado em `_Revisar` ou com status `failed` | P2 |

## Requisitos não funcionais

A ferramenta mexe em arquivos pessoais sem supervisão, então segurança e privacidade pesam mais que velocidade.

- **Segurança dos arquivos.** Nunca apaga e nunca sobrescreve. Cada movimentação é registrada no banco antes de ser executada, para que uma falha no meio da execução seja recuperável. A única remoção permitida é a da origem em uma movimentação entre volumes, depois de conferido o hash da cópia.
- **Reversão com limites declarados.** O `flib undo` restaura nome e local originais de todo arquivo que não mudou desde a operação. Arquivos editados, movidos à mão ou cujo caminho original foi ocupado são pulados e listados no resultado.
- **Idempotência.** Rodar duas vezes seguidas não altera nada na segunda vez. Como cada execução processa tudo o que ainda não está no banco, um dia perdido com o Mac desligado é recuperado na execução seguinte.
- **Concorrência.** Só um processo escreve por vez, garantido por uma trava de instância. O banco usa o modo WAL com tempo de espera, e as leituras do servidor MCP não bloqueiam uma execução em andamento.
- **Privacidade.** Só um trecho limitado do conteúdo vai para o provedor de IA, com tamanho configurável. Arquivos que batem com a lista `never_send` ou em que a ferramenta detecta segredos pelo conteúdo não são enviados e não recebem descrição. Um provedor local deve existir como alternativa para quem não quer enviar nada.
- **Segredos.** A chave de API é lida de variável de ambiente e nunca fica em arquivo versionado.
- **Custo previsível.** Há teto de arquivos e de tokens por execução, o modelo é configurável, um arquivo já classificado (mesmo hash) não é reprocessado e um arquivo que falha repetidamente deixa de ser reenviado.
- **Resiliência ao provedor.** Limite de requisições por minuto, novas tentativas com espera crescente e timeout por chamada. A classificação usa temperatura zero para reduzir a variação entre execuções.
- **Plataforma.** Somente a versão atual do macOS; Windows e Linux estão fora do escopo. Não há serviço residente: o processo roda e termina.
- **Permissões do macOS.** A ferramenta verifica o acesso às pastas de entrada no início de cada execução. Sem a permissão, falha com uma mensagem que explica como liberá-la, em vez de terminar em silêncio.
- **Nomes com acento.** Nomes e caminhos são normalizados para a forma composta (NFC) antes de irem ao banco, para que a busca por "orçamento" encontre o arquivo qualquer que seja a forma gravada no disco.
- **Observabilidade.** Código de saída diferente de zero em caso de erro, para o agendador conseguir alertar.
- **Desempenho.** Meta inicial: 100 arquivos novos processados em menos de 10 minutos com provedor remoto.

## Arquitetura

O processamento é um pipeline linear de seis etapas, e só uma delas (a classificação) usa IA. Cada etapa é um módulo com entrada e saída tipadas, o que permite testar e substituir cada uma isoladamente.

1. **Scanner.** Lista as pastas de entrada, aplica as exclusões, trata subpastas e aplicativos `.app` como unidade, espera o arquivo ficar estável e descarta o que já está no banco (por hash).
2. **Extrator.** Produz um `FileContext` por arquivo: metadados, origem do download e um trecho de texto, ou a imagem. Um extrator por formato, registrado por extensão. É aqui que roda a detecção de segredos: um arquivo sensível sai marcado e não segue para o classificador.
3. **Classificador.** Envia o `FileContext`, as regras e exemplos de correções anteriores ao provedor de IA e recebe uma `Classification` validada por esquema. Cuida de limite de requisições, novas tentativas e timeout.
4. **Planejador.** Transforma as classificações em um plano de operações: aplica o padrão de nomes e a política de renomeação da categoria, resolve colisões e desvia para `_Revisar` o que tem baixa confiança. O modo simulação para aqui e imprime o plano.
5. **Executor.** Adquire a trava de instância e aplica o plano. Grava cada operação no banco como planejada, move o arquivo e marca como concluída.
6. **Indexador.** Atualiza o registro do arquivo e o índice de busca e aplica a etiqueta do Finder.

A separação entre as etapas 3 e 5 é a regra mais importante do projeto: o modelo nunca recebe acesso ao sistema de arquivos. Ele devolve dados, e o código decide se são válidos antes de agir.

### Provedores de IA

Todo provedor implementa a mesma interface (`classify(context, rules) -> Classification`), e adicionar um provedor novo não deve exigir mudança fora do diretório de provedores.

| Provedor | Autenticação | Marco |
| --- | --- | --- |
| `anthropic` | Chave de API em `ANTHROPIC_API_KEY` | M1 |
| `claude-code` | Chama `claude -p` e usa a assinatura já logada | M2 |
| `ollama` | Nenhuma, modelo local | M3 |
| `openai-compat` | Chave e URL de qualquer endpoint compatível | M3 |

### Servidor MCP

O servidor MCP é a interface de busca principal para quem usa assistentes de IA: em vez de abrir um terminal, o usuário pergunta "cadê o boleto da energia de outubro?" ao Claude ou a qualquer cliente compatível. O comando `flib mcp` sobe o servidor por stdio, lendo o mesmo banco SQLite, sem rede e sem serviço residente.

| Ferramenta | Tipo | O que faz |
| --- | --- | --- |
| `search_files` | Leitura | Devolve os arquivos que batem com uma consulta, com filtros opcionais de categoria, período e extensão |
| `get_file` | Leitura | Devolve o registro completo de um arquivo: caminho atual, nome original, descrição, tags e histórico de operações |
| `list_recent` | Leitura | Lista os arquivos processados nos últimos N dias |
| `list_review` | Leitura | Lista os arquivos aguardando decisão em `_Revisar` |
| `get_stats` | Leitura | Devolve contagens por categoria e por status, e o resumo da última execução |
| `recategorize_file` | Escrita | Move um arquivo para outra categoria configurada e, opcionalmente, aplica um novo nome |
| `undo` | Escrita | Reverte uma execução inteira ou a última operação de um arquivo |

- **Escrita pelo mesmo caminho do pipeline.** `recategorize_file` e `undo` não mexem em arquivos diretamente: geram operações que passam pelo planejador e pelo executor. Valem as mesmas garantias da execução diária: nada é apagado, nada é sobrescrito, e tudo fica no diário e pode ser revertido.
- **Destinos fechados.** `recategorize_file` só aceita categorias definidas na configuração e sanitiza o nome. O servidor não aceita caminhos arbitrários.
- **Modo somente leitura opcional.** A opção `mcp.read_only: true` na configuração desliga as duas ferramentas de escrita.
- **Descrições são dados, não instruções.** O texto indexado veio de arquivos baixados da internet e pode conter instruções maliciosas. As respostas são estruturadas e a documentação orienta o cliente a tratá-las como conteúdo não confiável.
- **Sem conteúdo bruto.** O servidor devolve metadados e descrições, nunca o conteúdo do arquivo. Quem quiser ler o arquivo usa o caminho devolvido, com as permissões do próprio cliente.

## Modelo de dados

Um único arquivo SQLite guarda três tabelas e um índice FTS5. O banco fica fora das pastas organizadas (por padrão no diretório de dados do usuário) para não ser movido por engano.

```sql
CREATE TABLE files (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  original_path TEXT NOT NULL,
  current_name  TEXT NOT NULL,
  current_path  TEXT NOT NULL,
  extension     TEXT,
  size_bytes    INTEGER,
  source_url    TEXT,            -- origem do download, lida do macOS
  category      TEXT,
  description   TEXT,
  tags          TEXT,            -- JSON array
  confidence    REAL,
  status        TEXT NOT NULL,   -- organized | review | skipped | missing | failed | sensitive
  attempts      INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  provider      TEXT,
  model         TEXT,
  run_id        INTEGER REFERENCES runs(id),
  processed_at  TEXT NOT NULL
);

CREATE TABLE runs (
  id           INTEGER PRIMARY KEY,
  mode         TEXT NOT NULL,    -- run | simulate
  trigger      TEXT NOT NULL,    -- schedule | manual | mcp
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  files_seen   INTEGER,
  files_moved  INTEGER,
  files_review INTEGER,
  errors       INTEGER
);

CREATE TABLE operations (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER NOT NULL REFERENCES runs(id),
  file_id    INTEGER NOT NULL REFERENCES files(id),
  src_path   TEXT NOT NULL,
  dst_path   TEXT NOT NULL,
  status     TEXT NOT NULL,      -- planned | done | undone | failed | skipped
  created_at TEXT NOT NULL
);

CREATE TABLE corrections (
  id            INTEGER PRIMARY KEY,
  file_id       INTEGER NOT NULL REFERENCES files(id),
  field         TEXT NOT NULL,   -- category | name
  old_value     TEXT,
  new_value     TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE VIRTUAL TABLE files_fts USING fts5(
  original_name, current_name, description, tags,
  content='files', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
```

- `operations` é o diário que sustenta o `flib undo`: reverter é percorrer as operações de uma execução na ordem inversa, pulando as que não podem mais ser revertidas.
- `corrections` guarda cada recategorização ou renomeação feita pelo usuário. O classificador usa as mais recentes como exemplos.
- `attempts` e `last_error` ficam no banco, e não em memória, para que um arquivo problemático não volte a ser tentado a cada execução.
- O tokenizador remove acentos, então buscar "orcamento" encontra "orçamento". Nomes e caminhos entram normalizados em NFC.
- O banco abre em modo WAL, com um único escritor por vez.
- Mudanças de esquema entram como migrações numeradas em `migrations/`, nunca editando uma migração já publicada.

## Configuração

Todo o comportamento ajustável fica em um único arquivo YAML, e o repositório traz um exemplo comentado. O usuário muda categorias e regras sem tocar no código.

```yaml
language: pt-BR              # en | pt-BR: idioma dos nomes gerados e dos padrões abaixo

inputs:
  - ~/Downloads

output_root: ~/Downloads/Organizado   # padrão conforme language
review_folder: _Revisar               # padrão conforme language

provider:
  name: anthropic
  model: <modelo>            # definido pelo usuário
  max_chars_per_file: 4000
  requests_per_minute: 30
  max_retries: 3
  timeout_seconds: 60

limits:
  max_files_per_run: 200
  stable_seconds: 60         # tamanho e data inalterados por este tempo
  max_attempts: 3            # depois disso o arquivo vira "failed"
  confidence_threshold: 0.7
  scanned_pdf_max_pages: 3

naming:
  pattern: "{date}_{subject}"   # ex.: 2026-11-16_nota-fiscal-energia.pdf
  date_order: DMY
  max_length: 80
  rename_confidence_threshold: 0.8

categories:                  # omitida, vale a lista padrão do idioma
  - name: Financeiro
    hint: boletos, notas fiscais, extratos, comprovantes
  - name: Contratos
    hint: contratos, termos, propostas assinadas
  - name: Instaladores
    hint: aplicativos, imagens de disco e pacotes de instalação
    rename: false            # mantém o nome original
  - name: Imagens
  - name: Outros

folders: as_unit             # as_unit | ignore

finder_tags:
  enabled: true              # etiqueta = nome da categoria

ignore:
  - "*.crdownload"
  - "*.part"
  - ".DS_Store"
  - ".localized"
  - "._*"

never_send:                  # classificados só por metadados
  - "*.pem"
  - "*.key"
  - ".env*"

sensitive:
  detect_secrets: true       # chaves privadas, tokens, credenciais

mcp:
  read_only: false
```

- As dicas (`hint`) de cada categoria entram no prompt e são a principal forma de corrigir classificações erradas.
- O arquivo é validado na inicialização; configuração inválida aborta a execução antes de tocar em qualquer arquivo.

## Estrutura do repositório e contribuição

A estrutura espelha o pipeline: cada etapa é um diretório, e os dois pontos de extensão mais prováveis (provedores e extratores) têm um arquivo por implementação.

```text
file-librarian/
├── README.md               # o que é, instalação, uso em 5 minutos
├── CONTRIBUTING.md         # setup, padrões, fluxo de PR
├── CODE_OF_CONDUCT.md
├── SECURITY.md             # como reportar vulnerabilidades
├── LICENSE
├── CHANGELOG.md
├── pyproject.toml
├── config.example.yaml
├── docs/
│   ├── PRD.md              # este documento
│   ├── architecture.md
│   ├── adding-a-provider.md
│   ├── adding-an-extractor.md
│   └── decisions/          # ADRs numerados
├── src/file_librarian/
│   ├── cli.py
│   ├── config.py
│   ├── models.py           # FileContext, Classification, Operation
│   ├── scanner.py
│   ├── extractors/         # um arquivo por formato
│   ├── providers/          # um arquivo por provedor
│   ├── planner.py
│   ├── executor.py
│   ├── db/
│   │   ├── store.py
│   │   └── migrations/
│   ├── search.py
│   ├── mcp_server.py       # servidor MCP (busca, recategorizar, desfazer)
│   └── scheduler/          # agente do launchd
├── tests/
│   ├── fixtures/           # arquivos de exemplo, sem dados reais
│   ├── unit/
│   └── integration/
└── .github/
    ├── workflows/ci.yml
    ├── ISSUE_TEMPLATE/
    └── PULL_REQUEST_TEMPLATE.md
```

Regras para manter o projeto fácil de contribuir:

- **Setup em um comando.** Clonar, instalar as dependências de desenvolvimento e rodar os testes deve levar menos de 10 minutos e não exigir chave de API.
- **Testes sem rede.** A suíte usa um provedor falso com respostas fixas. Nenhum teste chama uma API real nem toca em pastas do usuário; tudo roda em diretórios temporários.
- **CI obrigatória.** Lint, checagem de tipos e testes rodam em macOS a cada PR.
- **Issues rastreáveis.** Toda issue de funcionalidade cita o ID do requisito (RF-xx). Issues simples recebem o rótulo `good first issue`.
- **Decisões registradas.** Mudanças de arquitetura entram como ADR em `docs/decisions/` antes do código.
- **Guias de extensão.** Adicionar um provedor ou um extrator tem um guia passo a passo próprio, com um exemplo mínimo.
- **Commits e versões.** Conventional Commits e versionamento semântico, com `CHANGELOG.md` atualizado a cada release.
- **Dados de teste.** Nenhum arquivo pessoal entra no repositório; os fixtures são sintéticos.
- **Idioma.** Código, comentários, commits, issues, pull requests e documentação em inglês. Os nomes de categorias e de pastas como `_Revisar` são valores de configuração do usuário e podem ficar em qualquer idioma; os padrões do projeto são em inglês.

## Roadmap

São cinco marcos em sequência, cada um com um critério de saída verificável. As datas ficam em aberto até o M0 terminar.

| Marco | Entrega | Critério de saída |
| --- | --- | --- |
| M0 Fundação | Repositório, CI, esquema do banco, CLI com comandos vazios, documentos de contribuição, protótipo da leitura da origem do download | CI verde em macOS; um contribuidor externo consegue rodar os testes seguindo só o `CONTRIBUTING.md` |
| M1 MVP | Todos os requisitos P0 com o provedor `anthropic` | Uma pasta de teste com 50 arquivos variados, incluindo uma subpasta, um `.app` e um arquivo com credenciais, é organizada, pesquisada e desfeita sem perda |
| M2 Uso diário | `flib schedule`, `flib reconcile`, `flib init`, imagens e PDFs escaneados, etiquetas do Finder, aprendizado com correções, arquivos do iCloud, servidor MCP, provedor `claude-code` | Duas semanas rodando todo dia na máquina do mantenedor sem intervenção manual |
| M3 Aberto | Provedores `ollama` e `openai-compat`, filtros de busca, `flib reprocess`, `flib doctor`, guias de extensão completos | Um provedor ou extrator adicionado por alguém de fora do projeto |
| v1.0 | Duplicatas, busca semântica opcional, resumo periódico, pacote publicado no PyPI | Esquema do banco e formato de configuração declarados estáveis |

## Métricas de sucesso

O projeto funciona se o usuário para de abrir a pasta Downloads para procurar coisas. As metas abaixo são propostas iniciais, a calibrar depois do M2.

| Métrica | Meta | Como medir |
| --- | --- | --- |
| Perda de arquivos | Zero | Todo hash que entrou tem um caminho válido no banco após `flib reconcile` |
| Classificação automática | 90% ou mais dos arquivos fora de `_Revisar` | Contagem por `status` na tabela `files` |
| Categoria correta | 95% ou mais | Revisão manual de uma amostra de 50 arquivos |
| Nome correto | 85% ou mais dos nomes gerados aceitos sem ajuste | A mesma amostra de 50 arquivos, mais a contagem de renomeações em `corrections` |
| Erros repetidos | Queda mês a mês das correções por 100 arquivos | Contagem em `corrections` por período |
| Fila de revisão | Nenhum arquivo parado em `_Revisar` há mais de 30 dias | Consulta por `status` e data |
| Qualidade da busca | Arquivo certo entre os 5 primeiros em 90% das consultas | Conjunto fixo de 20 consultas de teste |
| Tempo de setup do contribuidor | Menos de 10 minutos | Do clone ao primeiro teste passando |

## Riscos e questões em aberto

O maior risco é a IA errar sem ninguém ver, e por isso a reversão e a pasta `_Revisar` são requisitos P0.

| Risco | Mitigação |
| --- | --- |
| Classificação errada em execução desassistida | Limiar de confiança, categorias fechadas, `flib undo`, nome original sempre guardado, aprendizado com correções |
| Nome gerado pior que o original | Limiar próprio para renomear, política por categoria, nome original guardado e métrica de nomes aceitos |
| Resposta malformada ou inventada do modelo | Validação por esquema; resposta inválida vira `_Revisar`, nunca uma ação |
| Texto dentro de um arquivo tentando instruir o modelo | O modelo só devolve dados; o planejador limita o destino às pastas configuradas e sanitiza o nome |
| Conteúdo sensível enviado a um provedor remoto | Lista `never_send`, detecção de segredos pelo conteúdo, limite de caracteres, provedor local como alternativa |
| Aplicativo ou projeto quebrado por reorganização interna | Subpastas e `.app` tratados como unidade, nunca abertos |
| Duas execuções ou o MCP escrevendo ao mesmo tempo | Trava de instância única e banco em modo WAL |
| Arquivo que sempre falha consumindo custo todo dia | Contagem de tentativas no banco e status `failed` |
| Custo inesperado na primeira execução em pasta grande | Teto de arquivos por execução, estimativa de custo no modo simulação e `flib init` com amostra |
| Banco e disco fora de sincronia após mudanças manuais | `flib reconcile` e status `missing` |
| Reversão incompleta depois de mudanças manuais | Conferência de hash antes de reverter e lista explícita do que foi pulado |
| Assistente induzido a mover arquivos pelas ferramentas de escrita do MCP | Destino limitado às categorias configuradas, operações registradas no diário e reversíveis, modo somente leitura disponível |
| Permissões de privacidade do macOS bloqueando o acesso às pastas de entrada na execução agendada | `flib schedule` testa o acesso e orienta a liberar a permissão; sem acesso, a execução falha com mensagem clara e código de saída diferente de zero |
| Pasta de entrada ou de destino dentro do iCloud Drive | Arquivos só na nuvem são pulados; movimentação entre volumes confere o hash antes de remover a origem |

Decisões em aberto (as marcadas foram tomadas no M0):

- [x] Licença: MIT
- [x] Versão mínima do macOS: só a versão atual (ADR 0002)
- [x] Destino dos arquivos: subpasta dentro de Downloads, com nome conforme o idioma
- [ ] Guardar ou não um trecho do texto extraído no banco (melhora a busca, aumenta o tamanho e a exposição de conteúdo)
- [ ] Teto de custo aceitável por execução diária
- [ ] Subpastas em Downloads: mover inteiras para uma categoria ou apenas ignorar (o rascunho assume mover inteiras)
- [ ] Destino dos arquivos sensíveis: ficam onde estão ou vão para uma pasta própria
