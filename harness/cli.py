"""Interfaccia minimale a riga di comando.

Uso:
    python -m harness                                  # conversazione interattiva
    python -m harness --modello ollama:llama3.2:3b     # altro modello, stesso codice
    python -m harness --richiesta "Cosa devo riordinare?"   # una sola richiesta e poi esce

Comandi durante la conversazione: /nuova (azzera la conversazione), /esci.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import StdioServerParameters

from harness.agente import Agente
from harness.client_mcp import ClientMCP
from harness.log_strutturato import LogStrutturato
from harness.modelli import DefinizioneTool, crea_modello

RADICE = Path(__file__).resolve().parent.parent


def stampa_evento(evento: dict) -> None:
    """Mostra a schermo, in forma compatta, i passi principali registrati nel log."""
    tipo = evento["tipo"]
    if tipo == "richiesta_modello":
        print(f"  … il modello sta elaborando (passo {evento['iterazione']})", flush=True)
    elif tipo == "risposta_modello":
        print(f"    risposta in {evento['durata_s']} s, {evento['token_uscita']} token generati", flush=True)
    elif tipo == "invocazione_tool":
        print(f"  · tool {evento['nome']} {json.dumps(evento['parametri'], ensure_ascii=False)}")
    elif tipo == "risultato_tool" and not evento["ok"]:
        print(f"  · errore del tool: {evento['risultato'][:200]}")
    elif tipo == "tool_non_disponibile":
        print(f"  · il modello ha chiesto un tool inesistente: {evento['nome']}")
    elif tipo == "parametri_non_validi":
        print(f"  · parametri non validi per {evento['nome']}: {'; '.join(evento['errori'])[:200]}")
    elif tipo == "errore_modello":
        print(f"  · errore del modello: {evento['errore']}")


async def chiedi_conferma(tool: DefinizioneTool, argomenti: dict) -> bool:
    print(f"\n  ⚠ Operazione di SCRITTURA: {tool.nome}")
    for chiave, valore in argomenti.items():
        print(f"      {chiave}: {json.dumps(valore, ensure_ascii=False)}")
    risposta = await asyncio.to_thread(input, "  Confermi? [s/N] ")
    return risposta.strip().lower() in {"s", "si", "sì", "y", "yes"}


async def esegui(args: argparse.Namespace) -> None:
    modello = crea_modello(args.modello, pensiero=args.pensiero)
    env = {**os.environ, "MAGAZZINO_LOG_LEVEL": "WARNING"}
    if args.db:
        env["MAGAZZINO_DB"] = str(Path(args.db).resolve())
    server = StdioServerParameters(command=sys.executable, args=["-m", "server_mcp"], cwd=str(RADICE), env=env)
    log = LogStrutturato(args.cartella_log, ascoltatori=[stampa_evento])

    async with ClientMCP(server) as client:
        agente = Agente(modello, client, log, chiedi_conferma, max_iterazioni=args.max_iterazioni)
        log.scrivi("avvio", modello=modello.nome, tools=[t.nome for t in client.tools],
                   tools_scrittura=[t.nome for t in client.tools if t.scrittura], max_iterazioni=args.max_iterazioni)
        print(f"Modello: {modello.nome} · tool: {len(client.tools)} · log: {log.percorso}")

        async def turno(testo: str) -> None:
            esito = await agente.esegui_turno(testo)
            print(f"\nAgente> {esito.risposta}")
            print(f"  [arresto: {esito.motivo_arresto} · passi: {esito.iterazioni} · tool: {esito.chiamate_tool}"
                  f" · token: {esito.token_ingresso} in / {esito.token_uscita} out]\n")

        if args.richiesta:
            await turno(args.richiesta)
        else:
            print("Scrivi una richiesta (/nuova per ricominciare, /esci per uscire).\n")
            while True:
                try:
                    testo = (await asyncio.to_thread(input, "Tu> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if testo in {"/esci", "/exit", "/quit"}:
                    break
                if testo == "/nuova":
                    agente.nuova_conversazione()
                    log.scrivi("nuova_conversazione")
                    print("  (conversazione azzerata)\n")
                    continue
                if testo:
                    await turno(testo)
    log.chiudi()


def main() -> None:
    parser = argparse.ArgumentParser(description="Harness agentico per il magazzino")
    parser.add_argument("--modello", default="ollama:qwen3:4b-instruct-2507-q4_K_M", help="fornitore:modello (default: ollama:qwen3:4b-instruct-2507-q4_K_M)")
    parser.add_argument("--richiesta", help="esegue una sola richiesta e termina")
    parser.add_argument("--max-iterazioni", type=int, default=10, help="passi massimi per richiesta (default: 10)")
    parser.add_argument("--pensiero", action="store_true", help="attiva il ragionamento esteso dei modelli che lo supportano")
    parser.add_argument("--db", help="base dati da usare (default: data/magazzino.db)")
    parser.add_argument("--cartella-log", default=str(RADICE / "logs"), help="cartella dei log (default: logs/)")
    try:
        asyncio.run(esegui(parser.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
