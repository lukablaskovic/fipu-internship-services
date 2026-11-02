import aiohttp
import re
import asyncio
import uuid
import os
import aiohttp_cors
import datetime as dt
import baserow as br
import json
from baserow import BaserowClient
import os, sys
from datetime import datetime
import hashlib
import tempfile
from dotenv import load_dotenv
import logging
from urllib.parse import quote
from access import access_control, verify_binding

load_dotenv()
routes = aiohttp.web.RouteTableDef()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = None

TABLES_MAP_DEV = {
    "Student": "186615",
    "Poslodavac": "186616",
    "Zadaci_za_odabir": "186618",
    "Student_preferencije": "186619",
    "Alokacija": "186614",
    "Prijavnica": "186620",
    "Dnevnik_prakse": "186621",
}

TABLES_MAP = {
    "Student": "845",
    "Poslodavac": "846",
    "Zadaci_za_odabir": "847",
    "Student_preferencije": "848",
    "Alokacija": "849",
    "Prijavnica": "850",
    "Dnevnik_prakse": "851",
    "Djelatnik": "852"
}


if br.LOCAL_CONFIG:
    TABLES_MAP = br.LOCAL_CONFIG["tables"]

client = BaserowClient()

from academic_years import register_academic_year_routes
register_academic_year_routes(routes, os.getenv("ACADEMIC_ARCHIVE_DIR"), bool(br.LOCAL_CONFIG) and os.getenv("ALLOW_LOCAL_DEMO") == "true")

from avatar import register_avatar_route
def profile_table(coordinator):
    if coordinator:
        return TABLES_MAP["Djelatnik"], br.Djelatnik_Mappings
    return TABLES_MAP["Student"], br.Student_Mappings


register_avatar_route(routes, client, profile_table)


def profile_row(email, coordinator=False):
    table_id, mapping = profile_table(coordinator)
    rows = client.get_table_rows(table_id, [
        f"filter__{mapping['email']}__equal={quote(email, safe='')}", "size=2"])
    if "data" not in rows:
        raise aiohttp.web.HTTPBadGateway(text="Cannot verify profile")
    found = rows["data"]["results"]
    if len(found) > 1 or (found and found[0].get("email", "").lower() != email):
        raise aiohttp.web.HTTPConflict(text="Ambiguous profile identity")
    return found[0] if found else None


def student_row(email):
    student = profile_row(email)
    if student and student.get("process_instance_id"):
        # A legacy link is evidence of ownership only when exactly one student has it.
        iid = quote(student["process_instance_id"], safe="")
        matches = client.get_table_rows(TABLES_MAP["Student"], [
            f"filter__{br.Student_Mappings['process_instance_id']}__equal={iid}", "size=2"])
        if "data" not in matches:
            raise aiohttp.web.HTTPBadGateway()
        if [r["id"] for r in matches["data"]["results"]] != [student["id"]]:
            raise aiohttp.web.HTTPConflict(text="Ambiguous internship owner")
    return student


@routes.get("/api/administratori")
async def list_administrators(request):
    return aiohttp.web.json_response(request["administrator_emails"])


@routes.get("/api/session")
async def session(request):
    # The deployed dashboard reads the signed-in profile from the legacy student key.
    student = profile_row(request["email"], True) if request["coordinator"] else student_row(request["email"])
    return aiohttp.web.json_response({"email": request["email"], "coordinator": request["coordinator"],
                                      "student": student})


def owned_row(request, table, field, value, mapping):
    student = student_row(request["email"])
    if not student:
        raise aiohttp.web.HTTPNotFound()
    row_id = client.get_row_id_by_attribute(TABLES_MAP[table], field, value, mapping)
    row = client.get_row(TABLES_MAP[table], row_id).get("data") if row_id else None
    if not row or [r["id"] for r in row.get("Student", [])] != [student["id"]] or not (
            row.get("process_instance_id") and row["process_instance_id"] == student.get("process_instance_id")):
        raise aiohttp.web.HTTPNotFound()
    return row


@routes.post("/api/student")
async def add_new_student(req):
    data = await req.json()

    email = req["email"]
    if data.get("email", email) != email:
        raise aiohttp.web.HTTPForbidden()
    if req["coordinator"]:
        staff = profile_row(email, True)
        if not staff:
            result = client.create_row(TABLES_MAP["Djelatnik"], {
                "email": email, "ime": data.get("ime"), "prezime": data.get("prezime"), "avatar": data.get("avatar"),
            })
            staff = (result or {}).get("data") or profile_row(email, True)
            if not staff:
                raise aiohttp.web.HTTPBadGateway(text="Cannot save staff profile")
        return aiohttp.web.json_response({"data": staff})

    jmbag = "0000000000"

    existing_email = client.get_row_id_by_attribute(
        TABLES_MAP["Student"], "email", email, br.Student_Mappings
    )

    if existing_email:
        logger.warning("Student with email %s already exists", email)
        return aiohttp.web.Response(
            text=json.dumps(
                {
                    "error": "Student with entered email already exists",
                    "status_code": 400,
                    "ERROR_CODE": "BCS_STUDENT_ALREADY_EXISTS",
                }
            ),
            content_type="application/json",
            status=400,
        )

    res = client.create_row(
        TABLES_MAP["Student"],
        {
            "JMBAG": jmbag,
            "ime": data.get("ime"),
            "prezime": data.get("prezime"),
            "email": email,
            "godina_studija": data.get("godina_studija"),
            "avatar": data.get("avatar"),
        },
    )

    if not res.get("error"):
        logger.info(
            "Successfully added new student with JMBAG %s and email %s", jmbag, email
        )
    else:
        logger.error(
            "Failed to add new student with JMBAG %s and email %s", jmbag, email
        )

    return aiohttp.web.Response(text=json.dumps(res), content_type="application/json")


