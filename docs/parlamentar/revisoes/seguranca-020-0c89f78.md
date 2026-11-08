<!-- Candidato revisado: 0c89f781e3955cd3597f7ccc3ba611c5df95ec76 (árvore 6efcc6b8fd7b525c84713be4d3e4438570515894), branch feat/parlamentar-020, delta d62b6aa..0c89f78. Revisão de segurança independente. Veredito: PASS (sem P0/P1). N3 e N6 corrigidos, N5 parcial; N7 novo (P2): custo quadrático do filtro de log, recomendado corrigir antes do deploy. -->

# Revisão de segurança independente, só o delta: leis-mcp 0.2.0 + overlay parlamentar (candidato `0c89f78`)

## Veredito: **PASS**

O delta `d62b6aa..0c89f78` não introduz achado P0 nem P1.

Situação dos achados da rodada anterior (`seguranca-020-d62b6aa.md`):

- **N3 (resíduo): corrigido.**
- **N6: corrigido.**
- **N5: corrigido em parte.** O `TypeError` na validação foi resolvido. `RecursionError`, `InvalidURL` e um `ValueError` no caminho HTTP ainda escapam, mas a falha continua fechada e sem vazamento.

Superfície nova:

- **Fallback git no `update`:** não abre um laço que o publicador controle.
- **Tentar o próximo endereço depois de timeout:** continua limitado pelo prazo da etapa.

Achado novo:

- **N7 (P2):** o regex novo do filtro de log tem custo quadrático. Com o teto de cabeçalho do httpcore (100 KiB), um valor hostil de `Location` pode ocupar a CPU por dezenas de segundos em cada linha de log, em nível INFO. Isso exige o publicador configurado ou um destino de redirect dele. Não bloqueia o gate, mas a correção é trivial e **recomendo fazê-la antes do deploy**.

Este PASS vale só para o gate de segurança do delta. Não autoriza push, merge nem deploy.

## Artefato verificado

- `(checkout)`, branch `feat/parlamentar-020`.
  - `git rev-parse HEAD` = `0c89f781e3955cd3597f7ccc3ba611c5df95ec76`
  - `HEAD^{tree}` = `6efcc6b8fd7b525c84713be4d3e4438570515894`
  - Checkout limpo.
- O delta é um commit (`0c89f78 fix(snapshot): last review follow-ups`): 6 arquivos, +146/−19. Arquivos tocados:
  - `catalog/snapshot.py`
  - `cli.py`
  - `services/provider.py`
  - `messages.py`
  - `docs/parlamentar/operacao.md`
  - `tests/test_snapshot.py`
- `server/http.py`, `server/app.py` e o overlay não mudaram. O middleware de autenticação é o mesmo já verificado em execução nas duas rodadas anteriores.

## O que foi executado e o que não foi

Scripts em `.../sec-review/probes2/` (reutilizados). As saídas desta rodada estão em `.../sec-review/work3/`.

**Executado:**

1. `uv run pytest -q -m "not integration"`: **252 passed, 22 deselected**.
2. `probes2/p1_sqlite2.py` contra o código novo (`work3/p1_sqlite3_results.json`). Também cobre os casos de regressão de N1, N2 e N4.
3. `probes2/p_http2.py`, com a pilha real httpx/httpcore, HTTPS local e log em DEBUG (`work3/p_http3_results.json`).
4. Rastreio de onde as exceções do N5 escapam (`work3/p_n5_tracebacks.out`).
5. `probes2/p_provider.py`, mais um cenário com relógio simulado de 120 s (`work3/p_provider.out`).
6. Log do provider com um `GitError` sintético contendo URL com senha e token (`work3/p_provider_log.out`).
7. Tempo de `scrub_urls` em entradas longas de 16 KB e 64 KB (`work3/p_regex.out`).
8. Leitura de código:
   - fallback do `update` (`cli.py:116-140`);
   - laço de conexão (`snapshot.py:169-183`);
   - limites de cabeçalho do httpcore (`_sync/http11.py:45`, `MAX_INCOMPLETE_EVENT_SIZE = 100 * 1024`);
   - log de requisição do httpx (`httpx/_client.py:1025`).

**Não executado:**

- Servidor real nesta rodada. A autenticação não mudou no delta e a mudança no log do provider foi testada de forma isolada.
- Build git real pelo fallback do `update`, que foi avaliado por leitura de código.
- Teste em execução do bloqueio do event loop pelo N7. O efeito descrito vem de medição isolada mais leitura de código.
- Índice real.
- Produção.
- `pytest -m integration`.

## Status dos achados

### N3 (resíduo): **CORRIGIDO**

O regex agora ignora maiúsculas, também reconhece caminho relativo (`/…`) e consome a query até a aspa (`snapshot.py:55-56`).

