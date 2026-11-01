import json
from pathlib import Path
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from academic_years import register_academic_year_routes


class ArchiveRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.file_id = 'a' * 64
        (root / 'files').mkdir()
        (root / 'files' / self.file_id).write_bytes(b'synthetic document')
        (root / 'index.json').write_text(json.dumps({'years': [{'key': '2024-2025'}]}))
        (root / '2024-2025.json').write_text(json.dumps({'tables': {'Student': [{'id': 7}]}, 'documents': [{'id': self.file_id, 'name': 'test.pdf', 'available': True}]}))
        routes = web.RouteTableDef()
        register_academic_year_routes(routes, root, True)
        app = web.Application()
        app.add_routes(routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {'X-Local-User': 'admin@local.test', 'Origin': 'http://localhost:5173'}

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.cleanup()

    async def test_admin_can_read_but_not_change_archive(self):
        response = await self.client.get('/api/academic-years/2024-2025', headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['tables']['Student'], [{'id': 7}])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        for method in ['post', 'patch', 'delete']:
            response = await getattr(self.client, method)('/api/academic-years/2024-2025', headers=self.headers)
            self.assertEqual(response.status, 405)

    async def test_anonymous_student_and_other_websites_are_denied(self):
        for headers in [{}, {'X-Local-User': 'student@local.test'}, dict(self.headers, Origin='https://evil.test')]:
            response = await self.client.get('/api/academic-years', headers=headers)
            self.assertEqual(response.status, 403)

    async def test_documents_require_year_membership_and_admin(self):
        url = '/api/academic-years/2024-2025/files/' + self.file_id
        self.assertEqual((await self.client.get(url)).status, 403)
        response = await self.client.get(url, headers=self.headers)
        self.assertEqual(await response.read(), b'synthetic document')
        self.assertEqual((await self.client.get(url.replace('2024-2025', '2023-2024'), headers=self.headers)).status, 404)
        self.assertEqual((await self.client.get(url.replace(self.file_id, 'b' * 64), headers=self.headers)).status, 404)

    def test_routes_disabled_outside_local_development(self):
        routes = web.RouteTableDef()
        register_academic_year_routes(routes, self.directory.name, False)
        self.assertEqual(len(routes), 0)


if __name__ == '__main__':
    unittest.main()
