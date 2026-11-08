<!-- Candidato revisado: 8b09e7677f64b584987841e8be3e2fc12c68eade (árvore 1928139a009ed9430181345b5e6b90808b304dfb), branch feat/parlamentar-020. Revisão de segurança independente. Veredito: PASS (sem P0/P1; 4 advertências P2). -->

# Revisão de segurança independente: leis-mcp 0.2.0 + overlay parlamentar (candidato `8b09e76`)

## Veredito: **PASS**

Não há achado P0 nem P1. Os dois bloqueios da revisão anterior estão corrigidos e foram reproduzidos de forma independente neste código:

- **P1-020-1**: um SQLite íntegro mas inutilizável já não substitui um índice saudável.
- **P1-020-2**: existem prazos totais, tetos de bytes e reserva de disco em todas as etapas, e uma recusa por orçamento não cai no build via git.

A autenticação HTTP continua valendo depois do merge, conferida com o servidor rodando localmente.

Ficam quatro advertências **P2**, nenhuma bloqueante para o escopo atual (serviço somente leitura, fonte do índice configurada pelo operador, coleta manual):

- **N1**: um índice de expressão faz o `integrity_check` consumir memória sem limite (arquivo de 739 B levou o processo a cerca de 1,75 GB de RSS).
- **N2**: os metadados das linhas não são conferidos contra o caminho, e o upstream pode escrever no namespace `parlamentar/`.
- **N3**: com `-v`, o log DEBUG do httpcore mostra o `Location` assinado, e erros do fallback git expõem caminhos internos ao cliente MCP.
- **N4**: banco em modo WAL é aceito, e os arquivos `*.tmp-shm` e `*.tmp-wal` ficam para trás.

As advertências P2-020-3, P2-020-5 e P2-020-6 da revisão anterior continuam total ou parcialmente abertas (detalhes abaixo).

Este PASS vale só para o gate de segurança deste artefato. Não autoriza push, merge nem deploy, que dependem também da QA independente e da autorização do dono (docs/parlamentar/plano-e-status.md, "Portões").

## Artefato verificado

- `(checkout)`, branch `feat/parlamentar-020`. `git rev-parse HEAD` = `8b09e7677f64b584987841e8be3e2fc12c68eade`, `HEAD^{tree}` = `1928139a009ed9430181345b5e6b90808b304dfb`, iguais aos informados. Árvore de trabalho limpa (só arquivos ignorados: `.venv`, `__pycache__`, `.ruff_cache`).
- Base do diff: `origin/cursor/parliamentary-overlay-bfbf` = `83cfe3cb…`, que é também o merge-base. O merge `703e89b` tem os pais `83cfe3c` e `3f275e267fe7de353a2f06bddf5a2230d21f19a6` (upstream 0.2.0). Depois dele vêm `87597d1` (reescrita do `snapshot.py`), `7678f7c` (merge de docs) e `8b09e76` (docs).
- Diff `origin/cursor/parliamentary-overlay-bfbf...feat/parlamentar-020`: 29 arquivos, +2279/−48. Não toca `src/leis_mcp/overlay/**` nem `src/leis_mcp/server/http.py`: o middleware de autenticação e o overlay são byte a byte os do PR 3, já revisado.

## O que foi executado e o que não foi

**Executado** (scripts e saídas em `(sandbox)/sec-review/probes/` e `.../work/`):

