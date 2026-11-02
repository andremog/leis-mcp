<div align="center">

<img src="https://github.com/mazulo/leis-mcp/raw/main/docs/banner.jpg" alt="§ leis-mcp: a lei exata, direto no seu assistente de IA" width="820">

### Busque e cite o texto oficial de mais de 1,2 milhão de normas brasileiras sem sair do seu assistente.

[![PyPI](https://img.shields.io/pypi/v/leis-mcp?color=f2b84b&logo=pypi&logoColor=white)](https://pypi.org/project/leis-mcp/)
[![CI](https://github.com/mazulo/leis-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/mazulo/leis-mcp/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%C2%B7%20HTTP-f2b84b)](https://modelcontextprotocol.io/)
[![Normas](https://img.shields.io/badge/normas-1.218.990-43d39e)](https://github.com/ctresb/leis)
[![uv](https://img.shields.io/badge/uv-ready-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](#docker)
[![License: MIT](https://img.shields.io/badge/license-MIT-8d97ad)](https://github.com/mazulo/leis-mcp/blob/main/LICENSE)

[Início rápido](#início-rápido) · [Ferramentas](#ferramentas) · [Clientes](#clientes) · [Como funciona](#como-funciona) · [Limitações](#limitações)

<br>

<a href="https://gist.github.com/user-attachments/assets/d19cb5e2-fcf0-48f7-a5db-8e6663d1ac0f"><img src="https://github.com/mazulo/leis-mcp/raw/main/docs/demo.jpg" alt="Demonstração do leis-mcp em vídeo (25 s): o assistente devolve o texto exato da Lei do Inquilinato, art. 23, III" width="820"></a>

<sub>Clique na imagem para ver a demonstração (25 s, com música).</sub>

</div>

<br>

**leis-mcp** é um servidor [MCP](https://modelcontextprotocol.io/) que dá ao Claude Code, ao Claude Desktop e a qualquer cliente MCP acesso à legislação brasileira do repositório público [ctresb/leis](https://github.com/ctresb/leis). Em vez de parafrasear de memória, o assistente busca a norma, lê o dispositivo e cita o texto exato, com a fonte oficial e os avisos de revogação.

```text
ler_artigo("8245/1991", "23", inciso="III")
→ "III - restituir o imóvel, finda a locação, no estado em que o recebeu, salvo as deteriorações decorrentes do seu uso normal;"
```

> [!IMPORTANT]
> O texto é convertido automaticamente das fontes oficiais e pode conter erros de conversão ou OCR. Ele não substitui a publicação no Diário Oficial. Toda resposta do servidor traz o link `fonte` e esse aviso.

## Destaques

- 📜 **Texto exato.** Artigo, parágrafo, inciso e alínea, com o caput como contexto e a citação pronta (`art. 23, III`).
- 🔗 **Sempre com fonte.** Cada resposta traz o link oficial (`normas.leg.br`, LexML) e o caminho no acervo.
- ⚠️ **Avisos automáticos.** Norma revogada, texto não compilado, dispositivo revogado ou vetado, trecho tachado.
- 🗂️ **Acervo inteiro.** Normas federais, estaduais, municipais e de reguladores, com mais de 40 apelidos (`CLT`, `CDC`, `LGPD`, `lei do inquilinato`…).
- ⚡ **Rápido e local.** Índice SQLite em ~6 s, conteúdo baixado sob demanda e conferido pelo hash do blob. O que está em cache funciona offline.
- 🔒 **Só leitura.** O repositório de origem nunca é alterado.

## Início rápido

Requisitos: [uv](https://docs.astral.sh/uv/) e git. O [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) é recomendado.

```bash
uvx leis-mcp index                          # clone leve + índice (~1 min na primeira vez)
claude mcp add leis -- uvx leis-mcp serve
```

Pronto. Pergunte ao Claude: *"O inquilino é obrigado a devolver o imóvel como recebeu? Cite a lei."*

> [!TIP]
> `uvx leis-mcp warm` pré-carrega as ~40 normas dos apelidos, para que `buscar_texto` sem `norma` já tenha onde procurar.

<details>
<summary><b>A partir do código-fonte</b></summary>

```bash
git clone https://github.com/mazulo/leis-mcp && cd leis-mcp
uv sync
uv run leis-mcp index
claude mcp add leis -- uv --directory "$PWD" run leis-mcp serve
```

</details>

## Ferramentas

| Ferramenta | Para quê |
|---|---|
| `buscar_norma(referencia?, numero?, ano?, tipo?, esfera?, uf?, limite)` | Acha a norma e devolve os metadados do cabeçalho (título, ementa, data, `revogada`, `versao_texto`), o caminho, o link do GitHub e a `fonte`. Aceita `"8.245"`, `"8245/1991"`, `"Lei 8.245/91"`, `"Lei nº 8.245, de 18 de outubro de 1991"`, `"LC 123/2006"`, `"DL 5.452"` e apelidos. |
| `ler_artigo(norma, artigo, paragrafo?, inciso?, alinea?, bloco?)` | Devolve o texto exato do dispositivo, o caput como contexto, a citação e os avisos. |
| `buscar_texto(consulta, norma?, esfera?, tipo?, ano?, uf?, limite)` | Procura uma palavra ou trecho numa norma (baixada na hora) ou em todas as normas já em cache. Ignora acentos e maiúsculas e indica o artigo de cada ocorrência. |
| `listar_normas(esfera?, tipo?, ano?, uf?, municipio?, orgao?, limite, offset)` | Navegação em níveis: esfera, depois tipo, UF ou órgão, depois ano, depois a lista de normas. |
| Recurso `leis://apelidos` | Lista os apelidos aceitos. |

<details>
<summary><b>Exemplo de resposta</b> de <code>ler_artigo("8245/1991", "23", inciso="III")</code></summary>

```json
{
  "norma": {"titulo": "Lei nº 8.245 de 18/10/1991", "ementa": "Dispõe sobre as locações dos imóveis urbanos…",
            "revogada": false, "versao_texto": "compilada (texto vigente)",
            "caminho": "federal/leis-ordinarias/1991/lei-8245-1991-10-18.md"},
  "dispositivo": {"citacao": "art. 23, III",
                  "texto": "III - restituir o imóvel, finda a locação, no estado em que o recebeu, salvo as deteriorações decorrentes do seu uso normal;",
                  "caput": "**Art. 23.** O locatário é obrigado a:"},
  "avisos": [],
  "fonte": "https://normas.leg.br/?urn=urn:lex:br:federal:lei:1991-10-18;8245",
  "aviso_legal": "Texto convertido automaticamente das fontes oficiais (…). Não substitui a publicação no Diário Oficial."
}
```

</details>

<details>
<summary><b>Apelidos aceitos</b></summary>

- **Constituição:** `constituição`/`CF`, `ADCT`
- **Códigos:** `código civil`/`CC`, `CPC`, `código penal`/`CP`, `CPP`, `CLT`, `CDC`, `CTN`, `CTB`, `código eleitoral`, `código florestal`
- **Estatutos:** `ECA`, `estatuto do idoso`, `estatuto da OAB`, `estatuto da cidade`, `estatuto do desarmamento`, `LBI`
- **Leis:** `lei do inquilinato`, `lei dos juizados`, `LGPD`, `marco civil da internet`, `maria da penha`, `nova lei de licitações`, `lei de improbidade administrativa`, `lei de drogas`, `LEP`, `lei de falências`, `LINDB`, `lei do mandado de segurança`, `lei da ação civil pública`, `LAI`, `LRF`, `simples nacional`, `lei anticorrupção`, `lei de arbitragem`, `lei de abuso de autoridade`

</details>

### Exemplos de perguntas

| Pergunta ao Claude | O que o servidor faz |
|---|---|
| "O inquilino é obrigado a devolver o imóvel como recebeu? Cite a lei." | `ler_artigo("lei do inquilinato", "23", inciso="III")` |
| "A caução do aluguel pode ficar em poupança?" | `ler_artigo("8245/1991", "38", paragrafo="2")` |
| "Serviço mal feito: o que o CDC garante?" | `ler_artigo("CDC", "20")` |
| "Preciso de advogado no juizado especial?" | `ler_artigo("9099/1995", "9")` |
| "O que diz o ADCT, art. 10?" | `ler_artigo("ADCT", "10")` |
| "Onde a CLT fala em grupo econômico?" | `buscar_texto("grupo econômico", norma="CLT")` |

## Clientes

### Claude Code

```bash
claude mcp add leis -- uvx leis-mcp serve
```

Use `-s user` para disponibilizar em todos os projetos e `-s project` para gravar em `.mcp.json`.

### Claude Desktop

Em `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`):

```json
{
  "mcpServers": {
    "leis": {
      "command": "uvx",
      "args": ["leis-mcp", "serve"]
    }
  }
}
```

Se o Desktop não achar o `uvx`, use o caminho absoluto (`which uvx`).

### Docker

```bash
docker compose build
docker compose run --rm leis-mcp index
docker compose run --rm leis-mcp warm
docker compose run --rm -i leis-mcp serve                 # stdio
docker compose --profile http up leis-mcp-http            # HTTP: http://localhost:8000/mcp
```

Os dados ficam no volume `leis-data`. Para registrar no Claude Code:

```bash
claude mcp add leis -- docker compose -f /caminho/para/leis-mcp/docker-compose.yml run --rm -i leis-mcp serve
```

No Claude Desktop, troque o comando por `"command": "docker", "args": ["compose", "-f", "/caminho/para/leis-mcp/docker-compose.yml", "run", "--rm", "-i", "leis-mcp", "serve"]`.

### HTTP

```bash
uvx leis-mcp serve --transport streamable-http --port 8000      # servido em /mcp
claude mcp add --transport http leis http://localhost:8000/mcp
```

Com `LEIS_MCP_API_KEY` definida, toda requisição ao `/mcp` precisa do cabeçalho `Authorization: Bearer <chave>`; sem ele, a resposta é `401`. `GET /health` fica sempre aberto, para o health check da plataforma. Sem a variável, nada muda. A porta padrão vem de `LEIS_MCP_PORT` ou `PORT`, e o host de `LEIS_MCP_HOST` (a imagem Docker usa `0.0.0.0`).

### Deploy no Railway

O `railway.json` usa o `Dockerfile` e sobe o servidor HTTP com health check em `/health`.

1. Crie um serviço a partir do repositório no GitHub.
2. Adicione um volume montado em `/data` (é o `LEIS_MCP_HOME` da imagem). Reserve ~1 GB: clone leve (~250 MB), índice (~350 MB) e cache.
3. Variáveis do serviço:
   - `LEIS_MCP_API_KEY`: uma chave longa e aleatória (por exemplo, `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
   - `LEIS_MCP_WARM_ON_START=1` (opcional): pré-carrega os apelidos em segundo plano a cada início.
   - O `PORT` é definido pelo Railway.
4. Em *Networking*, gere um domínio público. O endpoint fica em `https://<app>.up.railway.app/mcp`.

No primeiro deploy o índice é montado no volume (cerca de 1 a 2 minutos) antes de o servidor responder; nos seguintes ele já existe. Para atualizar o acervo, rode `leis-mcp update` no serviço (por exemplo, via `railway ssh`).

No Claude Code (inclusive na web, pelo `.mcp.json` do repositório):

```bash
claude mcp add --transport http leis https://<app>.up.railway.app/mcp --header "Authorization: Bearer <chave>"
```

```json
{
  "mcpServers": {
    "leis": {
      "type": "http",
      "url": "https://<app>.up.railway.app/mcp",
      "headers": { "Authorization": "Bearer ${LEIS_MCP_API_KEY}" }
    }
  }
}
```

No `.mcp.json` versionado, use a referência `${LEIS_MCP_API_KEY}` e nunca a chave em si.

No Claude.ai, em *Configurações > Conectores > Adicionar conector personalizado*, informe a URL e, em *Request headers*, o cabeçalho `Authorization` com o valor `Bearer <chave>` (com o espaço). Esse recurso está em beta e só aparece para algumas organizações; sem ele, o Claude.ai aceita apenas OAuth ou servidor sem autenticação, e o leis-mcp não aceita a chave na URL.

## Como funciona

```mermaid
flowchart LR
    client["Assistente<br>cliente MCP"] -- "stdio ou HTTP" --> server["leis-mcp"]
    server --> index[("Índice SQLite<br>1,2 M normas")]
    server --> cache[("Cache por blob")]
    cache -. "sob demanda,<br>fixado no commit" .-> raw["raw.githubusercontent.com<br>ctresb/leis"]
    index -. "git ls-tree" .-> clone["Clone leve<br>só árvores"]
```

| Peça | O que faz |
|---|---|
| Clone leve | `git clone --depth 1 --filter=blob:none --sparse` baixa só as árvores (~250 MB), sem o conteúdo das normas. |
| Índice SQLite | `git ls-tree` sobre o commit gera uma tabela com caminho, blob, esfera, tipo, ano, número, data, UF, município e órgão (~1,2 M linhas em ~6 s, ~350 MB). Não usa rede. |
| Conteúdo sob demanda | Cada norma é baixada do `raw.githubusercontent.com` fixada no commit indexado (~0,4 s), conferida contra o hash do blob git e guardada em `cache/objects/<blob>`. Se a rede falhar, o fallback é `git cat-file`, lento na primeira vez (~30 s). |
| Parser | Encontra artigos, parágrafos, incisos e alíneas e tolera as variações reais do acervo. |
| Busca textual | Usa [ripgrep](https://github.com/BurntSushi/ripgrep) nos arquivos em cache, ou Python puro se o `rg` não estiver instalado. |

Os dados ficam em `~/.cache/leis-mcp`, configurável com `LEIS_MCP_HOME`.

<details>
<summary><b>Variações que o parser reconhece</b></summary>

- **Artigos:** `**Art. 1º**`, `Art. 1o`, `Art. 1.º`, `Art. 10.`, `Art. 1.000.`, `Art. 4º-A`, `Art. 359-M-A`, artigo sem negrito e `Art. l97.` (OCR que troca 1 por l).
- **Parágrafos:** `§ 2º`, `§ 2°`, `§2º`, `§ 3º-A`, `Parágrafo único`.
- **Incisos e alíneas:** incisos com `-`, `–`, `—` (com ou sem espaço) e alíneas `a)`.
- **Fim do artigo:** o artigo termina no próximo artigo, num título (`#`) ou na assinatura (`Brasília, ...`).
- **Numeração reiniciada:** quando a numeração recomeça na mesma norma (CF + ADCT, o decreto-lei que aprova a CLT), o texto é dividido em blocos. Por padrão vale o maior bloco, e `bloco="transitórias"` escolhe outro.

</details>

<details>
<summary><b>Estrutura do código</b></summary>

```
src/leis_mcp/
  config.py, models.py, errors.py, text.py
  messages.py        todo texto devolvido ao usuário (pt-BR)
  catalog/           GitRepository, PathParser, IndexBuilder / NormIndex (SQLite)
  references/        ReferenceParser + tabelas de apelidos
  content/           FrontMatterParser, ContentStore (cache por blob)
  legal_text/        labels (Art., §, inciso, alínea), DocumentParser, ProvisionExtractor, notices
  search/            AccentInsensitivePattern, RipgrepEngine / PythonEngine, TextSearch
  services/          NormResolver, NormLoader, NormPresenter, LegislationService (fachada)
  server/            LeisServer: adaptador MCP (nomes de ferramentas e parâmetros em pt-BR)
  cli.py             Cli: serve, index, update, status, warm, read
```

O código é em inglês; só o que chega ao usuário (respostas, erros, descrições das ferramentas) é pt-BR.

</details>

## CLI

Rode com `uvx leis-mcp <comando>` ou, no código-fonte, com `uv run leis-mcp <comando>`:

```bash
leis-mcp index                         # clone leve + índice
leis-mcp update                        # git fetch + reindexação
leis-mcp status                        # commit indexado, total, itens em cache
leis-mcp warm                          # pré-carrega as leis dos apelidos
leis-mcp warm --no-aliases --sphere federal --kind leis-complementares
leis-mcp read "Lei 8.245/91" 23 -c III # testa sem cliente MCP (-p parágrafo, -c inciso, -s alínea, -b bloco)
leis-mcp serve                         # stdio (padrão)
```

Se o índice não existir, `serve` gera o índice antes de iniciar. Ainda assim, rode `index` antes para o cliente não esperar.

## Testes

```bash
uv run pytest -m "not integration"   # unitários, sem rede (fixtures pequenas)
uv run pytest                        # tudo, incluindo integração com conteúdo real e e2e MCP via stdio
```

Os testes de integração usam o índice de `LEIS_MCP_HOME` e são pulados se ele não existir. Eles cobrem a Lei 8.245/91 (art. 23, III e art. 38, § 2º), o CDC (art. 20, I), o CC (arts. 422 e 1.000), a Lei 9.099/95 (art. 9º), a CLT, a CF e o ADCT, e todos os apelidos. O teste e2e sobe o servidor por stdio com o cliente MCP oficial e chama todas as ferramentas.

## Limitações

- **Busca textual sem o acervo inteiro:** `buscar_texto` sem `norma` procura só nas normas já em cache. Baixar as 1,2 M normas não é viável. Use `warm` para ampliar o cache. Não há busca semântica nem embeddings.
- **Ementas e títulos:** só aparecem para normas já baixadas. O índice tem apenas o que se deduz do caminho.
- **Referências ambíguas:** sem ano (por exemplo, `"decreto 5452"`), o servidor pede o ano. `buscar_norma` lista as opções.
- **Qualidade do texto:** a conversão automática e o OCR podem ter erros (por exemplo, `servir - se`). O texto é devolvido como está e o parser não o corrige.
- **Revogação:** o aviso depende do cabeçalho (`revogada`, `versao_texto`) e das notas no próprio texto. Não há verificação independente de vigência.
- **Metadados de normas não federais:** o número e a data de normas estaduais, municipais e de reguladores vêm de heurísticas sobre o nome do arquivo.
- **Rede:** o conteúdo vem do `raw.githubusercontent.com`, que pode impor limite de requisições. O que já está em cache funciona offline.

## Créditos

O acervo é o [ctresb/leis](https://github.com/ctresb/leis), que converte as normas das fontes oficiais para Markdown. O leis-mcp só lê esse repositório. A trilha do vídeo é *Intergalactic*, de Alex Jones / Xander Jones, da Biblioteca de áudio do YouTube.
