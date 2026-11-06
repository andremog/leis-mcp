# Inventário da primeira onda

Coletado em 6 de novembro de 2026 a partir do HTML oficial. Pacote `p-b7441d4e7f37f9a3c6de`. Não é lista exaustiva do direito parlamentar. Vigência de todos os itens: **não verificada**.

O hash e a URL de cada arquivo estão em `src/leis_mcp/data/parliamentary/bundle/provenance/`. O corpo servido está em `bundle/norms/`.

## No índice

| Identidade | O que é | Natureza | Fonte oficial usada |
|---|---|---|---|
| `br:federal:congresso-nacional:resolucao:1:2002` | Resolução nº 1, de 2002-CN (medida provisória). Piloto. | consolidado pela fonte (página "norma atualizada") | https://www2.camara.leg.br/legin/fed/rescon/2002/resolucao-1-8-maio-2002-497942-normaatualizada-pl.html |
| `br:federal:congresso-nacional:resolucao:1:1970` | Regimento Comum. O HTML declara "texto consolidado até janeiro de 2023". | consolidado pela fonte, com esse corte | https://www2.camara.leg.br/legin/fed/rescon/1970-1979/resolucao-1-11-agosto-1970-497934-republicacaoatualizada-171822-pl.html |
| `br:federal:congresso-nacional:resolucao:1:2006` | CMO e tramitação do art. 166 da Constituição. | consolidado pela fonte (página "norma atualizada"; há notas de 2025) | https://www2.camara.leg.br/legin/fed/rescon/2006/resolucao-1-22-dezembro-2006-548706-normaatualizada-pl.html |
| `br:federal:camara-dos-deputados:resolucao:17:1989` | RICD. O HTML traz notas até a Resolução nº 33, de 2026. | consolidado pela fonte; não conferido com o PDF "até a Resolução nº 34, de 2026" | https://www2.camara.leg.br/legin/fed/rescad/1989/resolucaodacamaradosdeputados-17-21-setembro-1989-320110-normaatualizada-pl.html |
| `br:federal:camara-dos-deputados:resolucao:25:2001` | Código de Ética e Decoro Parlamentar da Câmara. | consolidado pela fonte | https://www2.camara.leg.br/legin/fed/rescad/2001/resolucaodacamaradosdeputados-25-10-outubro-2001-320496-normaatualizada-pl.html |
| `br:federal:senado-federal:resolucao:20:1993` | Código de Ética do Senado. | **histórico**: publicação original, não a compilação do Senado | https://www2.camara.leg.br/legin/fed/ressen/1993/resolucao-20-17-marco-1993-481141-publicacaooriginal-1-pl.html |

Publicação original da Resolução nº 1/2002-CN, não indexada como segunda norma (evita duas identidades iguais): https://www2.camara.leg.br/legin/fed/rescon/2002/resolucao-1-8-maio-2002-497942-publicacaooriginal-1-pl.html

Ficha do catálogo da Resolução nº 1/2006-CN diz "Não consta revogação expressa". Isso foi anotado e **não** virou `vigencia` verificada: https://www2.camara.leg.br/legin/fed/rescon/2006/resolucao-1-22-dezembro-2006-548706-norma-pl.html

### Conferência do art. 7º da Resolução nº 1/2002-CN

O texto normalizado, lido pelo parser já existente, começa com:

> Art. 7º Aprovada na Câmara dos Deputados, a matéria será encaminhada ao Senado Federal, que, para apreciá-la, terá até o 42º (quadragésimo segundo) dia de vigência da Medida Provisória, contado da sua publicação no Diário Oficial da União.

É a mesma frase da página "norma atualizada". O § 1º traz "autógrafos". O teste offline usa um recorte real desse HTML em `tests/fixtures/parliamentary/res1-2002-art7.html`.

### Outros dispositivos usados como prova

- Resolução nº 1/2006-CN, art. 47, § 4º-A, e art. 2º, III, alínea `a`.
- Regimento Comum, art. 104-A (prazo do veto) e art. 1º, IV, com "(Revogado pela Constituição Federal de 1988)" preservado. Também art. 10-A.
- RICD, art. 1º do regimento ("Palácio do Congresso Nacional") e parágrafo único. A resolução que aprova o regimento repete a numeração; a resposta avisa.
- Código de Ética da Câmara, art. 3º, I, no bloco maior.
- Código de Ética do Senado, art. 3º, alínea `a`, no texto histórico.

Apelidos novos: `resolucao 1/2002-cn`, `regimento comum`, `resolucao 1/2006-cn`, `ricd`, `regimento interno da camara`, `codigo de etica da camara`, `codigo de etica do senado`, `resolucao 20/1993 do senado`, entre os listados no inventário JSON. Não substituem CDC, CLT nem os apelidos que já existiam.

## Fora desta onda

| Item | Situação |
|---|---|
| RISF, Resolução nº 93/1970 do Senado | Não convertido. O consolidado visto está em PDF/RTF/ePub, não no HTML que o normalizador cobre. Páginas: https://www.congressonacional.leg.br/web/atividade/legislacao/regimento-interno , https://www2.senado.leg.br/bdsf/handle/id/816709 , PDF https://www25.senado.leg.br/documents/12427/45868/RISFCompilado.pdf/cd5769c8-46c5-4c8a-9af7-99be436b89c4 |
| Resolução do Senado nº 40/2001 | Ficha localizada e não ingerida: https://legis.senado.leg.br/norma/562458 e https://legis.senado.leg.br/norma/562458/publicacao/16433576 |
| Resoluções do Senado nº 43/2001 e nº 48/2007 | Não coletadas. URL de texto estruturado não confirmada por download nesta onda. |
| Resolução nº 1/2011-CN (Parlasul) | Citada em https://www.congressonacional.leg.br/legislacao-e-publicacoes/regimento-do-congresso-nacional . HTML da norma não coletado. |
| Atos da Mesa (CEAP, gabinete, pessoal, viagens), questões de ordem e súmulas | Não coletados. |
| Vetos como série | Não há inventário de vetos. O rito está no Regimento Comum, art. 104-A. |
| LC nº 95/1998, Lei nº 1.579/1952, Lei nº 1.079/1950 | Não entraram no overlay. São leis federais que o acervo upstream costuma ter. Esta execução não montou o índice de 1,2 milhão de normas para conferir. |
| Regimento Comum depois de janeiro de 2023 | O PDF do Senado descrito como atualizado até julho de 2025 não foi parseado. |
| RICD "até a Resolução nº 34, de 2026" | O portal descreve um PDF assim. O HTML ingerido mostra notas até a Resolução nº 33, de 2026, e não foi diffado contra esse PDF. |

Sessão conjunta, comissão mista e apreciação de veto aparecem no Regimento Comum indexado. Isso não é um repertório separado de atos da Mesa nem de vetos individuais.
