# Operação do overlay parlamentar

Esta onda implementa e testa no repositório. Não altera o app no Fly, não lê segredo e não agenda coleta. Publicar a branch depende de QA independente e de revisão de segurança separadas: quem escreveu o código não é o aprovador.

Base desta onda: `6e2f930bc4bf1902598cfdb05db2878be0ced06e` (`main`). Branch: `cursor/parliamentary-overlay-bfbf`.

## O que o deploy não precisa mudar

- `fly.toml`: mesma máquina, mesmo volume, mesma porta, mesmo health check.
- `LEIS_MCP_API_KEY`: continua só como segredo do Fly. Não entra no repositório, no log do overlay nem no pacote.
- Dockerfile: o `COPY src` já leva `src/leis_mcp/data/parliamentary`. O wheel gerado por `uv build` inclui esses arquivos (conferido nesta onda).
- Não há variável nova nem serviço externo.

O disco do pacote é 669.293 bytes. Ativado, o diretório do overlay fica em 702.145 bytes, dos quais o SQLite tem 32.768 bytes. Isso não substitui a medição do índice upstream e não foi conferido no volume do Fly. Esta execução não abriu o app `leis-mcp-andremog` nem leu segredo.

Num diretório local só para teste (`/tmp/leis-integration`, apagável), `leis-mcp index` clonou o upstream e montou o índice em 12,8 s: 1.218.990 normas, commit `c9f21b4ab11aa0d7ca6fe861d75028e94219c2d6`. Tamanhos nesse diretório: clone 258.613.260 bytes, `index.sqlite` 348.499.968 bytes, overlay 702.145 bytes, cache depois da integração 2.282.675 bytes, total 583 MB. Um processo que abre esse índice e lê o art. 7º da Resolução nº 1/2002-CN ficou com `ru_maxrss` de 37.440 KB. O SQLite não entra inteiro na RAM nesse caminho. Não medi o pico de RSS do `index` em si; o número antigo de ~360 MB no build continua sendo o do adendo de segurança, não uma medição refeita aqui.

## Subida

Na primeira execução, se `overlay/active.json` não existe, o processo copia o pacote da imagem para `LEIS_MCP_HOME/overlay/versions/` e grava o ponteiro com `origin: bundle`. Se o ponteiro já é `bundle` e a imagem trouxe outra versão, a nova vira a ativa e a anterior fica para rollback. Ponteiro gravado por `collect`, `activate --from` ou `rollback` não é substituído na subida.

`leis-mcp update` continua só no clone e no `index.sqlite`. O processo que já está no ar mantém os arquivos SQLite que abriu. Depois de um update, ou de um rollback, reinicie o app para reler o disco. É o mesmo cuidado que o índice upstream já exigia.

Não cron, não `warm` específico e não coleta na subida. A coleta usa rede e é comando de manutenção.

## Comandos

```bash
leis-mcp overlay status
leis-mcp overlay activate                 # reinstala o pacote da imagem (origin bundle)
leis-mcp overlay activate --from DIR      # pacote montado à mão (origin manual)
leis-mcp overlay rollback
leis-mcp overlay collect --into DIR       # baixa o inventário; não ativa
leis-mcp overlay collect --into DIR --html-dir DIR --activate
```

`collect` sem `--html-dir` fala só com a allowlist (Câmara, Senado, Congresso, normas.leg.br, lexml.gov.br), em HTTPS, com os tetos descritos na arquitetura. O HTML não é instrução.

## Rollback

1. `leis-mcp overlay rollback` — troca `active.json` para a versão anterior. Se não houver anterior, o comando falha e o ponteiro fica como está.
2. Reinicie o processo (`fly apps restart` só depois que esta branch estiver publicada e o revisor tiver autorizado o deploy).
3. `leis-mcp overlay status` deve mostrar a versão anterior.
4. Para voltar ao pacote da imagem: `leis-mcp overlay activate` e reinicie de novo.

A ativação grava a versão inteira antes de trocar o ponteiro. Um processo morto no meio deixa lixo em `staging/` ou uma versão que ninguém aponta. O ponteiro antigo continua válido.

## Testes desta onda

Ambiente: Python 3.12, `uv` 0.11.2. A integração usou `LEIS_MCP_HOME=/tmp/leis-integration` com o índice montado nesta máquina.

```text
uv run ruff check src tests          # passou
uv run ruff format --check src tests # passou
uv run pytest -m "not integration"   # 150 passed
uv run pytest -m integration         # 21 passed
```

Os 150 incluem a suíte que já existia e os testes novos do overlay: recorte real do art. 7º, artigo 4º-A com texto tachado, inciso e alínea, homônimo do DF que não substitui o Congresso Nacional quando o órgão é informado, sobrevivência do overlay quando o `index.sqlite` é trocado, ativação idempotente, rollback, e recusa do coletor (HTTP, usuário na URL, IP de metadata, IP privado, redirect para `127.0.0.1`, corpo acima do teto).

O pacote foi montado com:

```bash
uv run leis-mcp overlay collect --html-dir /tmp/leis-src --into src/leis_mcp/data/parliamentary/bundle
```

Os HTML em `/tmp/leis-src` tinham sido baixados das URLs do inventário, com `User-Agent: leis-mcp-overlay/0.1`. O `fetched_at` de cada proveniência é o horário `Date` dessa resposta, em UTC.

## Plano para a publicação, depois da revisão

1. QA independente e revisão de segurança lêem o diff, os testes e este diretório. O autor não aprova a própria onda.
2. Só então: push da branch, PR para `main`, CI, `fly deploy` do app que já existe.
3. Não criar segredo novo. Conferir que `LEIS_MCP_API_KEY` continua definido antes do deploy, como o README já manda.
4. Depois do deploy, com a chave que o operador já tem (não nesta execução): `/health` sem chave continua 200; `/mcp` sem chave continua 401; uma chamada autenticada a `buscar_norma` / `ler_artigo` da Resolução nº 1/2002-CN devolve o art. 7º acima e não uma norma do DF.
5. Não agendar `overlay collect` no Fly. Atualizar o pacote é outra onda, com HTML novo, versão nova e o mesmo rollback.

Se o primeiro boot da imagem nova falhar antes de gravar o ponteiro, o índice upstream segue utilizável na próxima subida: o overlay só é anexado quando o pacote abre. Uma versão ativa corrompida registra o erro e o processo segue só com o upstream; pedido com órgão do Congresso, nesse caso, não cai no homônimo do DF, porque o filtro de órgão continua e o upstream não tem esse órgão.
