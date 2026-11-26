"""The routine standing-policy path can approve, defer or reject a purchase (R9)."""

import unittest
from unittest.mock import patch

from environments.norvale.native import router
from environments.norvale.native.company import Engine

POLICY = dict(
    role="SCM_MGR",
    effective_date="2027-01-01",
    review_date="2027-01-20",
    approval_limit=100,
    reorder_target=10,
    suppliers=["S"],
    items=["M"],
)
LINE = dict(item_code="M", qty=5, rate=10)
QUOTE = dict(supplier="S", grand_total=50, valid_till="2027-01-09", items=[LINE])


class RoutinePolicy(unittest.TestCase):
    def outcome(self, **quote):
        engine = Engine.__new__(Engine)
        engine.day, engine.state = 3, {"standing_policies": [POLICY], "decisions": {}}
        engine.persist, engine.log = lambda: None, lambda *a, **kw: None
        evidence = dict(supplier="S", grand_total=50, items=[LINE], supplier_quote=dict(QUOTE, **quote))
        with patch.object(router, "route", return_value={"judgment": False}):
            output = engine.choose("po", "SCM_MGR", evidence, "Review")
        self.assertEqual(engine.state["decisions"]["po"]["mode"], "standing-policy")
        return output["action"]

    def test_approves_a_purchase_matching_its_current_quote(self):
        self.assertEqual(self.outcome(), "approve")

    def test_defers_an_expired_quote(self):
        self.assertEqual(self.outcome(valid_till="2027-01-03"), "defer")

    def test_rejects_a_purchase_that_differs_from_its_quote(self):
        for change in (dict(supplier="T"), dict(grand_total=40), dict(items=[dict(LINE, rate=8)])):
            with self.subTest(change):
                self.assertEqual(self.outcome(**change), "reject")


if __name__ == "__main__":
    unittest.main()
