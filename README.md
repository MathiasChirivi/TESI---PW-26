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
| Harness agentico con interfaccia a riga di comando | Fatto (Fase 3) |
| Script per i 10 scenari di test | Fatto (Fase 4) |

## Requisiti

- Python 3.10 o successivo
- SDK ufficiale MCP per Python (`mcp`, versione in `requirements.txt`)
- [Ollama](https://ollama.com) in esecuzione in locale, con almeno un modello che supporti i tool:
  `ollama pull qwen3:4b-instruct-2507-q4_K_M` (predefinito, senza ragionamento esteso) e, per il confronto,
  `ollama pull llama3.2:3b`

## Installazione ed esecuzione

```bash
# 1. Ambiente virtuale (consigliato)
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate

# 2. Installare le dipendenze
pip install -r requirements.txt

# 3. Creare e popolare la base dati (file data/magazzino.db)
python scripts/popola_db.py

# 4. Eseguire i test (back-end, server MCP, ciclo agentico con modello finto)
python -m unittest -v

# 5. Provare i tool del server MCP uno per uno (su una copia della base dati)
python scripts/prova_tool.py            # aggiungere --schemi per gli schemi JSON
```

## Uso dell'agente

```bash
ollama serve                                   # in un terminale a parte (o app Ollama aperta)
python -m harness                              # conversazione interattiva con qwen3:4b-instruct-2507-q4_K_M
python -m harness --modello ollama:llama3.2:3b # stesso harness, altro modello
python -m harness --richiesta "Cosa devo riordinare questa settimana?"
```

Opzioni: `--max-iterazioni N` (default 10), `--pensiero` (ragionamento esteso dei modelli che lo
supportano), `--db percorso` (base dati diversa). Durante la conversazione: `/nuova` azzera il
contesto, `/esci` termina. Prima di ogni tool di scrittura l'harness chiede conferma (`s`/`N`).
Ogni sessione produce un log strutturato in `logs/sessione_<data_ora>.jsonl`, un evento JSON per
riga: richieste al modello, risposte, tool invocati, parametri, risultati, conferme e token consumati.

## Test funzionali (10 scenari)

```bash
python scripts/esegui_scenari.py                              # modello predefinito
python scripts/esegui_scenari.py --modello ollama:llama3.2:3b # confronto
python scripts/esegui_scenari.py --solo 4 --interattivo       # un solo scenario, conferme a mano
```

Gli scenari sono in `scenari/scenari.json` (richiesta, esito atteso, risposte alle conferme,
controlli automatici). Ogni scenario parte da una base dati appena generata. I risultati
finiscono in `risultati/<modello>_<data_ora>/`: `riepilogo.md` (tabella per il rapporto),
`riepilogo.json` e il log strutturato di ogni scenario.

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
scripts/esegui_scenari.py  esecuzione dei 10 scenari di test e riepilogo dei risultati
scenari/scenari.json    i 10 scenari: richiesta, esito atteso, controlli
harness/agente.py       ciclo agentico: iterazioni, arresto, errori, conferme, log
harness/modelli.py      astrazione dal fornitore del modello (adattatore Ollama + modello finto per i test)
harness/client_mcp.py   collegamento al server MCP e conversione dei tool
harness/log_strutturato.py  log JSON Lines di ogni passo
harness/cli.py          interfaccia a riga di comando
tests/                  test del back-end, del server MCP e del ciclo agentico
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

## Harness agentico

| Requisito della traccia | Dove |
| --- | --- |
| Ciclo di esecuzione con massimo di iterazioni e condizioni di arresto esplicite | `harness/agente.py` (`esegui_turno`) |
| Tool non disponibili, falliti o con parametri non validi | `harness/agente.py` (`_esegui_chiamata`), `harness/client_mcp.py` |
| Conferma dell'utente prima dei tool di scrittura | `harness/agente.py` + `chiedi_conferma` in `harness/cli.py` |
| Log strutturato di ogni passo, con token consumati | `harness/log_strutturato.py` |
| Modello sostituibile senza modificare il resto del codice | `harness/modelli.py` (`ModelloLinguistico`, `crea_modello`) |

Il ciclo agentico è scritto a mano: non si usano framework agentici. Si usano solo l'SDK
ufficiale MCP e l'API HTTP di Ollama (con la libreria standard `urllib`).
