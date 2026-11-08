<!-- Candidato revisado: d62b6aa37b976e7ce3767ae649bd538735f53fd8 (árvore a5b856561fd7c19308f01a09ab4f0ff8928037b6), branch feat/parlamentar-020, delta 8b09e76..d62b6aa. Revisão de segurança independente. Veredito: PASS (sem P0/P1; N1, N2, N4 e P2-020-5 corrigidos; N3 parcial; duas advertências P2 novas de baixo impacto). -->

# Revisão de segurança independente, só o delta: leis-mcp 0.2.0 + overlay parlamentar (candidato `d62b6aa`)

## Veredito: **PASS**

O delta `8b09e76..d62b6aa` não introduz achado P0 nem P1. Situação dos achados da rodada anterior (`seguranca-020-8b09e76.md`):

- **N1, N2, N4 e P2-020-5: corrigidos.** Cada um foi reproduzido de novo contra o código novo, e a recusa agora acontece.
- **N3: corrigido em parte.** O caminho comum está coberto. Ainda vazam em DEBUG o `Location` relativo, o `Location` com esquema em maiúsculas e o `Location` com espaço na query. A senha do repositório git ainda aparece no traceback de `log.exception`. Os dois só chegam ao log do servidor, nunca ao cliente MCP.

A superfície nova também foi verificada:

- **DNS próprio:** o TLS continua verificando o nome do host, a rota por proxy não resolve o destino localmente e a thread de DNS não fica pendurada além do resolver do sistema.
- **Filtro de log por regex:** tem custo quadrático num caso adversarial, sem impacto prático.
- **Reparo automático:** um índice corrompido é reparado uma única vez e não força downloads repetidos do arquivo. Se a fábrica falhar sempre enquanto o índice instalado continua válido, cada chamada de ferramenta faz um GET do manifesto sem cooldown (N6, P2 baixo).

Este PASS vale só para o gate de segurança do delta. Não autoriza push, merge nem deploy. P2-020-3 e P2-020-6 continuam como decisão do dono.

## Artefato verificado

- `(checkout)`, branch `feat/parlamentar-020`. `git rev-parse HEAD` = `d62b6aa37b976e7ce3767ae649bd538735f53fd8`, `HEAD^{tree}` = `a5b856561fd7c19308f01a09ab4f0ff8928037b6`. A árvore de trabalho está limpa.
- O delta é um único commit (`d62b6aa fix(snapshot): close QA and security review follow-ups`), com 7 arquivos e +476/−79. Arquivos tocados:
  - `catalog/snapshot.py`
  - `catalog/git_repository.py`
  - `services/provider.py`
  - `overlay/allowlist.py`
  - `messages.py`
  - `docs/parlamentar/operacao.md`
  - `tests/test_snapshot.py`
- `server/http.py`, `server/app.py`, `cli.py` e os dados do overlay não mudaram.

## O que foi executado e o que não foi

Scripts em `(sandbox)/sec-review/probes2/`. Saídas em `.../work2/`, mais `p1_sqlite2_results.json` e `p_http2_results.json` na pasta `sec-review`.

**Executado:**

1. `uv run pytest -q -m "not integration"`: **237 passed, 22 deselected**.
2. `probes2/p1_sqlite2.py`: 24 SQLite e arquivos criados para o teste, passando por `download()`. Os fixtures agora têm o esquema exato do `IndexBuilder` e metadados vindos do `PathParser`. Em cada caso há um índice anterior saudável e um overlay, com comparação de impressões digitais antes e depois.
3. O `.gz` de 739 B da rodada anterior (`work/expr/archive.gz`, com índice de expressão de 900 MB por linha), num processo novo, medindo o RSS.
4. `probes2/p_http2.py` usa a pilha real httpx/httpcore com o cliente padrão, contra HTTPS local autoassinado. São 21 cenários:
   - DNS real e injetado;
   - resolvedor que aponta outro nome para 127.0.0.1;
   - DNS lento, falho, vazio e primeiro endereço recusado;
   - redirects com segredo (absoluto, relativo, `HTTPS` em maiúsculas, userinfo, espaço);
   - prazos e tetos;
   - certificado não confiável.

   O log foi capturado em DEBUG.
5. `probes2/p_proxy.py`: proxy CONNECT local com `HTTPS_PROXY`, sem `NO_PROXY`, e destinos `localhost` e `evil.test`.
6. `probes2/p_provider.py`: `ServiceProvider` real com `IndexSnapshot` sobre MockTransport. Três cenários: índice ilegível, fábrica que sempre falha e `index.sqlite` que é um diretório.
7. Dois servidores reais `leis-mcp serve --transport streamable-http` com chave aleatória. Um tem índice pronto por HTTPS local. O outro tem índice ausente e fallback git para uma URL local com `user:repopass@…?tok=repoqtok`. Foram feitos os testes de autenticação, chamadas de ferramenta e busca de segredos nos logs.
8. Allowlist do coletor com 22 endereços, via resolvedor injetado.
9. Regex do filtro: custo em entradas adversariais de 60 KB.
10. Desempenho e prazo de `_check_rows` com 300 mil linhas, e UNIQUE forjado com `writable_schema` sobre linhas duplicadas.

