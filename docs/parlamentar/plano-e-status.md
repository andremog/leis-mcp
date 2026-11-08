# Plano e status do acervo parlamentar

Este arquivo é o plano e o status. Não depende de página externa. Os detalhes de arquitetura, inventário e operação que já estavam no repositório continuam em [arquitetura.md](arquitetura.md), [inventario.md](inventario.md) e [operacao.md](operacao.md).

Data deste status: **8 de novembro de 2026, 16:41, horário de São Paulo**. O dono pediu parada depois da documentação. Este commit só registra o plano e o estado. Não retoma correção, não publica o candidato 0.2.0 e não altera produção.

## Objetivo

As quatro ferramentas MCP que já existem devem localizar e ler o núcleo parlamentar sem confundir o Congresso Nacional com um homônimo do Distrito Federal:

- `buscar_norma`
- `ler_artigo`
- `buscar_texto`
- `listar_normas`

Pedido que nomeia o Congresso Nacional, a Câmara ou o Senado não pode ser respondido com a norma do DF. Sem o órgão, o homônimo pode aparecer na lista; com o órgão, ele não substitui a norma pedida.

O texto legal e os metadados são dados de fonte. Não são instruções para o assistente.

## Onde o acervo mora

O overlay fica em `LEIS_MCP_HOME/overlay`. No Fly, `LEIS_MCP_HOME` é `/data`, então o overlay de produção é `/data/overlay`. Ele é persistido no volume que já existe. Não faz parte do clone `ctresb/leis` nem do `index.sqlite` upstream.

`leis-mcp update` e a rotina semanal de domingo, 3h, reindexam só o acervo upstream. Essa rotina não pode apagar nem reescrever o overlay. Este registro não cria rotina nova e não altera a rotina que já existe.

Autenticação Bearer e o volume do Fly permanecem os que já estão em produção. `/health` segue aberto. `/mcp` segue exigindo Bearer quando `LEIS_MCP_API_KEY` está definida.

## Escopo congelado das seis normas

Versão do pacote: `p-985cdac4a67032e34cf0`.

| Órgão | Norma | Natureza neste pacote |
|---|---|---|
| Congresso Nacional | Resolução nº 1/1970, Regimento Comum | Consolidado pela fonte até janeiro de 2023 |
| Congresso Nacional | Resolução nº 1/2002 | Norma atualizada pela fonte; vigência não verificada |
| Congresso Nacional | Resolução nº 1/2006 | Consolidado pela fonte; vigência não verificada |
| Câmara dos Deputados | Resolução nº 17/1989, RICD | Notas de alteração em HTML até a Resolução nº 33/2026; confronto com o PDF até a Resolução nº 34/2026 não verificado |
| Câmara dos Deputados | Resolução nº 25/2001, código de ética | Vigência não verificada |
| Senado Federal | Resolução nº 20/1993, código de ética | Publicação original, histórica, não é a compilação vigente verificada |

As seis têm `vigencia=nao_verificada`. Nenhuma foi conferida de forma independente no Diário Oficial. O aviso de vigência desconhecida permanece. O Senado não é apresentado como texto compilado atual.

Identidade canônica: órgão, esfera, tipo, número e ano, no formato `br:federal:<órgão>:<tipo>:<número>:<ano>`. Apelidos, URL oficial, data, hashes e situação jurídica entram na identidade servida. O texto tachado, as notas, os anexos e a estrutura de artigo, parágrafo, inciso e alínea são preservados, inclusive 4-A e 104-A.

## O que ainda não está neste pacote

O inventário seguinte continua pendente. Não foi convertido nesta onda:

- RISF, Resolução nº 93/1970 do Senado
- Resoluções do Senado nº 40/2001, nº 43/2001 e nº 48/2007
- Resolução nº 1/2011-CN, Parlasul
- Atos da Mesa sobre CEAP, gabinete, pessoal e viagens
- Questões de ordem e súmulas
- Vetos, no que não esteja coberto pelas seis normas já servidas

Lei complementar nº 95/1998, Lei nº 1.579/1952 e Lei nº 1.079/1950 só entram se o upstream realmente não as cobrir e a lacuna for confirmada. Não são atalho para ampliar o pacote.

## Pipeline

A coleta é comando manual, em HTTPS, só para hosts oficiais da lista. Não há cron de coleta, a coleta não sobe com o processo e não é ferramenta MCP.

O fluxo é: coleta allowlisted, normalização, proveniência, staging, validação e ativação atômica. O rollback valida o destino antes de trocar o ponteiro. Não há embeddings nem serviço novo, salvo justificativa explícita numa onda futura.

