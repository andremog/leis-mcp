# Operação do overlay parlamentar

O código revisado pelas duas portas independentes é `486b4e097e0c358915d3b7c4b09c5a70c01a8c70`. Esta nota só corrige o texto: não muda coletor, pacote nem `fly.toml`. O `fly.toml` continua com o mesmo app, o mesmo volume e o mesmo segredo. A coleta não é agendada. Quem escreveu o código não aprova o merge em `main`.

Base desta onda: `6e2f930bc4bf1902598cfdb05db2878be0ced06e` (`main`). Branch: `cursor/parliamentary-overlay-bfbf`.

## O que o deploy não precisa mudar

- `fly.toml`: mesma máquina, mesmo volume, mesma porta, mesmo health check.
- `LEIS_MCP_API_KEY`: continua só como segredo do Fly. Não entra no repositório, no log do overlay nem no pacote.
- Dockerfile: o `COPY src` já leva `src/leis_mcp/data/parliamentary`. O wheel gerado por `uv build` inclui esses arquivos (conferido nesta onda).
- Não há variável nova nem serviço externo.

O pacote versionado (manifesto, seis normas e proveniência, sem o SQLite) mede 672.241 bytes nesta revisão. O índice SQLite nasce na ativação e não faz parte desse pacote. Isso não substitui a medição do índice upstream e não foi conferido no volume do Fly. Esta execução não abriu o app `leis-mcp-andremog` nem leu segredo.

Num diretório local só para teste (`/tmp/leis-integration`, apagável), `leis-mcp index` clonou o upstream e montou o índice em 12,8 s: 1.218.990 normas, commit `c9f21b4ab11aa0d7ca6fe861d75028e94219c2d6`. Tamanhos nesse diretório: clone 258.613.260 bytes, `index.sqlite` 348.499.968 bytes, overlay 702.145 bytes, cache depois da integração 2.282.675 bytes, total 583 MB. Um processo que abre esse índice e lê o art. 7º da Resolução nº 1/2002-CN ficou com `ru_maxrss` de 37.440 KB. O SQLite não entra inteiro na RAM nesse caminho. Não medi o pico de RSS do `index` em si; o número antigo de ~360 MB no build continua sendo o do adendo de segurança, não uma medição refeita aqui.

## Subida

Na primeira execução, se `overlay/active.json` não existe, o processo valida o pacote da imagem num staging e só então publica em `versions/` e grava o ponteiro com `origin: bundle`. Se o ponteiro já é `bundle` e a imagem trouxe outra versão, a nova vira a ativa e a anterior fica para rollback. Ponteiro `manual`, `collect` ou `rollback` não é substituído por uma imagem mais nova. Ativar de novo a mesma versão com `--from` ou `collect --activate` muda a origem para `manual` ou `collect` e preserva o campo `previous`. Ativar o pacote da imagem (`overlay activate`, sem `--from`) devolve a origem `bundle`, e a subida seguinte volta a acompanhar a imagem.

`leis-mcp update` continua só no clone e no `index.sqlite`. O processo que já está no ar mantém os arquivos SQLite que abriu. Depois de um update, ou de um rollback, reinicie o app para reler o disco. É o mesmo cuidado que o índice upstream já exigia.

Não cron, não `warm` específico e não coleta na subida. A coleta é o comando manual `overlay collect`: não é ferramenta MCP e não entra em agendamento.

## Comandos

```bash
leis-mcp overlay status
leis-mcp overlay activate                 # reinstala o pacote da imagem (origin bundle)
leis-mcp overlay activate --from DIR      # pacote montado à mão (origin manual)
leis-mcp overlay rollback
leis-mcp overlay collect --into ./pacote-novo          # caminho relativo novo; recusa se já existir
leis-mcp overlay collect --into /caminho/absoluto/do-pacote --replace
leis-mcp overlay collect --html-dir /tmp/leis-src --into /caminho/do/bundle --replace --activate
```

`collect` sem `--html-dir` fala só com a allowlist (Câmara, Senado, Congresso, normas.leg.br, lexml.gov.br), em HTTPS, com os tetos descritos na arquitetura. O HTML não é instrução.

## Rollback

1. `leis-mcp overlay rollback` — valida a versão anterior e só então troca `active.json`. Se a versão anterior não confere, o ponteiro fica byte a byte como estava.
2. Reinicie o processo (`fly apps restart` só depois que esta branch estiver publicada e o revisor tiver autorizado o deploy).
3. `leis-mcp overlay status` deve mostrar a versão anterior.
4. Para voltar ao pacote da imagem: `leis-mcp overlay activate` e reinicie de novo.