**Não executado:**

- Índice real publicado e qualquer host de terceiro.
- Produção.
- Build da imagem.
- `pytest -m integration`.
- pip-audit de novo: `uv.lock` e `pyproject.toml` não mudaram no delta.
- OOM real numa VM de 512 MB.
- Rede NAT64.

## Status dos achados

### N1 (memória via índice de expressão): **CORRIGIDO**

`snapshot.py:448-473` confere o esquema exato antes de tudo: `sqlite_master` contra o SQL normalizado do `IndexBuilder` e índices só do builder. O `integrity_check` só roda depois. Em `:435` há `setlimit(SQLITE_LIMIT_LENGTH, 1 MiB)`, e em `:444-445` um `MemoryError` vira `SnapshotResourceError`.

- O `.gz` de 739 B, num processo novo, foi recusado com "índice ix_e" em 0,00 s. O RSS foi de 31 para **32 MB**; antes eram 1.749 MB.
- Também são recusados: coluna gerada com `printf` de 900 MB, `CHECK`, `COLLATE NOCASE`, identificador entre aspas, índice extra comum, view, trigger, FTS5 e a reprodução original do P1-020-1 ("esquema de norms/meta").
- Um valor de 2 MB em `meta` é recusado com `DataError`.
- Um UNIQUE forjado com `writable_schema` sobre caminhos duplicados é recusado pelo `integrity_check`.
- Esquema só com caixa e espaços diferentes é aceito, o que é semanticamente igual. `ANALYZE` (`sqlite_stat1`) é aceito.

### N2 (rótulo falso de órgão e linhas em `parlamentar/`): **CORRIGIDO**

`snapshot.py:487` recusa `path LIKE 'parlamentar/%'`, e `:499-515` (`_check_rows`, só no modo completo) exige `PathParser.is_indexable` e metadados iguais aos derivados do caminho.

- Uma linha do DF rotulada `federal`/`congresso-nacional` é recusada: "metadados não derivam do caminho 'estaduais/df/resolucoes/resolucao-1-2002.md'".
- Uma linha `parlamentar/…` é recusada.
- `README.md` e um caminho inteiro são recusados por não serem indexáveis.
- `year` como texto é aceito porque a afinidade INTEGER converte o valor ao gravar. Não é desvio.
- Desempenho: 300 mil linhas em 2,8 s, o que dá cerca de 11 s para 1,2 milhão, coerente com os 13 s documentados. Com um prazo de 0,5 s, a recusa vem em 0,50 s.

### N3 (vazamentos em log e mensagem): **PARCIALMENTE CORRIGIDO**

Corrigido:

- O filtro por regex (`snapshot.py:55-90`) passou a cobrir os loggers `httpx` e `httpcore.*`. Um `Location` absoluto com `X-Amz-Signature` ou com userinfo não aparece mais em DEBUG.
- Ao cliente MCP, um erro que não é de snapshot vira "a montagem local falhou (detalhes no log do servidor)" (`provider.py:63-66`). Isso foi conferido no servidor real com fallback git: caminho e stderr não chegam ao cliente.
- A linha `Cloning …` omite usuário e senha (`git_repository.py:41, 123-132`).

Resíduos, todos só no log do servidor:

1. **`Location` relativo, com esquema em maiúsculas ou com espaço.** `_QUERY` e `_USERINFO` só reconhecem `http(s)://` em minúsculas, e o padrão para no primeiro espaço. Reprodução: `probes2/p_http2.py` em DEBUG registrou:
   - `(b'Location', b'/rel/arc2?token=SIGSECRET123')`
   - `b'HTTPS://127.0.0.1:…/up/arc2?token=SIGSECRET123'`
   - `b'https://…/sp/a c=SIGSECRET123'`

   Só acontece com `-v`. Os redirects reais do GitHub são absolutos e em minúsculas. Correção: usar a flag `re.I` e também tirar a query dos valores de `Location` relativos, ou não subir o httpcore acima de INFO.
2. **Senha do repositório no traceback.** `provider.py:80` (`log.exception`) inclui a `CalledProcessError` encadeada, com a linha de comando inteira do git. Reprodução: `work2/server2.log`, linha 32, mostra `'https://user:repopass@127.0.0.1:18443/nonexistent-repo?tok=repoqtok'`. A linha 49 (stderr do git) mostra `?tok=repoqtok`. A operacao.md diz que "o log do clone também omite usuário e senha", mas o traceback contradiz isso. Só ocorre com `LEIS_MCP_REPO_URL` com credencial, que não é o padrão. Correção: `raise GitError(...) from None`, ou redigir os argumentos em `git_repository.py:95-100`.