@routes.patch("/api/student/process")
async def set_student_internship_process(req):
    data = await req.json()

    email = req["email"]
    if data.get("email", email) != email:
        raise aiohttp.web.HTTPForbidden()
    process_instance_id = data.get("process_instance_id")
    model_prakse = data.get("Model_prakse")
    JMBAG = data.get("JMBAG")
    godina_studija = data.get("godina_studija")

    student = student_row(email)
    if not student:
        raise aiohttp.web.HTTPNotFound(text="Student not found")
    student_id = student["id"]
    if "previous_instance_id" not in data:
        raise aiohttp.web.HTTPBadRequest()
    verify_binding(data, email)
    if not isinstance(JMBAG, str) or not JMBAG.isascii() or not JMBAG.isdigit() or len(JMBAG) != 10:
        raise aiohttp.web.HTTPBadRequest(text="Invalid JMBAG")
    duplicate = client.get_row_id_by_attribute(TABLES_MAP["Student"], "JMBAG", JMBAG, br.Student_Mappings)
    if duplicate is not None and duplicate != student_id:
        raise aiohttp.web.HTTPConflict(text="JMBAG already assigned")
    current_id = student.get("process_instance_id") or None
    if current_id not in (data["previous_instance_id"], process_instance_id):
        raise aiohttp.web.HTTPConflict(text="Student internship has changed")

    res = client.update_row(
        TABLES_MAP["Student"],
        student_id,
        {
            "process_instance_id": process_instance_id,
            "Model_prakse": model_prakse,
            "godina_studija": godina_studija,
            "JMBAG": JMBAG,
        },
    )

    if not res.get("error"):
        logger.info("Successfully updated student with email %s", email)
    else:
        logger.error("Failed to update student with email %s", email)

    return aiohttp.web.Response(text=json.dumps(res), content_type="application/json")


@routes.delete("/api/student/email/{value}")
async def delete_student(req):
    logger.info("delete_student function started")

    value = req.match_info.get("value", None)
    logger.info("Received email value: %s", value)

    if not value:
        logger.error("Invalid email value: %s", value)
        return aiohttp.web.Response(
            text=json.dumps({"error": "Invalid email."}),
            status=400,
            content_type="application/json",
        )

    try:
        res = client.delete_row_by_attribute(
            TABLES_MAP["Student"], "email", value, br.Student_Mappings
        )
        logger.info("Deleted student with email: %s, Result: %s", value, res)
    except Exception as e:
        logger.exception("Error while deleting student with email: %s", value)
        return aiohttp.web.Response(
            text=json.dumps({"error": "Internal server error."}),
            status=500,
            content_type="application/json",
        )

    return aiohttp.web.Response(text=json.dumps(res), content_type="application/json")


@routes.post("/api/zadaci_za_odabir")
async def add_new_assignment(req):
    try:
        data = await req.json()
        names = data.get("Poslodavac") if isinstance(data, dict) else None
        if not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str) or not names[0].strip():
            return aiohttp.web.json_response({"error": "Naziv organizacije je obavezan."}, status=400)
        company_name = names[0].strip()
        companies = client.get_table_rows(TABLES_MAP["Poslodavac"], [
            f"filter__{br.Poslodavac_Mappings['naziv']}__equal={quote(company_name, safe='')}", "size=1"
        ])
        if "data" not in companies:
            return aiohttp.web.json_response({"error": "Provjera organizacije nije uspjela."}, status=502)
        if companies["data"]["results"]:
            existing_company_id = companies["data"]["results"][0]["id"]
        else:
            details = data.get("new_company")
            new_company_data = {"naziv": company_name}
            for field in ("web", "direktor", "maticni_broj", "OIB", "adresa"):
                value = details.get(field) if isinstance(details, dict) else None
                if not isinstance(value, str) or not value.strip():
                    return aiohttp.web.json_response({"error": f"Obavezno polje: {field}"}, status=400)
                new_company_data[field] = value.strip()
            for field, pattern in (("OIB", r"[0-9]{11}"), ("maticni_broj", r"[0-9]+"), ("web", r"https?://[^\s/\\?#]+[^\s\\]*")):
                if not re.fullmatch(pattern, new_company_data[field], re.IGNORECASE):
                    return aiohttp.web.json_response({"error": f"Neispravan podatak: {field}"}, status=400)
            company_creation_response = client.create_row(
                table_id=TABLES_MAP["Poslodavac"], data=new_company_data
            )
            if "data" in company_creation_response:
                existing_company_id = company_creation_response["data"]["id"]
                logging.info("Created new company with ID: %s", existing_company_id)
            else:
                return aiohttp.web.json_response({"error": "Spremanje organizacije nije uspjelo."}, status=502)
        selection = data.get("selekcija")
        if isinstance(selection, bool):
            # Local text fields and Baserow boolean fields both accept these wire values.
            selection = "true" if selection else "false"
        new_row_data = dict(
            {
                "Poslodavac": [existing_company_id],
                "voditelj_odobrio": "u razradi",
                "poslodavac_email": data.get("poslodavac_email"),
                "broj_studenata": data.get("broj_studenata"),
                "opis_zadatka": data.get("opis_zadatka"),
                "preferirane_tehnologije": data.get("preferirane_tehnologije"),
                "potrebno_imati": data.get("potrebno_imati"),
                "trajanje_sati": data.get("trajanje_sati"),
                "preferencije_za_studenta": data.get("preferencije_za_studenta"),
                "lokacija": data.get("lokacija"),
                "zeljeno_okvirno_vrijeme_pocetka": data.get(
                    "zeljeno_okvirno_vrijeme_pocetka"
                ),
                "angazman_fipu": data.get("angazman_fipu"),
                "napomena": data.get("napomena"),
                "selekcija": selection,
                "proces_selekcije": data.get("proces_selekcije"),
            },
        )

        creation_response = client.create_row(
            TABLES_MAP["Zadaci_za_odabir"], new_row_data
        )

        if "data" in creation_response:
            new_row_id = creation_response["data"]["id"]

            formatted_id = f"Zadatak {new_row_id} - {company_name}"
            update_response = client.update_row(
                TABLES_MAP["Zadaci_za_odabir"], new_row_id, {"id_zadatak": formatted_id}
            )

            return aiohttp.web.json_response(update_response, status=200 if "data" in update_response else 502)
        return aiohttp.web.json_response({"error": "Spremanje zadatka nije uspjelo."}, status=502)
    except Exception:
        logger.exception("Assignment submission failed")
        return aiohttp.web.json_response({"error": "Prijava zadatka nije uspjela."}, status=500)


@routes.post("/api/poslodavac")
async def add_new_company(req):
    try:
        data = await req.json()
        company_name = data.get("naziv")

        logger.info("Received request to add new company: %s", company_name)

        existing_company_id = client.get_row_id_by_attribute(
            "Poslodavac", "naziv", company_name, br.Poslodavac_Mappings
        )
        if existing_company_id:
            logger.info("Company %s already exists", company_name)
            return aiohttp.web.Response(
                text=json.dumps({"error": "Company already exists"}),
                content_type="application/json",
            )

        new_company_data = {"naziv": company_name}
        creation_response = client.create_row(
            table_id=TABLES_MAP["Poslodavac"], data=new_company_data
        )

        logger.info("Successfully added new company: %s", company_name)
        return aiohttp.web.Response(
            text=json.dumps(creation_response), content_type="application/json"
        )
    except Exception as e:
        logger.exception("Error while adding new company: %s", str(e))
        return aiohttp.web.Response(
            text=json.dumps({"error": "Internal server error"}),
            content_type="application/json",
            status=500,
        )


