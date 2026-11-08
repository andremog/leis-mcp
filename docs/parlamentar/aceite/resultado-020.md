# Aceite do plano: commit `9c14761`, 8 de novembro de 2026

Roteiro: [aceite.py](aceite.py). Processos novos (CLI e servidor MCP via stdio), índice real de 1.218.990 normas baixado pelo próprio código, publicador HTTPS local para o critério do `update`. Cada linha corresponde a um item de Objetivo ou Aceite em [plano-e-status.md](../plano-e-status.md).

O art. 4º-A do plano é o § 4º-A do art. 47 da Res. 1/2006-CN, que é onde o pacote o tem (inventário). Fora deste roteiro: o teste ao vivo da coleta na Câmara (DNS direto bloqueado no sandbox) e o deploy no Fly.

```text

## Objetivo
PASSOU  as 4 ferramentas existem
PASSOU  buscar_norma acha Resolução 1/1970 (Congresso Nacional)
PASSOU  buscar_norma com órgão não traz homônimo (Resolução 1/1970 (Congresso Nacional))
PASSOU  ler_artigo lê Resolução 1/1970 (Congresso Nacional) pelo nome do órgão
PASSOU  buscar_norma acha Resolução 1/2002 (Congresso Nacional)
PASSOU  buscar_norma com órgão não traz homônimo (Resolução 1/2002 (Congresso Nacional))
PASSOU  ler_artigo lê Resolução 1/2002 (Congresso Nacional) pelo nome do órgão
PASSOU  buscar_norma acha Resolução 1/2006 (Congresso Nacional)
PASSOU  buscar_norma com órgão não traz homônimo (Resolução 1/2006 (Congresso Nacional))
PASSOU  ler_artigo lê Resolução 1/2006 (Congresso Nacional) pelo nome do órgão
PASSOU  buscar_norma acha Resolução 17/1989 (Câmara dos Deputados)
PASSOU  buscar_norma com órgão não traz homônimo (Resolução 17/1989 (Câmara dos Deputados))
PASSOU  ler_artigo lê Resolução 17/1989 (Câmara dos Deputados) pelo nome do órgão
PASSOU  buscar_norma acha Resolução 25/2001 (Câmara dos Deputados)
PASSOU  buscar_norma com órgão não traz homônimo (Resolução 25/2001 (Câmara dos Deputados))
PASSOU  ler_artigo lê Resolução 25/2001 (Câmara dos Deputados) pelo nome do órgão
PASSOU  buscar_norma acha Resolução 20/1993 (Senado Federal)
PASSOU  buscar_norma com órgão não traz homônimo (Resolução 20/1993 (Senado Federal))
PASSOU  ler_artigo lê Resolução 20/1993 (Senado Federal) pelo nome do órgão
PASSOU  sem órgão, a lista pode trazer o homônimo do DF
PASSOU  sem órgão, a do Congresso também aparece
PASSOU  buscar_texto com órgão CN só devolve normas do CN
PASSOU  listar_normas por órgão CN: 1970, 2002, 2006

## Aceite 1 (filtros)
PASSOU  buscar_norma número+ano+tipo+esfera+órgão
PASSOU  ano sem norma do CN não devolve o DF nem outra
PASSOU  esfera estaduais/UF DF devolve só o DF
PASSOU  listar_normas órgão+ano lista a Res. 20/1993 do Senado
PASSOU  buscar_texto órgão+ano (Câmara, 2001)
PASSOU  buscar_texto órgão+tipo+esfera (Senado)

## Aceite 2 (art. 7º)
PASSOU  caput presente
PASSOU  §§ 1º a 7º presentes e em ordem
PASSOU  exatamente os §§ 1º a 7º abrem linha (nenhum § 8º)
PASSOU  § 7º isolado, com o caput como contexto

## Aceite 3 (artigos com letra)
PASSOU  art. 104-A do Regimento Comum
PASSOU  o 104-A não vaza para o art. 104
PASSOU  § 4º-A do art. 47 da Res. 1/2006-CN
PASSOU  art. 13-A do RICD

## Aceite 4 (avisos)
PASSOU  vigência desconhecida: Resolução 1/1970 (Congresso Nacional)
PASSOU  vigência desconhecida: Resolução 1/2002 (Congresso Nacional)
PASSOU  vigência desconhecida: Resolução 1/2006 (Congresso Nacional)
PASSOU  vigência desconhecida: Resolução 17/1989 (Câmara dos Deputados)
PASSOU  vigência desconhecida: Resolução 25/2001 (Câmara dos Deputados)
PASSOU  vigência desconhecida: Resolução 20/1993 (Senado Federal)
PASSOU  corte de consolidação de janeiro de 2023 (Regimento Comum)
PASSOU  notas do RICD até a Res. 33/2026
PASSOU  Senado marcado como histórico, não compilado vigente

## Upstream
PASSOU  CDC art. 6º, III do acervo upstream

## Aceite 5 (update)
PASSOU  update instala um upstream de commit novo
PASSOU  índice upstream foi trocado
PASSOU  overlay byte a byte igual depois do update
PASSOU  status: upstream novo + overlay p-985cdac4a67032e34cf0

## Aceite 6 (restart/rollback)
PASSOU  segunda versão do overlay ativada
PASSOU  processo novo lê a segunda versão do disco
PASSOU  rollback volta a p-985cdac4a67032e34cf0
PASSOU  rollback não mexe no índice upstream
PASSOU  processo novo depois do rollback lê o art. 7º, § 7º
PASSOU  a segunda versão sumiu depois do rollback

## Aceite 7 (lacunas)
PASSOU  8 lacunas no manifesto do pacote
PASSOU  plano lista as mesmas lacunas como pendentes

59/59 verificações passaram
```
