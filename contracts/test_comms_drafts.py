"""A small model may word employee mail; it cannot change the decision or the records named (R9, R18)."""

import json
import unittest
from unittest.mock import Mock, patch

from environments.norvale.native import judgment, long_run, router
from environments.norvale.native.company import Engine

TEMPLATE = "Native purchase PUR-ORD-2027-00001 for 48 Kg SLES is due 2027-01-06."
GOOD = "Hi, could you look at PUR-ORD-2027-00001? It is 48 Kg of SLES, due 2027-01-06. Thanks."
ONLINE = {
    "NORVALE_OFFLINE": "0",
    "OPENAI_API_KEY": "stub",
    "NORVALE_COMMS_MODEL": "small",
    "NORVALE_SPEND_CEILING_USD": "",
}


def company():
    engine = Engine.__new__(Engine)
    engine.state = {}
    engine.persist, engine.log, engine.mail = Mock(), Mock(), Mock()
    return engine


def replies(body):
    def provider(name, request, on_attempt):
        provider.sent.append(json.loads(request.data))
        content = json.dumps({"body": body})
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}], "usage": {"cost": 0.001}}

    provider.sent = []
    return provider


class Drafts(unittest.TestCase):
    def compose(self, engine, provider, env=ONLINE):
        with patch.dict("os.environ", env), patch.object(router, "provider_json", provider):
            return engine.compose("po", "BUYER", "SCM_MGR", "Approval requested", TEMPLATE)

    def test_default_is_the_template_and_calls_nothing(self):
        engine, provider = company(), replies(GOOD)
        self.assertEqual(self.compose(engine, provider, {**ONLINE, "NORVALE_COMMS_MODEL": ""}), TEMPLATE)
        self.assertEqual((provider.sent, engine.state), ([], {}))

    def test_faithful_draft_is_used_paid_for_once_and_survives_a_retry(self):
        engine, provider = company(), replies(GOOD)
        self.assertEqual(self.compose(engine, provider), GOOD)
        self.assertEqual(self.compose(engine, provider, {**ONLINE, "NORVALE_COMMS_MODEL": ""}), GOOD)
        self.assertEqual([r["model"] for r in provider.sent], ["small"])
        self.assertEqual(engine.state["spend"]["usd"], 0.001)

    def test_drafts_that_change_records_fall_back_to_the_template(self):
        for bad in (
            GOOD.replace("00001", "00002"),
            GOOD.replace("48 Kg of SLES", "some stock"),
            GOOD + " Also order CAPB.",
            "",
        ):
            engine = company()
            self.assertEqual(self.compose(engine, replies(bad)), TEMPLATE, bad)
            self.assertIn("comms_draft_rejected", [c.args[0] for c in engine.log.call_args_list])

    def test_spend_ceiling_is_not_swallowed(self):
        engine = company()
        engine.state["spend"] = {"usd": 1.0, "calls": 1, "unpriced_calls": 0}
        with self.assertRaises(long_run.SpendCeilingExceeded):
            self.compose(engine, replies(GOOD), {**ONLINE, "NORVALE_SPEND_CEILING_USD": "1"})

    def test_decision_headline_stays_code_written(self):
        engine = company()
        output = dict(action="reject", message="PUR-ORD-2027-00001 differs from its quote.")
        draft = "Sorry, PUR-ORD-2027-00001 does not match its quote."
        with patch.dict("os.environ", ONLINE), patch.object(router, "provider_json", replies(draft)):
            engine.report_outcome("k", "BUYER", "PUR-ORD-2027-00001", output)
        self.assertEqual(engine.mail.call_args.args[4], "PUR-ORD-2027-00001: reject. " + draft)
        self.assertIn("SLES", judgment.names(TEMPLATE))


if __name__ == "__main__":
    unittest.main()
