"""Authenticated avatar updates; image URLs are stored, never fetched server-side."""
import asyncio
import os
from urllib.parse import urlsplit

from aiohttp import web
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "182844940133-k5k1t08dposga8083n6nek2r4gjo9a7l.apps.googleusercontent.com")


def authenticated_email(headers, local_dev=False):
    if local_dev and headers.get("X-Local-User") in {"student@local.test", "admin@local.test"}:
        return headers["X-Local-User"]
    scheme, _, token = headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not token:
        raise web.HTTPUnauthorized()
    try:
        claims = verify_oauth2_token(token, Request(), GOOGLE_CLIENT_ID)
    except (ValueError, GoogleAuthError):
        raise web.HTTPUnauthorized()
    email = claims.get("email", "")
    # Existing records are keyed by email; accept only Google-authoritative email claims.
    if not claims.get("email_verified") or not (claims.get("hd") or email.endswith("@gmail.com")):
        raise web.HTTPUnauthorized()
    return email


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


def register_avatar_route(routes, client, table_id, mapping, local_dev=False):
    @routes.patch("/api/student/avatar")
    async def update_avatar(request):
        loop = asyncio.get_running_loop()
        email = await loop.run_in_executor(None, authenticated_email, request.headers, local_dev)
        try:
            avatar = validate_avatar(await request.json())
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid JSON")
        try:
            row_id = await loop.run_in_executor(None, client.get_row_id_by_attribute, table_id, "email", email, mapping)
            if not row_id:
                raise web.HTTPNotFound()
            result = await loop.run_in_executor(None, client.update_row, table_id, row_id, {"avatar": avatar})
            if not result or result.get("error"):
                raise web.HTTPBadGateway(text="Avatar could not be saved")
        except web.HTTPException:
            raise
        except Exception:
            raise web.HTTPBadGateway(text="Avatar could not be saved")
        return web.json_response({"avatar": avatar})
