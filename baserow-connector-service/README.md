# Baserow connector service

Service is an example of API for interactions with Baserow DB.

## Installation
```
pip install -r requirements.txt
```

You will need to create `.env` in which you will store your API key and Base ID (your database id)

```python
BASEROW_TOKEN = "xxxxxxxxxxxxx"
BASE_URL = "xxxxxxxxxxxxx"
```

### Profile avatar

`PATCH /api/student/avatar` accepts `{ "avatar": "https://…" }`. It updates only
that field on the existing student record belonging to the verified Google ID
token in `Authorization: Bearer …`. Install the updated requirements before
starting the service. `GOOGLE_CLIENT_ID` defaults to the dashboard's client ID;
set it if deploying a different Google client. Expired tokens require a fresh
Google login. Image URLs are not fetched by this service.

With `BASEROW_CONFIG_FILE` loaded for the local demo stack only, `X-Local-User`
may identify `student@local.test` or `admin@local.test`. Never enable the demo
configuration in production. Other headers cannot select the record to update.

Run `python -m unittest test_avatar.py` to check validation, identity isolation,
and persistence success/failure without contacting Google or Baserow.