1. `uv run pytest -q -m "not integration"`: **216 passed, 22 deselected**.
2. `probes/p1_sqlite.py` roda `download()` de verdade, com MockTransport, sobre 25 SQLite e arquivos criados para o teste. Em cada caso há um índice anterior saudável e um arquivo de overlay, e o script compara as impressões digitais do diretório antes e depois. Resultados em `p1_sqlite_results.json`.
3. `probes/p_http.py` usa a pilha real httpx 0.28.1 + httpcore 1.0.9 com o cliente padrão do código (`_default_client`), contra servidores HTTPS locais com certificado autoassinado (`SSL_CERT_FILE`, `NO_PROXY=127.0.0.1,127.0.0.2`). São 22 cenários de redirect, tempo, tamanho, encoding, URL e TLS. Resultados em `p_http_results.json`.
4. `probes/p_http_logs.py` repete os cenários de redirect procurando o segredo nos logs, em nível INFO e em DEBUG.
5. Probes de SQLite hostil: `probes/disguise.py` (view e tabela virtual disfarçadas por `writable_schema`), `probes/p_check.py` e `probes/p_exprindex_dl.py` (CHECK e índice de expressão), além de testes de modo WAL e de metadados (`probes/p_metadata.py`).
6. Servidor real `leis-mcp serve --transport streamable-http` em 127.0.0.1, com `LEIS_MCP_API_KEY` aleatória e `LEIS_MCP_INDEX_URL` apontando para um servidor HTTPS local. Cobriu a subida a frio com download real, a autenticação, uma chamada de ferramenta, um segundo servidor com snapshot ausente e fallback git (URL de repositório local e falsa), e a busca da chave nos logs.
7. CLI real: `leis-mcp update` sobre um índice quebrado do mesmo commit, quatro `leis-mcp index` concorrentes, e caminhos com symlink no home.
8. Recusa por reserva de disco: `Limits(disk_reserve > livre)`.
9. Dependências: `uv export --frozen --no-dev`, depois `uvx pip-audit --no-deps` sobre 127 pacotes: **"No known vulnerabilities found"**. Versões relevantes: httpx 0.28.1, httpcore 1.0.9, h11 0.16.0, mcp 2.3.0, starlette 1.10.0, uvicorn 0.54.0.
10. Varredura de segredos no diff (padrões de token GitHub, AWS, Fly, chaves privadas, bearer): nada encontrado, só textos de documentação e testes sintéticos.

**Não executado:**

- Download do índice publicado real (GitHub `mazulo/leis-mcp`) e qualquer acesso a host de terceiro, além da consulta de advisories do pip-audit.
- Produção (`leis-mcp-andremog.fly.dev`), volume do Fly, build da imagem Docker, wheel e MCPB.
- `pytest -m integration`.
- Exaustão real de disco (simulada pela reserva) e OOM real numa VM de 512 MB. A memória foi medida e o OOM foi reproduzido com `ulimit -v`.
- Resolução de DNS lenta.
- Execução dos workflows do GitHub.

## Status dos achados anteriores (revisão `449dbdd`)

### P1-020-1: SQLite íntegro mas inutilizável substitui índice saudável. **CORRIGIDO**

Código: `src/leis_mcp/catalog/snapshot.py:341-418` (`_validate`, `_inspect`, `_unique_path_index`, `_accept`), `:245-248` (`installed_commit` valida), `:280-298` (validação antes de `os.replace`).

Em cada caso abaixo houve recusa com `SnapshotError` controlado, o índice anterior e o overlay ficaram byte a byte iguais (`unchanged: true`) e não sobrou nenhum `.tmp` ou `.partial`:

- a reprodução original da revisão anterior, `norms(path)` sem as colunas usadas (recusa: "colunas ausentes");
- `norms` como view, view recursiva extra e trigger (recusa: "view/trigger");
- FTS5 e tabela extra (recusa: "tabelas […]");
- índice único ausente, parcial ou em `(path, blob)`;
- `meta.total` divergente, `norms` do manifesto divergente e encolhimento abaixo de 50%;
- commit divergente ou não hexadecimal;
- linha com `..`, `./` ou caminho absoluto, blob de 1 byte, ausência de `constituicao.md` e arquivo que não é SQLite.

Também foram testados disfarces com `writable_schema`: uma linha `type='table'` com `CREATE VIEW meta …` ou com `CREATE  VIRTUAL TABLE meta USING fts5`. O próprio SQLite 3.45.1 os rejeita ("malformed database schema", "vtable constructor failed"), e a recusa vira `SnapshotError`.

Índice do mesmo commit quebrado: `leis-mcp update` registrou "Local index … is not usable", baixou de novo e instalou. A segunda execução devolveu `updated: false` só com o índice já válido.

**Resíduo:** o `integrity_check` avalia índices de expressão em `norms` sem teto de memória. Ver N1 (P2).

### P1-020-2: sem orçamentos de tamanho, expansão e tempo. **CORRIGIDO**

