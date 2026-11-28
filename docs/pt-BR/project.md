# Contribuições, segurança e mudanças

## Contribuir { #contributing }

Contribuições são bem-vindas. Comece pelo
[CONTRIBUTING.md](https://github.com/luizpereiradev/file-librarian/blob/main/CONTRIBUTING.md),
pela [arquitetura](https://luizpereiradev.github.io/file-librarian/architecture/) (em inglês)
e pelos [requisitos](https://luizpereiradev.github.io/file-librarian/requirements/) (em inglês).
Adicionar um formato de arquivo ou um provedor de IA tem um guia próprio:
[adicionar um extrator](https://luizpereiradev.github.io/file-librarian/adding-an-extractor/) (em inglês)
e [adicionar um provedor](https://luizpereiradev.github.io/file-librarian/adding-a-provider/) (em inglês).
`uv run pytest` roda a suíte inteira offline, em paralelo em todos os núcleos
(`-n 0` para um processo só). Dúvidas vão para as
[Discussions](https://github.com/luizpereiradev/file-librarian/discussions).

Este site é gerado a partir de `docs/` com o MkDocs e o tema Material:

```sh
uv run --group docs python -m scripts.docs serve     # gera os dois idiomas e serve em http://127.0.0.1:8000
```

As páginas em inglês ficam em `docs/` e as versões em português do Brasil em
`docs/pt-BR/` (com os mesmos nomes de arquivo). Uma página pública nova
precisa da sua tradução antes de entrar na navegação em português. A
referência da CLI é gerada a partir da própria linha de comando
(`uv run python -m scripts.docs_cli`), e as capturas de tela e os exemplos de
terminal a partir da [biblioteca de demonstração](getting-started.md#try-it-on-a-demo-library)
(`uv run python -m scripts.screenshots`, `uv run python -m scripts.docs_samples`).

## Segurança { #security }

Relate vulnerabilidades em particular, como descreve o
[SECURITY.md](https://github.com/luizpereiradev/file-librarian/blob/main/SECURITY.md),
nunca numa issue pública. Ele também diz o que está no escopo e como a cadeia
de suprimentos das dependências é protegida
([ADR 0003](https://luizpereiradev.github.io/file-librarian/decisions/0003-supply-chain/), em inglês).
O que o flib envia, guarda e protege está em [Privacidade e segurança](privacy.md).

## Changelog { #changelog }

O [CHANGELOG.md](https://github.com/luizpereiradev/file-librarian/blob/main/CHANGELOG.md)
lista o que entrou em cada versão, e o que está na próxima.

## Roadmap { #roadmap }

Tudo o que foi planejado para M0–M3 e o backlog P2 está implementado; os
[requisitos](https://luizpereiradev.github.io/file-librarian/requirements/) (em inglês)
mostram o status de cada item. Próximos passos: a v0.1.0 depois da validação
com uso diário, e então o empacotamento para a v1.0 (uma versão no PyPI).

## Licença { #license }

[MIT](https://github.com/luizpereiradev/file-librarian/blob/main/LICENSE). A
arte do ícone (`src/file_librarian/assets/icon-1024.png`) foi gerada por IA
para este projeto.
