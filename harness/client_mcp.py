"""Collegamento al server MCP tramite l'SDK ufficiale.

Traduce i tool MCP nel formato neutro dell'harness (DefinizioneTool) e restituisce
l'esito di ogni invocazione come EsitoTool, senza mai sollevare eccezioni verso
l'agente: anche un server irraggiungibile diventa un esito di errore.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any

from mcp import Client

from harness.modelli import DefinizioneTool


@dataclass
class EsitoTool:
    ok: bool
    contenuto: str  # testo restituito al modello (JSON compatto se il tool ha un'uscita strutturata)


def semplifica_schema(schema: dict) -> dict:
    """Sostituisce i riferimenti $ref con le definizioni e toglie i titoli generati da Pydantic.

    I modelli piccoli gestiscono male gli schemi con $ref: uno schema "piatto" è più facile da seguire.
    """
    definizioni = schema.get("$defs", {})

    def risolvi(nodo: Any) -> Any:
        if isinstance(nodo, dict):
            if "$ref" in nodo:
                return risolvi(definizioni[nodo["$ref"].split("/")[-1]])
            return {
                chiave: risolvi(valore)
                for chiave, valore in nodo.items()
                if chiave != "$defs" and not (chiave == "title" and isinstance(valore, str))
            }
        if isinstance(nodo, list):
            return [risolvi(elemento) for elemento in nodo]
        return nodo

    return risolvi(schema)


class ClientMCP:
    """Sessione con il server MCP. Si usa con `async with ClientMCP(server) as client:`."""

    def __init__(self, server: Any, timeout_s: float = 30):
        self._client = Client(server, read_timeout_seconds=timeout_s)
        self.tools: list[DefinizioneTool] = []

    async def __aenter__(self) -> "ClientMCP":
        await self._client.__aenter__()
        risultato = await self._client.list_tools()
        self.tools = [
            DefinizioneTool(
                nome=t.name,
                descrizione=inspect.cleandoc(t.description or ""),
                schema_parametri=semplifica_schema(t.input_schema),
                # Prudenza: un tool è di sola lettura solo se lo dichiara esplicitamente.
                scrittura=not (t.annotations is not None and t.annotations.read_only_hint is True),
            )
            for t in risultato.tools
        ]
        return self

    async def __aexit__(self, *eccezione) -> None:
        await self._client.__aexit__(*eccezione)

    def tool(self, nome: str) -> DefinizioneTool | None:
        return next((t for t in self.tools if t.nome == nome), None)

    async def chiama(self, nome: str, argomenti: dict) -> EsitoTool:
        try:
            risultato = await self._client.call_tool(nome, argomenti)
        except Exception as errore:  # trasporto interrotto, timeout, server terminato...
            return EsitoTool(ok=False, contenuto=f"Il server MCP non ha risposto: {type(errore).__name__}: {errore}")

        if risultato.structured_content is not None and not risultato.is_error:
            contenuto = json.dumps(risultato.structured_content, ensure_ascii=False, separators=(",", ":"))
        else:
            contenuto = "\n".join(getattr(blocco, "text", str(blocco)) for blocco in risultato.content)
        return EsitoTool(ok=not risultato.is_error, contenuto=contenuto)