@routes.patch("/api/poslodavac/update")
async def update_company(request):
    try:
        data = await request.json()
        logging.info(f"Request data: {data}")

        naziv = data.get("naziv")
        web = data.get("web")
        direktor = data.get("direktor")
        maticni_broj = data.get("maticni_broj")
        OIB = data.get("OIB")
        adresa = data.get("adresa")

        if not naziv:
            response = aiohttp.web.Response(
                text=json.dumps({"error": "naziv is required."}),
                status=400,
                content_type="application/json",
            )
            logging.error(f"Error: naziv is required. Response: {response}")
            return response

        table_id = TABLES_MAP["Poslodavac"]
        row_id = client.get_row_id_by_attribute(
            table_id, "naziv", naziv, br.Poslodavac_Mappings
        )

        if not row_id:
            response = aiohttp.web.Response(
                text=json.dumps({"error": "Company not found."}),
                status=404,
                content_type="application/json",
            )
            logging.error(f"Error: Company not found. Response: {response}")
            return response

        client.update_row(
            table_id,
            row_id,
            {
                "web": web,
                "direktor": direktor,
                "maticni_broj": maticni_broj,
                "OIB": OIB,
                "adresa": adresa,
            },
        )

        response = aiohttp.web.Response(
            text=json.dumps({"message": "Company updated successfully."}),
            status=200,
            content_type="application/json",
        )
        logging.info(f"Company updated successfully. Response: {response}")
        return response
    except Exception as e:
        response = aiohttp.web.Response(
            text=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )
        logging.error(f"Error: {str(e)}. Response: {response}")
        return response


@routes.patch("/api/zadaci_za_odabir/odobrenje")
async def update_voditelj_odobrio(req):
    try:
        data = await req.json()
        logging.info(f"Request data: {data}")

        voditelj_odobrio = data.get("voditelj_odobrio")
        if voditelj_odobrio is None:
            response = aiohttp.web.Response(
                status=400, text="Missing 'voditelj_odobrio' in request body."
            )
            logging.error(
                f"Error: Missing 'voditelj_odobrio' in request body. Response: {response}"
            )
            return response

        id_zadatak = data.get("id_zadatak")
        if not id_zadatak:
            response = aiohttp.web.Response(
                status=400, text="Missing 'id_zadatak' in request body."
            )
            logging.error(
                f"Error: Missing 'id_zadatak' in request body. Response: {response}"
            )
            return response

        row_id = client.get_row_id_by_attribute(
            TABLES_MAP["Zadaci_za_odabir"],
            "id_zadatak",
            id_zadatak,
            br.Zadaci_za_odabir_Mappings,
        )

        client.update_row(
            TABLES_MAP["Zadaci_za_odabir"],
            row_id,
            {"voditelj_odobrio": voditelj_odobrio},
        )
        response = aiohttp.web.Response(status=200, text="Updated successfully.")
        logging.info(f"Updated successfully. Response: {response}")
        return response

    except Exception as e:
        response = aiohttp.web.Response(status=500, text=f"Error: {e}")
        logging.error(f"Error: {e}. Response: {response}")
        return response


def create_workflow_row(request, table, key_field, data):
    key = request.headers.get("Idempotency-Key")
    if key is None:
        data[key_field] = str(uuid.uuid4())
    else:
        try:
            operation = uuid.UUID(key)
        except ValueError:
            raise aiohttp.web.HTTPBadRequest(text="Invalid operation key")
        row_key = str(uuid.uuid5(operation, table))
        field = getattr(br, table + "_Mappings")[key_field]
        result = client.get_table_rows(TABLES_MAP[table], [f"filter__{field}__equal={row_key}", "size=2"])
        if not result or "data" not in result:
            raise aiohttp.web.HTTPBadGateway(text="Could not verify previous write")
        rows = result["data"]["results"]
        if len(rows) > 1:
            raise aiohttp.web.HTTPConflict(text="Ambiguous previous write")
        data[key_field] = row_key
        if rows:
            return rows[0]
    created = client.create_row(TABLES_MAP[table], data)
    if created and "error" in created and created["status_code"] == 400:
        raise aiohttp.web.HTTPBadRequest(text="Baserow rejected row data")
    if not created or "data" not in created:
        raise aiohttp.web.HTTPBadGateway(text="Could not confirm row creation")
    return created["data"]


@routes.post("/api/student_preferencije")
async def register_assignments(req):
    logger.info("Route /api/student_preferencije accessed")

    data = await req.json()
    preference = create_workflow_row(req, "Student_preferencije", "id_preferencije", {
        "JMBAG": data.get("JMBAG"), "Student": data["Student"],
        "Prvi_odabir": data.get("Prvi_odabir"), "Drugi_odabir": data.get("Drugi_odabir"),
        "Treci_odabir": data.get("Treci_odabir"), "napomena": data.get("napomena"),
    })
    allocation = create_workflow_row(req, "Alokacija", "id_alokacija", {
        "JMBAG": data.get("JMBAG"), "Student_preferencije": [preference["id_preferencije"]],
        "Student": data["Student"], "datum_prijave": dt.datetime.now().isoformat() + "Z",
        "process_instance_id": data["id_instance"] or "", "frontend_url": data["_frontend_url"],
    })
    return aiohttp.web.json_response({"student_id": preference["id"], "id_preferencije": preference["id_preferencije"],
                                      "id_alokacija": allocation["id_alokacija"]})


@routes.get("/api/student_preferencije/detailed/{id_preferencije}")
async def fetch_student_preferences_detailed(request):
    id_preferencije = request.match_info.get("id_preferencije", None)

    logger.info(
        "Received request for student preferences with id_preferencije: %s",
        id_preferencije,
    )

    if not id_preferencije:
        logger.error("Missing id_preferencije in request")
        return aiohttp.web.Response(
            text=json.dumps({"error": "Missing id_preferencije."}),
            status=400,
            content_type="application/json",
        )

    table_id = TABLES_MAP["Student_preferencije"]
    row_id = client.get_row_id_by_attribute(
        table_id, "id_preferencije", id_preferencije, br.Student_preferencije_Mappings
    )

    if not row_id:
        logger.warning("Student with id_preferencije: %s not found", id_preferencije)
        return aiohttp.web.Response(
            text=json.dumps({"error": "Student not found."}),
            status=404,
            content_type="application/json",
        )

    student_preferences = client.get_row(table_id, row_id)

    if "error" in student_preferences:
        logger.error(
            "Error fetching student preferences: %s", student_preferences["error"]
        )
        return aiohttp.web.Response(
            text=json.dumps(student_preferences),
            status=student_preferences["status_code"],
            content_type="application/json",
        )

    for preference_key in ["Prvi_odabir", "Drugi_odabir", "Treci_odabir"]:
        preference = student_preferences["data"].get(preference_key)
        if preference:
            zadatak_id = preference[0]["id"] if preference else None
            if zadatak_id:
                zadatak_data = client.get_row(
                    TABLES_MAP["Zadaci_za_odabir"], zadatak_id
                )
                preference[0]["details"] = (
                    zadatak_data["data"] if "data" in zadatak_data else None
                )

    logger.info(
        "Successfully fetched student preferences for id_preferencije: %s",
        id_preferencije,
    )
    return aiohttp.web.Response(
        text=json.dumps(student_preferences),
        content_type="application/json",
    )


