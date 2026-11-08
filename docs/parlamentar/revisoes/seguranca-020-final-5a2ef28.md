<!-- Candidato revisado: 5a2ef28334684396ccd0d09b8604f8bd513978a5 (árvore 9cd5b70502a5e7b2f8a0238ce9cba2f4b11eb26a), branch feat/parlamentar-020, delta 67671ee..5a2ef28. Gate final de segurança. Veredito: PASS (sem P0/P1). P2 residuais listados para issue de acompanhamento. -->

# Gate final de segurança: leis-mcp 0.2.0 + overlay parlamentar (candidato `5a2ef28`)

## Veredito: **PASS**

Não há P0 nem P1 no delta `67671ee..5a2ef28`, nem no acumulado das rodadas anteriores. O gate de segurança não bloqueia o push.

- **N8:** corrigido nos logs reais do httpx e do httpcore, nos dois níveis (INFO e DEBUG).
- **N3 e N7:** sem regressão.
- **Regressão menor (P2):** o delta estreitou um `except` e trouxe de volta um caso do N5. Um `Location` com IPv6 malformado escapa como `ValueError`. A falha continua fechada e sem segredo.

Os P2 residuais estão listados no fim, para a issue de acompanhamento.

Este PASS cobre só a segurança. Não substitui a QA nem a autorização do dono para push, merge e deploy.

## Artefato verificado

- **Checkout:** `(checkout)`, branch `feat/parlamentar-020`, sem alterações locais.
  - `git rev-parse HEAD` = **`5a2ef28334684396ccd0d09b8604f8bd513978a5`**
  - `HEAD^{tree}` = **`9cd5b70502a5e7b2f8a0238ce9cba2f4b11eb26a`**
- **Delta:** um commit, `5a2ef28 fix(snapshot): redact httpx/httpcore logs at the source; scrubber edge cases`. São 2 arquivos (`catalog/snapshot.py` e `tests/test_snapshot.py`), com +105/−17.
- **Não mudaram:** `server/http.py`, `server/app.py`, `cli.py`, `services/provider.py` e o overlay.

## O que foi executado e o que não foi

As saídas estão em `.../sec-review/work5/`.

**Executado:**

1. `uv run pytest -q -m "not integration"`: **271 passed, 22 deselected**.
2. `probes4/p_bypass.py` (sondas do N8): pilha real httpx/httpcore contra HTTPS local, nos níveis INFO e DEBUG (`work5/p_bypass.out`).
3. `probes2/p_http2.py` (sondas do N3, mais prazos, tetos, TLS e DNS): 19 cenários com log em DEBUG (`work5/p_http5.out`, `work5/p_http5_results.json`).
4. Testes unitários de `scrub_urls` (20 formatos) e de `_RedactQuery` sobre registros reais do httpx e do httpcore. Desempenho com 7 formatos de 100 KiB. Casos do N5. Tudo em `work5/p_unit_perf_n5.out`.
5. Leitura das chamadas de log do httpx 0.28.1. Só `_client.py:1025` e `:1740` registram mensagens, as duas com `'HTTP Request: %s %s …'`, e o segundo argumento é a URL. Trocar esse argumento por `_safe(...)` não quebra a formatação.

**Não executado:**

- Servidor real nesta rodada. A autenticação não mudou desde a verificação em execução das rodadas `8b09e76` e `d62b6aa`.
- Índice real.
- Produção.
- `pytest -m integration`.

## Status dos achados

### N8 (formatos de URL que passavam pelo filtro): **CORRIGIDO nos logs reais**

O registro `HTTP Request` do httpx agora passa a URL por `_safe`, e as linhas de cabeçalho do httpcore saem como `… (headers omitted)` (`snapshot.py:114-131`).

Resultado de ponta a ponta: **0 linhas vazando** nos quatro formatos, tanto em INFO quanto em DEBUG:

- `'` no caminho antes da query;
- relativo sem barra;
- só query;
- `//u:senha@host`.

Teste unitário dos registros:

