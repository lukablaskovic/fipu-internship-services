import os
import unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from access import access_control


class DirectoryAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_requires_named_coordinator(self):
        async def list_administrators(request):
            return web.json_response(request["administrator_emails"])

        with patch.dict(os.environ, WORKFLOW_SERVICE_TOKEN="x"*32,
                        COORDINATOR_EMAILS="admin@local.test,lblaskovi@unipu.hr, FACULTY@unipu.hr,faculty@unipu.hr,no@example.org"):
            app = web.Application(middlewares=[access_control(True)])
        app.router.add_get("/api/administratori", list_administrators)
        async with TestClient(TestServer(app)) as client:
            for headers, status in [({}, 401), ({"X-Local-User": "student@local.test"}, 403),
                                    ({"X-Local-User": "admin@local.test"}, 403),
                                    ({"X-Workflow-Token": "x"*32}, 401)]:
                response = await client.get("/api/administratori", headers=headers)
                self.assertEqual(response.status, status)
            claims = {"email": "LBLASKOVI@unipu.hr", "email_verified": True, "hd": "unipu.hr", "exp": 2**32}
            with patch("access.verified_claims", return_value=claims):
                response = await client.get("/api/administratori", headers={"Authorization": "Bearer test"})
            self.assertEqual(await response.json(), ["admin@local.test", "faculty@unipu.hr", "lblaskovi@unipu.hr"])
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
