"""Test del server MCP: il client si collega al server nello stesso processo.

Esecuzione dalla cartella del progetto:
    python -m unittest -v
"""

import asyncio
import os
import random
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from mcp import Client

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "scripts"))

import popola_db  # noqa: E402
from server_mcp.server import server  # noqa: E402

TOOL_ATTESI = {
    "giacenza_articolo": True,  # True = sola lettura
    "articoli_sotto_scorta": True,
    "movimenti_articolo": True,
    "registra_movimento": False,
    "crea_ordine_fornitore": False,
}


class TestServerMCP(unittest.TestCase):
    def setUp(self):
        self._cartella = tempfile.TemporaryDirectory()
        percorso = Path(self._cartella.name) / "test.db"
        popola_db.scrivi(percorso, popola_db.genera(random.Random(42), date.today()))
        self._db_precedente = os.environ.get("MAGAZZINO_DB")
        os.environ["MAGAZZINO_DB"] = str(percorso)

    def tearDown(self):
        if self._db_precedente is None:
            os.environ.pop("MAGAZZINO_DB", None)
        else:
            os.environ["MAGAZZINO_DB"] = self._db_precedente
        self._cartella.cleanup()

    def chiama(self, nome, argomenti):
        async def esegui():
            async with Client(server) as client:
                return await client.call_tool(nome, argomenti)

        return asyncio.run(esegui())

    def test_cinque_tool_con_schemi_e_annotazioni(self):
        async def elenca():
            async with Client(server) as client:
                return (await client.list_tools()).tools

        tools = {t.name: t for t in asyncio.run(elenca())}
        self.assertEqual(set(tools), set(TOOL_ATTESI))
        for nome, sola_lettura in TOOL_ATTESI.items():
            self.assertTrue(tools[nome].description, nome)
            self.assertIsNotNone(tools[nome].output_schema, nome)
            self.assertEqual(tools[nome].annotations.read_only_hint, sola_lettura, nome)

    def test_lettura(self):
        risultato = self.chiama("giacenza_articolo", {"ricerca": "FX-8"})
        self.assertFalse(risultato.is_error)
        self.assertEqual(risultato.structured_content["articoli"][0]["codice"], "TA-103")

    def test_scrittura(self):
        risultato = self.chiama("registra_movimento", {"codice": "FE-102", "tipo": "carico", "quantita": 10})
        self.assertFalse(risultato.is_error)
        dati = risultato.structured_content
        self.assertEqual(dati["giacenza_attuale"], dati["giacenza_precedente"] + 10)

    def test_errori_restituiti_come_is_error(self):
        casi = [
            ("registra_movimento", {"codice": "FE-102", "tipo": "scarico", "quantita": 500}, "giacenza"),
            ("registra_movimento", {"codice": "FE-102", "tipo": "reso", "quantita": 5}, "tipo"),
            ("movimenti_articolo", {"codice": "XX-999"}, "XX-999"),
            ("crea_ordine_fornitore", {"id_fornitore": 5, "righe": []}, "righe"),
            ("tool_inesistente", {}, "Unknown tool"),
        ]
        for nome, argomenti, atteso in casi:
            with self.subTest(nome=nome, argomenti=argomenti):
                risultato = self.chiama(nome, argomenti)
                self.assertTrue(risultato.is_error)
                self.assertIn(atteso, risultato.content[0].text)


if __name__ == "__main__":
    unittest.main()
