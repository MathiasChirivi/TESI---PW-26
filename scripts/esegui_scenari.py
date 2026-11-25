"""Esegue i 10 scenari di test (scenari/scenari.json) e raccoglie i risultati per il rapporto.

Ogni scenario parte da una base dati appena generata e da una conversazione nuova,
così i risultati si possono ripetere. Le conferme richieste dai tool di scrittura
ricevono le risposte previste dallo scenario ("s" o "n"), oppure quelle digitate a
mano con --interattivo.

Uso:
    python scripts/esegui_scenari.py                         # tutti gli scenari, modello predefinito
    python scripts/esegui_scenari.py --modello ollama:llama3.2:3b
    python scripts/esegui_scenari.py --solo 7 10             # solo alcuni scenari
    python scripts/esegui_scenari.py --solo 4 --interattivo  # conferme da tastiera (per gli screenshot)

Risultati in risultati/<modello>_<data_ora>/:
    riepilogo.md      tabella per il rapporto (richiesta, atteso, ottenuto, token, tempi)
    riepilogo.json    gli stessi dati in formato JSON
    scenario_NN.jsonl log strutturato completo di ogni scenario
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "scripts"))

from mcp import StdioServerParameters  # noqa: E402

import popola_db  # noqa: E402
from harness.agente import Agente  # noqa: E402
from harness.cli import stampa_evento  # noqa: E402
from harness.client_mcp import ClientMCP  # noqa: E402
from harness.log_strutturato import LogStrutturato  # noqa: E402
from harness.modelli import DefinizioneTool, crea_modello  # noqa: E402

SCENARI = RADICE / "scenari" / "scenari.json"
MODELLO_DEFAULT = "ollama:qwen3:4b-instruct-2507-q4_K_M"


def leggi_eventi(percorso: Path) -> list[dict]:
    return [json.loads(riga) for riga in percorso.read_text(encoding="utf-8").splitlines() if riga.strip()]


def verifica(scenario: dict, eventi: list[dict], risposta: str) -> list[tuple[str, bool]]:
    """Controlli automatici sull'esito: aiutano a giudicare, il giudizio finale resta umano."""
    v = scenario.get("verifiche", {})
    invocati = [e for e in eventi if e["tipo"] == "invocazione_tool"]
    risultati = {e["n"]: e for e in eventi if e["tipo"] == "risultato_tool"}
    nomi_invocati = [e["nome"] for e in invocati]
    # Chiamate chieste dal modello, anche quelle poi bloccate (tool inesistente, parametri, conferma negata)
    richiesti = [c["nome"] for e in eventi if e["tipo"] == "risposta_modello" for c in e["chiamate"]]
    scrittura = {"registra_movimento", "crea_ordine_fornitore"}
    scritture_ok = [
        e for e in eventi if e["tipo"] == "risultato_tool" and e["nome"] in scrittura and e["ok"]
    ]
    controlli: list[tuple[str, bool]] = []

    for nome in v.get("tool_usati", []):
        controlli.append((f"usa {nome}", nome in nomi_invocati))
    if v.get("nessuna_scrittura"):
        controlli.append(("nessun tool di scrittura invocato", not any(n in scrittura for n in nomi_invocati)))
    if v.get("nessuna_scrittura_riuscita"):
        controlli.append(("nessuna scrittura andata a buon fine", not scritture_ok))
    if v.get("nessun_tool"):
        controlli.append(("nessun tool richiesto dal modello", not richiesti))
    if v.get("nessuna_richiesta_conferma"):
        controlli.append(("nessuna conferma richiesta", not any(e["tipo"] == "richiesta_conferma" for e in eventi)))
    if v.get("conferma_negata"):
        controlli.append(("conferma chiesta e negata", any(e["tipo"] == "conferma" and not e["approvata"] for e in eventi)))
    for nome in v.get("errore_tool", []):
        controlli.append((f"{nome} invocato e fallito", any(
            e["tipo"] == "risultato_tool" and e["nome"] == nome and not e["ok"] for e in eventi)))
    for atteso in v.get("scritture_eseguite", []) + v.get("chiamate_riuscite", []):
        trovata = any(
            e["nome"] == atteso["nome"]
            and all(e["parametri"].get(k) == val for k, val in atteso["parametri"].items())
            and any(r["nome"] == e["nome"] and r["ok"] and r["n"] > e["n"] for r in risultati.values())
            for e in invocati
        )
        dettagli = ", ".join(f"{k}={val}" for k, val in atteso["parametri"].items())
        controlli.append((f"{atteso['nome']}({dettagli}) eseguito", trovata))
    for testo in v.get("risposta_contiene", []):
        controlli.append((f"la risposta contiene «{testo}»", testo.lower() in risposta.lower()))
    if v.get("risposta_contiene_una"):
        parole = v["risposta_contiene_una"]
        controlli.append((f"la risposta cita {' / '.join(parole)}", any(p in risposta.lower() for p in parole)))
    return controlli


