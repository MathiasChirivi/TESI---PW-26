"""Server MCP del magazzino di Minuterie Valdora S.r.l. (azienda fittizia).

Espone le cinque operazioni del back-end come tool MCP, usando l'SDK ufficiale
MCP per Python (classe MCPServer). Ogni tool dichiara:
  - uno schema dei parametri di ingresso, ricavato dalle annotazioni dei tipi;
  - uno schema di uscita, ricavato dal modello Pydantic restituito;
  - delle annotazioni (read_only_hint, destructive_hint) che indicano all'harness
    se il tool modifica i dati.

Gli errori previsti del back-end (parametri non validi, articolo inesistente,
giacenza insufficiente...) diventano ToolError: il client riceve un risultato con
isError=True e il messaggio in italiano, che l'agente può leggere e correggere.

Avvio:  python -m server_mcp          (trasporto stdio)
Base dati: variabile d'ambiente MAGAZZINO_DB, altrimenti data/magazzino.db
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from magazzino import ErroreMagazzino, Magazzino

RADICE = Path(__file__).resolve().parent.parent
DB_DEFAULT = RADICE / "data" / "magazzino.db"

server = MCPServer(
    name="magazzino-valdora",
    instructions=(
        "Tool per la gestione del magazzino di un ingrosso di ferramenta: consultare giacenze, "
        "articoli sotto scorta e movimenti; registrare carichi e scarichi; creare ordini ai fornitori. "
        "Le quantità sono espresse nell'unità di misura dell'articolo (pz, conf, rotolo, m)."
    ),
)

LETTURA = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


@contextmanager
def apri_magazzino() -> Iterator[Magazzino]:
    """Apre una connessione per ogni chiamata e converte gli errori del back-end in ToolError."""
    percorso = Path(os.environ.get("MAGAZZINO_DB", DB_DEFAULT))
    try:
        with Magazzino(percorso) as magazzino:
            yield magazzino
    except ErroreMagazzino as errore:
        raise ToolError(str(errore)) from errore


# --------------------------------------------------------------------------- modelli di uscita


class Articolo(BaseModel):
    codice: str
    descrizione: str
    categoria: str
    unita_misura: str
    giacenza: int
    scorta_minima: int
    sotto_scorta: bool
    fornitore: str


class EsitoRicerca(BaseModel):
    trovati: int = Field(description="Numero di articoli trovati (al massimo 20)")
    articoli: list[Articolo]


class ArticoloSottoScorta(BaseModel):
    codice: str
    descrizione: str
    unita_misura: str
    giacenza: int
    scorta_minima: int
    mancanti: int = Field(description="Quantità che manca per tornare alla scorta minima")
    id_fornitore: int
    fornitore: str


class ElencoSottoScorta(BaseModel):
    totale: int
    articoli: list[ArticoloSottoScorta]


class Movimento(BaseModel):
    id_movimento: int
    tipo: Literal["carico", "scarico"]
    quantita: int
    data_ora: str
    riferimento: str | None


class Periodo(BaseModel):
    da: str
    a: str


class StoricoMovimenti(BaseModel):
    codice: str
    descrizione: str
    periodo: Periodo
    totale_carichi: int
    totale_scarichi: int
    giacenza_attuale: int
    movimenti: list[Movimento]


class MovimentoRegistrato(BaseModel):
    id_movimento: int
    codice: str
    descrizione: str
    tipo: Literal["carico", "scarico"]
    quantita: int
    data_ora: str
    riferimento: str | None
    giacenza_precedente: int
    giacenza_attuale: int
    sotto_scorta: bool = Field(description="True se dopo il movimento la giacenza è sotto la scorta minima")


class RigaOrdine(BaseModel):
    codice: str = Field(description="Codice articolo, es. FE-102")
    quantita: int = Field(gt=0, description="Quantità da ordinare, nell'unità di misura dell'articolo")


class RigaOrdineCreata(BaseModel):
    codice: str
    descrizione: str
    quantita: int
    prezzo_acquisto: float
    importo: float


class OrdineCreato(BaseModel):
    id_ordine: int
    fornitore: str
    data_ordine: str
    stato: str
    righe: list[RigaOrdineCreata]
    totale_stimato: float = Field(description="Somma degli importi in euro, a prezzo di acquisto")


# --------------------------------------------------------------------------- tool di lettura


@server.tool(title="Giacenza articolo", annotations=LETTURA)
def giacenza_articolo(
    ricerca: Annotated[
        str,
        Field(description="Codice articolo (es. FE-102) oppure parole della descrizione (es. 'viti autofilettanti 4x40')"),
    ],
) -> EsitoRicerca:
    """Cerca uno o più articoli per codice o descrizione e restituisce giacenza e scorta minima.

    Con un codice esatto restituisce solo quell'articolo; con delle parole restituisce gli
    articoli la cui descrizione le contiene tutte. Se trova più articoli, chiedere all'utente
    quale intende prima di qualsiasi operazione di scrittura.
    """
    with apri_magazzino() as m:
        articoli = [Articolo(**a) for a in m.giacenza_articolo(ricerca)]
    return EsitoRicerca(trovati=len(articoli), articoli=articoli)


@server.tool(title="Articoli sotto scorta", annotations=LETTURA)
def articoli_sotto_scorta() -> ElencoSottoScorta:
    """Elenca gli articoli con giacenza inferiore alla scorta minima, con il fornitore abituale
    e la quantità mancante. Utile per decidere cosa riordinare."""
    with apri_magazzino() as m:
        articoli = [ArticoloSottoScorta(**a) for a in m.articoli_sotto_scorta()]
    return ElencoSottoScorta(totale=len(articoli), articoli=articoli)


@server.tool(title="Movimenti articolo", annotations=LETTURA)
def movimenti_articolo(
    codice: Annotated[str, Field(description="Codice articolo, es. TA-103")],
    data_da: Annotated[
        str | None, Field(description="Data iniziale AAAA-MM-GG, inclusa. Se assente: 30 giorni prima di data_a")
    ] = None,
    data_a: Annotated[str | None, Field(description="Data finale AAAA-MM-GG, inclusa. Se assente: oggi")] = None,
) -> StoricoMovimenti:
    """Elenca carichi e scarichi di un articolo in un periodo, con i totali e la giacenza attuale."""
    with apri_magazzino() as m:
        return StoricoMovimenti(**m.movimenti_articolo(codice, data_da, data_a))


# --------------------------------------------------------------------------- tool di scrittura


@server.tool(
    title="Registra carico o scarico",
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def registra_movimento(
    codice: Annotated[str, Field(description="Codice articolo, es. FE-102")],
    tipo: Annotated[
        Literal["carico", "scarico"], Field(description="'carico' per merce in entrata, 'scarico' per merce in uscita")
    ],
    quantita: Annotated[int, Field(gt=0, description="Quantità nell'unità di misura dell'articolo")],
    riferimento: Annotated[
        str | None, Field(description="Numero della bolla o dell'ordine cliente, facoltativo")
    ] = None,
) -> MovimentoRegistrato:
    """SCRITTURA. Registra un carico o uno scarico di magazzino e aggiorna la giacenza.

    Uno scarico superiore alla giacenza disponibile viene rifiutato e non modifica nulla.
    """
    with apri_magazzino() as m:
        return MovimentoRegistrato(**m.registra_movimento(codice, tipo, quantita, riferimento))


@server.tool(
    title="Crea ordine al fornitore",
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def crea_ordine_fornitore(
    id_fornitore: Annotated[int, Field(description="Identificativo del fornitore (vedi articoli_sotto_scorta)")],
    righe: Annotated[
        list[RigaOrdine], Field(min_length=1, description="Articoli da ordinare, tutti dello stesso fornitore")
    ],
) -> OrdineCreato:
    """SCRITTURA. Crea un ordine a un fornitore, in stato 'creato'.

    Tutti gli articoli devono avere come fornitore abituale quello indicato; per articoli di
    fornitori diversi servono ordini separati.
    """
    with apri_magazzino() as m:
        return OrdineCreato(**m.crea_ordine_fornitore(id_fornitore, [r.model_dump() for r in righe]))


def main() -> None:
    server.run()  # trasporto stdio


if __name__ == "__main__":
    main()
