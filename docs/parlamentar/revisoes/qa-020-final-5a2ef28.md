<!-- Candidato revisado: 5a2ef28334684396ccd0d09b8604f8bd513978a5 (árvore 9cd5b70502a5e7b2f8a0238ce9cba2f4b11eb26a), branch feat/parlamentar-020. Gate final de QA independente, delta 67671ee..5a2ef28, 8 de novembro de 2026. Veredito: PASS (só QA; não é revisão de segurança). -->

# QA independente, gate final: `feat/parlamentar-020` em `5a2ef28`

## Veredito: **PASS**

Este delta não traz P0 nem P1, e as rodadas anteriores também não deixaram nenhum aberto (`8b09e76` → `d62b6aa` → `0c89f78` → `67671ee` → `5a2ef28`).

- P2-H está fechado. O filtro de log agora cobre as aspas e continua legível.
- P2-I está fechado, e o `except` redundante foi removido.
- Os P2 residuais estão listados no fim, para virar issue de acompanhamento.

O PASS é só do portão de QA. A revisão de segurança, que inclui o N8, é um gate separado. O PASS também não autoriza deploy.

## HEAD e identidade conferidos

- `git rev-parse HEAD`: `5a2ef28334684396ccd0d09b8604f8bd513978a5`.
- `HEAD^{tree}`: `9cd5b70502a5e7b2f8a0238ce9cba2f4b11eb26a`.
- Branch `feat/parlamentar-020`, árvore de trabalho limpa.
- Delta `67671ee..HEAD`: um commit, "fix(snapshot): redact httpx/httpcore logs at the source; scrubber edge cases". São dois arquivos, `src/leis_mcp/catalog/snapshot.py` e `tests/test_snapshot.py`, com +105/−17. Li o delta inteiro.
- Nenhum ref remoto contém HEAD, ou seja, ainda não houve push.

## O que executei

| Execução | Resultado |
|---|---|
| `ruff check src tests` / `ruff format --check src tests` | limpos (74 arquivos) |
| `pytest -q -m "not integration"` | **271 passed**, 22 deselected |
| `pytest -q -m integration`, com o índice real `c9f21b4` (1.218.990 normas) | **21 passed, 1 failed**. A falha é `test_bounded_official_resolution_page`, com `FetchRejected: Falha ao resolver www2.camara.leg.br`: o DNS direto não resolve neste sandbox. É limitação de ambiente, não passe. Repete em todas as rodadas. |
| Sondas de log P2-E/P2-H (`probe_scrub.py`, 21 linhas e 8 entradas hostis) | 21/21 sem vazamento e com o diagnóstico legível; detalhes abaixo |
| `leis-mcp -v update` real, em DEBUG | 0 ocorrências de `X-Amz`, `signature=`, `sig=`, `token=` ou `jwt` |
| Mutação, cópia `mut5/` | 10 mutantes: 6 mortos e 4 sobreviventes. Os dois da redação na origem, N8a e N8c, se cobrem um ao outro: aplicados juntos, derrubam 4 testes. Ficam 2 lacunas (R4). |
| Sondas anteriores refeitas (`*-final.txt`) | `probes2` 31/31; fallback do `update` com os 9 cenários idênticos à rodada anterior; `multiaddr`, `provider2`, `location`, `chunked`, `prepare` e `tls` sem mudança |
| MCP stdio de ponta a ponta sobre o índice real (`mcp-final.json`) | art. 7º da Res. 1/2002-CN com §§ 1º a 7º e o "42º"; 104-A com o corte de jan/2023; o filtro de órgão devolve só o CN; Senado marcado como histórico; CDC do upstream OK |
| HTTP local com `LEIS_MCP_API_KEY` | `/health` 200; `/mcp` sem chave 401; chave errada 401; chave certa 200; a chave não aparece no log |

Não executei a revisão de segurança, o wheel, a imagem, o deploy, o Fly nem a produção. Também não houve DNS direto real nem coleta oficial da Câmara.

## P2-H e P2-E: o diagnóstico de log continua legível

Os resultados estão em `probe_scrub-final.txt`.

