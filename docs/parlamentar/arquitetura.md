# Acervo parlamentar: decisão e desenho

## Problema

O índice do leis-mcp é um SQLite montado com `git ls-tree` do clone leve de `ctresb/leis`. Esse acervo não contém a Resolução nº 1, de 2002, do Congresso Nacional. Em 6 de novembro de 2026, a referência "Resolução nº 1/2002 do Congresso Nacional" só encontrava homônimos do Distrito Federal e de municípios, e o filtro federal `numero=1`, `ano=2002`, `tipo=resolucoes` voltava zero.

O `leis-mcp update` troca `index.sqlite` por um arquivo novo (`index.tmp` + `os.replace`). Qualquer linha gravada dentro desse arquivo some na sincronização semanal do upstream.

## Decisão

Manter o motor atual (SQLite + o parser de artigos que já existe). Não copiar a arquitetura de embeddings nem abrir serviço novo.

O overlay mora em `LEIS_MCP_HOME/overlay`, fora do clone e fora do `index.sqlite`:

```text
overlay/
  active.json                 # ponteiro; a troca é um os.replace com fsync
  versions/p-<20 hex>/
    READY                     # só existe depois da validação
    manifest.json
    index.sqlite              # gerado aqui; um sqlite importado não é confiável
    norms/parlamentar/...md
    provenance/*.json
  staging/.<id>.<pid>.<token>/   # validação; não é servida
```

A identidade canônica é `br:<esfera>:<órgão>:<tipo>:<número>:<ano>`, por exemplo `br:federal:congresso-nacional:resolucao:1:2002`. No índice, o órgão vai na coluna `agency`, que as ferramentas já expõem como `orgao`. O caminho começa com `parlamentar/` para não colidir com um arquivo do clone.

`NormIndex` continua lendo um SQLite. `CompositeIndex` junta o índice upstream e o do overlay. O conteúdo do overlay é lido do diretório da versão ativa; não passa por `raw.githubusercontent.com`.

## O que cada peça faz

| Peça | Papel |
|---|---|
| `overlay/allowlist.py` + `collector.py` | Busca HTTPS só em hosts oficiais, com `httpcore` 1.0.9 declarado. Recusa IP literal, usuário na URL, endereço não público, redirecionamento fora da lista, conteúdo comprimido e corpo acima de 8 MB. O TCP vai para o IP já checado, com SNI no nome oficial. Cada conexão, TLS, leitura e escrita recebe um tempo finito, no máximo 45 s por operação; o DNS fica no máximo em 5 s. Isso não é um prazo total acumulado de 45 s. O pool só fecha depois de ler o corpo. Proxies de ambiente não são usados. O comando é manual: não sobe com o processo, não é agendado e não é ferramenta MCP. |
| `overlay/normalizer.py` | HTML oficial vira Markdown. Texto tachado (`s`, `del`, `strike`, `line-through`) permanece entre `~~`. Nota de revogação e de emenda fica no texto. Artigo, parágrafo, inciso e alínea continuam no parser que já existia, inclusive `4-A` e `104-A`. |
| `overlay/assemble.py` | Lê `data/parliamentary/inventory.json`, normaliza e grava um pacote. Não é agendado e não entra no `update`. |
| `overlay/store.py` | Copia para um staging único, confere caminhos, hashes, metadados, proveniência e o SQLite, grava `READY` e só então publica em `versions/`. O ponteiro muda depois. `rollback` valida o destino antes de gravar o ponteiro. Um diretório existente que não confere não é ativado. |
| `data/parliamentary/bundle` | Pacote desta onda, já normalizado, para a imagem subir sem rede. |

O identificador de versão (`p-` e 20 hexadecimais) é um hash do corpo e dos metadados que a resposta mostra: natureza, `versao_texto`, fonte, apelidos, título, ementa, publicação, órgão e caminho. A data da coleta fica de fora. Recoletar só por causa do relógio não cria outra versão. Corrigir natureza, fonte ou apelido cria outra versão e passa a valer.

## Vigência e natureza do texto

Nenhuma norma desta onda teve a vigência conferida no Diário ou numa base de multivigência. O campo `vigencia` é `nao_verificada` e a resposta traz o aviso correspondente. `revogada` não é preenchido: não há afirmação de que a norma esteja revogada nem de que esteja vigente.

`natureza_texto`:

- `consolidado_pela_fonte` — a página oficial é "norma atualizada" ou "republicação atualizada". `versao_texto` diz isso e inclui a palavra "compilada", então o aviso de texto não compilado não dispara. O corte da consolidação, quando a própria página o declara, fica na frase.
- `historico` — publicação original, sem consolidação. O aviso de texto não compilado dispara. É o caso da Resolução nº 20/1993 do Senado nesta onda.

Notas de alteração ("redação dada", "acrescido", "revogado") ficam no corpo. O normalizador não apaga esse texto.

## Como a busca evita o homônimo

"Resolução nº 1/2002 do Congresso Nacional", "resolucao 1/2002-cn" e `orgao=congresso-nacional` fixam `agency=congresso-nacional`. O homônimo do DF tem esfera estadual e órgão vazio, então não entra. Sem o órgão, `buscar_norma` ainda pode listar o homônimo, mas a norma federal do overlay vem primeiro. `ler_artigo("resolução 1/2002")` cai na federal porque as esferas são diferentes e o ranking federal vem na frente.

Apelidos (`regimento comum`, `ricd`, `codigo de etica da camara`, …) apontam para o caminho do arquivo, não para um número solto.

## O que não mudou

- Autenticação Bearer de `/mcp` e `/health` aberto.
- `fly.toml`, Dockerfile, volume e segredos. Nenhum segredo novo.
- Rotina de `leis-mcp update`. Ela não lê nem apaga `overlay/`.
- Não há embeddings, banco externo nem assinatura nova.

## Limite conhecido do coletor

A checagem de IP acontece antes do pedido. A conexão TCP usa esse IP; o certificado e o SNI continuam no nome oficial. Cada redirecionamento repete a checagem e calcula de novo o orçamento daquele salto. Dentro de um salto, conexão, TLS e leituras de cabeçalho podem reutilizar o mesmo teto finito, então um fluxo lento pode passar de 45 s no relógio. Isso fecha o rebinding entre o `getaddrinfo` e um segundo lookup. Não fecha um desvio feito no caminho, depois do socket aberto, nem um proxy transparente da rede. Uma falha de rede na leitura do corpo, depois dos cabeçalhos, pode escapar como erro do transporte; a montagem recusa a norma e não ativa o pacote. O teste que baixa a página oficial da Resolução nº 1/2002-CN está na suíte sem a marca `integration`. A coleta não é ferramenta MCP. Um ancestral que seja link simbólico é recusado antes de criar diretório. O identificador canônico não entra em nome de arquivo se tiver barra ou `..`. `collect --replace` não substitui nada dentro do home, do clone, do cache nem do overlay.

O HTML baixado é dado. Não é executado. O texto devolvido ao cliente continua sendo dado de fonte: o servidor pede ao assistente que não trate frase do dispositivo como instrução, mas não impede um cliente de fazer isso. O YAML do cabeçalho é gerado com `yaml.safe_dump` e relido com o SafeLoader que o projeto já usa.

Se o ponteiro, o manifesto ou o índice do overlay estiver ilegível ou incompleto, o processo registra o erro e sobe só com o índice upstream. Pedido que nomeia o Congresso Nacional não passa a devolver o homônimo do DF. Um índice upstream quebrado continua a falhar; esse erro não é engolido.
