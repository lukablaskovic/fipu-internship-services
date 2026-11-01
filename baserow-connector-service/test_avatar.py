import unittest
from unittest.mock import Mock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from avatar import authenticated_email, register_avatar_route, validate_avatar


class AvatarValidationTests(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(validate_avatar({"avatar": "https://example.com/avatar.png"}), "https://example.com/avatar.png")
        for value in [None, [], "", "http://example.com/a", "javascript:alert(1)", "https://user:pass@example.com/a", "https://example.com/ a", "https://example.com:bad/a", "https://[bad", "x" * 2049]:
            with self.subTest(value=value), self.assertRaises(web.HTTPBadRequest):
                validate_avatar({"avatar": value})

    def test_identity(self):
        with self.assertRaises(web.HTTPUnauthorized):
            authenticated_email({"X-Local-User": "admin@local.test"})
        self.assertEqual(authenticated_email({"X-Local-User": "admin@local.test"}, True), "admin@local.test")
        with patch("avatar.verify_oauth2_token", return_value={"email": "student@unipu.hr", "email_verified": True, "hd": "unipu.hr"}) as verify:
            self.assertEqual(authenticated_email({"Authorization": "Bearer signed-token"}), "student@unipu.hr")
            self.assertEqual(verify.call_args.args[0], "signed-token")
            self.assertTrue(verify.call_args.args[2].endswith(".apps.googleusercontent.com"))
        with patch("avatar.verify_oauth2_token", side_effect=ValueError("expired")), self.assertRaises(web.HTTPUnauthorized):
            authenticated_email({"Authorization": "Bearer expired"})
        with patch("avatar.verify_oauth2_token", return_value={"email": "someone@example.com", "email_verified": True}), self.assertRaises(web.HTTPUnauthorized):
            authenticated_email({"Authorization": "Bearer signed-token"})


class AvatarRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = Mock()
        self.backend.get_row_id_by_attribute.return_value = 7
        self.backend.update_row.return_value = {"data": {"avatar": "https://example.com/a.png"}}
        routes = web.RouteTableDef()
        register_avatar_route(routes, self.backend, "students", {}, True)
        app = web.Application()
        app.add_routes(routes)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()

    async def test_save_and_failures(self):
        headers = {"X-Local-User": "student@local.test"}
        data = {"avatar": "https://example.com/a.png", "email": "other@local.test"}
        response = await self.http.patch("/api/student/avatar", json=data, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"avatar": data["avatar"]})
        self.backend.get_row_id_by_attribute.assert_called_with("students", "email", "student@local.test", {})
        self.backend.update_row.assert_called_once_with("students", 7, {"avatar": data["avatar"]})
        response = await self.http.patch("/api/student/avatar", json=data)
        self.assertEqual(response.status, 401)
        response = await self.http.patch("/api/student/avatar", json={"avatar": "http://bad.test"}, headers=headers)
        self.assertEqual(response.status, 400)
        self.backend.update_row.assert_called_once()
        self.backend.update_row.return_value = {"error": "Baserow unavailable"}
        response = await self.http.patch("/api/student/avatar", json=data, headers=headers)
        self.assertEqual(response.status, 502)
        self.backend.get_row_id_by_attribute.return_value = None
        response = await self.http.patch("/api/student/avatar", json=data, headers=headers)
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