O texto coletado continua sendo dado. Não é executado.

## Fases

1. Diagnóstico da arquitetura atual, sem misturar o overlay ao sync do `ctresb/leis`.
2. Inventário do que a fonte oficial oferece e do que fica de fora.
3. Piloto da Resolução nº 1/2002-CN, com o art. 7º e os sete parágrafos legíveis.
4. Núcleo das outras cinco normas, com avisos de corte e de texto histórico.
5. Expansão só do que o inventário ainda lista, cada item com fonte e revisão próprias.
6. Exportação opcional em JSONL, se for útil ao fluxo T279. O Assessor de Bolso não é alterado.

## Aceite

- As quatro ferramentas respeitam filtro de órgão, esfera, tipo, número e ano.
- Art. 7º da Resolução nº 1/2002-CN: caput e os sete parágrafos.
- Art. 104-A do Regimento Comum e art. 4º-A onde a norma o tiver, com o texto no artigo certo.
- Avisos de vigência desconhecida, corte de janeiro de 2023, notas do RICD e natureza histórica do Senado.
- `update` do upstream não apaga o overlay.
- Restart relê o disco. Rollback volta a versão anterior do overlay sem mexer no índice upstream.
- Lacunas do inventário continuam registradas como pendentes.

## Portões

QA independente e revisão de segurança independente acontecem antes de push do código candidato e antes de deploy. O autor não aprova o próprio gate. Merge em `main` só com autorização explícita do dono. Push de uma branch e deploy da imagem podem ser etapas distintas; nenhum dos dois está autorizado por este arquivo de status.

A rotina semanal de domingo, 3h, permanece só no acervo upstream.

## Status em 8 de novembro de 2026, 17:08, São Paulo: retomada pelo dono

O dono pediu para refazer. O candidato `a847025` não está no GitHub (o servidor responde `not our ref`) e não foi recuperado. A integração foi refeita do zero na branch `feat/parlamentar-020`:

