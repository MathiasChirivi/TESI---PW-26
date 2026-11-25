"""Livello di astrazione dal fornitore del modello linguistico.

Il resto dell'harness conosce solo i tipi definiti qui (Messaggio, ChiamataTool,
DefinizioneTool, RispostaModello) e la classe astratta ModelloLinguistico. Ogni
fornitore è un adattatore che traduce questi tipi nel formato della propria API.

Per usare un altro modello basta cambiare la stringa passata a crea_modello,
ad esempio "ollama:qwen3:4b-instruct-2507-q4_K_M" -> "ollama:llama3.2:3b". Per un altro fornitore si
scrive una nuova sottoclasse e la si registra in FORNITORI: agente, client MCP,
log e interfaccia non cambiano.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Ruolo = Literal["system", "user", "assistant", "tool"]


@dataclass
class ChiamataTool:
    id: str
    nome: str
    argomenti: dict | None  # None se il modello ha prodotto argomenti non leggibili come JSON
    argomenti_grezzi: str = ""


@dataclass
class Messaggio:
    ruolo: Ruolo
    contenuto: str = ""
    chiamate: list[ChiamataTool] = field(default_factory=list)  # solo per ruolo "assistant"
    id_chiamata: str | None = None  # solo per ruolo "tool"
    nome_tool: str | None = None  # solo per ruolo "tool"


@dataclass
class DefinizioneTool:
    nome: str
    descrizione: str
    schema_parametri: dict
    scrittura: bool  # True se il tool modifica i dati: serve la conferma dell'utente


@dataclass
class RispostaModello:
    testo: str
    chiamate: list[ChiamataTool]
    token_ingresso: int
    token_uscita: int
    durata_s: float = 0.0
    ragionamento: str = ""  # eventuale ragionamento del modello, separato dalla risposta


class ErroreModello(Exception):
    """Il modello non è raggiungibile o ha risposto in modo inatteso."""


class ModelloLinguistico(ABC):
    """Interfaccia comune a tutti i fornitori di modelli."""

    nome: str

    @abstractmethod
    def genera(self, messaggi: list[Messaggio], tools: list[DefinizioneTool]) -> RispostaModello:
        """Invia la conversazione e i tool disponibili; restituisce testo e/o chiamate ai tool."""


# --------------------------------------------------------------------------- Ollama (modelli in locale)


class ModelloOllama(ModelloLinguistico):
    """Adattatore per l'API /api/chat di Ollama (http://localhost:11434)."""

    def __init__(
        self,
        modello: str,
        url: str = "http://localhost:11434",
        contesto: int = 8192,
        temperatura: float = 0.0,
        seme: int = 42,
        pensiero: bool = False,
        timeout_s: float = 300,
    ):
        self.nome = f"ollama:{modello}"
        self._modello = modello
        self._url = url.rstrip("/") + "/api/chat"
        self._opzioni = {"num_ctx": contesto, "temperature": temperatura, "seed": seme}
        self._pensiero: bool | None = pensiero
        self._timeout = timeout_s
        self._contatore = 0

    def genera(self, messaggi: list[Messaggio], tools: list[DefinizioneTool]) -> RispostaModello:
        messaggi_api = [self._messaggio_ollama(m) for m in messaggi]
        if not self._pensiero and self._modello.startswith("qwen3") and "instruct" not in self._modello and messaggi_api and messaggi_api[0]["role"] == "system":
            # qwen3 ignora a volte il parametro think=false: l'istruzione /no_think nel prompt
            # di sistema disattiva il ragionamento esteso, che con 8 GB di RAM rallenta molto.
            messaggi_api[0] = {**messaggi_api[0], "content": messaggi_api[0]["content"] + "\n/no_think"}
        corpo = {
            "model": self._modello,
            "messages": messaggi_api,
            "tools": [
                {"type": "function", "function": {"name": t.nome, "description": t.descrizione, "parameters": t.schema_parametri}}
                for t in tools
            ],
            "stream": False,
            "options": self._opzioni,
        }
        if self._pensiero is not None:
            corpo["think"] = self._pensiero

        inizio = time.monotonic()
        try:
            dati = self._post(corpo)
        except ErroreModello as errore:
            # Alcuni modelli (es. llama3.2) non accettano il parametro "think": si riprova senza.
            if "think" in str(errore).lower() and "think" in corpo:
                self._pensiero = None
                corpo.pop("think")
                dati = self._post(corpo)
            else:
                raise
        durata = time.monotonic() - inizio

        messaggio = dati.get("message") or {}
        chiamate = [self._chiamata(c) for c in messaggio.get("tool_calls") or []]
        testo, ragionamento = self._separa_ragionamento(messaggio.get("content") or "")
        ragionamento = (messaggio.get("thinking") or "") + ragionamento
        return RispostaModello(
            testo=testo,
            chiamate=chiamate,
            token_ingresso=int(dati.get("prompt_eval_count") or 0),
            token_uscita=int(dati.get("eval_count") or 0),
            durata_s=round(durata, 2),
            ragionamento=ragionamento.strip(),
        )

    @staticmethod
    def _separa_ragionamento(contenuto: str) -> tuple[str, str]:
        """Toglie dalla risposta l'eventuale ragionamento racchiuso tra <think> e </think>."""
        if "</think>" not in contenuto:
            return contenuto.strip(), ""
        ragionamento, _, testo = contenuto.rpartition("</think>")
        return testo.strip(), ragionamento.replace("<think>", "").strip()

    def _post(self, corpo: dict) -> dict:
        richiesta = urllib.request.Request(
            self._url, data=json.dumps(corpo).encode("utf-8"), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(richiesta, timeout=self._timeout) as risposta:
                return json.loads(risposta.read().decode("utf-8"))
        except urllib.error.HTTPError as errore:
            dettaglio = errore.read().decode("utf-8", errors="replace")
            raise ErroreModello(f"Ollama ha risposto {errore.code}: {dettaglio}") from errore
        except (urllib.error.URLError, TimeoutError, ConnectionError) as errore:
            raise ErroreModello(f"Ollama non raggiungibile su {self._url} ({errore}). È avviato 'ollama serve'?") from errore
        except json.JSONDecodeError as errore:
            raise ErroreModello("Risposta di Ollama non valida") from errore

    @staticmethod
    def _messaggio_ollama(m: Messaggio) -> dict:
        if m.ruolo == "assistant" and m.chiamate:
            return {
                "role": "assistant",
                "content": m.contenuto,
                "tool_calls": [
                    {"function": {"name": c.nome, "arguments": c.argomenti if c.argomenti is not None else {}}}
                    for c in m.chiamate
                ],
            }
        if m.ruolo == "tool":
            return {"role": "tool", "content": m.contenuto, "tool_name": m.nome_tool}
        return {"role": m.ruolo, "content": m.contenuto}

    def _chiamata(self, grezza: dict) -> ChiamataTool:
        self._contatore += 1
        funzione = grezza.get("function") or {}
        argomenti = funzione.get("arguments")
        testo = argomenti if isinstance(argomenti, str) else json.dumps(argomenti, ensure_ascii=False)
        if isinstance(argomenti, str):
            try:
                argomenti = json.loads(argomenti)
            except json.JSONDecodeError:
                argomenti = None
        if argomenti is not None and not isinstance(argomenti, dict):
            argomenti = None
        return ChiamataTool(
            id=grezza.get("id") or f"chiamata_{self._contatore}",
            nome=str(funzione.get("name") or ""),
            argomenti=argomenti,
            argomenti_grezzi=testo,
        )


# --------------------------------------------------------------------------- modello finto per i test


class ModelloFinto(ModelloLinguistico):
    """Restituisce risposte preparate in anticipo: serve a testare l'harness senza un modello vero."""

    def __init__(self, risposte: list[RispostaModello | Callable[[list[Messaggio]], RispostaModello]]):
        self.nome = "finto"
        self._risposte = list(risposte)
        self.richieste: list[list[Messaggio]] = []

    def genera(self, messaggi: list[Messaggio], tools: list[DefinizioneTool]) -> RispostaModello:
        self.richieste.append(list(messaggi))
        if not self._risposte:
            raise ErroreModello("Il modello finto non ha altre risposte preparate")
        risposta = self._risposte.pop(0)
        return risposta(messaggi) if callable(risposta) else risposta


# --------------------------------------------------------------------------- registro dei fornitori

FORNITORI: dict[str, Callable[..., ModelloLinguistico]] = {
    "ollama": ModelloOllama,
}


def crea_modello(specifica: str, **opzioni) -> ModelloLinguistico:
    """Crea un modello da una stringa 'fornitore:modello', es. 'ollama:qwen3:4b'."""
    fornitore, separatore, modello = specifica.partition(":")
    if not separatore or not modello:
        raise ValueError(f"Specifica del modello non valida: {specifica!r}. Formato atteso: fornitore:modello")
    if fornitore not in FORNITORI:
        raise ValueError(f"Fornitore sconosciuto: {fornitore!r}. Disponibili: {', '.join(FORNITORI)}")
    return FORNITORI[fornitore](modello, **opzioni)