Em DEBUG, nenhuma linha vazou nos três casos da rodada anterior: `Location` relativo, `HTTPS://` em maiúsculas e espaço na query. Também não vazou nos casos de redirect assinado entre hosts e de userinfo.

O provider registra só `tipo: mensagem filtrada`, sem traceback (`provider.py:88-89`). Com um `GitError` encadeado a um `CalledProcessError` que continha `user:repopass@…?tok=repoqtok`, o log ficou assim: `Index preparation failed: GitError: Falha ao executar git clone: fatal: repository 'https://127.0.0.1/x' not found`. A senha, o token e o traceback não aparecem. O cliente recebe "a montagem local falhou (detalhes no log do servidor)".

Efeito colateral aceitável: o filtro também corta texto comum depois de um `/…?` até a próxima aspa. Isso só reduz informação no log.

### N5 (contrato de erros): **PARCIALMENTE CORRIGIDO**

Corrigido:

- `path` do tipo BLOB agora gera `SnapshotError (TypeError)` (`snapshot.py:447`).
- `Location` com IPv6 malformado agora gera `SnapshotError`.

Ainda escapam (`work3/p_n5_tracebacks.out`):

1. **Manifesto com aninhamento profundo.** Gera `RecursionError` em `snapshot.py:314`, porque `latest()` só captura `ValueError` e `UnicodeDecodeError`.
2. **Nome de arquivo com CR/LF.** Gera `httpx.InvalidURL`, porque `build_request` em `:561` continua fora do `try`.
3. **Redirect para a porta 99999.** O `except` captura o erro do httpx, mas o próprio `self._safe(current)` dentro do handler (`:601` → `_safe` em `:669`) lança `ValueError` ao ler `parts.port`.

Impacto: a falha continua fechada. Índice e overlay ficam intactos, nenhuma mensagem leva segredo e o cliente MCP vê a mensagem genérica. Em `update`, porém, essas exceções não são `SnapshotError`: não caem no git e terminam em traceback para o operador.

Correção:

- capturar `RecursionError` em `latest()`;
- mover `build_request` para dentro do `try`;
- tornar `_safe` tolerante a porta inválida (`try: parts.port except ValueError`).

### N6 (fábrica que falha sempre): **CORRIGIDO**

`provider.py:62-67` (`_repaired_at`) limita o reparo a uma tentativa a cada `RETRY_AFTER`.

- Dez chamadas em 2 s resultaram em **1** GET de manifesto (antes eram 10).
- Doze chamadas ao longo de 120 s simulados resultaram em **2** GETs.
- As mensagens agora distinguem "estou baixando um novo" de "a montagem local falhou".
- Índice com lixo no disco: 1 GET de manifesto, 1 GET de arquivo e depois "pronto".
- `index.sqlite` sendo um diretório: o cooldown vale.

### Regressões que continuam corrigidas

Sem regressão de N1, N2 e N4 nas mesmas sondas:

- índice de expressão: recusado;
- DF rotulado como Congresso: recusado;
- linha em `parlamentar/`: recusada;
- WAL: recusado.

Prazos, tetos, TLS e DNS seguem iguais:

- cabeçalho lento: recusa em 5,0 s;
- corpo lento: recusa em 6,01 s;
- DNS lento: recusa em 3,0 s;
- divergência de nome no certificado: recusada.

P2-020-5 não mudou no delta.

## Superfície nova

### Fallback git no `update` (`cli.py:126-140`)

Um `SnapshotError` que não é de orçamento, vindo de `latest()`, `installed_commit()` ou `download()`, leva a `clone()` + `fetch_latest()` + `IndexBuilder.build()`. Um publicador hostil consegue isso respondendo HTTP 500, 404 ou um índice que a validação recusa.

- **Não há repetição em laço.** O `update` é um comando de execução única. O fallback roda uma vez por invocação: a rotina semanal ou uma execução manual. O caminho do servidor (`ServiceProvider` → `_prepare_index`) não mudou: só monta quando falta índice, e o cooldown de 60 s vale.
- **Custo.** Igual ao do `update --from-git` anterior à 0.2.0: `fetch` incremental mais montagem local. O `fly.toml` dimensiona para isso, com cerca de 360 MB de RAM e `index.tmp` de cerca de 350 MB ao lado do índice. O clone completo (cerca de 250 MB) só acontece uma vez numa instalação sem clone, e no volume do Fly o clone já existe. O caminho git não tem a reserva de disco nem o prazo total do snapshot. Isso é anterior a esta onda e já constava como limitação.
- **Integridade.** O build git vem direto do `ctresb/leis` e deriva os metadados pelo `PathParser`, então o publicador não ganha controle sobre o conteúdo. Ele pode apenas rebaixar o `update` ao caminho antigo.
- **Recusas de orçamento** (`SnapshotResourceError`) continuam sem fallback (`cli.py:133-134`).