**Os dois vazamentos do P2-H estão fechados.**
- `error 'fetch https://a.example/x then https://user:SECRET@b.example/y failed' end` vira `… https://b.example/y failed' end`.
- `bad line "GET /index.json?sig=SECRET HTTP/1.1" end` vira `bad line "GET /index.json" end`.

**A legibilidade do P2-E se mantém.**
- `… fatal: path a/b?c not found; retrying later with more details` fica intacto.
- `Erro em federal/leis#123: depois deste ponto vem texto importante` fica intacto.
- `Qual o prazo? A resposta=sim depois` e `Isso é válido? Sim, segue texto` ficam intactos.
- `HTTPS://H/P?SIG=X rest of message` vira `HTTPS://H/P rest of message`.

**Casos novos também são cobertos:** URL relativa ao esquema com credenciais (`//user:SECRET@host/p` vira `//host/p`), token solto do tipo `arc2?token=SECRET` e `Location` com aspas desbalanceadas.

**Linearidade:** entradas hostis de 100 KiB e de 1 MB levam no máximo 0,005 s. Linhas acima de 16 KiB são truncadas e a URL do fim não aparece.

**Log real (`debug-update-final.log`):**
- As linhas INFO do httpx mostram esquema, host e caminho, seguidos de `"HTTP/1.1 302 Found"` e `"HTTP/1.1 200 OK"`. O `_safe` é aplicado direto no argumento.
- As linhas de cabeçalho do httpcore viram `send_request_headers.started (headers omitted)` e `receive_response_headers.complete (headers omitted)`.
- Connect, TLS (com `server_hostname='github.com'`) e corpo continuam visíveis. O status HTTP só aparece na linha INFO do httpx (ver R2).

O httpx 0.28.1 só emite log com argumentos em `_client.py:1025` e `:1740` (`HTTP Request: %s %s …`). Por isso, a regra que reescreve `args[1]` em todo registro do logger `httpx` não atinge outras mensagens.

## Mutação do P2-I e do delta

| Mutante | Resultado |
|---|---|
| sem o corte de 16 KiB (`snapshot.py:63-64`) | **morto** (`test_url_scrubbing_truncates_very_long_lines`) |
| mensagem antiga de reparo falho (`provider.py:64`) | **morto** (`test_failed_repair_names_the_unreadable_download`) |
| aspas: só o primeiro pedaço é examinado | morto (2 testes) |
| aspas: o resto não é cortado depois da query | morto |
| sem omitir os cabeçalhos do httpcore | morto (2 testes) |
| sem remover userinfo | morto (5 testes) |
| sem `_safe` na origem (N8a) **e** sem a segunda camada (N8c), juntos | morto (4 testes). Separados, um cobre o outro: é defesa em profundidade, não lacuna. |
| sem o ramo de URL relativa ao esquema (`snapshot.py:94-95`) | **sobrevive**, ver R4 |
| sem o ramo de token solto `x?k=v` (`snapshot.py:99-100`) | **sobrevive**, ver R4 |

O `except (httpx.HTTPError, httpx.InvalidURL, ValueError, RecursionError)` voltou a ser só `except httpx.HTTPError` (`snapshot.py:663`). Os quatro `Location` malformados da minha sonda (`[::1`, porta `abc`, porta `99999`, NUL) continuam dando `SnapshotError` controlado com fallback git, sem exceção crua (`probe_location-final.txt`).

## Achados

### P0
Nenhum.

### P1
Nenhum.

## P2 residuais para a issue de acompanhamento

