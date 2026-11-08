<!-- Candidato revisado: 67671eed23e5b8da678acfd4470f518c1d6a6d04 (árvore 7ff1b4274086d69bc6c00b35bf2ec67934eee7fc), branch feat/parlamentar-020, delta 0c89f78..67671ee. Revisão de segurança independente. Veredito: PASS (sem P0/P1; N7 e N5 corrigidos, N3 sem regressão; N8 novo, P2 baixo: formatos incomuns de URL passam pelo filtro novo). -->

# Revisão de segurança independente, só o delta: leis-mcp 0.2.0 + overlay parlamentar (candidato `67671ee`)

## Veredito: **PASS**

O delta `0c89f78..67671ee` não introduz achado P0 nem P1.

- **N7 (custo do filtro de log): corrigido.** Com 100 KiB de entrada hostil, o pior caso medido foi de 2,4 ms. Antes chegava a 24 s com 64 KB.
- **N5 (contrato de erros): corrigido.** Os quatro casos que escapavam agora viram `SnapshotError` sem segredo, e `_safe` não lança mais exceção.
- **N3: sem regressão.** Todos os casos das rodadas anteriores continuam sem vazamento, em INFO e em DEBUG.

Achei uma forma nova de vazar, de baixo impacto (**N8, P2**). O filtro novo trabalha token a token e não reconhece alguns formatos incomuns de URL:

- **Em INFO** (nível de produção), vaza a query de uma URL que tenha `'` no caminho antes do `?`. Isso foi verificado de ponta a ponta com httpx real.
- **Só em DEBUG** (`-v`), vazam três formatos de `Location`: relativo sem barra inicial, só query, e `//usuário:senha@host`.

Os redirects assinados reais do GitHub não têm nenhum desses formatos, então o caso que motivou o filtro continua coberto. A correção é simples e pode entrar depois do gate.

Este PASS vale só para o gate de segurança do delta. Não autoriza push, merge nem deploy.

## Artefato verificado

- `(checkout)`, branch `feat/parlamentar-020`.
  - `git rev-parse HEAD` = `67671eed23e5b8da678acfd4470f518c1d6a6d04`
  - `HEAD^{tree}` = `7ff1b4274086d69bc6c00b35bf2ec67934eee7fc`
  - Checkout limpo.
- O delta é um único commit (`67671ee fix(snapshot): linear log scrubber and remaining review nits`). Ele toca 5 arquivos, com +118/−11:
  - `catalog/snapshot.py`
  - `services/provider.py`
  - `messages.py`
  - `docs/parlamentar/operacao.md`
  - `tests/test_snapshot.py`
- `server/http.py`, `server/app.py`, `cli.py` e o overlay não mudaram.

## O que foi executado e o que não foi

Scripts em `.../sec-review/probes2/` e `.../probes4/`. Saídas em `.../sec-review/work4/`.

**Executado:**

1. `uv run pytest -q -m "not integration"`: **261 passed, 22 deselected**.
2. `probes2/p_http2.py`: 19 cenários com a pilha real httpx/httpcore contra HTTPS local, com log em DEBUG. Cobre `Location` absoluto, relativo, em maiúsculas, com espaço, com userinfo e com redirect assinado entre hosts, além dos cenários de DNS, prazos, tetos e TLS. Resultados em `work4/p_http4_results.json`.
3. `probes4/p_bypass.py`: quatro formatos de URL fora do comum, de ponta a ponta, em INFO e em DEBUG (`work4/p_bypass.out`).
4. Teste unitário de `scrub_urls` com 14 formatos (`work4/p_scrub_unit.out`).
5. Tempo de `scrub_urls` com 10 formatos de 100 KiB e do filtro sobre um `LogRecord` real de 100 KiB (`work4/p_scrub_perf.out`).
6. Os casos do N5 e o comportamento de `_safe` com URLs inválidas (`work4/p_n5.out`).

**Não executado:**

- Servidor real nesta rodada. A autenticação não mudou no delta; a verificação em execução é das rodadas anteriores.
- Índice real.
- Produção.
- `pytest -m integration`.

## Status dos achados

### N7 (custo quadrático do filtro): **CORRIGIDO**

`snapshot.py:55-85` trocou o filtro: um regex de tokens (`'…'`, `"…"` ou sequência sem espaço nem aspas) chama `_scrub_token`, que usa só `find`, e o texto é truncado em 16 KiB.

| Formato (100 KiB) | Tempo (melhor de 3) |
|---|---|
| Sequências de `/`, `/a`, esquemas repetidos, `@` e `?` | 0,12 a 0,14 ms |
| Aspas simples alternadas | 2,4 ms |
| Aspas mistas | 1,8 ms |
| Tokens curtos com query | 2,3 ms |
| Texto comum | 1,0 ms |
| Filtro sobre `LogRecord` do httpcore com `Location` de 100 KiB | 0,53 ms (mensagem final com 16.385 caracteres) |

O regex de tokens tem duas alternativas que começam por aspa. Uma aspa sem par só falha uma vez por tipo de aspa, então a varredura continua linear, e a truncagem limita a entrada de qualquer forma.

### N5 (contrato de erros): **CORRIGIDO**

