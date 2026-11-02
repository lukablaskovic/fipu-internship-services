"""Authenticated avatar URLs and bounded raster-image uploads."""
import asyncio
import os
import tempfile
from urllib.parse import urlsplit

from aiohttp import web
def validate_avatar(data):
    avatar = data.get("avatar") if isinstance(data, dict) else None
    if not isinstance(avatar, str) or not avatar or len(avatar) > 2048:
        raise web.HTTPBadRequest(text="Invalid avatar URL")
    try:
        url = urlsplit(avatar)
        valid = url.scheme == "https" and url.hostname and not url.username and not url.password
        if not valid or any(char.isspace() or ord(char) < 32 for char in avatar) or "\\" in avatar:
            raise ValueError()
        url.port  # Reject invalid ports as well as malformed hosts.
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid avatar URL")
    return avatar


async def upload_avatar(request, client, loop):
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file" or not field.filename:
        raise web.HTTPBadRequest(text="Expected an image file")
    content = bytearray()
    limit = 2 * 1024 * 1024
    while True:
        chunk = await field.read_chunk()
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(content))
    if await reader.next() is not None:
        raise web.HTTPBadRequest(text="Expected exactly one image")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        extension, mime = ".png", "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        extension, mime = ".jpg", "image/jpeg"
    elif content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        extension, mime = ".webp", "image/webp"
    else:
        raise web.HTTPBadRequest(text="Expected a JPG, PNG or WebP image")
    if field.headers.get("Content-Type") != mime:
        raise web.HTTPBadRequest(text="Image type does not match its contents")
    # Never use an untrusted filename for local storage.
    with tempfile.NamedTemporaryFile(suffix=extension) as image:
        image.write(content)
        image.flush()
        result = await loop.run_in_executor(None, client.upload_file, image.name)
    uploaded = (result or {}).get("data", {})
    if not uploaded.get("is_image") or not uploaded.get("url"):
        raise web.HTTPBadGateway(text="Image could not be uploaded")
    url = uploaded["url"]
    # Local Baserow serves trusted upload URLs over HTTP in the demo stack.
    if urlsplit(url).scheme not in {"http", "https"}:
        raise web.HTTPBadGateway(text="Invalid uploaded image URL")
    return url


def register_avatar_route(routes, client, profile_table):
    @routes.patch("/api/student/avatar")
    async def update_avatar(request):
        loop = asyncio.get_running_loop()
        table_id, mapping = profile_table(request["coordinator"])
        email = request["email"]
        multipart = request.content_type == "multipart/form-data"
        if not multipart:
            try:
                avatar = validate_avatar(await request.json())
            except ValueError:
                raise web.HTTPBadRequest(text="Invalid JSON")
        try:
            row_id = await loop.run_in_executor(None, client.get_row_id_by_attribute, table_id, "email", email, mapping)
            if not row_id:
                raise web.HTTPNotFound()
            if multipart:
                try:
                    avatar = await upload_avatar(request, client, loop)
                except (ValueError, AssertionError):
                    raise web.HTTPBadRequest(text="Invalid image upload")
            result = await loop.run_in_executor(None, client.update_row, table_id, row_id, {"avatar": avatar})
            if not result or result.get("error"):
                raise web.HTTPBadGateway(text="Avatar could not be saved")
        except web.HTTPException:
            raise
        except Exception:
            raise web.HTTPBadGateway(text="Avatar could not be saved")
        return web.json_response({"avatar": avatar})
