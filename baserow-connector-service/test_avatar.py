import unittest
from unittest.mock import Mock, patch
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from access import access_control, authenticated_email
from avatar import register_avatar_route, validate_avatar


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
        with patch("access.verify_oauth2_token", return_value={"exp": 9900000000, "email": "student@unipu.hr", "email_verified": True, "hd": "unipu.hr"}) as verify:
            self.assertEqual(authenticated_email({"Authorization": "Bearer signed-token"}), "student@unipu.hr")
            self.assertEqual(verify.call_args.args[0], "signed-token")
            self.assertTrue(verify.call_args.args[2].endswith(".apps.googleusercontent.com"))
        with patch("access.verify_oauth2_token", side_effect=ValueError("expired")), self.assertRaises(web.HTTPUnauthorized):
            authenticated_email({"Authorization": "Bearer expired"})
        with patch("access.verify_oauth2_token", return_value={"exp": 9900000000, "email": "someone@example.com", "email_verified": True}), self.assertRaises(web.HTTPForbidden):
            authenticated_email({"Authorization": "Bearer wrong-domain"})


class AvatarRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = Mock()
        self.backend.get_row_id_by_attribute.return_value = 7
        self.backend.update_row.return_value = {"data": {"avatar": "https://example.com/a.png"}}
        routes = web.RouteTableDef()
        register_avatar_route(routes, self.backend, lambda coordinator: ("staff" if coordinator else "students", {}))
        with patch("access.service_token", return_value="x" * 32):
            app = web.Application(middlewares=[access_control(True)])
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

    async def upload(self, data=b"\x89PNG\r\n\x1a\nimage", mime="image/png", headers=None):
        form = FormData()
        form.add_field("file", data, filename="../../photo.png", content_type=mime)
        return await self.http.patch("/api/student/avatar", data=form,
                                     headers=headers if headers is not None else {"X-Local-User": "student@local.test"})

    async def test_upload_and_identity(self):
        import os
        temporary_paths = []
        def store(path):
            self.assertTrue(os.path.isfile(path))
            temporary_paths.append(path)
            return {"data": {"url": "https://example.com/upload.png", "is_image": True}}
        self.backend.upload_file.side_effect = store
        response = await self.upload()
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"avatar": "https://example.com/upload.png"})
        self.backend.update_row.assert_called_once_with("students", 7, {"avatar": "https://example.com/upload.png"})
        self.backend.get_row_id_by_attribute.assert_called_with("students", "email", "student@local.test", {})
        self.assertFalse(os.path.exists(temporary_paths[0]))
        self.backend.upload_file.reset_mock()
        response = await self.upload(headers={})
        self.assertEqual(response.status, 401)
        self.backend.get_row_id_by_attribute.return_value = None
        response = await self.upload()
        self.assertEqual(response.status, 404)
        self.backend.upload_file.assert_not_called()

    async def test_upload_validation(self):
        for data, mime, status in [(b"not an image", "image/png", 400),
                                   (b"<svg/>", "image/svg+xml", 400),
                                   (b"\x89PNG\r\n\x1a\n", "image/jpeg", 400),
                                   (b"x" * (2 * 1024 * 1024 + 1), "image/png", 413)]:
            response = await self.upload(data, mime)
            self.assertEqual(response.status, status)
        self.backend.upload_file.assert_not_called()
        self.backend.update_row.assert_not_called()

    async def test_upload_failure_does_not_change_avatar(self):
        self.backend.upload_file.return_value = {"error": "unavailable"}
        response = await self.upload()
        self.assertEqual(response.status, 502)
        self.backend.upload_file.return_value = {"data": {"url": "https://example.com/file.png", "is_image": False}}
        response = await self.upload()
        self.assertEqual(response.status, 502)
        self.backend.update_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()