1. **R1 (P2-5, decisão do dono).** O merge traz `.github/workflows/index.yml`, com agendamento semanal (`cron: "0 6 * * 1"`) e permissão de escrita em releases. É uma rotina nova no repositório, enquanto `INDEX_URL` aponta para `mazulo/leis-mcp`. Falta decidir se o agendamento fica desabilitado no fork ou se a URL passa a apontar para o próprio fork.
2. **R2 (diagnóstico).** Os eventos `*_headers.*` do httpcore em DEBUG ficaram reduzidos a "(headers omitted)", inclusive o status e os cabeçalhos que não são sensíveis (`snapshot.py`, `_RedactQuery.filter`). O status continua na linha INFO do httpx. Dentro de um valor entre aspas, o texto depois da primeira URL com query é descartado; isso é intencional, segundo o comentário do código.
3. **R3 (provider).** Uma corrupção que só aparece no meio de uma consulta, com `meta` legível, não dispara reparo. O resultado é um `sqlite3.DatabaseError` cru até alguém rodar `leis-mcp update`. Está documentado no `operacao.md`.
4. **R4 (testes).** Dois ramos novos do filtro não têm teste, embora a minha sonda mostre que funcionam: URL relativa ao esquema (`snapshot.py:94-95`) e token solto `x?k=v` (`snapshot.py:99-100`).
5. **R5 (operação).** O validador exige o SQL do `IndexBuilder` e o `PathParser` locais. Se o publicador mudar esse código, todo snapshot é recusado e o `update` passa a cair no git, com o custo de clone e build descrito no `operacao.md`. Ao trazer mudanças do upstream, convém sincronizar `catalog/index.py` e `catalog/path_parser.py`.
6. **R6 (ambiente e cobertura).** O teste ao vivo da Câmara nunca passou neste sandbox, por falta de DNS direto. Ele precisa de uma execução bem-sucedida num ambiente com rede antes de servir de evidência. Além disso, o DNS próprio do backend só foi exercitado com resolvedor injetado e com TLS em loopback.

## Regressões conferidas OK, nesta rodada e acumuladas

- **Prazos pela pilha real httpx/httpcore com backend que respeita os timeouts:**
  - cabeçalhos lentos cortados aos 120,0 s, no manifesto, no arquivo pelo CLI (sem git) e pela rota do proxy;
  - corpo lento cortado aos 120,0 s;
  - corpo que para depois dos cabeçalhos dá erro de orçamento, sem git;
  - TLS, connect e DNS limitados ao prazo;
  - quando o primeiro endereço não responde, o próximo é tentado.
- **Corpo de redirect:** nunca lido.
- **Tetos:** conferidos a cada leitura de rede (no máximo 131.072 B para um teto de 64 KiB).
- **Validação:** são recusados os índices únicos de fachada, o UNIQUE forjado com `writable_schema`, esquema diferente do builder, metadados que não derivam do caminho (inclusive uma norma do DF reetiquetada como CN), modo WAL e valor gigante. `sqlite_stat1` é aceito. O índice real passa em cerca de 13 s.
- **Reparo de mesmo commit:** funciona para arquivo lixo, truncado e total errado.
- **404 no arquivo:** o manifesto é relido uma única vez.
- **Fallback do `update`:** recusas de orçamento e falta de git saem com exit 2 sem chamar o git; as demais falhas caem no git; índice e overlay ficam intactos.
- **Provider:** 1 preparo para 12 chamadas concorrentes, intervalo de 60 s entre reparos, mensagens pt-BR próprias. O reparo real pelo MCP stdio funciona.
- **TLS:** o certificado é verificado pelo nome do host com conexão ao IP resolvido.
- **Overlay:** hash idêntico depois de um reparo real.
- **MCP, upstream, `/health` e Bearer:** OK.

## Limitações

- O backend de rede é falso nas sondas de prazo e de fallback. Na sonda de fallback, builder e repositório também são falsos, então o clone e o build git reais não rodaram.
- Sem DNS direto real e sem coleta da Câmara (limitação do sandbox).
- Sem revisão de segurança, wheel, imagem, Fly ou produção. Este PASS não libera o deploy sem o gate de segurança e a autorização do dono.

## Artefatos

Diretório: `(sandbox)/qa-review/`

- `probe_scrub.py` e `probe_scrub-final.txt`
- `*-final.txt` (sondas refeitas)
- `debug-update-final.log`
- `mcp-final.json`
- `mut5/` (cópia usada na mutação)
- relatórios anteriores: `qa-020-8b09e76.md`, `qa-020-d62b6aa.md`, `qa-020-0c89f78.md` e `qa-020-67671ee.md`
