# Acervo parlamentar: decisão e desenho

## Problema

O índice do leis-mcp é um SQLite montado com `git ls-tree` do clone leve de `ctresb/leis`. Esse acervo não contém a Resolução nº 1, de 2002, do Congresso Nacional. Em 6 de novembro de 2026, a referência "Resolução nº 1/2002 do Congresso Nacional" só encontrava homônimos do Distrito Federal e de municípios, e o filtro federal `numero=1`, `ano=2002`, `tipo=resolucoes` voltava zero.

O `leis-mcp update` troca `index.sqlite` por um arquivo novo (`index.tmp` + `os.replace`). Qualquer linha gravada dentro desse arquivo some na sincronização semanal do upstream.

## Decisão

Manter o motor atual (SQLite + o parser de artigos que já existe). Não copiar a arquitetura de embeddings nem abrir serviço novo.

O overlay mora em `LEIS_MCP_HOME/overlay`, fora do clone e fora do `index.sqlite`:

```text
overlay/
  active.json                 # ponteiro; a troca é um os.replace
  versions/<id>/
    manifest.json
    index.sqlite              # mesmo formato de colunas do índice upstream
    norms/parlamentar/...md
    provenance/*.json
  staging/<id>/               # cópia incompleta; só vira versão no rename
```

A identidade canônica é `br:<esfera>:<órgão>:<tipo>:<número>:<ano>`, por exemplo `br:federal:congresso-nacional:resolucao:1:2002`. No índice, o órgão vai na coluna `agency`, que as ferramentas já expõem como `orgao`. O caminho começa com `parlamentar/` para não colidir com um arquivo do clone.

`NormIndex` continua lendo um SQLite. `CompositeIndex` junta o índice upstream e o do overlay. O conteúdo do overlay é lido do diretório da versão ativa; não passa por `raw.githubusercontent.com`.

## O que cada peça faz

| Peça | Papel |
|---|---|
| `overlay/allowlist.py` + `collector.py` | Busca HTTPS só em hosts oficiais. Recusa IP literal, usuário na URL, endereço não público, redirecionamento fora da lista, corpo acima de 8 MB e mais de 3 redirecionamentos. Timeout de conexão 10 s e de leitura 30 s. |
| `overlay/normalizer.py` | HTML oficial vira Markdown. Texto tachado (`s`, `del`, `strike`, `line-through`) permanece entre `~~`. Nota de revogação e de emenda fica no texto. Artigo, parágrafo, inciso e alínea continuam no parser que já existia, inclusive `4-A` e `104-A`. |
| `overlay/assemble.py` | Lê `data/parliamentary/inventory.json`, normaliza e grava um pacote. Não é agendado e não entra no `update`. |
| `overlay/store.py` | Copia o pacote para `staging`, renomeia para `versions/<id>` e só então troca `active.json`. A mesma versão não é copiada de novo. `rollback` aponta para a versão anterior. |
| `data/parliamentary/bundle` | Pacote desta onda, já normalizado, para a imagem subir sem rede. |

O identificador de versão é um hash do texto normalizado, não da data da coleta. Rodar a coleta de novo sobre o mesmo HTML não cria outra versão.

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

A checagem de IP acontece antes do pedido e cada redirecionamento é validado de novo. Entre a resolução DNS e a conexão a biblioteca HTTP resolve o nome outra vez. Um rebinding nesse intervalo não é fechado por esta versão. A lista de hosts e a recusa de IP literal cobrem o caso comum (metadata, loopback, rede privada, open redirect).

O HTML baixado é dado. Não é executado e não entra em prompt. O YAML do cabeçalho é gerado pelo normalizador com `yaml.safe_dump` e relido com o SafeLoader que o projeto já usa.
