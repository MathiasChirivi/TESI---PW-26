"""Ciclo agentico.

Per ogni richiesta dell'utente l'agente ripete, al massimo `max_iterazioni` volte:
  1. invia al modello la conversazione e l'elenco dei tool;
  2. se il modello risponde senza chiamare tool, quella è la risposta finale (arresto);
  3. altrimenti esegue ogni chiamata, nell'ordine:
       - tool inesistente            -> errore restituito al modello
       - argomenti non validi        -> errore restituito al modello (validazione con lo schema)
       - tool di scrittura           -> conferma dell'utente (human-in-the-loop)
       - invocazione sul server MCP  -> risultato (o errore) restituito al modello
  4. torna al punto 1.

Condizioni di arresto esplicite (EsitoTurno.motivo_arresto):
  - "risposta_finale"            il modello ha risposto senza chiamare tool
  - "limite_iterazioni"          raggiunto il numero massimo di passi
  - "troppi_errori_consecutivi"  troppe chiamate fallite di seguito
  - "errore_modello"             il modello non è raggiungibile o risponde male
Ogni passo è registrato nel log strutturato.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from harness.client_mcp import ClientMCP
from harness.log_strutturato import LogStrutturato
from harness.modelli import ChiamataTool, DefinizioneTool, ErroreModello, Messaggio, ModelloLinguistico
from harness.prompt import istruzioni_sistema

RISPOSTA_FINALE = "risposta_finale"
LIMITE_ITERAZIONI = "limite_iterazioni"
TROPPI_ERRORI = "troppi_errori_consecutivi"
ERRORE_MODELLO = "errore_modello"

FunzioneConferma = Callable[[DefinizioneTool, dict], Awaitable[bool]]


@dataclass
class EsitoTurno:
    risposta: str
    motivo_arresto: str
    iterazioni: int
    chiamate_tool: int
    token_ingresso: int
    token_uscita: int


@dataclass
class _EsitoChiamata:
    testo: str  # ciò che il modello riceve come risultato del tool
    stato: str  # "ok" | "errore" | "annullato"


class Agente:
    def __init__(
        self,
        modello: ModelloLinguistico,
        client: ClientMCP,
        log: LogStrutturato,
        conferma: FunzioneConferma,
        max_iterazioni: int = 8,
        max_errori_consecutivi: int = 3,
    ):
        self.modello = modello
        self.client = client
        self.log = log
        self.conferma = conferma
        self.max_iterazioni = max_iterazioni
        self.max_errori_consecutivi = max_errori_consecutivi
        self.nuova_conversazione()

    def nuova_conversazione(self) -> None:
        self.storia: list[Messaggio] = [Messaggio("system", istruzioni_sistema())]

    # ------------------------------------------------------------------ ciclo principale

    async def esegui_turno(self, richiesta: str) -> EsitoTurno:
        self.storia.append(Messaggio("user", richiesta))
        self.log.scrivi("richiesta_utente", testo=richiesta, modello=self.modello.nome)
        token_in = token_out = chiamate_eseguite = errori_consecutivi = 0

        def fine(motivo: str, risposta: str, iterazioni: int) -> EsitoTurno:
            esito = EsitoTurno(risposta, motivo, iterazioni, chiamate_eseguite, token_in, token_out)
            self.log.scrivi("fine_turno", **esito.__dict__)
            return esito

        for iterazione in range(1, self.max_iterazioni + 1):
            # 1. Richiesta al modello (in un thread: la chiamata HTTP è bloccante)
            self.log.scrivi(
                "richiesta_modello",
                iterazione=iterazione,
                messaggi=len(self.storia),
                tools=[t.nome for t in self.client.tools],
            )
            try:
                risposta = await asyncio.to_thread(self.modello.genera, self.storia, self.client.tools)
            except ErroreModello as errore:
                self.log.scrivi("errore_modello", iterazione=iterazione, errore=str(errore))
                return fine(ERRORE_MODELLO, f"Il modello non è disponibile: {errore}", iterazione)

            token_in += risposta.token_ingresso
            token_out += risposta.token_uscita
            self.log.scrivi(
                "risposta_modello",
                iterazione=iterazione,
                testo=risposta.testo,
                ragionamento=risposta.ragionamento,
                chiamate=[{"nome": c.nome, "argomenti": c.argomenti or c.argomenti_grezzi} for c in risposta.chiamate],
                token_ingresso=risposta.token_ingresso,
                token_uscita=risposta.token_uscita,
                durata_s=risposta.durata_s,
            )
            self.storia.append(Messaggio("assistant", risposta.testo, chiamate=risposta.chiamate))

            # 2. Nessuna chiamata ai tool: risposta finale
            if not risposta.chiamate:
                return fine(RISPOSTA_FINALE, risposta.testo, iterazione)

            # 3. Esecuzione delle chiamate richieste dal modello
            for chiamata in risposta.chiamate:
                esito = await self._esegui_chiamata(chiamata)
                chiamate_eseguite += 1
                self.storia.append(
                    Messaggio("tool", esito.testo, id_chiamata=chiamata.id, nome_tool=chiamata.nome)
                )
                if esito.stato == "errore":
                    errori_consecutivi += 1
                elif esito.stato == "ok":
                    errori_consecutivi = 0

            if errori_consecutivi >= self.max_errori_consecutivi:
                return fine(
                    TROPPI_ERRORI,
                    "Non sono riuscito a completare la richiesta: le operazioni sono fallite più volte di seguito. "
                    "Prova a riformularla indicando codice articolo e quantità.",
                    iterazione,
                )

        return fine(
            LIMITE_ITERAZIONI,
            f"Mi sono fermato dopo {self.max_iterazioni} passi senza arrivare a una risposta. "
            "Prova a dividere la richiesta in parti più semplici.",
            self.max_iterazioni,
        )

    # ------------------------------------------------------------------ singola chiamata a un tool

    async def _esegui_chiamata(self, chiamata: ChiamataTool) -> _EsitoChiamata:
        # a) Tool non disponibile
        tool = self.client.tool(chiamata.nome)
        if tool is None:
            disponibili = ", ".join(t.nome for t in self.client.tools)
            self.log.scrivi("tool_non_disponibile", nome=chiamata.nome, disponibili=disponibili)
            return _EsitoChiamata(
                f"ERRORE: il tool '{chiamata.nome}' non esiste. Tool disponibili: {disponibili}.", "errore"
            )

        # b) Parametri non validi: argomenti illeggibili o non conformi allo schema del tool
        if chiamata.argomenti is None:
            self.log.scrivi("parametri_non_validi", nome=tool.nome, argomenti=chiamata.argomenti_grezzi,
                            errori=["argomenti non in formato JSON"])
            return _EsitoChiamata("ERRORE: gli argomenti non sono un oggetto JSON valido.", "errore")
        errori = [
            f"{'/'.join(map(str, e.absolute_path)) or 'argomenti'}: {e.message}"
            for e in Draft202012Validator(tool.schema_parametri).iter_errors(chiamata.argomenti)
        ]
        if errori:
            self.log.scrivi("parametri_non_validi", nome=tool.nome, argomenti=chiamata.argomenti, errori=errori)
            return _EsitoChiamata("ERRORE: parametri non validi. " + "; ".join(errori), "errore")

        # c) Human-in-the-loop: i tool di scrittura richiedono la conferma dell'utente
        if tool.scrittura:
            self.log.scrivi("richiesta_conferma", nome=tool.nome, argomenti=chiamata.argomenti)
            approvata = await self.conferma(tool, chiamata.argomenti)
            self.log.scrivi("conferma", nome=tool.nome, approvata=approvata)
            if not approvata:
                return _EsitoChiamata(
                    "OPERAZIONE ANNULLATA: l'utente non ha confermato, non è stato modificato nulla. "
                    "Non riprovare senza una nuova richiesta dell'utente.",
                    "annullato",
                )

        # d) Invocazione sul server MCP
        self.log.scrivi("invocazione_tool", nome=tool.nome, parametri=chiamata.argomenti)
        inizio = time.monotonic()
        esito = await self.client.chiama(tool.nome, chiamata.argomenti)
        self.log.scrivi(
            "risultato_tool",
            nome=tool.nome,
            ok=esito.ok,
            risultato=esito.contenuto,
            durata_s=round(time.monotonic() - inizio, 3),
        )
        if esito.ok:
            return _EsitoChiamata(esito.contenuto, "ok")
        return _EsitoChiamata(f"ERRORE dal tool {tool.nome}: {esito.contenuto}", "errore")
