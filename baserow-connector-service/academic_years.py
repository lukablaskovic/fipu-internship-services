"""Local-only, read-only academic archives. Historical events are never executed."""
import json
from pathlib import Path
import re
from urllib.parse import quote

from aiohttp import web


def register_academic_year_routes(routes, directory, local_dev=False):
    if not directory or not local_dev:
        return
    root = Path(directory)

    def authorize(request):
        # Local demo access is intentionally confined to the loopback development stack.
        if request.headers.get('X-Local-User') != 'admin@local.test':
            raise web.HTTPForbidden()
        if request.headers.get('Origin') not in {None, 'http://localhost:5173', 'http://127.0.0.1:5173'}:
            raise web.HTTPForbidden()

    def read_snapshot(year):
        if not re.fullmatch(r'20\d{2}-20\d{2}', year):
            raise web.HTTPNotFound()
        index = json.loads((root / 'index.json').read_text())
        if year not in {item['key'] for item in index['years']}:
            raise web.HTTPNotFound()
        return json.loads((root / (year + '.json')).read_text())

    @routes.get('/api/academic-years')
    async def list_years(request):
        authorize(request)
        if not (root / 'index.json').exists():
            raise web.HTTPServiceUnavailable(text='Academic-year import has not finished')
        return web.json_response(json.loads((root / 'index.json').read_text()), headers={'Cache-Control': 'no-store'})

    @routes.get('/api/academic-years/{year}')
    async def get_year(request):
        authorize(request)
        data = read_snapshot(request.match_info['year'])
        return web.json_response(data, headers={'Cache-Control': 'no-store'})

    @routes.get('/api/academic-years/{year}/files/{file_id}')
    async def get_document(request):
        authorize(request)
        data = read_snapshot(request.match_info['year'])
        file_id = request.match_info['file_id']
        document = next((d for d in data['documents'] if d['id'] == file_id and d['available']), None)
        if not document or not re.fullmatch(r'[a-f0-9]{64}', file_id):
            raise web.HTTPNotFound()
        path = root / 'files' / file_id
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Type': 'application/octet-stream',
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(document['name'], safe=''),
        })