Código: `snapshot.py:72-86` (`Limits`), `:88-168` (`_Deadline`, `_DeadlineBackend`, `_DeadlineStream`, que limitam connect, TLS, leitura e escrita ao que resta do prazo da etapa), `:300-339` (`_fetch` e `_extract` com teto e checagem de espaço a cada 32 MiB), `:429-487` (`_get`), `:530-538` (reserva) e `:345-352` (progress handler preso ao prazo). `cli.py:199-213` não faz fallback git em `SnapshotResourceError`.

Pilha real httpx/httpcore com o cliente padrão:

| Cenário | Resultado | Tempo |
|---|---|---|
| Cabeçalhos lentos (1 linha/s) com `manifest_seconds=5` | `SnapshotResourceError` | 5,0 s |
| Corpo lento (1 byte a cada 0,5 s) com `archive_seconds=6` | `SnapshotResourceError` | 6,01 s |
| Handshake TLS travado com prazo de 4 s | `SnapshotResourceError` | 4,0 s |
| Manifesto de 70.000 bytes | recusa no teto de 64 KiB | — |
| `Content-Length: 999999999999` | recusa antes do corpo | — |
| Corpo chunked infinito com teto de 4 MiB | recusa no teto | 0,03 s |
| `Content-Encoding: gzip` | recusa | — |
| Bomba gzip (8 MiB de zeros) com `database_bytes=1 MiB` | recusa "extraído" | — |
| Reserva de disco maior que o espaço livre | `SnapshotResourceError` antes de gravar | — |

Em todas as recusas o diretório ficou intacto e sem temporários.

Pior caso total de uma preparação: cerca de 30 + 900 + 600 s, mais uma releitura em caso de 404 (o dobro, perto de 51 min). Esse tempo é finito.

**Limites conhecidos, não bloqueantes:**

- `_usable()` roda `quick_check` sem prazo (`snapshot.py:521-528`), mas só sobre um arquivo que já passou pela validação com prazo, ou sobre um índice local.
- O DNS (`getaddrinfo`) não é limitado pelo backend.
- O fallback git, quando o snapshot falha por motivo que não é de recurso, continua sem orçamento próprio. É o comportamento anterior à 0.2.0.

### P2-020-3: confiança na fonte e na rede do índice pronto. **PARCIALMENTE CORRIGIDO (resíduo aceito)**

- Corrigido: redirects manuais só em HTTPS (`snapshot.py:441, 541-544`); HTTPS→HTTP é recusado (confirmado). Laço de redirects recusado depois de 6 saltos. `trust_env=False` com proxy explícito apenas por `HTTPS_PROXY`/`NO_PROXY` (`:489-504`). TLS verificado; certificado não confiável vira `ConnectError` → `SnapshotError`.
- Em aberto: redirect para **outro host e endereço privado** em HTTPS continua aceito. O redirect de 127.0.0.1 para `https://127.0.0.2:<porta>/…` foi seguido e instalado.
- O hash e o `meta.commit` vêm do mesmo publicador, que é de terceiro (`mazulo/leis-mcp`, `config.py:10`). Isso está documentado corretamente em operacao.md ("não autentica o publicador").
- Com a 0.2.0, a rotina de `update` passa a confiar nesse publicador em vez do `git fetch` direto do `ctresb/leis`. O texto servido continua amarrado ao `ctresb/leis`, porque o conteúdo é baixado de `raw.githubusercontent.com/ctresb/leis/<commit>/<path>` e conferido pelo hash do blob. Os metadados não são conferidos (ver N2).

### P2-020-4: URL com credencial em logs e erros. **MAJORITARIAMENTE CORRIGIDO**

`_safe()` remove userinfo, query e fragmento (`snapshot.py:546-551`). O filtro `_RedactQuery` cobre o logger `httpx` (`:51-69`), e as exceções httpx viram só o nome do tipo (`:480-482`).

Testes:

- URL assinada em redirect, depois HTTP 500: o segredo não aparece no erro nem no log INFO.
- `https://op:hunter2@…`: a senha não aparece no erro nem no log.
- `Location` com porta inválida ou IPv6 malformado: a exceção que escapa (`ValueError`) não carrega o segredo.

Resíduo em N3: o DEBUG do httpcore com `-v`, e caminhos ou stderr do git na mensagem visível ao cliente MCP.

### P2-020-5: `is_global` do coletor aceita multicast e site-local. **EM ABERTO**