- `httpx.URL("https://u:p@h/x?sig=SECRET")` vira `HTTP Request: GET https://h/x "HTTP/1.1 200 OK"`.
- O cabeçalho do httpcore com `Location` vira `receive_response_headers.complete (headers omitted)`.
- Outros registros do httpcore, como `connect_tcp.started`, ficam intactos.

O filtro genérico, que agora é só uma segunda camada, também passou a cortar:

- `arc2?token=`, `?token=` e `url=/rel?token=`;
- `//user:pass@`;
- URL absoluta sem `=` na query;
- valores entre aspas com espaço depois da primeira URL que tem query.

Os formatos que ainda passam por essa camada estão em R2.

### N3 (vazamentos em log): **SEM REGRESSÃO**

Nenhuma linha vazou nos 19 cenários, entre eles:

- `Location` absoluto assinado, relativo com `/`, em maiúsculas, com espaço e com userinfo;
- redirect assinado entre hosts.

O provider continua registrando sem traceback e com filtro, sem mudança no delta.

### N7 (custo do filtro): **SEM REGRESSÃO**

Pior caso de `scrub_urls` com 100 KiB: **5,8 ms** (aspas alternadas). Os outros formatos ficaram entre 0,13 e 2,4 ms. O custo continua linear e a truncagem em 16 KiB continua valendo. As linhas de cabeçalho do httpcore nem chegam ao filtro genérico.

### N5 (contrato de erros): **REGRESSÃO PARCIAL, P2**

Continuam virando `SnapshotError` sem segredo: JSON profundo, CR/LF no nome do arquivo, porta 99999 e porta não numérica.

**Regressão:** um `Location` com IPv6 malformado (`https://[zz/x?token=…`) volta a escapar como `ValueError: Invalid IPv6 URL`.

- **Causa:** `snapshot.py:663`. O delta estreitou o `except` para `httpx.HTTPError`, mas `urljoin(current, location)` roda dentro desse `try` e lança `ValueError`. O comentário "build_request already turned malformed URLs into SnapshotError" não cobre esse caso.
- **Impacto:** a falha continua fechada. O índice e o overlay não mudam, a mensagem não tem segredo e o cliente MCP vê a mensagem genérica. No `update`, porém, o erro termina em traceback e não cai no git.

## Controles preservados

- **Prazos, tetos, TLS e DNS:** iguais à rodada anterior.
  - Cabeçalho lento: recusa em 5 s.
  - Corpo lento: recusa em 6 s.
  - DNS lento: recusa em 3 s.
  - Chunked acima de 4 MiB: recusado.
  - Nome divergente no certificado: recusado.
  - Certificado não confiável: recusado.
- **Validação do índice e overlay:** sem mudança no delta. As provas estão em `seguranca-020-d62b6aa.md` e `seguranca-020-0c89f78.md`.
- **Autenticação HTTP:** sem mudança no delta.
  - Verificado em execução nas rodadas anteriores: `/health` 200, `/mcp` 401 sem chave, com chave errada, com cabeçalho duplicado e com `bearer` minúsculo, e 200 com a chave certa.
  - A chave aparece 0 vezes no log.

## P2 residuais (para a issue de acompanhamento)

Nenhum bloqueia. A severidade de todos é P2, de baixa a média.

**R1. Regressão do N5: `ValueError` vindo de `urljoin`.**
- Onde: `snapshot.py:663`.
- Problema: um `Location` com IPv6 malformado escapa como `ValueError`.
- Correção: capturar `ValueError` (e `RecursionError`) de novo, ou envolver `urljoin` e converter em `SnapshotError`.
- Teste de regressão: `Location: https://[zz/…` deve resultar em `SnapshotError`.

**R2. Filtro genérico de segunda camada ainda deixa passar formatos raros.**
- Onde: `snapshot.py:91-110`, `_scrub_piece`.
- Esses formatos não aparecem nos logs reais do httpx/httpcore, que agora são tratados na origem. Afetam só outros textos filtrados, como mensagens de erro e stderr do git no log do servidor.
- Passam, segundo o teste unitário:
  - segunda URL com userinfo no mesmo token sem espaço (`'https://a/x,https://u:S@b/y'`);
  - `u:S@h/x` sem esquema;
  - relativo sem `=` (`arc2?S`);
  - fragmento relativo (`arc2#token=S`);
  - parâmetros com `;` (`https://h/x;token=S`).
