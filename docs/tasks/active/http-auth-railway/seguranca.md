# Verificação de segurança: http-auth-railway

- Revisor/verificador independente (não escreveu o código), 02/11/2026.
- Escopo: diff `bc35f3c..e71dc44` (8 arquivos) + correção própria do verificador (commit `fix(http): fail closed on websocket scopes`).
- Risco da onda: **alto** (autenticação + exposição pública na internet). Dados servidos: legislação pública, somente leitura.

## Resultado

**Sem P0 nem P1 abertos.** Um P2 foi corrigido pelo verificador (ver abaixo); os demais P2 ficam como trabalho seguinte.

## Checagens determinísticas

| Checagem | Resultado |
|---|---|
| `uv run ruff check src tests` / `ruff format --check` | OK |
| `uv run pytest -m "not integration"` | 139 passed (137 antes da correção + 2 novos) |
| `uv run pytest -m integration` (índice local) | 21 passed |
| Varredura de segredos no diff (`detect-secrets` + grep de padrões de token) | nenhum achado |
| `pip-audit` nas dependências travadas (`uv export --frozen --no-dev`) | nenhuma vulnerabilidade conhecida |
| Dependências novas | nenhuma (`uvicorn`/`starlette` já vinham com `mcp`) |

## Smoke local (curl contra o servidor real, chave aleatória, porta livre)

Rodado em dois modos: CLI com `--host 127.0.0.1` e app montado com `host=0.0.0.0` (como no Railway) servido em loopback.

- 401 em: sem cabeçalho; chave errada; `bearer` minúsculo; cabeçalho `Authorization` duplicado (bom+ruim e bom+bom); `/health/../mcp` e `/health/%2e%2e/mcp` (`--path-as-is`); `/mcp/`; `/mcp%2F`; `/health%2F..%2Fmcp`; `//mcp`; `/health/`; `POST /health`; `OPTIONS`, `GET` e `DELETE /mcp` sem chave; chave na query string; upgrade websocket sem chave.
- 200 em: `GET /health` e `HEAD /health` (sem chave); `POST /mcp` `initialize` com a chave certa.
- Host header `leis-andre.up.railway.app` com a chave: **200** no modo `0.0.0.0` (Railway) e 421 no modo `127.0.0.1` (proteção anti-DNS-rebinding do SDK, que só liga em host de loopback). Logo o Railway não será bloqueado.
- A chave não aparece nos logs do servidor; só apareceu na linha de acesso da requisição de teste que propositalmente pôs a chave na query string (o uvicorn registra a query). Não colocar a chave na URL (o README já diz isso e o servidor não aceita).

## Revisão do código

Pontos verificados e OK:
- `ApiKeyMiddleware` é ASGI puro (não quebra SSE); a liberação é por igualdade exata `path == "/health"` e método GET/HEAD, e a resposta é gerada pelo próprio middleware sem chamar o app interno, então não há bypass por normalização de caminho.
- Comparação em tempo constante (`hmac.compare_digest`) sobre bytes do cabeçalho inteiro `Bearer <chave>`; exige exatamente um cabeçalho `Authorization`; 401 não revela o motivo.
- `Settings.api_key` com `repr=False`; a chave não é logada; o aviso de servidor aberto não imprime a chave.
- Anti-DNS-rebinding/Origin: com `0.0.0.0` o SDK não valida Host/Origin, mas a chave Bearer exigida em todo `/mcp` neutraliza DNS rebinding (o navegador da vítima não tem a chave). Sem CORS configurado, navegadores não leem respostas cross-origin.
- Thread de warm-up: instância própria do serviço; o índice SQLite é aberto `mode=ro` com lock; o cache de conteúdo é verificado por hash do blob. Sem risco de corrupção. Falhas são logadas sem derrubar o servidor.
- Sem novas entradas de usuário que alcancem rede ou shell (sem SSRF/injeção novos).
- `VOLUME` removido do Dockerfile: correto, o Railway rejeita `VOLUME`; o README manda criar o volume em `/data`.

## Achados

### P0
Nenhum.

### P1
Nenhum.

