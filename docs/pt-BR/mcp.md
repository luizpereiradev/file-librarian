# Use pelo Claude (MCP)

O `flib mcp` é um servidor [MCP](https://modelcontextprotocol.io) local via
stdio, para que o Claude (ou qualquer cliente MCP) responda "cadê a conta de
luz do mês passado?" a partir da sua biblioteca. Ele só roda enquanto o
cliente está aberto e lê o mesmo banco de dados que o `flib search`. Ele mesmo
não usa a rede, com uma exceção: com a busca por significado ativada e um
`search.semantic.base_url` remoto, uma busca por significado envia a consulta
para lá (e a ferramenta `search_files` avisa isso).

| Ferramenta | O que faz |
| --- | --- |
| `search_files` | Busca em nomes, descrições e tags, com os mesmos filtros do `flib search`: categoria, extensão, período, emissor, tipo, valor, moeda, vencimento e período do documento; `semantic: true` também busca por significado ([Busca por significado](daily-use.md#search-by-meaning-optional)); duplicados só com `include_duplicates` |
| `summarize_amounts` | Soma os valores dos documentos encontrados por moeda, opcionalmente por emissor, mês, ano, tipo ou categoria, como o `flib search --sum` ("quanto paguei para a Power Co em 2026?") |
| `list_due` | Contas, faturas e impostos com vencimento, vencidos e por vencer, com totais por moeda, como o `flib due` ("o que vence esta semana?") |
| `get_file` | Tudo o que o índice sabe sobre um arquivo: onde está e onde esteve, categoria, descrição, tags, tipo, emissor, valor, datas e período, hash, tamanho, origem do download, se está só no iCloud, por que está na revisão (se estiver) e o histórico (movimentações, renomeações, correções, desfazeres com o momento em que cada um foi desfeito; mais recentes primeiro, últimos 20) |
| `list_recent` | Os arquivos organizados mais recentemente, mais recentes primeiro, opcionalmente de uma categoria |
| `list_review` | O que espera na pasta de revisão, mais recentes primeiro, com a confiança da IA e o motivo de cada arquivo estar lá |
| `get_stats` | Contagens por status e por categoria, tamanho total arquivado, a última execução e o custo dela no provedor, e o custo total do mês |
| `recategorize_file` | Move um arquivo para outra categoria configurada e/ou o renomeia, como o `flib fix` |
| `undo` | Desfaz uma execução ou a última movimentação de um arquivo, como o `flib undo` |

Um arquivo na revisão diz por que está lá: `low_confidence` (a confiança da
IA ficou abaixo de `limits.confidence_threshold`), `unknown_category` (a IA
escolheu uma categoria que você não configurou), `category_removed` (`flib reprocess
--remove-category --to-review`) ou `moved_by_hand` (o `flib reconcile` o
encontrou na pasta de revisão). Os dois primeiros vêm com a categoria que a
IA sugeriu.

Os custos são estimativas, registradas por execução; uma execução com preços
desconhecidos fica sem custo registrado, em vez de um custo zero, e o
`get_stats` conta essas execuções à parte
([Custos e limites](providers.md#costs-and-caps)).

Os resultados trazem metadados, descrições e os campos que a IA leu em cada
documento, nunca o conteúdo dos arquivos; arquivos sensíveis não mostram
descrição, tags, campos nem origem, e os valores deles nunca entram em somas.
As ferramentas de escrita passam pelo mesmo diário que a CLI, então o
`flib undo` desfaz o que um assistente fez, e elas se recusam a rodar
enquanto uma execução está em andamento. Use `mcp.read_only: true` para
oferecer só as ferramentas de leitura.

## 1. Ative { #1-enable-it }

O servidor MCP é opcional, porque o SDK dele adiciona 16 pacotes de que o
resto do flib não precisa. Responda sim quando o `./scripts/install.sh`
perguntar, ou rode-o de novo com a opção:

```sh
./scripts/install.sh --with-mcp
```

As atualizações seguintes o mantêm; `--without-mcp` o remove. Sem ele, o
`flib mcp` explica como ativá-lo.

## 2. Adicione ao Claude Code { #2-add-it-to-claude-code }

```sh
claude mcp add --scope user file-librarian -- ~/.local/bin/flib mcp
```

## Ou ao Claude Desktop { #or-to-claude-desktop }

Adicione isto a
`~/Library/Application Support/Claude/claude_desktop_config.json` e reinicie
o app. O Desktop não expande `~` nem lê o `PATH` do seu shell, então informe
o caminho absoluto que o `./scripts/install.sh` mostrou:

```json
{
  "mcpServers": {
    "file-librarian": {
      "command": "/Users/you/.local/bin/flib",
      "args": ["mcp"]
    }
  }
}
```

Adicione `"--config", "/path/to/config.yaml"` a `args` se a sua configuração
não estiver no lugar padrão.

O que um assistente vê, e para onde ele envia, está em
[Privacidade e segurança](privacy.md#what-leaves-your-mac).