- Correção: remover userinfo de toda ocorrência de `://` e de `//`, e cortar em `?` e `#` em qualquer token que tenha `?` ou `#`.

**R3. P2-020-3 (decisão do dono).**
- Redirect HTTPS para outro host ou endereço privado continua aceito.
- O publicador padrão é de terceiro (`mazulo/leis-mcp`) e é a âncora de confiança da existência e da atualidade do índice.
- O conteúdo continua amarrado ao `ctresb/leis` pelo hash do blob, e os metadados são derivados localmente.

**R4. P2-020-6 (decisão do dono).**
- O workflow `index.yml` apaga os arquivos antigos imediatamente.
- O cliente relê o manifesto uma vez quando recebe 404.

**R5. Resíduo informativo de P2-020-5.**
- O coletor aceita `64:ff9b::/96` (NAT64 com IPv4 privado embutido).
- Só importa numa rede NAT64.

**R6. Caminho git sem orçamento próprio.**
- O fallback git do `index`/`update` (que o publicador pode forçar com uma recusa que não é de orçamento) e o `--from-git` não têm reserva de disco nem prazo total.
- Isso é anterior a esta onda.
- O publicador só consegue rebaixar o processo ao caminho antigo, no máximo uma vez por invocação.

**R7. Limites não verificados nesta série.**
- Tempo de validação e pico de disco com o índice real de 1,2 milhão de normas no volume de 2 GB do Fly (antigo, `.gz` e extraído ao lado do clone e do cache).
- OOM real numa VM de 512 MB.

## Limitações

- Não houve servidor real nesta rodada; a autenticação não mudou.
- Não houve teste com o índice real nem em produção.
- A lista de formatos do R2 não é exaustiva.
- Este relatório cobre o delta final. O histórico está em:
  - `seguranca-020-8b09e76.md`
  - `seguranca-020-d62b6aa.md`
  - `seguranca-020-0c89f78.md`
  - `seguranca-020-67671ee.md`

## Adendo 45940c7

**Veredito: PASS. O R1 está fechado.**

### Identidade

- Worktree `(worktree)`, branch `fix/qa-p2`, sem alterações locais.
- HEAD `45940c700e63e186f270448dba4bbd3ae633cdd7`, pai `5a2ef28334684396ccd0d09b8604f8bd513978a5`, árvore `67b5db177b9e5293f1119e832c5490f0b072983c`.

### Diff

- O commit toca `catalog/snapshot.py` (+9/−1) e `tests/test_snapshot.py` (+8).
- No tratamento do redirect, `urljoin`, `_require_https` e `urlsplit(...).port` ficam dentro de um `try/except (ValueError, RecursionError)`. A exceção vira `SnapshotError(SNAPSHOT_REFUSED_URL, url="(URL inválida)")`.
- Nada mais mudou.

### Suíte offline

- Rodei com o código do worktree: `PYTHONPATH=…/leis-mcp-fix/src`, sem bytecode e sem cache do pytest. O import foi conferido.
- Resultado: **274 passed, 22 deselected**. O worktree continuou limpo depois.

### Sonda de `Location` malformado

Saída em `work6/p_location.out`. Todos os 8 casos terminam em `SnapshotError`, sem segredo na mensagem nem no log:

| `Location` | Resultado |
|---|---|
| `https://[zz/x` | "(URL inválida) (redirect inválido)" |
| porta 99999 | "(URL inválida) (redirect inválido)" |
| porta -1 | "(URL inválida) (redirect inválido)" |
| `https://u:S@[zz/x` | "(URL inválida) (redirect inválido)" |
| `https://[::1/x` | `RemoteProtocolError` (recusado pelo httpx) |
| porta `abc` | `RemoteProtocolError` (recusado pelo httpx) |
| `http://` | recusado ("só https") |
| host vazio | recusado ("só https") |

### Pendências

Os P2 residuais R2 a R7 continuam como estavam, para a issue de acompanhamento.