@routes.post("/api/alokacija")
async def alokacija_studenta(request):
    logger.info("Received request for student allocation")

    # Parse incoming request data
    data = await request.json()
    student = data.get("Student")
    id_alokacija = data.get("id_alokacija")
    alocirani_zadatak_id = data.get("Alocirani_zadatak")
    status_zahtjeva = "evaluacija_u_tijeku"

    if not student or not alocirani_zadatak_id:
        logger.error("Missing JMBAG or Alocirani zadatak in request data")
        return aiohttp.web.Response(
            text=json.dumps({"error": "Missing JMBAG or Alocirani zadatak"}),
            status=400,
            content_type="application/json",
        )

    if not id_alokacija:
        logger.error("Missing id_alokacija in request data")
        return aiohttp.web.Response(
            text=json.dumps(
                {"error": f"Alokacija ${id_alokacija} not found in Alokacija table."}
            ),
            status=404,
            content_type="application/json",
        )

    update_data = {
        "Alocirani_zadatak": [alocirani_zadatak_id],
        "status_zahtjeva": status_zahtjeva,
    }

    row_id = client.get_row_id_by_attribute(
        TABLES_MAP["Alokacija"], "id_alokacija", id_alokacija, br.Alokacija_Mappings
    )

    update_response = client.update_row(
        table_id=TABLES_MAP["Alokacija"], row_id=row_id, data=update_data
    )

    if "error" in update_response:
        logger.error("Error updating student allocation: %s", update_response["error"])
        return aiohttp.web.Response(
            text=json.dumps(update_response["error"]),
            status=update_response["status_code"],
            content_type="application/json",
        )

    logger.info("Successfully allocated student with JMBAG: %s", student)
    return aiohttp.web.Response(
        text=json.dumps(update_response), content_type="application/json"
    )


@routes.post("/api/alokacija/direct")
async def alokacija_direct_b(request):
    logger.info("Route /api/alokacija accessed")

    data = await request.json()
    iid = str(uuid.UUID(data["id_instance"]))
    table = TABLES_MAP["Alokacija"]
    previous = client.get_table_rows(table, [
        f"filter__{br.Alokacija_Mappings['process_instance_id']}__equal={iid}", "size=2"])
    if "data" not in previous:
        raise aiohttp.web.HTTPBadGateway()
    rows = previous["data"]["results"]
    if rows:
        if len(rows) != 1 or [r["id"] for r in rows[0]["Student"]] != data["Student"]:
            raise aiohttp.web.HTTPConflict(text="Ambiguous allocation")
        return aiohttp.web.json_response({"id_alokacija": rows[0]["id_alokacija"]})

    result = client.create_row(table, {
        "id_alokacija": str(uuid.uuid4()), "JMBAG": data["JMBAG"], "Student": data["Student"],
        "Student_preferencije": [], "datum_prijave": dt.datetime.now().isoformat() + "Z",
        "process_instance_id": iid, "frontend_url": os.environ["FRONTEND_URL"], "Napomena": data.get("Napomena"),
    })
    if not result or "data" not in result:
        raise aiohttp.web.HTTPBadGateway(text="Could not create allocation")
    return aiohttp.web.json_response({"id_alokacija": result["data"]["id_alokacija"]})


@routes.post("/api/odustajanje")
async def odustajanje(request):
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise aiohttp.web.HTTPBadRequest(text="Withdrawal requires JSON")
    instance_id = data.get("id_instance") if isinstance(data, dict) else None
    try:
        instance_id = str(uuid.UUID(instance_id))
    except (ValueError, TypeError, AttributeError):
        raise aiohttp.web.HTTPBadRequest(text="id_instance must be a UUID")
    table_id = TABLES_MAP["Alokacija"]
    rows = client.get_table_rows(table_id, [
        f"filter__{br.Alokacija_Mappings['process_instance_id']}__equal={instance_id}",
        "size=200",
    ])
    if "error" in rows:
        raise aiohttp.web.HTTPBadGateway(text="Could not read allocations")
    if rows["data"].get("next"):
        raise aiohttp.web.HTTPConflict(text="Unexpected number of allocations")
    for row in rows["data"]["results"]:
        result = client.update_row(table_id, row["id"], {"status_zahtjeva": "student_odustao"})
        if "error" in result:
            raise aiohttp.web.HTTPBadGateway(text="Could not update allocation")
    return aiohttp.web.json_response({"status": "OK"})


@routes.patch("/api/status_zahtjeva")
async def update_status_zahtjeva(request):
    logger.info("Received request to update status zahtjeva")

    data = await request.json()

    table_id = TABLES_MAP["Alokacija"]

    id_alokacija = data.get("id_alokacija")
    novi_status_zahtjeva = data.get("status_zahtjeva")

    # B
    Alocirani_zadatak = data.get("Alocirani_zadatak")

    if not id_alokacija or not novi_status_zahtjeva:
        logger.error("Missing id_alokacija or status_zahtjeva in request data")
        return aiohttp.web.Response(
            text=json.dumps({"error": "Missing id_alokacija or status_zahtjeva"}),
            status=400,
            content_type="application/json",
        )

    row_id = client.get_row_id_by_attribute(
        table_id, "id_alokacija", id_alokacija, br.Alokacija_Mappings
    )
    if not row_id:
        logger.warning("Alokacija with id_alokacija: %s not found", id_alokacija)
        return aiohttp.web.Response(
            text=json.dumps({"error": "Alokacija not found"}),
            status=404,
            content_type="application/json",
        )

    if not Alocirani_zadatak:
        update_response = client.update_row(
            table_id, row_id, {"status_zahtjeva": novi_status_zahtjeva}
        )
    else:
        update_response = client.update_row(
            table_id,
            row_id,
            {
                "status_zahtjeva": novi_status_zahtjeva,
                "Alocirani_zadatak": Alocirani_zadatak,
            },
        )

    if "error" in update_response:
        logger.error("Error updating status zahtjeva: %s", update_response["error"])
        return aiohttp.web.Response(
            text=json.dumps(update_response["error"]),
            status=update_response["status_code"],
            content_type="application/json",
        )

    logger.info(
        "Successfully updated status zahtjeva for id_alokacija: %s", id_alokacija
    )
    return aiohttp.web.Response(
        text=json.dumps(update_response), content_type="application/json"
    )