A ativação valida hashes, metadados, proveniência, o identificador do diretório e a cobertura um-para-um do SQLite (índice único de `path`, sem linha faltando, sobrando ou repetida) no staging. `READY` e o rename para `versions/p-…` acontecem antes do ponteiro. Se a tentativa falha, o staging dessa tentativa é apagado; a próxima ativação também limpa `staging/` antes de começar. Isso não apaga `versions/`, a versão ativa nem a anterior, e não é prova contra queda de energia: o diretório `versions` não recebe fsync depois do rename. Um `versions/` incompleto não é ativado no retry. O ponteiro antigo continua válido. Um ancestral que seja link simbólico é recusado antes de criar diretório. `collect --into` não apaga um diretório qualquer: sem `--replace` recusa destino existente; com `--replace` só substitui um pacote já marcado `leis-mcp-parliamentary-bundle` que esteja fora do home, do clone, do cache e do overlay, inclusive descendentes e ancestrais desses caminhos. Raízes como `/` e `/data` são recusadas.

## Testes desta onda

Ambiente: Python 3.12, `uv` 0.11.2. A integração usou `LEIS_MCP_HOME=/tmp/leis-integration` com o índice montado nesta máquina.

```text
uv run ruff check src tests          # passou
uv run ruff format --check src tests # passou
uv run pytest -m "not integration"   # 170 passed
uv run pytest -m integration         # 21 passed
```

Os testes unitários incluem a suíte anterior e as regressões do overlay: hash inválido, índice esvaziado ou com total ilegível, manifesto sem `blob` ou proveniência, ponteiro JSON inválido, versão renomeada, linhas duplicadas no índice, troca de metadados, origem, rollback, caminho `..`, symlink de ancestral, destino dentro do overlay recusado, citação `cf. Art.`, tachado parcial, ferramentas MCP e o wheel. A integração usa o índice local de 1.218.990 normas.

O pacote que está no repositório foi montado assim, com substituição explícita do pacote reconhecido:

```bash
uv run leis-mcp overlay collect --html-dir /tmp/leis-src --into "$PWD/src/leis_mcp/data/parliamentary/bundle" --replace
```

Os HTML em `/tmp/leis-src` tinham sido baixados das URLs do inventário, com `User-Agent: leis-mcp-overlay/0.1`. O `fetched_at` de cada proveniência é o horário `Date` dessa resposta, em UTC.

## Nota de publicação

QA e segurança independentes passaram o código `486b4e097e0c358915d3b7c4b09c5a70c01a8c70` para o pacote congelado de seis normas. Nesta máquina o builder registrou `ruff` limpo, 170 testes fora de `integration` e 21 de integração. Esses números são do autor, não uma reexecução dos revisores. O merge em `main` continua dependendo de aprovação explícita do dono. O deploy pode usar a imagem desta branch sem esse merge.

Limites que não bloqueiam esta imagem e ficam para acompanhamento:

- R1. Cada conexão, TLS, leitura e escrita tem teto finito, no máximo 45 s por operação, e o DNS no máximo 5 s. A soma pode passar de 45 s. Não há garantia de prazo total estrito.
- R2. Erro de rede ao ler o corpo, depois dos cabeçalhos, pode não ser traduzido para a recusa de coleta. A montagem não grava nem ativa essa norma.
- R3. `tests/test_overlay_httpcore.py::test_bounded_official_resolution_page` acessa a página oficial e não tem a marca `integration`.

A coleta permanece manual, fora do MCP e fora de qualquer agenda. Não criar segredo novo. `LEIS_MCP_API_KEY` continua o segredo que o app já tem. Depois do deploy, `/health` sem chave continua 200, `/mcp` sem chave continua 401, e as quatro ferramentas autenticadas precisam devolver a identidade do Congresso Nacional, o art. 7º, o corte de consolidação e o aviso histórico do Senado, com o upstream ainda consultável. O rollback da imagem é o release anterior do Fly, com `fly releases rollback`; o rollback do overlay é `leis-mcp overlay rollback` e um restart. Não agendar `overlay collect`.

Se o ponteiro estiver ilegível, a versão apontada não existir, o manifesto ou o índice estiverem incompletos, o hash não conferir, faltar `blob` ou proveniência, o total do índice não for numérico, ou o diretório não for o identificador do manifesto, o processo registra o erro e sobe só com o upstream. Isso cobre `OverlayError`, JSON inválido e SQLite ilegível. Um erro do próprio índice upstream não é escondido. Pedido que nomeia o Congresso Nacional, nesse modo, não devolve o homônimo do DF. `/health` continua aberto e `/mcp` continua exigindo o Bearer. O coletor limita cada operação de rede, não o relógio total da coleta. Um desvio no caminho, depois do socket, continua fora desse controle. Hashes conferem integridade do pacote local, não autenticam a origem.
