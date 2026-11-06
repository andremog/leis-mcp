"""MCP server (official SDK `MCPServer`, formerly FastMCP) exposing the legislation tools."""

import functools
import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from .. import messages
from ..config import DEFAULT_HOST, DEFAULT_PORT, Transport
from ..errors import LeisMcpError
from ..references.parser import organ_slug
from ..services.legislation import LegislationService
from .http import ApiKeyMiddleware

log = logging.getLogger(__name__)


class LeisServer:
    """Adapter between MCP and `LegislationService`.

    Tool names, parameter names and descriptions are pt-BR because they are the public contract
    seen by MCP clients; everything behind this class is English.
    """

    _READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
    _DESCRIPTIONS = {
        "buscar_norma": "Localiza uma norma e devolve metadados do cabeçalho (título, ementa, data, revogada, versão do "
        "texto), caminho e link oficial da fonte. Para resolução do Congresso, da Câmara ou do Senado, informe o órgão "
        "na referência ('do Congresso Nacional') ou em `orgao`, para não confundir com homônimo estadual ou municipal.",
        "ler_artigo": "Devolve o texto exato de um artigo (ou parágrafo, inciso, alínea), com o caput como contexto, "
        "link da fonte e avisos de revogação ou texto não compilado.",
        "buscar_texto": "Busca um termo dentro de uma norma, ou nas normas já em cache local, indicando o artigo de "
        "cada ocorrência.",
        "listar_normas": "Navega pelo acervo: sem filtros devolve contagens por esfera; com esfera, por tipo; com tipo, "
        "por ano; com ano, lista as normas.",
    }

    def __init__(self, service: LegislationService):
        self._service = service
        self.mcp = MCPServer("leis-brasil", instructions=messages.SERVER_INSTRUCTIONS)
        handlers = {
            "buscar_norma": self.find_norm,
            "ler_artigo": self.read_article,
            "buscar_texto": self.search_text,
            "listar_normas": self.browse,
        }
        for name, handler in handlers.items():
            register = self.mcp.tool(name=name, description=self._DESCRIPTIONS[name], annotations=self._READ_ONLY)
            register(self._user_errors(handler))
        self.mcp.resource(
            "leis://apelidos",
            name="apelidos",
            mime_type="application/json",
            description="Apelidos de leis aceitos pelas ferramentas.",
        )(self.aliases)

    def run(
        self,
        transport: Transport = "stdio",
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        api_key: str | None = None,
    ) -> None:
        if transport == "stdio":
            self.mcp.run("stdio")
            return
        import uvicorn

        if not api_key and host not in ("127.0.0.1", "localhost", "::1"):
            log.warning("LEIS_MCP_API_KEY is not set: the HTTP endpoint on %s is open to anyone.", host)
        uvicorn.run(self.http_app(host, api_key), host=host, port=port, log_level="info")

    def http_app(self, host: str = DEFAULT_HOST, api_key: str | None = None) -> ApiKeyMiddleware:
        """Streamable HTTP app (`/mcp`) wrapped with the optional key check and the open `/health`."""
        return ApiKeyMiddleware(self.mcp.streamable_http_app(host=host), api_key)

    def find_norm(
        self,
        referencia: Annotated[
            str | None,
            Field(
                description="Referência livre: 'Lei 8.245/91', '8245/1991', '8.245', 'LC 123/2006', 'DL 5.452', "
                "ou apelido ('CDC', 'CLT', 'código civil', 'constituição')."
            ),
        ] = None,
        numero: Annotated[str | None, Field(description="Número da norma (alternativa a `referencia`).")] = None,
        ano: Annotated[int | None, Field(description="Ano (4 dígitos).")] = None,
        tipo: Annotated[
            str | None,
            Field(
                description="Tipo: 'lei', 'lei complementar', 'decreto-lei', 'decreto', 'medida provisória', "
                "'emenda constitucional' ou nome do diretório ('leis-ordinarias')."
            ),
        ] = None,
        esfera: Annotated[
            str | None,
            Field(description="federal, estaduais, municipais, reguladores, jurisprudencia ou tratados."),
        ] = None,
        uf: Annotated[str | None, Field(description="UF, para normas estaduais (ex.: 'SP').")] = None,
        orgao: Annotated[
            str | None,
            Field(
                description="Órgão do acervo parlamentar: 'congresso-nacional', 'camara-dos-deputados' ou "
                "'senado-federal'. Também aceita o nome ('Congresso Nacional')."
            ),
        ] = None,
        limite: Annotated[int, Field(description=f"Máximo de resultados (1 a {LegislationService.MAX_FOUND}).")] = 10,
    ) -> dict[str, Any]:
        return self._service.find_norms(referencia, numero, ano, tipo, esfera, uf, limite, organ_slug(orgao))

    def read_article(
        self,
        norma: Annotated[
            str,
            Field(
                description="Norma: '8245/1991', 'Lei 8.245/91', apelido ('CDC', 'CLT', 'CF', 'ADCT') ou "
                "caminho no repositório."
            ),
        ],
        artigo: Annotated[str, Field(description="Artigo: '23', '9º', '4-A', '1.000'.")],
        paragrafo: Annotated[str | None, Field(description="Parágrafo: '2', '2º' ou 'único'.")] = None,
        inciso: Annotated[str | None, Field(description="Inciso em romano ('III') ou número (3).")] = None,
        alinea: Annotated[str | None, Field(description="Alínea: 'a', 'b'...")] = None,
        bloco: Annotated[
            str | None,
            Field(
                description="Quando a numeração reinicia na norma (ex.: ADCT na CF), trecho do título do bloco "
                "('transitórias') ou índice."
            ),
        ] = None,
    ) -> dict[str, Any]:
        return self._service.read_provision(norma, artigo, paragrafo, inciso, alinea, bloco)

    def search_text(
        self,
        consulta: Annotated[str, Field(description="Palavra ou trecho (ignora maiúsculas e acentos).")],
        norma: Annotated[
            str | None,
            Field(
                description="Restringe a uma norma (baixada sob demanda). Sem isso, busca só nas normas já em "
                "cache local."
            ),
        ] = None,
        esfera: Annotated[str | None, Field(description="Filtro de esfera.")] = None,
        tipo: Annotated[str | None, Field(description="Filtro de tipo.")] = None,
        ano: Annotated[int | None, Field(description="Filtro de ano.")] = None,
        uf: Annotated[str | None, Field(description="Filtro de UF.")] = None,
        orgao: Annotated[
            str | None, Field(description="Órgão do acervo parlamentar (ex.: 'congresso-nacional').")
        ] = None,
        limite: Annotated[
            int, Field(description=f"Máximo de ocorrências (1 a {LegislationService.MAX_OCCURRENCES}).")
        ] = 20,
    ) -> dict[str, Any]:
        return self._service.search_text(consulta, norma, esfera, tipo, ano, uf, limite, organ_slug(orgao))

    def browse(
        self,
        esfera: Annotated[
            str | None,
            Field(
                description="federal, estaduais, municipais, reguladores, jurisprudencia, tratados. Vazio: "
                "contagem por esfera."
            ),
        ] = None,
        tipo: Annotated[
            str | None, Field(description="Tipo (ex.: 'leis-ordinarias'). Vazio: contagem por tipo/UF/órgão.")
        ] = None,
        ano: Annotated[int | None, Field(description="Ano. Vazio: contagem por ano.")] = None,
        uf: Annotated[str | None, Field(description="UF (estaduais/municipais).")] = None,
        municipio: Annotated[str | None, Field(description="Município (slug, ex.: 'rio-branco').")] = None,
        orgao: Annotated[str | None, Field(description="Órgão regulador ou tribunal (ex.: 'anvisa', 'stf').")] = None,
        limite: Annotated[int, Field(description=f"Itens por página (1 a {LegislationService.MAX_PAGE_SIZE}).")] = 50,
        offset: Annotated[int, Field(description="Deslocamento para paginação.")] = 0,
    ) -> dict[str, Any]:
        return self._service.browse(esfera, tipo, ano, uf, municipio, orgao, limite, offset)

    def aliases(self) -> dict[str, str]:
        return self._service.aliases()

    @staticmethod
    def _user_errors(handler: Callable[..., Any]) -> Callable[..., Any]:
        """Reports domain errors as MCP tool errors carrying the pt-BR message."""

        @functools.wraps(handler)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return handler(*args, **kwargs)
            except LeisMcpError as exc:
                raise ToolError(str(exc)) from exc

        return wrapper
