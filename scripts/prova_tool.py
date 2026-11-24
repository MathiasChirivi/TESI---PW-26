"""Prova ogni tool del server MCP da solo, prima di collegarlo all'agente.

Avvia il server come processo separato su stdio (come farà l'harness), elenca i
tool con i loro schemi e li invoca uno per uno, compresi alcuni casi di errore.
Lavora su una COPIA temporanea della base dati, quindi i dati non cambiano.

Uso:
    python scripts/prova_tool.py            # tutte le prove
    python scripts/prova_tool.py --schemi   # stampa anche gli schemi JSON completi

L'output è utile per le schede dei tool nel rapporto (esempio di invocazione).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

RADICE = Path(__file__).resolve().parent.parent
DB = RADICE / "data" / "magazzino.db"

PROVE = [
    ("giacenza_articolo", {"ricerca": "FE-102"}),
    ("giacenza_articolo", {"ricerca": "viti autofilettanti 4x40"}),
    ("articoli_sotto_scorta", {}),
    ("movimenti_articolo", {"codice": "TA-103"}),
    ("registra_movimento", {"codice": "FE-102", "tipo": "carico", "quantita": 50, "riferimento": "DDT 123"}),
    ("registra_movimento", {"codice": "FE-102", "tipo": "scarico", "quantita": 20, "riferimento": "Ordine cliente n. 9001"}),
    ("crea_ordine_fornitore", {"id_fornitore": 7, "righe": [{"codice": "NS-101", "quantita": 100}]}),
    # Casi di errore: il server risponde con isError=True e un messaggio leggibile
    ("registra_movimento", {"codice": "FE-102", "tipo": "scarico", "quantita": 500}),
    ("registra_movimento", {"codice": "FE-102", "tipo": "reso", "quantita": -1}),
    ("movimenti_articolo", {"codice": "XX-999"}),
    ("tool_inesistente", {}),
]


def stampa(titolo: str, contenuto) -> None:
    print(f"\n=== {titolo}")
    print(json.dumps(contenuto, ensure_ascii=False, indent=2) if not isinstance(contenuto, str) else contenuto)


async def main(mostra_schemi: bool) -> None:
    if not DB.exists():
        sys.exit("Base dati non trovata: esegui prima 'python scripts/popola_db.py'.")

    with tempfile.TemporaryDirectory() as cartella:
        copia = Path(cartella) / "prova.db"
        shutil.copy(DB, copia)
        parametri = StdioServerParameters(
            command=sys.executable,
            args=["-m", "server_mcp"],
            cwd=str(RADICE),
            env={**os.environ, "MAGAZZINO_DB": str(copia)},
        )
        async with Client(parametri) as client:
            elenco = await client.list_tools()
            print(f"Tool esposti dal server: {len(elenco.tools)}")
            for tool in elenco.tools:
                scrittura = not (tool.annotations and tool.annotations.read_only_hint)
                print(f"  - {tool.name:<24} {'SCRITTURA' if scrittura else 'lettura'}")
                if mostra_schemi:
                    stampa(f"schema di ingresso di {tool.name}", tool.input_schema)
                    stampa(f"schema di uscita di {tool.name}", tool.output_schema)

            for nome, argomenti in PROVE:
                risultato = await client.call_tool(nome, argomenti)
                esito = "ERRORE" if risultato.is_error else "ok"
                stampa(f"{nome}({json.dumps(argomenti, ensure_ascii=False)}) -> {esito}", (
                    risultato.structured_content
                    if risultato.structured_content is not None
                    else "\n".join(getattr(c, "text", str(c)) for c in risultato.content)
                ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schemi", action="store_true", help="stampa gli schemi JSON di ingresso e di uscita")
    asyncio.run(main(parser.parse_args().schemi))