### N4 (modo WAL e restos `-shm`/`-wal`): **CORRIGIDO**

`snapshot.py:426-432` recusa pelo cabeçalho (bytes 18 e 19) antes de abrir o SQLite. `:672-677` purga `*.tmp-shm` e `*.tmp-wal`. Reprodução: recusa "modo WAL", sem nenhum arquivo deixado para trás.

### P2-020-5 (`is_global` aceita site-local e multicast): **CORRIGIDO**

`overlay/allowlist.py:78-79`. Recusados: `fec0::1`, `224.0.0.1`, `ff02::1`, `ff0e::1`, `239.255.255.250`, além de loopback, RFC 1918, link-local, CGNAT, ULA, IPv4 mapeado, 6to4 privado, `192.0.0.170`, `198.18/15`, `240/4`, broadcast e `0.0.0.0`. Endereços públicos são aceitos.

Resíduo informativo: `64:ff9b::a00:1`, o prefixo NAT64 bem conhecido que embute `10.0.0.1`, é aceito porque `is_global` é `True`. Só importa numa rede NAT64 e se o DNS de um host oficial devolver isso. Pode-se recusar `64:ff9b::/96` e `64:ff9b:1::/48`.

### P2-020-3 e P2-020-6

Sem mudança. Continuam como decisão do dono.

## Superfície nova

### Resolução de DNS própria em `_DeadlineBackend` (`snapshot.py:169-213`)

- **O TLS continua verificando o nome do host.** Um resolvedor injetado mapeou `evil.test` para 127.0.0.1, onde está o servidor com certificado de `localhost`/127.0.0.1. O resultado foi `CERTIFICATE_VERIFY_FAILED: Hostname mismatch, certificate is not valid for 'evil.test'`. Com `localhost` e DNS real, o download foi aceito. O httpcore passa `origin.host` como `server_hostname`, independente do IP conectado.
- **Não há pinagem nova a desviar.** O comportamento é o mesmo de antes (o `create_connection` do httpcore também resolvia e percorria os endereços). Endereços privados continuam aceitos em HTTPS (P2-020-3). A lista de endereços é percorrida só em `ConnectError`: primeiro endereço recusado e segundo aceito funcionou.
- **Rota por proxy.** Com `HTTPS_PROXY=http://127.0.0.1:<p>`, o proxy recebeu `CONNECT localhost:18443` e `CONNECT evil.test:18443`. O resolvedor local foi chamado **0 vezes**: o destino é resolvido pelo proxy. O certificado continua verificado pelo nome (`evil.test` recusado por divergência). `NO_PROXY` continua decidido pelo nome do host.
- **Thread de DNS.** Com um resolvedor de 8 s e prazo de 3 s, a recusa veio com `SnapshotResourceError` em 3,0 s. A thread `index-dns` (daemon) continuou viva até o resolvedor voltar e tinha sumido 9 s depois. O custo é no máximo uma thread por tentativa, presa pelo tempo do `getaddrinfo` do sistema. Com o cooldown de 60 s e o worker único, isso não acumula. DNS falho e DNS vazio viram `SnapshotError` controlado. O `join(limit)` só fica sem prazo se `connect_tcp` rodar fora de um `_get`, o que o código não faz.

### Filtro de log por regex (`snapshot.py:55-90`)

- Funciona para URLs absolutas em minúsculas. Os resíduos estão no N3.
- **Custo.** `_QUERY` tem retrocesso quadrático com muitas ocorrências de `https://` sem `?`: `'https://a'*7000` (63 KB) leva 2,67 s, e `('https://x'+'a'*50)*1000` leva 0,35 s (`work2/p_regex.out`). O h11 limita o bloco de cabeçalhos a cerca de 16 KB, o que dá algo como 0,2 s por registro, só em DEBUG e só com o servidor configurado ou um de redirect. Isso não justifica achado. Sugestão: `[^\s'"?#]+` com âncora possessiva/atômica (Python 3.11+) ou um parser não regex.
- O filtro substitui `record.msg` e `args` só quando há mudança. Não encontrei efeito colateral.

### Reparo automático no provider (`provider.py:46-75`)

