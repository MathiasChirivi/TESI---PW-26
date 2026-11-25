"""Test del ciclo agentico con un modello finto (nessun modello vero necessario).

Il modello finto restituisce risposte preparate, così si verifica in modo
ripetibile il comportamento dell'harness: arresto, errori, conferme, log.
"""

import asyncio
import json
import os
import random
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "scripts"))

import popola_db  # noqa: E402
from harness import agente as ag  # noqa: E402
from harness.client_mcp import ClientMCP  # noqa: E402
from harness.log_strutturato import LogStrutturato  # noqa: E402
from harness.modelli import ChiamataTool, ErroreModello, ModelloFinto, RispostaModello  # noqa: E402
from server_mcp.server import server  # noqa: E402


def testo(t):
    return RispostaModello(testo=t, chiamate=[], token_ingresso=100, token_uscita=10)


def chiama(nome, argomenti):
    return RispostaModello(testo="", chiamate=[ChiamataTool("c1", nome, argomenti)], token_ingresso=100, token_uscita=20)


class TestAgente(unittest.TestCase):
    def setUp(self):
        self._cartella = tempfile.TemporaryDirectory()
        cartella = Path(self._cartella.name)
        popola_db.scrivi(cartella / "test.db", popola_db.genera(random.Random(42), date.today()))
        self._db_precedente = os.environ.get("MAGAZZINO_DB")
        os.environ["MAGAZZINO_DB"] = str(cartella / "test.db")
        self.log = LogStrutturato(cartella / "logs")
        self.conferme = []

    def tearDown(self):
        self.log.chiudi()
        if self._db_precedente is None:
            os.environ.pop("MAGAZZINO_DB", None)
        else:
            os.environ["MAGAZZINO_DB"] = self._db_precedente
        self._cartella.cleanup()

    def esegui(self, risposte, richiesta="richiesta di prova", conferma=True, max_iterazioni=8):
        async def conferma_fn(tool, argomenti):
            self.conferme.append((tool.nome, argomenti))
            return conferma

        async def corpo():
            modello = ModelloFinto(risposte)
            async with ClientMCP(server) as client:
                agente = ag.Agente(modello, client, self.log, conferma_fn, max_iterazioni=max_iterazioni)
                esito = await agente.esegui_turno(richiesta)
                return esito, agente, modello

        return asyncio.run(corpo())

    def eventi(self, tipo=None):
        righe = [json.loads(r) for r in self.log.percorso.read_text(encoding="utf-8").splitlines()]
        return [r for r in righe if tipo is None or r["tipo"] == tipo]

    def test_lettura_e_risposta_finale(self):
        esito, agente, _ = self.esegui([chiama("giacenza_articolo", {"ricerca": "FE-102"}), testo("Ci sono 90 pezzi.")])
        self.assertEqual(esito.motivo_arresto, ag.RISPOSTA_FINALE)
        self.assertEqual((esito.iterazioni, esito.chiamate_tool), (2, 1))
        self.assertEqual((esito.token_ingresso, esito.token_uscita), (200, 30))
        self.assertIn('"codice":"FE-102"', agente.storia[-2].contenuto)  # risultato del tool passato al modello
        self.assertEqual(self.conferme, [])  # i tool di lettura non chiedono conferma

    def test_scrittura_confermata(self):
        esito, agente, _ = self.esegui(
            [chiama("registra_movimento", {"codice": "FE-102", "tipo": "scarico", "quantita": 20}), testo("Fatto.")]
        )
        self.assertEqual(len(self.conferme), 1)
        self.assertEqual(len(self.eventi("invocazione_tool")), 1)
        self.assertIn('"giacenza_attuale":70', agente.storia[-2].contenuto)

    def test_conferma_negata_non_scrive(self):
        esito, agente, _ = self.esegui(
            [chiama("crea_ordine_fornitore", {"id_fornitore": 7, "righe": [{"codice": "NS-101", "quantita": 100}]}),
             testo("Ordine annullato.")],
            conferma=False,
        )
        self.assertEqual(esito.motivo_arresto, ag.RISPOSTA_FINALE)
        self.assertEqual(self.eventi("invocazione_tool"), [])
        self.assertIn("ANNULLATA", agente.storia[-2].contenuto)
        self.assertFalse(self.eventi("conferma")[0]["approvata"])

    def test_tool_non_disponibile(self):
        esito, agente, _ = self.esegui([chiama("cancella_tutto", {}), testo("Non posso.")])
        self.assertIn("non esiste", agente.storia[-2].contenuto)
        self.assertEqual(len(self.eventi("tool_non_disponibile")), 1)

    def test_parametri_non_validi_bloccati_prima_del_server(self):
        esito, agente, _ = self.esegui(
            [chiama("registra_movimento", {"codice": "FE-102", "tipo": "reso", "quantita": "venti"}), testo("Errore.")]
        )
        self.assertEqual(self.eventi("invocazione_tool"), [])
        self.assertEqual(self.conferme, [])  # nessuna conferma chiesta per una chiamata non valida
        self.assertIn("parametri non validi", agente.storia[-2].contenuto)

    def test_errore_del_tool_restituito_al_modello(self):
        esito, agente, _ = self.esegui(
            [chiama("registra_movimento", {"codice": "FE-102", "tipo": "scarico", "quantita": 500}), testo("Giacenza insufficiente.")]
        )
        self.assertIn("ERRORE dal tool", agente.storia[-2].contenuto)
        self.assertFalse(self.eventi("risultato_tool")[0]["ok"])

    def test_limite_iterazioni(self):
        risposte = [chiama("articoli_sotto_scorta", {}) for _ in range(3)]
        esito, _, _ = self.esegui(risposte, max_iterazioni=3)
        self.assertEqual(esito.motivo_arresto, ag.LIMITE_ITERAZIONI)
        self.assertEqual(esito.iterazioni, 3)

    def test_troppi_errori_consecutivi(self):
        risposte = [chiama("tool_inventato", {}) for _ in range(3)]
        esito, _, _ = self.esegui(risposte)
        self.assertEqual(esito.motivo_arresto, ag.TROPPI_ERRORI)

    def test_errore_del_modello(self):
        def guasto(_messaggi):
            raise ErroreModello("connessione rifiutata")

        esito, _, _ = self.esegui([guasto])
        self.assertEqual(esito.motivo_arresto, ag.ERRORE_MODELLO)

    def test_log_strutturato_completo(self):
        self.esegui([chiama("giacenza_articolo", {"ricerca": "FE-102"}), testo("90 pezzi.")])
        tipi = [e["tipo"] for e in self.eventi()]
        for atteso in ("richiesta_utente", "richiesta_modello", "risposta_modello", "invocazione_tool",
                       "risultato_tool", "fine_turno"):
            self.assertIn(atteso, tipi)
        self.assertEqual(self.eventi("risposta_modello")[0]["token_ingresso"], 100)


if __name__ == "__main__":
    unittest.main()