### P2
1. **[CORRIGIDO pelo verificador]** Escopos `websocket` passavam direto para o app interno sem checar a chave. Não explorável hoje (a imagem não tem `websockets`/`wsproto` e o MCP não tem rota websocket), mas é uma falha de "fail open". Correção: com chave definida, websocket sem `Authorization` válido é fechado com código 1008. Testes novos em `tests/test_http_auth.py` (o de rejeição falha sem a correção). **Atenção do André: essa correção é do verificador, não do builder.**
2. Sem chave, o serviço sobe aberto em host público (só um aviso no log). No Railway, esquecer `LEIS_MCP_API_KEY` deixa o `/mcp` aberto a qualquer um (dados públicos, mas consumo do plano). Sugestão futura: opção `LEIS_MCP_REQUIRE_KEY=1` que recusa subir sem chave fora do loopback. Mitigação agora: conferir a variável antes do primeiro deploy.
3. Esquema `bearer` em minúsculas é rejeitado (RFC 7235 diz que o esquema não diferencia maiúsculas). Seguro, só interoperabilidade; os clientes conhecidos mandam `Bearer`.
4. Container roda como root e a imagem base `python:3.12-slim` não é fixada por digest (o `uv` está fixado em 0.11.2). Aceitável no Railway (volumes montados como root); fixar digest e/ou usuário não-root é melhoria futura.
5. `PORT`/`LEIS_MCP_PORT` não numérico gera `ValueError` ao montar o parser (afeta todos os subcomandos). Erro de configuração, sem impacto de segurança.
6. Após `leis-mcp update` via `railway ssh`, o processo em execução continua com o índice antigo aberto até reiniciar o serviço. Não há atualização automática do acervo. Documentar "reinicie o serviço após o update".
7. Logs de acesso do uvicorn registram a query string; inofensivo enquanto a chave só vai no cabeçalho.

---

## Adendo 02/11/2026: troca de Railway por Fly.io

- O Railway deixou de aceitar cadastros; o André escolheu o Fly.io. Builder: `4e785aa` (fly.toml, remoção do railway.json, .dockerignore) e `05518bf` (README "Deploy no Fly.io"). Revisão/verificação em passada separada, relendo o diff e rodando tudo de novo.
- Risco: **alto** (exposição pública), mas o código de autenticação não mudou; a mudança é de configuração de deploy e documentação.

### Checagens determinísticas (rodadas de novo)

| Checagem | Resultado |
|---|---|
| `ruff check` / `ruff format --check` | OK |
| `pytest -m "not integration"` | 139 passed |
| `pytest -m integration` (índice recém-montado) | 21 passed |
| `fly.toml` | TOML válido (`tomllib`). `flyctl config validate` exige login no Fly, então não rodou; validar no primeiro deploy |
| Varredura de segredos (`detect-secrets` + grep de padrões de token/Fly) | nenhum achado |
| Dependências novas | nenhuma |

### Medições na box

- `leis-mcp index` do zero: 25 s, pico de **~360 MB de RSS**, 580 MB em disco (repo 247 MB, índice 333 MB, cache 6 MB). Por isso a VM tem 512 MB (256 MB estouraria na primeira montagem).
- Servidor ocioso com warm-up: ~90 MB.
- Smoke com o mesmo ambiente do `fly.toml` (`0.0.0.0`, porta via `LEIS_MCP_PORT`, chave aleatória): `/health` 200 sem chave; `/mcp` 401 sem chave e com chave errada; 200 com a chave e `Host: leis-mcp-andremog.fly.dev` (o proxy do Fly não será barrado pela proteção de Host do SDK). A chave não apareceu no log.

### Revisão

OK:
- `[processes] app` substitui só o CMD (doc do Fly), então roda `leis-mcp serve --transport streamable-http` pelo ENTRYPOINT da imagem; `[mounts]` e `[http_service]` ligados ao mesmo processo.
- Health check interno em HTTP puro; `force_https` só vale na borda, sem redirecionamento no check.
- A chave fica só em `fly secrets` e entra por stdin (`fly secrets import`), sem aparecer na linha de comando nem no histórico; o `fly.toml` não tem segredo.
- Montagem do índice e clone são atômicos (`index.tmp` + `os.replace`, `repo.partial` + rename): uma máquina parada no meio do primeiro início não deixa índice corrompido.

Achados:
- P0: nenhum. P1: nenhum.
- P2 **[CORRIGIDO na revisão]**: volume de 1 GB ficaria quase cheio no `leis-mcp update` (repo + índice antigo + `index.tmp` ≈ 950 MB). Volume passa a 2 GB no `fly.toml` e no README.
- P2: continua valendo o P2 nº 2 acima: sem `LEIS_MCP_API_KEY` o servidor sobe aberto. O README põe o segredo antes do primeiro deploy, em destaque. Conferir com `fly secrets list` antes do `fly deploy`.
- P2: com `auto_stop_machines = "stop"`, a sessão MCP fica na memória e se perde quando a máquina para; o cliente precisa reconectar (o README avisa). Um stream SSE aberto pelo cliente pode manter a máquina ligada enquanto ele estiver conectado (custo, não segurança).
- P2: a carência do health check é de 120 s e o primeiro início pode passar disso numa CPU compartilhada; as falhas só geram novas tentativas dentro do `wait_timeout` de 15 min. Se o primeiro deploy expirar, rodar `fly deploy` de novo (o índice já montado fica no volume).
- P2: imagem roda como root e a base não é fixada por digest (igual ao adendo anterior).
