"""Principal verification and policy for the connector's HTTP routes."""
import asyncio
import hmac
import json
import os
import re
from pathlib import Path
from functools import lru_cache
from time import time

from aiohttp import web
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "182844940133-k5k1t08dposga8083n6nek2r4gjo9a7l.apps.googleusercontent.com")
DOMAINS = {"unipu.hr", "student.unipu.hr", "fipu.hr"}
WORKFLOW = {"alokacija_direct_b", "register_assignments", "alokacija_studenta", "odustajanje", "update_status_zahtjeva",
            "fetch_public_alokacije", "fetch_poslodavac_from_zadatak", "fill_application_form", "update_dnevnik"}
SELF = {"session", "fetch_public_alokacije", "update_avatar", "add_new_student", "set_student_internship_process",
        "store_pdf_dnevnik_prakse", "store_pdf_ispunjena_potvrda"}
COORDINATOR = {"delete_student", "add_new_company", "update_company", "update_voditelj_odobrio",
               "fetch_student_preferences_detailed", "fetch_public_alokacije", "complete_deletion",
               "direct_upload_to_baserow", "store_poslodavac_logo", "list_years", "get_year", "get_document"}


@lru_cache(maxsize=128)
def verified_claims(token):
    # A coordinator dashboard makes many requests with the same signed token.
    try:
        return verify_oauth2_token(token, Request(), GOOGLE_CLIENT_ID)
    except TransportError:
        raise web.HTTPServiceUnavailable(text="Google verification unavailable")
    except (ValueError, GoogleAuthError):
        raise web.HTTPUnauthorized()


def authenticated_email(headers, local_dev=False):
    local = headers.get("X-Local-User", "")
    if local_dev and local:
        if headers.get("Origin") not in (None, "http://localhost:5173", "http://127.0.0.1:5173"):
            raise web.HTTPForbidden()
        if re.fullmatch(r"(student|admin|smoke-[0-9a-f-]{36})@local\.test", local):
            return local
        raise web.HTTPUnauthorized()
    scheme, _, token = headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not token:
        raise web.HTTPUnauthorized()
    claims = verified_claims(token)
    if claims.get("exp", 0) <= time():
        raise web.HTTPUnauthorized()
    email = (claims.get("email") or "").lower()
    if claims.get("email_verified") is not True:
        raise web.HTTPUnauthorized()
    if email in {v.strip() for v in os.getenv("EXTRA_STUDENT_EMAILS", "").lower().split(",")} and email.endswith("@gmail.com"):
        return email
    if email.rpartition("@")[2] not in DOMAINS or claims.get("hd") not in DOMAINS:
        raise web.HTTPForbidden()
    return email


def service_token():
    return Path(os.environ["WORKFLOW_TOKEN_FILE"]).read_text().strip() if os.getenv("WORKFLOW_TOKEN_FILE") else os.environ["WORKFLOW_SERVICE_TOKEN"]


def verify_binding(data, email):
    key = service_token()
    message = json.dumps(["bind", email, data.get("process_instance_id"), data.get("Model_prakse"),
                          data.get("previous_instance_id")]).encode()
    expected = hmac.digest(key.encode(), message, "sha256").hex()
    if not hmac.compare_digest(str(data.get("binding", "")).encode(), expected.encode()):
        raise web.HTTPForbidden()


def access_control(local_dev):
    key = service_token()
    if len(key) < 32:
        raise ValueError("WORKFLOW_SERVICE_TOKEN requires at least 32 characters")
    admins = {v.strip().lower() for v in os.getenv("COORDINATOR_EMAILS", "").split(",") if v.strip()}

    @web.middleware
    async def authorize(request, handler):
        name = handler.__name__
        if request.method == "OPTIONS" or request.match_info.http_exception is not None or name in {"status_check", "add_new_assignment"}:
            return await handler(request)
        table = request.match_info.get("table_name", "").capitalize()
        catalog = name == "fetch_table_rows" and table in {"Poslodavac", "Zadaci_za_odabir"}
        request["coordinator"] = False
        if name in WORKFLOW and hmac.compare_digest(request.headers.get("X-Workflow-Token", "").encode(), key.encode()):
            request["workflow"] = True
        elif catalog and not any(h in request.headers for h in ("Authorization", "X-Local-User")):
            pass
        else:
            email = await asyncio.to_thread(authenticated_email, request.headers, local_dev)
            request["email"] = email
            request["coordinator"] = email in admins and (email.rpartition("@")[2] in DOMAINS or local_dev)
            own_diary = name == "fetch_table_rows" and table == "Dnevnik_prakse"
            admin_route = name in COORDINATOR or name == "fetch_table_rows"
            if not (name in SELF or catalog or own_diary or (request["coordinator"] and admin_route)):
                raise web.HTTPForbidden()
        response = await handler(request)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    return authorize
