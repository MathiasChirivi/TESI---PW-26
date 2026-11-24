# PW 26 – Agente per la gestione del magazzino

Project Work: AI agent, server MCP e harness agentico per il magazzino di
**Minuterie Valdora S.r.l.**, un ingrosso di ferramenta e minuteria **fittizio**.
Tutti i dati sono sintetici e generati da script: non ci sono dati personali reali
né dati aziendali riservati.

## Stato

| Componente | Stato |
| --- | --- |
| Base dati SQLite e script di popolamento | Fatto (Fase 1) |
| Back-end di lettura e scrittura | Fatto (Fase 1) |
| Server MCP con 5 tool | Fatto (Fase 2) |
| Harness agentico | Da fare (Fase 3) |

## Requisiti

- Python 3.10 o successivo
- SDK ufficiale MCP per Python (`mcp`, versione in `requirements.txt`)

## Installazione ed esecuzione

```bash
# 1. Ambiente virtuale (consigliato)
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate

# 2. Installare le dipendenze
pip install -r requirements.txt

# 3. Creare e popolare la base dati (file data/magazzino.db)
python scripts/popola_db.py

# 4. Eseguire i test (back-end e server MCP)
python -m unittest -v

# 5. Provare i tool del server MCP uno per uno (su una copia della base dati)
python scripts/prova_tool.py            # aggiungere --schemi per gli schemi JSON
```

Il server MCP comunica su stdio e di norma viene avviato dall'harness. Per avviarlo a mano:
`python -m server_mcp` (la base dati si può indicare con la variabile `MAGAZZINO_DB`).

Lo script ricrea la base dati da zero a ogni esecuzione. Il seme casuale è fisso (`--seme 42`),
quindi i dati sono sempre gli stessi a parità di data di riferimento (`--oggi AAAA-MM-GG`).

## Struttura

```
db/schema.sql           schema delle 5 tabelle (fornitore, articolo, movimento, ordine_fornitore, riga_ordine)
scripts/popola_db.py    crea la base dati e la popola con dati sintetici
magazzino/backend.py    back-end: le 5 operazioni di lettura e scrittura
server_mcp/server.py    server MCP: espone le 5 operazioni come tool con schemi di ingresso e di uscita
scripts/prova_tool.py   prova manuale dei tool tramite un client MCP su stdio
tests/                  test del back-end e del server MCP
```

## Tool del server MCP

| Tool | Effetto sui dati |
| --- | --- |
| `giacenza_articolo` | Lettura |
| `articoli_sotto_scorta` | Lettura |
| `movimenti_articolo` | Lettura |
| `registra_movimento` | Scrittura |
| `crea_ordine_fornitore` | Scrittura |

I tool di lettura sono marcati con l'annotazione MCP `readOnlyHint`. Le operazioni di
scrittura lavorano in una transazione: se un controllo fallisce (quantità non valida,
giacenza insufficiente, articolo di un altro fornitore) non viene modificato nulla e il
server risponde con un risultato `isError` e un messaggio in italiano.