Conclusão: não é achado. Fica a observação de que o caminho git segue sem orçamento próprio.

### Próximo endereço depois de timeout (`snapshot.py:169-183`)

Cada tentativa recebe `min(connect=15 s, restante)` via `clamp`. Quando o prazo acaba, `clamp` lança `ConnectTimeout`, que agora também é capturado. O laço percorre os endereços que faltam, cada um falhando na hora, e relança o último. O httpx converte isso em `TimeoutException`, que vira `SnapshotResourceError`.

O total continua limitado pelo prazo da etapa e a lista de endereços é finita. O cenário de primeiro endereço recusado e segundo aceito continua passando. O timeout real de conexão não foi exercitado nesta rodada.

### N7 (P2, novo): custo quadrático do filtro de log, alcançável em INFO

**Código:** `snapshot.py:55`, `_QUERY = ((?:https?://|/)[^\s'"?#]*)[?#][^'"]*`. Ele é aplicado a toda mensagem dos loggers `httpx` e `httpcore.*` (`:64-90`) e, agora, a `str(exc)` em `provider.py:89`.

Quando o texto tem uma sequência longa de caracteres válidos de caminho que começa por `/` e não tem `?` nem `#`, cada posição inicial varre até o fim e falha. O custo é O(n²).

**Medição** (`work3/p_regex.out`):

| Tamanho da sequência | Tempo de `scrub_urls` |
|---|---|
| 16 KB | 0,5 s a 1,5 s, conforme o formato |
| 64 KB | 8 s a 24 s |

Texto sem `/` ou com `?` logo em seguida é linear.

**Alcance** (por leitura de código): o httpcore aceita blocos de cabeçalho de até 100 KiB (`httpcore/_sync/http11.py:45`). Um `Location` desse tamanho vira a URL da requisição seguinte. O httpx registra essa URL em **INFO** (`HTTP Request: GET …`, `httpx/_client.py:1025`), nível que está ligado em produção. Ela também pode entrar no `SnapshotFailed` registrado pelo provider. Pela curva medida, isso dá dezenas de segundos por linha, até 6 saltos por etapa.

**Impacto:** o `re` do Python não solta o GIL durante um `sub`. A thread de preparo pode então atrasar o event loop do servidor no mesmo processo, inclusive `/health` (o check do Fly tem timeout de 5 s), enquanto o índice é preparado ou reparado. Isso é inferência a partir da medição e do código; não executei o servidor com essa entrada.

Para isso é preciso controlar a resposta do publicador configurado (`LEIS_MCP_INDEX_URL`) ou de um destino de redirect dele. O prazo da etapa não interrompe o regex, mas o efeito acaba quando a etapa acaba. Não há vazamento nem corrupção.

A versão anterior do regex tinha o mesmo defeito em menor escala. Na rodada passada estimei cerca de 0,2 s supondo um teto de 16 KB, mas o teto efetivo do httpcore é 100 KiB.

**Correção sugerida** (barata):

- truncar o texto antes de filtrar (por exemplo, 4 KiB em `_RedactQuery.filter` e em `provider._run`);
- ou trocar o regex por varredura linear: `urlsplit` por token, ou `(?:https?://|/)[^\s'"?#]*+` com quantificador possessivo do Python 3.11+, que elimina o retrocesso.

Teste de regressão: uma sequência longa de caminho sem `?` deve ser filtrada em tempo linear (menos de 50 ms para 100 KiB).

## Controles preservados

- **Autenticação.** `server/http.py` e `server/app.py` não mudaram. A verificação em execução foi feita nas rodadas `8b09e76` e `d62b6aa`: `/health` 200, `/mcp` 401 sem chave, com chave errada, com cabeçalho duplicado ou com `bearer` minúsculo, 200 com a chave certa, e a chave aparecendo 0 vezes no log.
- **Validação do índice.** Sem regressão. O índice antigo e o overlay ficaram byte a byte iguais em todas as recusas.
- **Docs.** A operacao.md descreve corretamente o fallback git em `update`, a tentativa do próximo endereço, o reparo no máximo a cada 60 s e o log sem traceback e com filtro.

## Limitações

- O efeito do N7 sobre o event loop e o `/health` é inferência, não teste em execução.
- O build git pelo fallback do `update` não foi executado.
- O timeout de conexão com troca de endereço não foi exercitado.
- Não houve teste com o índice real nem em produção.
- A revisão cobre só o delta. Para o restante valem `seguranca-020-8b09e76.md` e `seguranca-020-d62b6aa.md`.
