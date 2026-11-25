"""Log strutturato: un evento JSON per riga (formato JSON Lines).

Ogni evento ha: istante, sessione, numero progressivo, tipo e i dati del passo.
I tipi principali sono:
  richiesta_utente, richiesta_modello, risposta_modello (con i token consumati),
  invocazione_tool, risultato_tool, richiesta_conferma, conferma,
  tool_non_disponibile, parametri_non_validi, errore_modello, fine_turno.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class LogStrutturato:
    def __init__(self, cartella: str | Path = "logs", ascoltatori: list[Callable[[dict], None]] | None = None):
        self.sessione = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # con i millisecondi
        cartella = Path(cartella)
        cartella.mkdir(parents=True, exist_ok=True)
        self.percorso = cartella / f"sessione_{self.sessione}.jsonl"
        self._file = self.percorso.open("a", encoding="utf-8")
        self._progressivo = 0
        self._ascoltatori = ascoltatori or []

    def scrivi(self, tipo: str, **dati) -> dict:
        self._progressivo += 1
        evento = {
            "istante": datetime.now().isoformat(timespec="milliseconds"),
            "sessione": self.sessione,
            "n": self._progressivo,
            "tipo": tipo,
            **dati,
        }
        self._file.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")
        self._file.flush()
        for ascoltatore in self._ascoltatori:
            ascoltatore(evento)
        return evento

    def chiudi(self) -> None:
        self._file.close()