@routes.get("/api/alokacija/public")
async def fetch_public_alokacije(request):
    logger.info("Received request to fetch public alokacije")

    table_id = TABLES_MAP["Alokacija"]

    # Extract the id_alokacija value from query parameters
    id_alokacija = request.query.get("id_alokacija", None)

    def format_output(row):
        zadatak_details = None
        try:
            zadatak_id = row.get("Alocirani_zadatak", None)

            if zadatak_id and len(zadatak_id) > 0:
                zadatak_data = client.get_row(
                    TABLES_MAP["Zadaci_za_odabir"], zadatak_id[0]["id"]
                )

                if "data" in zadatak_data:
                    zadatak_details = zadatak_data["data"]
            else:
                logger.debug("zadatak_id is None or empty")

        except Exception as e:
            logger.error("Error occurred: %s", e)

        return {
            "JMBAG": row.get("JMBAG", ""),
            "id_alokacija": row.get("id_alokacija", ""),
            "Alocirani_zadatak": (
                zadatak_id[0]["value"] if zadatak_id and len(zadatak_id) > 0 else None
            ),
            "opis_zadatka": (
                zadatak_details.get("opis_zadatka") if zadatak_details else None
            ),
            "poslodavac_email": (
                zadatak_details.get("poslodavac_email") if zadatak_details else None
            ),
            "poslodavac_naziv": (
                zadatak_details.get("Poslodavac")[0]["value"]
                if zadatak_details
                else None
            ),
            "status_zahtjeva": row.get("status_zahtjeva", ""),
            "popunjena_prijavnica": row.get("popunjena_prijavnica", ""),
            "predan_dnevnik_prakse": row.get("predan_dnevnik_prakse", ""),
            "Dnevnik_prakse": row.get("Dnevnik_prakse", ""),
            "Prijavnica": row.get("Prijavnica", ""),
            "process_instance_id": row.get("process_instance_id", ""),
        }

    if not request["coordinator"] and not request.get("workflow"):
        if id_alokacija is not None:
            row = owned_row(request, "Alokacija", "id_alokacija", id_alokacija, br.Alokacija_Mappings)
            return aiohttp.web.json_response(format_output(row))
        student = student_row(request["email"])
        if not student or not student.get("process_instance_id"):
            return aiohttp.web.json_response([])
        rows = client.get_table_rows(table_id, [
            f"filter__{br.Alokacija_Mappings['process_instance_id']}__equal={quote(student['process_instance_id'], safe='')}"])
        if "data" not in rows:
            raise aiohttp.web.HTTPBadGateway()
        return aiohttp.web.json_response([format_output(row) for row in rows["data"]["results"]
            if row.get("process_instance_id") == student["process_instance_id"] and
            [r["id"] for r in row.get("Student", [])] == [student["id"]]])

    if id_alokacija is not None:
        logger.debug("id_alokacija: %s", id_alokacija)

        # Get the student alokacija record ID using JMBAG
        row_id = client.get_row_id_by_attribute(
            table_id, "id_alokacija", id_alokacija, br.Alokacija_Mappings
        )

        if not row_id:
            logger.warning("Alokacija with id_alokacija: %s not found", id_alokacija)
            return aiohttp.web.Response(
                text=json.dumps({"error": "Alokacija not found"}),
                status=404,
                content_type="application/json",
            )

        # Fetch the specific student alokacija using row ID
        alokacija_data = client.get_row(table_id, row_id)
        if "error" in alokacija_data:
            logger.error("Error fetching alokacija data: %s", alokacija_data["error"])
            return aiohttp.web.Response(
                text=json.dumps(alokacija_data["error"]),
                status=alokacija_data["status_code"],
                content_type="application/json",
            )

        # Return the specific student alokacija
        logger.info("Successfully fetched alokacija for id_alokacija: %s", id_alokacija)
        return aiohttp.web.Response(
            text=json.dumps(format_output(alokacija_data["data"])),
            content_type="application/json",
        )

    else:
        # If no JMBAG was provided, fetch all rows
        alokacije_rows = client.get_table_rows(table_id)
        if "error" in alokacije_rows:
            logger.error("Error fetching alokacije rows: %s", alokacije_rows["error"])
            return aiohttp.web.Response(
                text=json.dumps(alokacije_rows["error"]),
                status=alokacije_rows["status_code"],
                content_type="application/json",
            )
        if not alokacije_rows["data"]:
            logger.warning("No data found for alokacije")
            return aiohttp.web.Response(
                text=json.dumps({"message": "Nema podataka."}),
                status=404,
                content_type="application/json",
            )

        # Format and return all alokacije
        results = [format_output(row) for row in alokacije_rows["data"]["results"]]
        logger.info("Successfully fetched all public alokacije")
        return aiohttp.web.Response(
            text=json.dumps(results), content_type="application/json"
        )


@routes.get("/api/poslodavac/zadatak")
async def fetch_poslodavac_from_zadatak(request):
    logger.info("Received request to fetch poslodavac from zadatak")

    # Extract the id_zadatak value from query parameters
    id_zadatak = request.query.get("id_zadatak", None)

    if not id_zadatak:
        logger.error("Missing id_zadatak in request")
        return aiohttp.web.Response(
            text=json.dumps({"error": "Missing id_zadatak."}),
            status=400,
            content_type="application/json",
        )
    else:
        table_id = TABLES_MAP["Zadaci_za_odabir"]
        row_id = client.get_row_id_by_attribute(
            table_id, "id_zadatak", id_zadatak, br.Zadaci_za_odabir_Mappings
        )

        if not row_id:
            logger.warning("Zadatak with id_zadatak: %s not found", id_zadatak)
            return aiohttp.web.Response(
                text=json.dumps({"error": "Zadatak not found."}),
                status=404,
                content_type="application/json",
            )

        zadatak_data = client.get_row(table_id, row_id)

        if "error" in zadatak_data:
            logger.error("Error fetching zadatak data: %s", zadatak_data["error"])
            return aiohttp.web.Response(
                text=json.dumps(zadatak_data),
                status=zadatak_data["status_code"],
                content_type="application/json",
            )

        poslodavac = zadatak_data["data"]["Poslodavac"][0]["value"]
        logger.info(
            "Successfully fetched poslodavac for zadatak with id_zadatak: %s",
            id_zadatak,
        )
        return aiohttp.web.Response(
            text=json.dumps(
                {
                    "poslodavac_naziv": poslodavac,
                    "opis_zadatka": zadatak_data["data"]["opis_zadatka"],
                    "poslodavac_email": zadatak_data["data"]["poslodavac_email"],
                }
            ),
            content_type="application/json",
        )