`src/leis_mcp/overlay/allowlist.py:75-80` não mudou. No Python do projeto, `ipaddress.ip_address('fec0::1').is_global` e `ip_address('224.0.0.1').is_global` retornam `True`. Continua limitado pelo uso manual, pela allowlist de hosts oficiais e pela verificação de TLS e SNI.

### P2-020-6: publicador apaga arquivos ainda referenciados. **PARCIALMENTE CORRIGIDO**

O cliente agora relê o manifesto uma vez quando o arquivo dá 404 (`snapshot.py:263-273`), o que trata o caso comum. O workflow `.github/workflows/index.yml:75-80` continua apagando os arquivos antigos logo depois. O comentário da linha 5 ("a manifest always has its archive") continua otimista. Isso não foi executado no GitHub.

## Achados novos

Não há P0 nem P1.

### N1 (P2): índice de expressão faz a validação consumir memória sem teto

**Código:** `snapshot.py:344-348` (conexão de validação sem `setlimit` nem limite de heap), `:360` (`PRAGMA integrity_check(1)`), `:363-372` (o filtro de esquema permite qualquer `CREATE INDEX` sobre `norms`, inclusive índice de expressão).

**Reprodução** (`probes/p_exprindex_dl.py`; saída em `work/p_expr.out`):

1. Montar um índice válido de 3 normas com `CREATE INDEX ix_e ON norms(length(printf('%.*c', 900000000, 'x')))`. O arquivo `.gz` resultante tem **739 bytes**.
2. Rodar `download()` com o manifesto correto.
3. O arquivo foi **aceito e instalado**, e o RSS do processo foi de 31 MB para **1.749 MB** durante o `integrity_check`.
4. Com `ulimit -v 700000`, que simula uma VM pequena, o resultado é `MemoryError`. Esse erro não é `SnapshotError` e escapa do contrato de erros.

Medições a parte (`work/p_check.out`): com 200 MB por linha, cerca de 413 MB de RSS. `CHECK` não é avaliado por `integrity_check` nem por `quick_check` nesta versão do SQLite.

**Impacto:** exige um arquivo montado de propósito por um publicador malicioso ou comprometido (o padrão é de terceiro). Não é erro comum de empacotamento.

- No Fly (`fly.toml`: 512 MB), um `update` ou a primeira subida podem ser mortos por OOM. Na subida a frio isso vira um ciclo de reinícios, porque o fallback git nunca é alcançado.
- Num `update` com índice saudável, o índice antigo fica preservado: a troca só acontece depois da validação, e os temporários são purgados sob o lock na execução seguinte.
- O prazo de 600 s não limita a memória, porque cada `printf` é uma única operação da VM do SQLite.

**Correção sugerida:** na conexão de validação, `db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, <p.ex. 1 MiB>)` (Python ≥ 3.11). Além disso, recusar índices de expressão (`PRAGMA index_xinfo` com `cid = -2`) ou, mais simples, exigir que o SQL normalizado do esquema seja igual ao conhecido (`IndexBuilder._SCHEMA` e `_INDEXES`). Converter `MemoryError` em `SnapshotResourceError`. Teste de regressão: o `.gz` de 739 B é recusado com RSS limitado.

### N2 (P2): metadados das linhas não são conferidos contra o caminho; o upstream pode escrever no namespace `parlamentar/`

**Código:** `snapshot.py:387-395` confere só `path`, `blob` e `sphere`. `kind`, `year`, `number`, `state` e `agency` vêm do publicador e não são recalculados. Não há recusa de `path LIKE 'parlamentar/%'`. Efeito no runtime: `overlay/catalog.py:69-74` (`by_path`) e `services/presenter.py:34-35` (`acervo: parlamentar`).

**Reprodução** (`probes/p_metadata.py`; saída em `work/p_metadata.out`): um snapshot com duas linhas a mais foi aceito:

- `estaduais/df/resolucoes/resolucao-1-2002.md` com `sphere='federal'` e `agency='congresso-nacional'` (o `PathParser` daria `estaduais/df`, sem agency);
- `parlamentar/federal/congresso-nacional/resolucao/1-2002.md`.

