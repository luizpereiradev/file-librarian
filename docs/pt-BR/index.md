---
title: flib, uma pasta Downloads organizada no seu Mac
hide:
  - navigation
---

# flib

**O flib mantém organizada a pasta Downloads do seu Mac: uma IA lê cada
arquivo novo e escolhe a categoria e o nome, código determinístico move o
arquivo, e um índice SQLite local encontra tudo de novo.**

`documento(3).pdf` vira `Financeiro/2026-10-05_conta-de-agua.pdf`, e
`flib search "conta de água"` encontra o arquivo meses depois, com o valor e o
vencimento que a IA leu nele.

<div class="flib-strip" markdown>

<figure>
<img src="assets/screenshots/pt-BR/menu.webp" alt="O gato do flib na barra de menus do macOS, com o menu aberto: 5 arquivos para revisar, 1 com falha, 1 vencido, 2 vencem em breve; Sem agendamento; Abrir o flib, Revisão (5), Vencimentos (3), Organizar agora, Configurações…, Sair" width="520">
<figcaption>O gato na barra de menus diz o que precisa de você.</figcaption>
</figure>

<figure>
<img src="assets/screenshots/pt-BR/review.webp" alt="A página Revisão do flib ui: quatro fotos sobre as quais a IA ficou em dúvida, agrupadas sob uma sugestão com o botão Aceitar Trabalho para os 4, e uma receita numa categoria que não está configurada" width="720">
<figcaption>Revisão: aceite a sugestão da IA ou corrija.</figcaption>
</figure>

<figure>
<img src="assets/screenshots/pt-BR/search.webp" alt="A página Busca do flib ui: três resultados para a palavra fatura, duas faturas e uma conta de energia, com totais por moeda" width="720">
<figcaption>Busca: por palavras, filtros e valores, com totais por moeda.</figcaption>
</figure>

</div>

## Instalação { #install }

macOS (versão atual) e [uv](https://docs.astral.sh/uv/) 0.13 ou mais recente
(`brew install uv`). Depois:

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
./scripts/install.sh
flib init
```

O `flib init` faz algumas perguntas, testa o flib em *cópias* dos seus
downloads recentes e oferece a execução diária. [Primeiros passos](getting-started.md)
explica cada etapa.

## O que sai do seu Mac { #what-leaves-your-mac }

- **Para o provedor de IA que você escolher:** o nome, os metadados e no
  máximo 4.000 caracteres do texto de cada arquivo novo. Com o
  [Ollama](providers.md#a-local-model-with-ollama-ollama), absolutamente nada.
- **Nunca:** arquivos com nome de credencial ou com segredos dentro (eles
  ficam onde estão, sem que nenhuma IA os leia), o seu índice, os seus logs, o
  seu histórico de desfazer.
- **Só se você ativar:** a busca por significado e o servidor MCP, cada um
  dizendo o que envia e para onde. [Privacidade e segurança](privacy.md) tem
  todos os detalhes.

## O que ele promete { #what-it-promises }

- **A IA só decide; quem move os arquivos é o código.** Nada é apagado, nada
  é sobrescrito.
- **Toda execução pode ser desfeita**, um arquivo ou uma execução inteira.
- **Segredos ficam onde estão**, e nada é baixado do iCloud.

!!! tip "Experimente sem mexer nos seus arquivos"
    Num clone do repositório, `uv sync && uv run python -m scripts.demo /tmp/flib-demo`
    monta uma pasta Downloads fictícia depois de alguns dias de flib, totalmente
    offline, e mostra os comandos para explorá-la. Veja
    [Primeiros passos](getting-started.md#try-it-on-a-demo-library).

!!! note "Status: alfa, preparando a v0.1.0"
    Tudo o que está documentado aqui está implementado e testado. A primeira
    versão recebe a tag depois de duas semanas de uso diário
    ([#24](https://github.com/luizpereiradev/file-librarian/issues/24)); até
    lá, instale a partir do repositório, como mostrado acima. Só para macOS.
