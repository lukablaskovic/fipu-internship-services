"""Exercise the withdrawal route with synthetic rows; never load service credentials."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import uuid
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class WithdrawalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = Mock()
        self.backend.get_table_rows.return_value = {"data": {"results": [{"id": 7}], "next": None}}
        self.backend.update_row.return_value = {"data": {"id": 7}}
        routes = web.RouteTableDef()
        source = ast.parse(Path(__file__).with_name("server.py").read_text())
        handler = next(node for node in source.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "odustajanje")
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "withdrawal-route", "exec"), {
            "routes": routes, "client": self.backend, "uuid": uuid, "aiohttp": aiohttp,
            "TABLES_MAP": {"Alokacija": "synthetic"},
            "br": SimpleNamespace(Alokacija_Mappings={"process_instance_id": "field_1"}),
        })
        app = web.Application()
        app.add_routes(routes)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()

    async def test_existing_allocation_and_repeat_are_idempotent(self):
        instance = str(uuid.uuid4())
        for _ in range(2):
            response = await self.http.post("/api/odustajanje", json={"id_instance": instance})
            self.assertEqual(response.status, 200)
        self.backend.get_table_rows.assert_called_with("synthetic", [f"filter__field_1__equal={instance}", "size=200"])
        self.backend.update_row.assert_called_with("synthetic", 7, {"status_zahtjeva": "student_odustao"})

    async def test_no_allocation_is_valid_but_lookup_and_write_failures_are_not(self):
        self.backend.get_table_rows.return_value = {"data": {"results": [], "next": None}}
        self.assertEqual((await self.http.post("/api/odustajanje", json={"id_instance": str(uuid.uuid4())})).status, 200)
        self.backend.update_row.assert_not_called()
        self.backend.get_table_rows.return_value = {"error": "synthetic failure"}
        self.assertEqual((await self.http.post("/api/odustajanje", json={"id_instance": str(uuid.uuid4())})).status, 502)
        self.backend.get_table_rows.return_value = {"data": {"results": [{"id": 7}]}}
        self.backend.update_row.return_value = {"error": "synthetic failure"}
        self.assertEqual((await self.http.post("/api/odustajanje", json={"id_instance": str(uuid.uuid4())})).status, 502)

    async def test_invalid_instance_cannot_broaden_the_filter(self):
        for body in [[], {}, {"id_instance": "x&filter__other=1"}, {"id_instance": 7}]:
            self.assertEqual((await self.http.post("/api/odustajanje", json=body)).status, 400)
        self.backend.get_table_rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
