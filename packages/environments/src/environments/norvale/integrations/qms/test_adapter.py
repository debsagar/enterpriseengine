import unittest

from components.qms import eligible


class EligibilityTest(unittest.TestCase):
    def test_fail_closed(self):
        release = {
            "docstatus": 1,
            "status": "Accepted",
            "workflow_state": "Released",
            "batch_no": "B1",
            "custom_spec_revision": "R1",
            "custom_lot_quantity": 10,
            "custom_lot_uom": "Nos",
            "name": "QI-1",
        }
        self.assertEqual(eligible([release], "B1", "R1", 10, "Nos"), release)
        for records in (
            [],
            [dict(release, docstatus=0)],
            [dict(release, status="Rejected")],
            [dict(release, workflow_state="Revoked")],
            [dict(release, batch_no="B2")],
            [dict(release, custom_spec_revision="R2")],
            [dict(release, custom_lot_quantity=9)],
            [dict(release, custom_lot_uom="Kg")],
            [release, release],
        ):
            with self.subTest(records=records):
                self.assertIsNone(eligible(records, "B1", "R1", 10, "Nos"))


class CreateRecoveryTest(unittest.TestCase):
    def test_find_native_record_after_lost_reply(self):
        from unittest.mock import patch

        from components.qms import CASE, OP, QMS

        qms = QMS("http://unused", "", "")
        with (
            patch.object(qms, "list", side_effect=[[], [{"name": "NC-1"}]]) as search,
            patch.object(qms, "request", side_effect=TimeoutError) as mutation,
            patch.object(qms, "get", return_value={"name": "NC-1", OP: "case-1", "subject": "pH"}),
        ):
            self.assertEqual(qms.create(CASE, "case-1", subject="pH")["name"], "NC-1")
        self.assertEqual(search.call_count, 2)
        mutation.assert_called_once()


class ErrorBodyTest(unittest.TestCase):
    def test_non_object_body_still_raises_api_error(self):
        from io import BytesIO
        from unittest.mock import patch
        from urllib.error import HTTPError

        from components.qms import QMS, APIError

        qms = QMS("http://unused", "", "")
        for body in (b"[]", b"null", b"<html>bad gateway</html>"):
            reply = HTTPError("http://unused", 502, "Bad Gateway", {}, BytesIO(body))
            with self.subTest(body=body), patch.object(qms.opener, "open", side_effect=reply):
                with self.assertRaises(APIError) as raised:
                    qms.request("GET", "method/ping")
                self.assertEqual((raised.exception.status, raised.exception.detail), (502, body.decode()))