@routes.post("/api/prijavnica")
async def fill_application_form(request):
    logger.info("Received request to fill application form")

    data = await request.json()

    prijavnica_data = {
        "Student": [data["Student"][0]],
        "student_OIB": data["student_OIB"],
        "student_broj_mobitela": data["student_broj_mobitela"],
        "student_email": data["student_email"],
        "mentor_ime": data["mentor_ime"],
        "mentor_prezime": data["mentor_prezime"],
        "mentor_email": data["mentor_email"],
        "detaljan_opis_zadatka": data["detaljan_opis_zadatka"],
        "dogovoreni_broj_sati": data["dogovoreni_broj_sati"],
        "pocetak_prakse": data["pocetak_prakse"],
        "kraj_prakse": data["kraj_prakse"],
        "alokacija_potvrda": data["alokacija_potvrda"],
        "kontakt_potvrda": data["kontakt_potvrda"],
        "Poslodavac": [data["Poslodavac"]],
        "mjesto_izvrsavanja": data["mjesto_izvrsavanja"],
        "Alokacija": [data["id_alokacija"]],
        "process_instance_id": data["id_instance"],
    }

    application = create_workflow_row(request, "Prijavnica", "id_prijavnica", prijavnica_data)

    id_alokacija = data["id_alokacija"]

    if not id_alokacija:
        logger.error("Missing id_alokacija in request data")
        return aiohttp.web.Response(
            text=json.dumps(
                {"error": f"Alokacija ID ${id_alokacija} not found in Alokacija table."}
            ),
            status=404,
            content_type="application/json",
        )

    update_data = {"popunjena_prijavnica": True}

    row_id = client.get_row_id_by_attribute(
        TABLES_MAP["Alokacija"], "id_alokacija", id_alokacija, br.Alokacija_Mappings
    )

    update_response = client.update_row(TABLES_MAP["Alokacija"], row_id, update_data)

    if "error" in update_response:
        logger.error("Error updating alokacija: %s", update_response["error"])
        return aiohttp.web.Response(
            text=json.dumps(update_response["error"]),
            status=update_response["status_code"],
            content_type="application/json",
        )

    dnevnik_data = {
        "Alokacija": [data["id_alokacija"]],
        "Student": [data["Student"][0]],
        "id_prijavnica": [application["id_prijavnica"]],
        "process_instance_id": data["id_instance"],
    }

    diary = create_workflow_row(request, "Dnevnik_prakse", "id_dnevnik_prakse", dnevnik_data)
    return aiohttp.web.json_response({"id_prijavnica": application["id_prijavnica"],
                                      "id_dnevnik_prakse": diary["id_dnevnik_prakse"]})


@routes.post("/api/dnevnik")
async def update_dnevnik(request):
    logger.info("Received request to update dnevnik")

    data = await request.json()

    id_dnevnik_prakse = data.get("id_dnevnik_prakse")
    nastavak_radnog_odnosa = data.get("nastavak_radnog_odnosa")
    prijavljen_rok = data.get("prijavljen_rok")

    if (
        not id_dnevnik_prakse
        or nastavak_radnog_odnosa is None
        or prijavljen_rok is None
    ):
        logger.error("Incomplete data in request")
        return aiohttp.web.Response(
            text=json.dumps({"error": "Incomplete data."}),
            status=400,
            content_type="application/json",
        )

    table_id = TABLES_MAP["Dnevnik_prakse"]
    row_id = client.get_row_id_by_attribute(
        table_id, "id_dnevnik_prakse", id_dnevnik_prakse, br.Dnevnik_prakse_Mappings
    )

    if not row_id:
        logger.warning(
            "Dnevnik prakse with id_dnevnik_prakse: %s not found", id_dnevnik_prakse
        )
        return aiohttp.web.Response(
            text=json.dumps({"error": "Dnevnik prakse not found."}),
            status=404,
            content_type="application/json",
        )

    update_data = {
        "nastavak_radnog_odnosa": nastavak_radnog_odnosa,
        "prijavljen_rok": prijavljen_rok,
    }
    update_response = client.update_row(table_id, row_id, update_data)

    if "error" in update_response:
        logger.error("Error updating dnevnik prakse: %s", update_response["error"])
        return aiohttp.web.Response(
            text=json.dumps(update_response["error"]),
            status=update_response["status_code"],
            content_type="application/json",
        )

    id_alokacija = data.get("id_alokacija")
    row_id = client.get_row_id_by_attribute(
        TABLES_MAP["Alokacija"], "id_alokacija", id_alokacija, br.Alokacija_Mappings
    )

    if not row_id:
        logger.warning("Alokacija with id_alokacija: %s not found", id_alokacija)
        return aiohttp.web.Response(
            text=json.dumps(
                {
                    "error": f"Student ${data['id_instance'],} not found in Alokacija table."
                }
            ),
            status=404,
            content_type="application/json",
        )

    update_data = {"predan_dnevnik_prakse": True}
    update_response = client.update_row(TABLES_MAP["Alokacija"], row_id, update_data)

    if "error" in update_response:
        logger.error("Error updating alokacija: %s", update_response["error"])
        return aiohttp.web.Response(
            text=json.dumps(update_response["error"]),
            status=update_response["status_code"],
            content_type="application/json",
        )

    logger.info(
        "Successfully updated dnevnik for id_dnevnik_prakse: %s", id_dnevnik_prakse
    )
    return aiohttp.web.Response(
        text=json.dumps(update_response), content_type="application/json"
    )


