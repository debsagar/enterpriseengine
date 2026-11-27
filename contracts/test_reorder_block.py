"""A closed reorder-level request stops blocking its item after a few days (#109)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from environments.norvale.native.company import REORDER_BLOCK_DAYS, Engine


def shortage(day, changes):
    """Run one planning day that still lacks BOT250 and return the reorder requests it newly raised."""
    engine = Engine.__new__(Engine)
    engine.day = day
    engine.state = dict(ops=dict(shortage_days={"BOT250": [0, 1]}, changes=dict(changes), production={}))
    engine.company_erp = Mock()
    engine.plan = lambda build: {"work": [], "shortages": ["BOT250"]}
    engine.write, engine.notify, engine.persist = Mock(), Mock(), Mock()
    engine.get = Mock(return_value={"description": "request"})
    engine.observed_usage = Mock(return_value={"BOT250": 10})
    engine.catalog = SimpleNamespace()
    engine.plan_production()
    return [key for key in engine.state["ops"]["changes"] if key not in changes]


class ReorderBlock(TestCase):
    def test_first_shortage_raises_the_request(self):
        self.assertEqual(shortage(2, {}), ["change:BOT250"])

    def test_rejected_or_deferred_request_expires(self):
        for closed in ("rejected_day", "deferred_day"):
            with self.subTest(closed):
                changes = {"change:BOT250": {"item": "BOT250", closed: 3}}
                self.assertEqual(shortage(3 + REORDER_BLOCK_DAYS - 1, changes), [])
                day = 3 + REORDER_BLOCK_DAYS
                self.assertEqual(shortage(day, changes), [f"change:BOT250:{day}"])

    def test_request_deferred_by_review_expires_and_is_never_reviewed_again(self):
        change = dict(item="BOT250", level=618, requested_day=0)
        reviewer = Engine.__new__(Engine)
        reviewer.config, reviewer.wh = {}, dict(rm="Raw", hold="Hold")
        reviewer.state = dict(decisions={}, ops=dict(purchases={}, changes={"change:BOT250": change}))
        reviewer.write, reviewer.mail, reviewer.persist, reviewer.log = Mock(), Mock(), Mock(), Mock()
        reviewer.get = Mock(return_value={"name": "TODO-1"})
        reviewer.observe, reviewer.observed_usage = Mock(return_value=[]), Mock(return_value={})
        reviewer.reorder_level = Mock(return_value=0)
        reviewer.catalog = SimpleNamespace(source_list={})
        reviewer.api = {"SCM_MGR": Mock()}
        reviewer.choose = Mock(return_value=dict(action="request_info", reason="why", message="which?"))
        for reviewer.day in range(2, 40):
            reviewer.approvals()
        self.assertEqual(reviewer.choose.call_count, 3)
        day = change["deferred_day"] + REORDER_BLOCK_DAYS
        self.assertEqual(shortage(day, {"change:BOT250": change}), [f"change:BOT250:{day}"])

    def test_open_or_approved_request_keeps_blocking(self):
        old = {"item": "BOT250", "rejected_day": 3}
        for current in ({"requested_day": 9}, {"approved_day": 9}, {"approved_day": 9, "implemented": 10}):
            with self.subTest(current=current):
                changes = {"change:BOT250": old, "change:BOT250:9": dict(item="BOT250", **current)}
                self.assertEqual(shortage(100, changes), [])
