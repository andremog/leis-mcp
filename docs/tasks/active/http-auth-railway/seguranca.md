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
