"""Markdown document for one parliamentary norm. The body is data, not instructions."""

from typing import Any

import yaml

from ..content.store import ContentStore


def repository_path(item: dict[str, Any]) -> str:
    return (
        f"parlamentar/{item['sphere']}/{item['organ']}/{item['kind']}/"
        f"{item['year']}/resolucao-{item['number']}-{item['year']}.md"
    )


def render_document(item: dict[str, Any], body: str, *, collected_at: str, content_hash: str) -> str:
    """Full file. `content_hash` is the hash of `body` only, so a new timestamp does not change it."""
    metadata = {
        "titulo": item["title"],
        "tipo": item["type_label"],
        "numero": str(item["number"]),
        "data": item["date"],
        "ementa": item["summary"],
        "fonte": item["source_url"],
        "publicacao": item.get("publication") or "",
        "versao_texto": item["text_version"],
        "vigencia": "nao_verificada",
        "natureza_texto": item["text_nature"],
        "identificador": item["canonical_id"],
        "orgao": item["organ"],
        "coletado_em": collected_at,
        "hash_conteudo": content_hash,
    }
    dumped = yaml.safe_dump(
        {key: value for key, value in metadata.items() if value not in ("", None)},
        allow_unicode=True,
        sort_keys=False,
    )
    text = body if body.endswith("\n") else body + "\n"
    return f"---\n{dumped}---\n\n{text}"


def git_blob(markdown: str) -> str:
    return ContentStore.blob_hash(markdown.encode())