Em `CompositeIndex(upstream, None)`, que é o modo de fallback com overlay inativo, `find(NormQuery(agency='congresso-nacional', number='1', years=(2002,)))` devolveu **as duas**, e `by_path('parlamentar/…')` devolveu a linha do upstream. O build via git nunca produz isso: `PathParser.is_indexable` e `parse` derivam os campos do caminho e não aceitam a esfera `parlamentar`.

**Impacto:** a garantia documentada (operacao.md: "Pedido que nomeia o Congresso Nacional, nesse modo, não devolve o homônimo do DF") passa a depender da honestidade do publicador do índice. Um publicador defeituoso ou malicioso pode rotular o texto do DF (com hash conferido, vindo do `ctresb/leis`) como norma do Congresso. É integridade de metadados, sem execução de código. O publicador já é uma âncora de confiança (P2-020-3), mas antes da 0.2.0 esses campos eram derivados localmente.

**Correção sugerida:** na validação, recalcular `PathParser().parse(path)` e exigir igualdade dos campos, ou pelo menos de `sphere`, `kind`, `year`, `number`, `state`, `city` e `agency`. Exigir também `PathParser().is_indexable(path)`, o que recusa `parlamentar/`. Em Python, 1,2 milhão de linhas cabe no prazo de 600 s, mas convém medir. Na falta disso, recusar pelo menos `path LIKE 'parlamentar/%'` e `agency` dos três órgãos parlamentares.

### N3 (P2, baixo): restos de vazamento em logs DEBUG e em mensagens ao cliente

1. **DEBUG do httpcore.** O filtro só cobre o logger `httpx` (`snapshot.py:68-69`). Com `leis-mcp -v` (`cli.py:30-31`, nível DEBUG), o `httpcore.http11 receive_response_headers.complete` registra o cabeçalho `Location` inteiro. Reprodução: `probes/p_http_logs.py` com `LVL=DEBUG` mostrou `b'https://127.0.0.2:…/x/arc?X-Amz-Signature=SIGSECRET123'` em 6 cenários. Com `LVL=INFO`, nenhum. O padrão do Fly é INFO, e a URL assinada do GitHub é de curta duração e para um asset público. Correção: aplicar o mesmo filtro ao logger `httpcore`, ou não subir o httpcore acima de INFO.
2. **Mensagem ao cliente MCP.** `provider.py:50` repassa `str(error)`. Com o snapshot indisponível e o fallback git, o cliente autenticado recebeu: "Não consegui preparar o índice: Falha ao executar git clone: Cloning into '/tmp/…/srvhome2/repo.partial'… fatal: repository 'https://127.0.0.1:18443/nonexistent-repo/' not found". Isso expõe o caminho interno e o stderr do git. O git ocultou a senha que estava em `LEIS_MCP_REPO_URL`. Já o log pré-existente `git_repository.py:41` (`Cloning %s`), fora deste diff, registra a URL com a senha. Correção: mensagem genérica ao cliente e detalhe só no log. Redigir userinfo em `git_repository.py`.

### N4 (P2, baixo): SQLite em modo WAL aceito; `*.tmp-shm` e `*.tmp-wal` não são purgados

**Código:** `snapshot.py:344` abre com `mode=ro` sem `immutable=1`. `:554-558` purga só `*.gz.partial` e `*.tmp`.

**Reprodução:** um índice com `PRAGMA journal_mode=WAL` (bytes 18 e 19 do cabeçalho = 2) foi aceito e instalado. A validação deixou `index.sqlite.<pid>.<tid>.tmp-shm` e `-wal` no home, e eles se acumulam a cada download. O runtime somente leitura cria `index.sqlite-shm` e `-wal`. Esses arquivos ficam associados ao nome, não ao inode, mesmo depois de um `os.replace` futuro. Como ninguém escreve (o WAL fica com 0 bytes), não se observou corrupção. É só lixo em disco e uma fragilidade.

**Correção:** recusar cabeçalho com modo WAL (bytes 18 e 19 diferentes de 1), ou validar com `?mode=ro&immutable=1`. Incluir `*.tmp-shm` e `*.tmp-wal` na purga.

### Observações (sem severidade; contrato de erros)

Algumas entradas malformadas escapam como exceção genérica em vez de `SnapshotError`:

- manifesto `[` × 60.000 → `RecursionError` (`snapshot.py:230`);
- nome de arquivo com CR/LF → `httpx.InvalidURL`, porque `build_request` em `:443` fica fora do `try`;
- `Location` com porta 99999 ou IPv6 malformado → `ValueError` (vem de `_safe` e `urlsplit`).