# Generic GET route for fetching rows from a table
@routes.get("/api/{table_name}")
async def fetch_table_rows(request):
    logger.info("Received request to fetch table rows")

    queryParams = []

    table_name = request.match_info.get("table_name", None).capitalize()
    if not table_name or table_name not in TABLES_MAP:
        logger.error("Invalid table name: %s", table_name)
        return aiohttp.web.Response(
            text=json.dumps({"error": "Invalid table name."}),
            status=400,
            content_type="application/json",
        )
    if table_name == "Dnevnik_prakse" and not request["coordinator"]:
        value = request.query.get("search")
        if not value or set(request.query) != {"search"}:
            raise aiohttp.web.HTTPForbidden()
        row = owned_row(request, table_name, "id_dnevnik_prakse", value, br.Dnevnik_prakse_Mappings)
        return aiohttp.web.json_response({"data": {"count": 1, "results": [row]}})
    table_id = TABLES_MAP[table_name]

    search = request.rel_url.query.get("search")
    if search:
        term = quote(search, safe="")
        if not request["coordinator"] and table_name in {"Poslodavac", "Zadaci_za_odabir"}:
            field = br.Poslodavac_Mappings["naziv"] if table_name == "Poslodavac" else br.Zadaci_za_odabir_Mappings["id_zadatak"]
            queryParams.append(f"filter__{field}__contains={term}")
        else:
            queryParams.append(f"search={term}")

    public = not request["coordinator"]
    def project(row):
        if not public:
            return row
        if table_name == "Poslodavac":
            keys = ("naziv", "web", "direktor", "maticni_broj", "OIB", "adresa", "logo")
        else:
            if (row.get("voditelj_odobrio") or {}).get("value") != "odobreno":
                return None
            keys = ("id_zadatak", "voditelj_odobrio", "Poslodavac", "poslodavac_email", "broj_studenata",
                    "dostupno_mjesta", "preferirane_tehnologije", "preferencije_za_studenta", "potrebno_imati",
                    "trajanje_sati", "zeljeno_okvirno_vrijeme_pocetka", "angazman_fipu", "napomena",
                    "opis_zadatka", "selekcija", "proces_selekcije", "lokacija")
        return {k: row[k] for k in ("id", *keys) if k in row}

    row_id = request.rel_url.query.get("id")
    if row_id:
        if not row_id.isascii() or not row_id.isdigit() or int(row_id) < 1:
            raise aiohttp.web.HTTPBadRequest()
        row_data = client.get_row(table_id, row_id)
        if "error" in row_data:
            logger.error("Error fetching row data: %s", row_data["error"])
            return aiohttp.web.Response(
                text=json.dumps(row_data["error"]),
                status=row_data["status_code"],
                content_type="application/json",
            )
        row_data["data"] = project(row_data["data"])
        if row_data["data"] is None:
            raise aiohttp.web.HTTPNotFound()
        logger.info(
            "Successfully fetched row data for table: %s, row_id: %s",
            table_name,
            row_id,
        )
        return aiohttp.web.Response(
            text=json.dumps(row_data), content_type="application/json"
        )

    rows = client.get_table_rows(table_id, queryParams)
    if "error" in rows:
        logger.error("Error fetching rows: %s", rows["error"])
        return aiohttp.web.Response(
            text=json.dumps(rows["error"]),
            status=rows["status_code"],
            content_type="application/json",
        )
    if public:
        results = [item for row in rows["data"]["results"] if (item := project(row)) is not None]
        rows["data"] = {"count": len(results), "results": results}
    logger.info("Successfully fetched rows for table: %s", table_name)
    return aiohttp.web.Response(text=json.dumps(rows), content_type="application/json")


@routes.post("/api/direct-file-upload")
async def direct_upload_to_baserow(request):
    logger.info("Received request to upload file directly to Baserow")

    reader = await request.multipart()

    field = await reader.next()

    assert field.name == "file"
    file_content = await field.read(decode=True)
    file_name = uuid.uuid4().hex + os.path.splitext(os.path.basename(field.filename))[1][:12]

    temp_dir = tempfile.gettempdir()
    temp_filepath = os.path.join(temp_dir, file_name)

    with open(temp_filepath, "wb") as tmpfile:
        tmpfile.write(file_content)

    try:
        upload_result = client.upload_file(temp_filepath)

        os.remove(temp_filepath)

        if "error" in upload_result:
            logger.error("Error uploading file: %s", upload_result["error"])
            return aiohttp.web.Response(
                text=str(upload_result["error"]),
                status=upload_result.get("status_code", 500),
            )
        else:
            logger.info("Successfully uploaded file: %s", file_name)
            return aiohttp.web.Response(text=json.dumps(upload_result), status=200)

    except Exception as e:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        logger.error("An error occurred while uploading file: %s", str(e))
        return aiohttp.web.Response(text=f"An error occurred: {str(e)}", status=500)


# Store files on Baserow server
@routes.post("/api/file-upload")
async def upload_to_baserow(request):
    logger.info("Received request to upload file to Baserow")

    if "Content-Type" not in request.headers:
        logger.error("Content-Type header not present.")
        return {"error": "Content-Type header not present."}, 400

    logger.info(f"++++++++Content-Type: {request.headers.get('Content-Type')}")

    reader = await request.multipart()

    field = await reader.next()

    assert field.name == "file"

    # Generate a unique filename using UUID
    filename = f"{uuid.uuid4()}_{os.path.basename(field.filename)}"
    file_content = await field.read(decode=True)

    with open(filename, "wb") as f:
        f.write(file_content)

    loop = asyncio.get_event_loop()
    client = BaserowClient()
    response = await loop.run_in_executor(None, client.upload_file, filename)
    os.remove(filename)

    if "error" in response:
        logger.error("Error uploading file: %s", response["error"])
    else:
        logger.info("Successfully uploaded file: %s", filename)

    # Return the response data and status
    return response, 200


import asyncio


async def store_file_in_baserow(
    request, table_id, field_name, field_value, table_mappings, file_field_name, row_id=None
):
    logger.info("Received request to store file in Baserow")

    # Retry logic
    retries = 3
    for attempt in range(1, retries + 1):
        logger.info(f"Attempt {attempt} to store file in Baserow")

        response, status = await upload_to_baserow(request)

        if status == 200:
            logger.info("File uploaded successfully")

            file_data = response["data"]
            baserow_data = {
                file_field_name: [
                    {
                        "url": file_data["url"],
                        "thumbnails": file_data["thumbnails"],
                        "name": file_data["name"],
                        "size": file_data["size"],
                        "mime_type": file_data["mime_type"],
                        "is_image": file_data["is_image"],
                        "image_width": file_data["image_width"],
                        "image_height": file_data["image_height"],
                        "uploaded_at": file_data["uploaded_at"],
                    }
                ]
            }

            client = BaserowClient()
            if row_id is None:
                row_id = client.get_row_id_by_attribute(table_id, field_name, field_value, table_mappings)

            if not row_id:
                logger.warning(
                    "Row not found in table_id: %s, field_name: %s, field_value: %s",
                    table_id,
                    field_name,
                    field_value,
                )
                return aiohttp.web.json_response(
                    {"error": "Row not found."}, status=404
                )

            result = client.update_row(table_id, row_id, baserow_data)
            if "error" in result:
                logger.error("Error updating row in Baserow: %s", result["error"])
            else:
                logger.info("Successfully stored file in Baserow")

            return aiohttp.web.json_response(result)

        logger.error(
            f"Error uploading file to Baserow on attempt {attempt}: {response}"
        )

        if attempt < retries:
            await asyncio.sleep(2)

    return aiohttp.web.json_response(
        {"error": "Failed to upload file after multiple attempts."}, status=500
    )