- **Índice corrompido no disco.** Com 5.000 bytes de lixo, foram feitos 1 GET de manifesto e 1 GET de arquivo, e as respostas seguintes foram "pronto". Não há downloads repetidos. Com o worker vivo, a resposta é "Preparando", sem worker paralelo.
- **Índice de publicador malicioso.** Um índice que passe na validação completa também passa na rápida de `_usable`, então `download()` devolve cedo (mesmo commit), sem baixar o arquivo de novo. Não encontrei como forçar downloads repetidos do arquivo.
- **Falha persistente na instalação.** Com `index.sqlite` como diretório, o `os.replace` falha com `IsADirectoryError`. O erro é registrado, o cooldown de 60 s vale e o cliente vê a mensagem genérica.

#### N6 (P2, baixo, novo): fábrica que sempre falha → GET do manifesto a cada chamada, sem cooldown e "Preparando" eterno

**Código:** `provider.py:49-60`. Uma tentativa de reparo que termina "com sucesso" sem trocar nada (`download()` devolve cedo porque o índice instalado é válido e do mesmo commit) não registra erro. Por isso o cooldown `RETRY_AFTER` não se aplica.

**Reprodução** (`probes2/p_provider.py`, cenário `factory-always-dberror`): índice instalado válido e fábrica que sempre lança `sqlite3.DatabaseError`. Dez `get()` com 0,2 s de intervalo fizeram **10 GETs de manifesto**, nenhum de arquivo, e todas as respostas foram "Preparando o índice…".

**Impacto:** é preciso uma condição em que a fábrica falhe com `DatabaseError` ou `IndexMissingError` enquanto `_usable` aceita o índice (algum erro de runtime que a validação não pega). Não encontrei como um publicador provoque isso. O efeito é um GET leve por chamada autenticada e uma mensagem enganosa, sem crescimento de disco nem de memória.

**Correção:** se o reparo terminar sem trocar o arquivo e a fábrica continuar falhando, registrar erro e aplicar o cooldown.

#### N5 (P2, baixo, novo): contrato de erros com casos que escapam

- `probes2/p1_sqlite2.py`, caso `path-as-blob`: uma linha com `path` do tipo BLOB faz `PathParser.is_indexable(bytes)` lançar `TypeError`, que não é capturado (`snapshot.py:446` só captura `sqlite3.Error`, `ValueError` e `RecursionError`).
- Continuam escapando como exceção genérica: manifesto com aninhamento profundo (`RecursionError` em `:313`), nome de arquivo com CR/LF (`httpx.InvalidURL`, porque `build_request` em `:561` fica fora do `try`) e `Location` com porta 99999 (`ValueError`).

Em todos esses casos a falha é fechada: o índice e o overlay ficaram intactos, o segredo não aparece no erro e não há fallback git. Ao cliente chega "a montagem local falhou (detalhes no log)", o que é impreciso mas seguro.

**Correção:** exigir `typeof(path)='text'` no SQL de linhas inválidas, e capturar `Exception` em `_check_rows` e em `latest()`/`_get` convertendo para `SnapshotError`.

## Controles preservados (verificados nesta rodada)

- **Autenticação HTTP.** Nos dois servidores reais:
  - `GET /health` → 200;
  - `POST /health` → 401;
  - `/mcp` sem chave, com chave errada, com cabeçalho duplicado e com `bearer` minúsculo → 401;
  - com a chave certa → 200;
  - `tools/list` sem chave numa sessão válida → 401.

  A chave apareceu **0 vezes** nos dois logs. O download a frio deu 9 normas (3 do upstream e 6 do overlay).
- **Prazos e tetos de rede** continuam valendo com a mudança para `iter_bytes()` sem tamanho fixo:
  - cabeçalhos lentos: recusa em 5,0 s;
  - corpo lento: recusa em 6,01 s;
  - chunked acima de 4 MiB: recusa;
  - bomba gzip: recusa;
  - HTTPS→HTTP: recusa (rodada anterior; código não mudou);
  - certificado não confiável: `SnapshotError`.
- **Recusas.** Em todas as recusas da validação o índice anterior e o overlay ficaram byte a byte iguais, sem temporários. O encolhimento abaixo de 50% continua recusado.
- **Docs.** A operacao.md confere com o código, com duas ressalvas. "nem nos logs do httpx e do httpcore (inclusive o Location em DEBUG)" e "o log do clone também omite usuário e senha" são verdadeiros no caso comum, mas têm os resíduos do N3. "Um índice local ilegível … baixa um novo" é verdadeiro para corrupção real, mas tem o caso de borda do N6.

## Limitações

- Não houve teste com o índice real de 1,2 milhão de normas. O desempenho foi extrapolado a partir de 300 mil linhas sintéticas.
- O OOM não foi testado numa VM real. Com o esquema exato, o vetor do N1 não chega a ser avaliado.
- Não houve teste numa rede NAT64 real.
- A revisão cobre só o delta. Para o restante vale o relatório `seguranca-020-8b09e76.md`.
- O publicador continua sendo âncora de confiança para a existência e o conteúdo do índice (P2-020-3). Os metadados agora são derivados localmente.
