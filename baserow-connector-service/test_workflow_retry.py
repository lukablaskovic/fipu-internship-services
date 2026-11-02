"""Exercise partial and lost-response writes with a synthetic Baserow client."""
import json
import uuid
import unittest
from unittest.mock import Mock, AsyncMock, patch

from aiohttp import web
import server


class WorkflowRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_allocation_response_reuses_both_rows(self):
        client = Mock()
        rows = {}
        def create(table, data):
            rows[table] = {"id": len(rows) + 1, **data}
            if table == server.TABLES_MAP["Alokacija"]:
                return None  # Baserow committed, but the connector lost the response.
            return {"data": rows[table]}
        def lookup(table, filters):
            row = rows.get(table)
            if row:
                self.assertIn(filters[0].split("=")[-1], row.values())
            return {"data": {"results": [row] if row else []}}
        client.create_row.side_effect = create
        client.get_table_rows.side_effect = lookup
        req = AsyncMock()
        req.headers = {"Idempotency-Key": str(uuid.uuid4())}
        req.json.return_value = {"Student": [1], "id_instance": str(uuid.uuid4()), "_frontend_url": "local"}
        with patch.object(server, "client", client):
            with self.assertRaises(web.HTTPBadGateway):
                await server.register_assignments(req)
            result = json.loads((await server.register_assignments(req)).text)
            self.assertEqual(result["id_alokacija"], rows[server.TABLES_MAP["Alokacija"]]["id_alokacija"])
            self.assertEqual(client.create_row.call_count, 2)
            client.get_table_rows.return_value = None
            client.get_table_rows.side_effect = None
            with self.assertRaises(web.HTTPBadGateway):
                await server.register_assignments(req)
            self.assertEqual(client.create_row.call_count, 2)