@routes.post("/api/upload/poslodavac-logo/{naziv}")
async def store_poslodavac_logo(request):
    logger.info("Received request to store employer logo")

    naziv = request.match_info.get("naziv")
    if not naziv:
        logger.error("Missing employer name in request")
        return aiohttp.web.json_response(
            {"error": "Missing employer name."}, status=400
        )

    response = await store_file_in_baserow(
        request,
        TABLES_MAP["Poslodavac"],
        "naziv",
        naziv,
        br.Poslodavac_Mappings,
        "logo",
    )

    if "error" in response:
        logger.error("Error storing employer logo: %s", response["error"])

    return response


@routes.put("/api/diary/{diary_id}/{column}")
async def store_diary_document(request):
    column = request.match_info["column"]
    if column not in {"dnevnik_prakse_upload", "ispunjena_potvrda_upload"}:
        raise aiohttp.web.HTTPNotFound()
    limit = 10 * 1024 * 1024
    content = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        content.extend(chunk)
        if len(content) > limit:
            raise aiohttp.web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(content))
    if not content.startswith(b"%PDF-"):
        raise aiohttp.web.HTTPBadRequest(text="Expected PDF")
    table = TABLES_MAP["Dnevnik_prakse"]
    row_id = client.get_row_id_by_attribute(table, "id_dnevnik_prakse", request.match_info["diary_id"], br.Dnevnik_prakse_Mappings)
    if not row_id:
        raise aiohttp.web.HTTPNotFound()
    row = client.get_row(table, row_id).get("data")
    if row is None:
        raise aiohttp.web.HTTPBadGateway()
    # A stable stored name lets a retry reuse the first successfully linked PDF.
    name = column + "-" + hashlib.sha256(content).hexdigest() + ".pdf"
    if not any(f.get("visible_name") == name for f in row.get(column) or []):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(content)
            pdf.flush()
            uploaded = (await asyncio.to_thread(client.upload_file, pdf.name)).get("data", {})
        if not uploaded.get("name"):
            raise aiohttp.web.HTTPBadGateway(text="Could not store PDF")
        result = client.update_row(table, row_id, {column: [{"name": uploaded["name"], "visible_name": name}]})
        if "data" not in result:
            raise aiohttp.web.HTTPBadGateway(text="Could not link PDF")
    return aiohttp.web.json_response({"status": "OK"})


@routes.delete("/api/complete-deletion")
async def complete_deletion(request):
    logger.info("Received request to delete data")
    data = await request.json()

    process_instance_id = data.get("process_instance_id")
    id_preferencije = data.get("id_preferencije")
    id_alokacija = data.get("id_alokacija")
    id_dnevnik_prakse = data.get("id_dnevnik_prakse")
    id_prijavnica = data.get("id_prijavnica")

    if not process_instance_id:
        logger.error("Missing process_instance_id in request data")
        return aiohttp.web.Response(
            text=json.dumps({"error": "Missing process_instance_id"}),
            status=400,
            content_type="application/json",
        )

    try:
        results = []

        # Always attempt to delete Student if process_instance_id is provided
        res_Student_del = client.delete_row_by_attribute(
            TABLES_MAP["Student"],
            "process_instance_id",
            process_instance_id,
            br.Student_Mappings,
        )
        results.append(res_Student_del)

        # Conditionally delete other records if corresponding IDs are provided
        if id_preferencije:
            res_Student_preferencije_del = client.delete_row_by_attribute(
                TABLES_MAP["Student_preferencije"],
                "id_preferencije",
                id_preferencije,
                br.Student_preferencije_Mappings,
            )
            results.append(res_Student_preferencije_del)

        if id_alokacija:
            res_Alokacija_del = client.delete_row_by_attribute(
                TABLES_MAP["Alokacija"],
                "id_alokacija",
                id_alokacija,
                br.Alokacija_Mappings,
            )
            results.append(res_Alokacija_del)

        if id_dnevnik_prakse:
            res_Dnevnik_prakse_del = client.delete_row_by_attribute(
                TABLES_MAP["Dnevnik_prakse"],
                "id_dnevnik_prakse",
                id_dnevnik_prakse,
                br.Dnevnik_prakse_Mappings,
            )
            results.append(res_Dnevnik_prakse_del)

        if id_prijavnica:
            res_Prijavnica_del = client.delete_row_by_attribute(
                TABLES_MAP["Prijavnica"],
                "id_prijavnica",
                id_prijavnica,
                br.Prijavnica_Mappings,
            )
            results.append(res_Prijavnica_del)

        # Check for errors only if Student, Student_preferencije, or Alokacija deletions fail
        errors = [res for res in results if "error" in res]

        if errors:
            logger.error("Error deleting data: %s", errors)
            return aiohttp.web.Response(
                text=json.dumps({"error": "Some deletions failed", "details": errors}),
                status=500,
                content_type="application/json",
            )

        logger.info(
            "Successfully deleted relevant data for instance: %s", process_instance_id
        )
        return aiohttp.web.Response(
            text=json.dumps(
                {
                    "message": f"Successfully deleted relevant data for instance {process_instance_id}"
                }
            ),
            content_type="application/json",
        )

    except Exception as e:
        logger.error("An error occurred: %s", str(e))
        return aiohttp.web.Response(
            text=json.dumps({"error": "Internal Server Error"}),
            status=500,
            content_type="application/json",
        )


@routes.get("/status")
async def status_check(request):
    """
    Health check endpoint to monitor the status of the service.
    Returns a 200 status code with a JSON payload if the service is running.
    """
    return aiohttp.web.json_response(
        {
            "microservice": "baserow-connector-service",
            "status": "OK",
            "message": "Service is running",
            "status_check_timestamp": datetime.now().isoformat(),
        },
        status=200,
    )


def run():
    global app

    app = aiohttp.web.Application(middlewares=[access_control(bool(br.LOCAL_CONFIG) and os.getenv("ALLOW_LOCAL_DEMO") == "true")])
    app.add_routes(routes)
    cors = aiohttp_cors.setup(
        app,
        defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*",
            )
        },
    )

    for route in list(app.router.routes()):
        cors.add(route)

    return app


async def serve():
    return run()


if __name__ == "__main__":
    app = run()
    aiohttp.web.run_app(app, port=os.getenv("PORT", 8081))

# conda activate bsc && npx nodemon server.py
