from pathlib import Path
import json
import unittest

from core import ValidationError, load_products
from nextemby import GenerationUncertainError, NextEmbyClient, NextEmbyError, Response


PRODUCTS = load_products(Path(__file__).resolve().parent.parent / "product-mapping.json")


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response = Response(200, {}, b"BATCH_ID:BATCH-TEST\nTEST-CARD-ONE\n")
        def transport(method, url, headers, body, timeout):
            self.requests.append((method, url, headers, body, timeout))
            return self.response
        self.client = NextEmbyClient("http://127.0.0.1:8091", "TEST-NOT-A-REAL-TOKEN", transport=transport)

    def test_exact_payload_and_single_card(self):
        result = self.client.generate(PRODUCTS["free_monthly"])
        self.assertEqual(result, {"batch_id": "BATCH-TEST", "card": "TEST-CARD-ONE"})
        method, url, headers, body, _ = self.requests[0]
        self.assertEqual((method, url), ("POST", "http://127.0.0.1:8091/api/admin/cards/generate"))
        self.assertEqual(json.loads(body), {"duration_days": 30, "count": 1, "template_name": "free"})
        self.assertEqual(headers["Authorization"], "Bearer TEST-NOT-A-REAL-TOKEN")

    def test_fixed_templates_and_quantity_cannot_be_overridden(self):
        for key, value in [("template_name", "SVIP"), ("duration_days", 365), ("count", 2), ("count", True)]:
            product = dict(PRODUCTS["free_monthly"])
            product[key] = value
            with self.assertRaises(ValidationError):
                self.client.generate(product)
        self.assertEqual(self.requests, [])

    def test_extra_card_missing_batch_truncated_and_unknown_response_rejected(self):
        for body in (
            b"BATCH_ID:ONE\nCARD-ONE\nCARD-TWO\n", b"CARD-ONE\n", b"BATCH_ID:ONE\n",
            b'{"card":"CARD-ONE"}', b"<html>Login</html>", b"BATCH_ID:ONE\nERROR: failed\n", b"\xff",
        ):
            self.response = Response(200, {}, body)
            with self.assertRaises(GenerationUncertainError):
                self.client.generate(PRODUCTS["free_monthly"])
        self.assertEqual(len(self.requests), 7)

    def test_redirect_is_not_followed_or_retried(self):
        self.response = Response(302, {"location": "https://untrusted.invalid/"}, b"")
        with self.assertRaises(GenerationUncertainError):
            self.client.generate(PRODUCTS["free_monthly"])
        self.assertEqual(len(self.requests), 1)

    def test_timeout_error_redacted_no_retry(self):
        calls = []
        def broken(*args):
            calls.append(1)
            raise TimeoutError("SECRET-REMOTE-CREDENTIAL")
        client = NextEmbyClient("http://127.0.0.1:8091", "TEST", transport=broken)
        with self.assertRaises(GenerationUncertainError) as caught:
            client.generate(PRODUCTS["free_monthly"])
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(len(calls), 1)

    def test_read_batches_is_get_and_strips_card_values(self):
        self.response = Response(200, {}, json.dumps({"batches": [{"batch_id": "batch-1", "remaining": 2, "cards": ["SECRET-CARD"], "token": "SECRET-TOKEN"}]}).encode())
        result = self.client.read_batches()
        self.assertEqual(result, [{"batch_id": "batch-1", "remaining": 2}])
        self.assertEqual(self.requests[0][0], "GET")
        self.assertIsNone(self.requests[0][3])

    def test_read_failure_not_treated_as_empty_inventory(self):
        self.response = Response(200, {}, b'{"success":false,"error":"SECRET"}')
        with self.assertRaises(NextEmbyError) as caught:
            self.client.read_batches()
        self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_url_or_header_rejected(self):
        for url in ("file:///tmp/secret", "http://user:password@host", "http://host?token=abc", "http://host/api", "host:8091", "https://bad host", "https://host\n"):
            with self.assertRaises(NextEmbyError):
                NextEmbyClient(url, "TEST")
        with self.assertRaises(NextEmbyError):
            NextEmbyClient("http://localhost:8091", "bad\r\nheader")

    def test_https_requirement_rejects_http_before_any_request(self):
        with self.assertRaises(NextEmbyError):
            NextEmbyClient('http://example.test', 'TEST', require_https=True)
        client = NextEmbyClient('https://example.test/', 'TEST', require_https=True)
        self.assertEqual(client.base_url, 'https://example.test')
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
