import unittest
from unittest.mock import AsyncMock, patch

import server


class AssignmentCompanyTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_requires_complete_company_and_reuses_existing_partner(self):
        details = dict(web="https://example.test", direktor="Demo", maticni_broj="00123",
                       OIB="00000000001", adresa="Demo 1")
        payload = {"Poslodavac": ["Demo"], "selekcija": False}
        request = AsyncMock()
        request.json.return_value = payload
        with patch.object(server, "client") as client:
            client.get_table_rows.return_value = {"data": {"results": []}}
            for field in details:
                payload["new_company"] = {**details, field: " "}
                self.assertEqual((await server.add_new_assignment(request)).status, 400)
            for field, invalid in (("OIB", "12"), ("maticni_broj", "abc"), ("web", "javascript:alert(1)")):
                payload["new_company"] = {**details, field: invalid}
                self.assertEqual((await server.add_new_assignment(request)).status, 400)
            client.create_row.assert_not_called()

            payload["new_company"] = details
            client.create_row.side_effect = [{"data": {"id": 7}}, {"data": {"id": 8}}, {"data": {"id": 9}}]
            client.update_row.return_value = {"data": {"id": 8}}
            self.assertEqual((await server.add_new_assignment(request)).status, 200)
            company, assignment = client.create_row.call_args_list
            self.assertEqual(company.kwargs["data"], {"naziv": "Demo", **details})
            self.assertEqual(assignment.args[1]["Poslodavac"], [7])

            self.assertEqual(assignment.args[1]["selekcija"], "false")

            del payload["new_company"]
            client.get_table_rows.return_value = {"data": {"results": [{"id": 7}]}}
            self.assertEqual((await server.add_new_assignment(request)).status, 200)
            self.assertEqual(client.create_row.call_count, 3)
            self.assertEqual(client.create_row.call_args.args[1]["Poslodavac"], [7])