async def esegui_scenario(scenario: dict, args, db_base: Path, cartella: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "magazzino.db"
        shutil.copy(db_base, db)
        env = {**os.environ, "MAGAZZINO_DB": str(db), "MAGAZZINO_LOG_LEVEL": "WARNING"}
        server = StdioServerParameters(command=sys.executable, args=["-m", "server_mcp"], cwd=str(RADICE), env=env)

        risposte_conferma = list(scenario.get("conferme", []))

        async def conferma(tool: DefinizioneTool, argomenti: dict) -> bool:
            print(f"\n  ⚠ Operazione di SCRITTURA: {tool.nome}")
            for chiave, valore in argomenti.items():
                print(f"      {chiave}: {json.dumps(valore, ensure_ascii=False)}")
            if args.interattivo:
                risposta = await asyncio.to_thread(input, "  Confermi? [s/N] ")
            else:
                risposta = risposte_conferma.pop(0) if risposte_conferma else "n"
                print(f"  Confermi? [s/N] {risposta}   (risposta prevista dallo scenario)")
            return risposta.strip().lower() in {"s", "si", "sì", "y"}

        log = LogStrutturato(cartella / "_tmp", ascoltatori=[stampa_evento])
        modello = crea_modello(args.modello)
        inizio = time.monotonic()
        async with ClientMCP(server) as client:
            agente = Agente(modello, client, log, conferma, max_iterazioni=args.max_iterazioni)
            log.scrivi("scenario", id=scenario["id"], categoria=scenario["tipo"], atteso=scenario["atteso"])
            print(f"\nTu> {scenario['richiesta']}")
            esito = await agente.esegui_turno(scenario["richiesta"])
            print(f"\nAgente> {esito.risposta}")
            print(f"  [arresto: {esito.motivo_arresto} · passi: {esito.iterazioni} · tool: {esito.chiamate_tool}"
                  f" · token: {esito.token_ingresso} in / {esito.token_uscita} out]")
        durata = round(time.monotonic() - inizio, 1)
        log.chiudi()

    percorso_log = cartella / f"scenario_{scenario['id']:02d}.jsonl"
    log.percorso.replace(percorso_log)
    eventi = leggi_eventi(percorso_log)
    controlli = verifica(scenario, eventi, esito.risposta)
    superato = all(ok for _, ok in controlli)
    for descrizione, ok in controlli:
        print(f"  {'✔' if ok else '✘'} {descrizione}")
    print(f"  => {'SUPERATO' if superato else 'NON SUPERATO'} (controlli automatici)")

    return {
        "id": scenario["id"],
        "tipo": scenario["tipo"],
        "richiesta": scenario["richiesta"],
        "atteso": scenario["atteso"],
        "risposta": esito.risposta,
        "tool": [
            {"nome": e["nome"], "parametri": e["parametri"]} for e in eventi if e["tipo"] == "invocazione_tool"
        ],
        "conferme": [e["approvata"] for e in eventi if e["tipo"] == "conferma"],
        "errori": [e["tipo"] for e in eventi if e["tipo"] in {"tool_non_disponibile", "parametri_non_validi", "errore_modello"}]
        + [f"errore_tool:{e['nome']}" for e in eventi if e["tipo"] == "risultato_tool" and not e["ok"]],
        "motivo_arresto": esito.motivo_arresto,
        "passi": esito.iterazioni,
        "token_ingresso": esito.token_ingresso,
        "token_uscita": esito.token_uscita,
        "durata_s": durata,
        "controlli": [{"descrizione": d, "ok": ok} for d, ok in controlli],
        "superato": superato,
        "log": percorso_log.name,
    }


def scrivi_riepilogo(risultati: list[dict], cartella: Path, modello: str) -> None:
    (cartella / "riepilogo.json").write_text(json.dumps(
        {"modello": modello, "data": datetime.now().isoformat(timespec="seconds"), "scenari": risultati},
        ensure_ascii=False, indent=2), encoding="utf-8")

    def cella(testo: str) -> str:
        return re.sub(r"\s+", " ", str(testo)).replace("|", "\\|").strip()

    righe = [
        f"# Risultati dei test – {modello}",
        "",
        f"Eseguiti il {datetime.now():%d/%m/%Y %H:%M}. Ogni scenario parte da una base dati appena generata.",
        "",
        "| # | Tipo | Richiesta | Esito atteso | Esito ottenuto | Tool invocati | Arresto | Token (in/out) | Tempo | Controlli |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in risultati:
        tool = ", ".join(t["nome"] for t in r["tool"]) or "nessuno"
        if r["conferme"]:
            tool += " (conferme: " + ", ".join("sì" if c else "no" for c in r["conferme"]) + ")"
        righe.append(
            f"| {r['id']} | {cella(r['tipo'])} | {cella(r['richiesta'])} | {cella(r['atteso'])} | {cella(r['risposta'])} "
            f"| {cella(tool)} | {r['motivo_arresto']} | {r['token_ingresso']} / {r['token_uscita']} | {r['durata_s']} s "
            f"| {'superato' if r['superato'] else 'NON superato'} |"
        )
    tot_in = sum(r["token_ingresso"] for r in risultati)
    tot_out = sum(r["token_uscita"] for r in risultati)
    superati = sum(r["superato"] for r in risultati)
    righe += [
        "",
        f"**Totale:** {superati}/{len(risultati)} scenari superati ai controlli automatici · "
        f"token {tot_in} in / {tot_out} out · media per scenario {tot_in // max(len(risultati), 1)} in / "
        f"{tot_out // max(len(risultati), 1)} out.",
        "",
        "I controlli automatici verificano tool invocati, conferme e scritture eseguite; "
        "il giudizio sull'esito ottenuto va confermato leggendo la risposta.",
    ]
    (cartella / "riepilogo.md").write_text("\n".join(righe) + "\n", encoding="utf-8")


async def main(args) -> None:
    scenari = json.loads(SCENARI.read_text(encoding="utf-8"))
    if args.solo:
        scenari = [s for s in scenari if s["id"] in args.solo]
    nome_modello = args.modello.split(":", 1)[1].replace(":", "-").replace("/", "-")
    cartella = RADICE / "risultati" / f"{nome_modello}_{datetime.now():%Y%m%d_%H%M%S}"
    cartella.mkdir(parents=True)

    db_base = cartella / "_base.db"
    popola_db.scrivi(db_base, popola_db.genera(random.Random(42), date.today()))

    risultati = []
    for scenario in scenari:
        print("\n" + "=" * 78)
        print(f"SCENARIO {scenario['id']} · {scenario['tipo']}")
        print(f"Atteso: {scenario['atteso']}")
        risultati.append(await esegui_scenario(scenario, args, db_base, cartella))

    db_base.unlink()
    shutil.rmtree(cartella / "_tmp", ignore_errors=True)
    scrivi_riepilogo(risultati, cartella, args.modello)
    superati = sum(r["superato"] for r in risultati)
    print("\n" + "=" * 78)
    print(f"{superati}/{len(risultati)} scenari superati · risultati in {cartella.relative_to(RADICE)}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Esegue gli scenari di test dell'agente")
    parser.add_argument("--modello", default=MODELLO_DEFAULT)
    parser.add_argument("--solo", type=int, nargs="+", help="numeri degli scenari da eseguire")
    parser.add_argument("--interattivo", action="store_true", help="conferme digitate a mano")
    parser.add_argument("--max-iterazioni", type=int, default=10)
    asyncio.run(main(parser.parse_args()))
