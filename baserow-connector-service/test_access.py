"""Exercise Google's verifier with an ephemeral signing key; never use a live credential."""
import json
import time
import unittest
from unittest.mock import Mock, patch

import rsa
from aiohttp import web
from google.auth import crypt, jwt

from access import authenticated_email, GOOGLE_CLIENT_ID, verified_claims


class CredentialTests(unittest.TestCase):
    def test_signature_audience_issuer_expiry_and_domain(self):
        public, private = rsa.newkeys(1024)
        signer = crypt.RSASigner.from_string(private.save_pkcs1(), key_id="test")
        now = int(time.time())
        claims = dict(sub="test", iat=now-60, exp=now+60, aud=GOOGLE_CLIENT_ID, iss="accounts.google.com",
                      email="test@student.unipu.hr", email_verified=True, hd="unipu.hr")
        certs = Mock(status=200, data=json.dumps({"test": public.save_pkcs1().decode()}).encode())

        def headers(**overrides):
            token = jwt.encode(signer, {**claims, **overrides}).decode()
            return {"Authorization": "Bearer " + token}

        self.addCleanup(verified_claims.cache_clear)
        with patch("access.Request") as transport:
            transport.return_value.return_value = certs
            valid = headers()
            self.assertEqual(authenticated_email(valid), claims["email"])
            test_email = "lukablaskovic2000@gmail.com"
            self.assertEqual(authenticated_email(headers(email=test_email, hd=None)), test_email)
            with self.assertRaises(web.HTTPUnauthorized):
                authenticated_email(headers(email=test_email, hd=None, email_verified=False))
            with self.assertRaises(web.HTTPForbidden):
                authenticated_email(headers(email="lukablaskovic2000+other@gmail.com", hd=None))
            with patch("access.time", return_value=now+61), self.assertRaises(web.HTTPUnauthorized):
                authenticated_email(valid)
            for invalid in (headers(aud="another-client"), headers(iss="another-issuer"), headers(exp=now-1),
                            headers(email_verified=False), {"Authorization": valid["Authorization"][:-4] + "AAAA"}):
                with self.assertRaises(web.HTTPUnauthorized):
                    authenticated_email(invalid)
            for invalid in (headers(hd="other.test"), headers(email="test@other.test")):
                with self.assertRaises(web.HTTPForbidden):
                    authenticated_email(invalid)
            with self.assertRaises(web.HTTPUnauthorized):
                authenticated_email({"X-Local-User": "admin@local.test"})
            with self.assertRaises(web.HTTPForbidden):
                authenticated_email({"X-Local-User": "admin@local.test", "Origin": "https://other.test"}, True)
