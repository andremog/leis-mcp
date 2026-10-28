"""User-facing text (pt-BR). Everything returned to MCP clients is defined here."""

LEGAL_NOTICE = (
    "Texto convertido automaticamente das fontes oficiais (pode conter erros de "
    "conversão/OCR). Não substitui a publicação no Diário Oficial."
)

SERVER_INSTRUCTIONS = """\
Acesso à legislação brasileira do repositório público ctresb/leis (Markdown convertido de fontes oficiais).
Antes de responder perguntas jurídicas, localize a norma (`buscar_norma`) e leia o dispositivo exato
(`ler_artigo`). Cite o texto literalmente, informe o link `fonte` e repasse os `avisos` (norma revogada,
texto não compilado, dispositivo revogado). O texto é conversão automática e não substitui o Diário Oficial.
Apelidos aceitos: CF/constituição, ADCT, CC/código civil, CPC, CP/código penal, CPP, CLT, CDC,
lei do inquilinato, lei dos juizados, CTN, ECA, LGPD e outros (recurso leis://apelidos).
"""

# Reference resolution
UNKNOWN_REFERENCE = (
    "Não entendi a referência {reference!r}. Exemplos: 'Lei 8.245/91', '8245/1991', "
    "'LC 123/2006', 'DL 5.452', 'CDC', 'constituição'."
)
NORM_REQUIRED = "Informe a norma."
NUMBER_REQUIRED = "Informe o número da norma (ex.: '8.245', 'Lei 8.245/91') ou um apelido."
PATH_NOT_INDEXED = "Caminho não encontrado no índice: {path}"
NORM_NOT_FOUND = "Nenhuma norma encontrada para {reference!r}. Use `buscar_norma` para pesquisar."
AMBIGUOUS_REFERENCE = "Referência ambígua {reference!r}: informe o ano (ex.: '{example}'). Opções: {options}"
NO_MATCHING_NORMS = "Nenhuma norma encontrada com esses filtros."
INVALID_SPHERE = "Esfera inválida: {sphere!r}. Use uma de: {options}."
INDEX_MISSING = "Índice não encontrado em {path}. Rode `leis-mcp index` primeiro."
GIT_FAILED = "Falha ao executar git {command}: {details}"
CLI_ERROR = "erro: {error}"

# Provision lookup
INVALID_ARTICLE = "Número de artigo inválido: {value!r}."
INVALID_PARAGRAPH = "Parágrafo inválido: {value!r}."
INVALID_CLAUSE = "Inciso inválido: {value!r}."
INVALID_SUBCLAUSE = "Alínea inválida: {value!r}."
NO_ARTICLES = "Não encontrei artigos em {path} (formato não reconhecido). Fonte: {source}"
ARTICLE_NOT_FOUND = "Art. {article} não encontrado."
ARTICLE_NOT_IN_BLOCK = "Art. {article} não encontrado no bloco {block!r}."
PARAGRAPH_NOT_FOUND = "{paragraph} não encontrado no Art. {article}."
CLAUSE_NOT_FOUND = "Inciso {clause} não encontrado no Art. {article}{scope}."
CLAUSE_FOUND_IN_PARAGRAPHS = " Existe em: {paragraphs} (informe `paragrafo`)."
SUBCLAUSE_NOT_FOUND = "Alínea {subclause}) não encontrada no Art. {article}."
DUPLICATE_ARTICLE = "Há outro Art. {article} nesta norma em: {others}. Mostrando: {shown}."
BLOCK_REFERENCE = "bloco={index} ({title})"
BLOCK_OPTION = "bloco {index}: {title}"
ARTICLE_SUGGESTION = "Art. {article}"
SUGGESTIONS = " Sugestões: {items}."
IN_NORM = " Norma: {title}."
MAIN_TEXT_BLOCK = "texto principal"
SOLE_PARAGRAPH = "parágrafo único"
ARTICLE_CITATION = "art. {article}"
SUBCLAUSE_CITATION = "alínea {subclause}"

# Front matter defaults for the Constitution, which has no YAML header
CONSTITUTION_KIND = "Constituição"
CONSTITUTION_TEXT_VERSION = "compilada (texto compilado do Planalto)"

# Notices
STRUCK_TEXT = "O trecho contém texto tachado (dispositivo revogado ou alterado) no original."
MENTIONS_REPEAL = "O texto do dispositivo menciona revogação: confira se o trecho está vigente."
MENTIONS_VETO = "O texto do dispositivo menciona veto."
NORM_REVOKED = "ATENÇÃO: a norma está marcada como REVOGADA (revogada: true)."
SOURCE_STATUS = "Situação informada na fonte: {status}."
NOT_CONSOLIDATED = "O texto NÃO é a versão compilada/vigente (versao_texto: {version!r}); pode estar desatualizado."
UNKNOWN_VERSION = "A fonte não informa se o texto é a versão compilada (vigente)."

# Text search and browsing
QUERY_TOO_SHORT = "A consulta precisa ter ao menos {minimum} caracteres."
NORM_SCOPE = "norma {path}"
CACHE_SCOPE = (
    "{count} normas em cache local (a busca não cobre o acervo inteiro; use `norma` para buscar "
    "numa norma específica, que é baixada sob demanda)"
)
BROWSE_HINT = "Ementas aparecem só para normas já em cache; use `buscar_norma` para ver metadados."