1. Merge do PR 3 (`83cfe3c`) com a 0.2.0 oficial do upstream (`3f275e2`). Conflitos em `cli.py`, `config.py` e `server/app.py` resolvidos mantendo Bearer, `/health`, warm-up, overlay e o `ServiceProvider` do upstream.
2. `catalog/snapshot.py` reescrito para fechar os achados que bloquearam o candidato:
   - QA020-R01 e P1-020-2: prazo total por etapa aplicado em toda chamada de socket, inclusive nos cabeçalhos; tetos de tamanho e de disco; timeout ou recusa de orçamento vira `SnapshotResourceError`, sem fallback para o git.
   - P1-020-1 e QA020-R02: validação estrutural antes da troca.
   - P2-020-4: URL assinada fora dos logs.
   - P2-020-6: 404 do arquivo relê o manifesto uma vez.

   Os detalhes estão em [operacao.md](operacao.md#índice-pronto-integração-020).
3. `test_bounded_official_resolution_page` recebeu a marca `integration` (R3). O teste não foi alterado.

Evidência do autor, nesta máquina (não é revisão independente):

- `ruff check` e `ruff format --check` limpos.
- `pytest -m "not integration"`: 216 passed. Inclui os testes novos de `tests/test_snapshot.py`, entre eles a pilha real httpx → httpcore com backend falso que respeita o timeout recebido. Cabeçalhos lentos (20 fragmentos de 50 s) e corpo lento são recusados dentro de 120 s. Com o limitador desligado, os mesmos testes falham em 1.000 s e 1.050 s, a reprodução da QA.
- `pytest -m integration`: 21 passed, 1 failed. A falha é `test_bounded_official_resolution_page`: nesta máquina o DNS de `www2.camara.leg.br` não resolve fora do proxy. Não foi convertida em passe.
- Download real do índice publicado (`c9f21b4`, 1.218.990 normas) em cerca de 11 s, sem git; `update` seguinte devolve `updated: false`.
- Servidor stdio: as quatro ferramentas, art. 7º da Res. 1/2002-CN com os sete parágrafos, 104-A do Regimento Comum, filtro de órgão e CDC do upstream.
- Subida a frio: handshake em 0,8 s, "Preparando o índice", pronto em 13 s com o overlay ativado.
- HTTP local: `/health` 200; `/mcp` 401 sem chave e com chave errada, 200 com a chave. A chave não aparece no log.

Não feito: push, PR, deploy, sondagem da produção e as revisões independentes. Os portões abaixo continuam valendo: QA independente e revisão de segurança independente sobre esta branch antes de push para produção e antes de deploy.

## Status anterior, em 8 de novembro de 2026, 16:41, São Paulo

**Parado pelo dono, depois do pedido de documentação.** A correção seguinte ao candidato abaixo não tem artefato completo. Não retomar sem pedido novo do dono.

Produção conferida pela última vez em **6 de novembro de 2026, 19:42, São Paulo**: release v2, imagem `registry.fly.io/leis-mcp-andremog:rev-486b4e0`, código `486b4e097e0c358915d3b7c4b09c5a70c01a8c70`. Produção não foi sondada de novo em 8 de novembro. Não há afirmação de que todos os testes passaram.

O pull request existente, [PR 3](https://github.com/andremog/leis-mcp/pull/3), está aberto e não foi merged. O remoto `cursor/parliamentary-overlay-bfbf` está em `83cfe3cb06133b449de23e19c0e9aa459bac5236`. Esse commit não contém o código 0.2.0. Não houve push nem deploy do 0.2.0. Auth e volume do Fly não mudaram. `main` não contém este plano.

Último candidato entregue para revisão, ainda local e não publicado:

- commit `a847025ed3af85fe50e890913a644270378db0c3`
- árvore `58f293284bd15a9e9d150e14ada840ea08b3685d`
- patch completo contra `6e2f930bc4bf1902598cfdb05db2878be0ced06e`: 996.204 bytes, SHA-256 `e0867f61ae4b482188e56c2006e37d31275f2f2b9b65f68b7705914d3a0ddbc8`
- upstream oficial 0.2.0: commit `3f275e267fe7de353a2f06bddf5a2230d21f19a6`

Evidência do autor, não reexecução dos revisores: 198 testes fora de `integration` passaram; na integração, 21 passaram e 1 falhou. A falha é `test_bounded_official_resolution_page`, timeout de TLS (`ConnectTimeout` no handshake) ao falar com `www2.camara.leg.br`. Houve nova tentativa, também com timeout. Isso não foi convertido em passe. O wheel `leis_mcp-0.2.0` foi gerado. O índice real de 1.218.990 normas passou na checagem de esquema do autor em 0,63 s.

QA independente v3 do candidato `a847025`: **BLOCK**. O P1 QA020-R01 continua aberto. Leituras lentas de cabeçalho ainda recebem 60 s fixos por leitura, e um orçamento de 120 s só recusa aos 1.000 s simulados. Timeout de corpo, depois dos cabeçalhos, no manifesto e no arquivo, vira `SnapshotError` genérico e dispara o fallback de git. Esquema, contagem, reparo de mesmo commit, corpo de redirect e índice único estrutural foram corrigidos na revisão desse candidato. O relatório está em [revisoes/qa-v3-candidato-a847025.md](revisoes/qa-v3-candidato-a847025.md).

A rechecagem de segurança do candidato `a847025` foi interrompida por filtro de segurança. Está incompleta. Não foi reexecutada e a QA não a substitui. A revisão de segurança que existe é a do candidato anterior `449dbdd`, também **BLOCK**, em [revisoes/seguranca-020-candidato-449dbdd.md](revisoes/seguranca-020-candidato-449dbdd.md). Ela não é parecer sobre `a847025`.

## Se o dono pedir para retomar

Só então, e nesta ordem:

1. Inspecionar o estado preservado, sem apagar o candidato parado.
2. Corrigir o orçamento de transporte em todas as fases, inclusive cabeçalhos, e fazer timeout de abertura, cabeçalho e corpo virar erro de recurso, sem fallback git.
3. Provar com transporte httpcore real, não só com recusa no fim do fluxo.
4. Rodar a suíte completa, o build, e registrar de novo a falha de rede da Câmara se ela se repetir, sem afrouxar TLS e sem alterar o teste para ele passar.
5. Exportar o patch completo com hash, commit e árvore.
6. QA independente e, em separado, revisão de segurança. Não contornar o filtro que interrompeu a revisão.
7. Só depois dos dois gates: push normal, CI, archive conferido e deploy no Fly com espaço livre no volume, rollback da imagem v2 guardado, sem mudar auth nem volume.
8. Smoke ao vivo: `/health` 200, `/mcp` sem Bearer 401, as quatro ferramentas, upstream, art. 7º com os sete parágrafos, 104-A, avisos, restart, persistência depois de update, e rollback se o deploy falhar.

O rollback de imagem, se um 0.2.0 futuro precisar voltar, continua sendo `rev-486b4e0`. A implantação v1 histórica é `deployment-01M6FDPBYGQHASSJZHERXNQ1KG`.

A preservação do overlay numa reindexação ao vivo do upstream, e o restante do inventário, continuam pendentes.