Isso é fail-closed: não há fallback git, o erro chega ao cliente sem segredo e não houve injeção de cabeçalho (o CRLF é recusado pelo httpx). Também é coerente com a doc, que promete git para "manifesto corrompido", mas convém normalizar para `SnapshotError`.

Na subida a frio não existe piso de tamanho quando o manifesto omite `norms`: um índice com só `constituicao.md` e metadados coerentes é aceito.

## Controles preservados (verificados)

- **Autenticação HTTP depois do merge.** Servidor real em 127.0.0.1 com chave aleatória:
  - `GET /health` → 200;
  - `POST /health` → 401;
  - `/mcp` sem chave → 401, com chave errada → 401, com o cabeçalho `Authorization` duplicado → 401, com `bearer` minúsculo → 401;
  - `/x` e `/health/` → 401;
  - com a chave certa → 200, e `tools/call listar_normas` funciona;
  - `tools/list` sem chave numa sessão válida → 401.

  A chave apareceu **0 vezes** nos logs dos dois servidores. `server/app.py:151-153` continua envolvendo o app em `ApiKeyMiddleware`, e `cli.py:104-108` passa `settings.api_key`. `http.py` não mudou. O estado "preparando", via `ServiceProvider`, fica atrás do middleware: não há rota nova sem autenticação.
- **Overlay.** Nenhum arquivo do overlay ou do pacote parlamentar mudou no diff. Download a frio real: 3 normas do upstream + 6 do overlay (`p-985cdac4a67032e34cf0`). Toda recusa de snapshot deixou o overlay byte a byte igual.
- **Sistema de arquivos.** `os.replace` atômico depois de `fsync` do arquivo e do diretório. Lock `flock` exclusivo: quatro `leis-mcp index` concorrentes fizeram **um** download, os quatro terminaram com 3 normas e não sobrou temporário. Um `index.sqlite` que era symlink para fora foi substituído pelo próprio link, sem tocar o alvo, e um `.tmp` que era symlink foi removido pela purga. O lock que era symlink foi seguido em modo `a` sem escrita: o alvo ficou intacto. O home continua sendo uma premissa local de confiança.
- **Nomes e caminhos.** O nome do arquivo no manifesto não pode ter `/ \ ? # %` nem começar por `.`. O sha256 precisa ter 64 caracteres hexadecimais. Os blobs têm 20 bytes, e o cache usa o hash, não o caminho. A URL de conteúdo passa por `quote()` e é conferida pelo hash do git.
- **Dependências.** Sem vulnerabilidade conhecida no pip-audit. `httpcore==1.0.9` fixado.
- **Docs.** As afirmações de operacao.md ("Índice pronto"), como tabela de orçamentos, lista de checagens, "índice e overlay ficam byte a byte", fallback git só fora de recurso, lock e `fsync`, conferem com o código e com os probes. Ressalvas: N2 em relação à promessa "não devolve o homônimo do DF" no modo de fallback, e N3 em relação a "não entram … no log do httpx", que é verdadeiro para o logger `httpx` em INFO mas não para o httpcore em DEBUG. Em plano-e-status.md, a evidência do autor sobre `/health` e `/mcp` foi reproduzida de forma independente.

## Limitações

- Não houve teste contra o índice publicado real, de cerca de 1,2 milhão de normas. Não foram medidos o tempo de `integrity_check` em CPU compartilhada nem o pico de disco no volume de 2 GB (índice antigo + `.gz` + extraído, ao lado do clone e do cache). Uma falta de espaço vira recusa controlada, mas pode impedir o `update`.
- O OOM foi reproduzido com `ulimit -v`, não numa VM do Fly. A exaustão de disco foi simulada pela reserva.
- O DNS lento não foi testado. O código não limita `getaddrinfo`.
- O fallback git (pré-existente) não foi auditado quanto a orçamentos.
- O workflow do publicador não foi executado. O código e os dados do upstream que não estão no diff (parser, consultas) foram lidos só no necessário.
- O PASS não cobre um publicador malicioso de forma geral: ele continua sendo âncora de confiança para os metadados do índice (P2-020-3, N2).