Todos os casos agora viram `SnapshotError`, e nenhuma mensagem leva o segredo:

- **Manifesto com aninhamento profundo:** "corrompido (index.json)".
- **Nome de arquivo com CR/LF:** "Endereço do índice pronto recusado: … (inválida)".
- **Redirect para a porta 99999:** "… de (URL inválida) …".
- **Porta não numérica e IPv6 malformado:** `SnapshotError` comum.

`_safe` devolve "(URL inválida)" e não lança exceção (`snapshot.py:694-702`). Como agora são `SnapshotError`, esses casos também passam a cair no fallback git do `update`, o que é coerente com a documentação.

### N3: **SEM REGRESSÃO**

Nos cenários das rodadas anteriores, em INFO e em DEBUG, houve **0 linhas vazando**:

- `Location` absoluto com `X-Amz-Signature`;
- relativo com `/`;
- `HTTPS://` em maiúsculas;
- com espaço;
- com userinfo;
- redirect assinado entre hosts.

O log do provider continua sem traceback e passa pelo mesmo filtro.

## Achado novo

### N8 (P2, baixo): formatos incomuns de URL passam pelo filtro token a token

**Código:** `snapshot.py:69-85` (`_scrub_token`). O token só é tratado como URL quando contém `://` ou começa com `/`. O userinfo só é removido da primeira URL do token, e só quando há esquema. O tokenizador também quebra o token em toda aspa, inclusive no meio de uma URL sem aspas.

**Reprodução de ponta a ponta** (`probes4/p_bypass.py`, httpx/httpcore reais, HTTPS local; saída em `work4/p_bypass.out`):

| Formato | INFO | DEBUG |
|---|---|---|
| URL com `'` no caminho antes do `?` (por exemplo `…/arc'x?sig=…`) | **vaza**: `HTTP Request: GET https://…/r4/arc'x?sig=SIGSECRET123 "HTTP/1.1 200 OK"` | vaza (mesma linha) |
| `Location` relativo sem barra (`arc2?token=…`) | ok | **vaza** no cabeçalho do httpcore |
| `Location` só com query (`?token=…`) | ok | **vaza** |
| `Location` relativo ao protocolo com userinfo (`//u:senha@host/…`) | ok | **vaza** |

No teste unitário (`work4/p_scrub_unit.out`) também passam:

- uma segunda URL com userinfo dentro do mesmo token (`'https://a/x,https://u:S@b/y'`);
- `chave=/rel?token=…` sem aspas.

Esses dois formatos não apareceram nos logs reais do httpx nem do httpcore.

**Impacto:** só os logs do servidor são afetados. O cliente MCP não recebe essas mensagens.

- Para vazar em INFO, a URL de destino do redirect (escolhida pelo servidor que redireciona) ou a `LEIS_MCP_INDEX_URL` configurada precisa ter uma aspa no caminho antes de uma query sensível.
- Os outros três formatos dependem de um `Location` fora do padrão e de `-v`.
- Os redirects assinados do GitHub são absolutos, em minúsculas e sem aspas, e continuam filtrados.
- Um servidor hostil que exponha o próprio token não é uma ameaça relevante. O risco é sobretudo de configuração do operador ou de hospedagem futura com outro formato de URL.

**Correção sugerida:**

- **Tratar na origem, não no texto:**
  - no registro `HTTP Request` do httpx, substituir o argumento da URL por `IndexSnapshot._safe(url)`;
  - no registro `receive_response_headers.complete` do httpcore, substituir o valor do cabeçalho `Location` (e de `Set-Cookie`, se aparecer) por um marcador.
- **Complementos em `_scrub_token`:**
  - cortar depois do primeiro `?` ou `#` em qualquer token que tenha `?` seguido de `=`;
  - remover userinfo também de tokens que começam com `//` e de todas as ocorrências de `://`;
  - no tokenizador, não abrir citação no meio de uma sequência sem espaço.
- **Testes de regressão:** os seis formatos acima.

## Controles preservados

- **Prazos, tetos, TLS e DNS:** iguais aos da rodada anterior.
  - Cabeçalho lento: recusa em 5,0 s.
  - Corpo lento: recusa em 6,01 s.
  - DNS lento: recusa em 3,0 s.
  - Chunked acima de 4 MiB: recusado.
  - Nome divergente no certificado: recusado.
  - Certificado não confiável: recusado.
- **Provider:** só mudou o texto da mensagem de reparo que falhou (`INDEX_REPAIR_FAILED`). O cooldown do N6 continua igual.
- **Autenticação:** sem mudança no delta. Vale a verificação em execução das rodadas `8b09e76` e `d62b6aa`.
- **Docs:** a operacao.md diz agora que o filtro remove query, fragmento e userinfo de URLs absolutas e relativas e que trunca em 16 KiB. Isso vale para os formatos comuns. Os formatos do N8 são exceção.

## Limitações

- O N8 foi testado com os formatos listados. A lista não é exaustiva.
- Não houve servidor real nesta rodada.
- Não houve teste com o índice real nem em produção.
- A revisão cobre só o delta. Para o restante valem `seguranca-020-8b09e76.md`, `seguranca-020-d62b6aa.md` e `seguranca-020-0c89f78.md`.
