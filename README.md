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
| Server MCP | Da fare (Fase 2) |
| Harness agentico | Da fare (Fase 3) |

## Requisiti

- Python 3.10 o successivo
- Nessuna libreria esterna per ora: si usa solo la libreria standard (`sqlite3`)

## Installazione ed esecuzione

```bash
# 1. Ambiente virtuale (consigliato)
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate

# 2. Creare e popolare la base dati (file data/magazzino.db)
python scripts/popola_db.py

# 3. Eseguire i test del back-end
python -m unittest -v
```

Lo script ricrea la base dati da zero a ogni esecuzione. Il seme casuale è fisso (`--seme 42`),
quindi i dati sono sempre gli stessi a parità di data di riferimento (`--oggi AAAA-MM-GG`).

## Struttura

```
db/schema.sql           schema delle 5 tabelle (fornitore, articolo, movimento, ordine_fornitore, riga_ordine)
scripts/popola_db.py    crea la base dati e la popola con dati sintetici
magazzino/backend.py    back-end: le 5 operazioni che diventeranno i tool MCP
tests/test_backend.py   test del back-end su una base dati temporanea
```

## Operazioni del back-end

| Metodo | Effetto sui dati |
| --- | --- |
| `giacenza_articolo(ricerca)` | Lettura |
| `articoli_sotto_scorta()` | Lettura |
| `movimenti_articolo(codice, data_da, data_a)` | Lettura |
| `registra_movimento(codice, tipo, quantita, riferimento)` | Scrittura |
| `crea_ordine_fornitore(id_fornitore, righe)` | Scrittura |

Le operazioni di scrittura lavorano in una transazione: se un controllo fallisce
(quantità non valida, giacenza insufficiente, articolo di un altro fornitore) non viene
modificato nulla e viene sollevata un'eccezione con un messaggio in italiano.
