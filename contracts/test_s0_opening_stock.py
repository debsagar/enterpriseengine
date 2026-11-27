"""Opening stock is usable before day-0 production, and any quality doubt withdraws that (#77)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import URLError

from environments.norvale.integrations.company_run.authority import Authority
from environments.norvale.integrations.company_run.demo import Demo
from environments.norvale.integrations.company_run.input_staging import stage
from environments.norvale.native.company_erp import CompanyERP, Pending

from contracts.test_production_profiles import OperatingMES

LOT = dict(batch="NV-OPEN", item="BULK", qty=100, uom="Nos", kind="opening", go_live=True)


def ledger(authority, rm=100.0, hold=0.0, lots=(LOT,)):
    state = {"lots": {lot["batch"]: dict(lot) for lot in lots}, "synced": {}, "pending": {}, "work": {}, "moves": {}}
    state.update(completions={}, shipments={})
    engine = SimpleNamespace(
        wh={"rm": "RM", "fg": "FG", "hold": "Hold"},
        day=0,
        config={},
        date=lambda: "2027-01-01",
        state={"company_erp": state},
        persist=lambda: None,
        log=lambda *a, **kw: None,
    )
    erp = CompanyERP(engine, authority)
    erp.balance = lambda lot, warehouse, role: rm if warehouse == "RM" else hold
    return erp


def quality(record=False, disposition=None):
    return Mock(inspected=Mock(return_value=record), disposition=Mock(return_value=disposition))


class OpeningStock(TestCase):
    def test_uninspected_go_live_stock_is_available_and_left_in_raw_materials(self):
        erp = ledger(quality(), rm=60.0)
        erp.state["completions"]["done"] = dict(target_id="SE-1", report={"inputs": [dict(batch="NV-OPEN", qty=40)]})
        lot = erp.state["lots"]["NV-OPEN"]
        self.assertEqual(erp.available(lot, "PLANNER"), (60.0, None))
        self.assertTrue(erp.sync(lot))
        self.assertEqual((erp.state["moves"], erp.state["synced"]), ({}, {}))

    def test_any_quality_record_or_quarantined_quantity_blocks_it(self):
        for label, erp in (
            ("held, failed, draft or future-dated record", ledger(quality(record=True))),
            ("quantity in quarantine", ledger(quality(), hold=5.0)),
            ("not go-live stock", ledger(quality(), lots=[dict(LOT, go_live=False)])),
            ("authority without an answer", ledger(SimpleNamespace(disposition=lambda *a, **kw: None))),
        ):
            with self.subTest(label):
                self.assertEqual(erp.available(erp.state["lots"]["NV-OPEN"], "PLANNER"), (0, None))

    def test_failed_quality_read_defers_one_lot_and_blocks_it_until_resynced(self):
        authority = quality()
        authority.inspected.side_effect = [URLError("offline"), False, False, False]
        erp = ledger(authority, lots=[LOT, dict(LOT, batch="NV-OPEN-2")])
        erp.recover_shipments = erp.poll_work = lambda *a, **kw: None
        erp.reserved_dispatch_batches = lambda: set()
        erp.inspections()
        self.assertEqual(list(erp.state["pending"]), ["sync:NV-OPEN"])
        self.assertEqual(erp.available(erp.state["lots"]["NV-OPEN"], "PLANNER"), (0, None))
        erp.inspections()
        self.assertEqual(erp.state["pending"], {})

    def test_authority_counts_any_inspection_record(self):
        authority = Authority("/nonexistent")
        cfg = dict(specifications={"BULK": "SPEC"}, qms_reader={})
        authority.quality_route = lambda lot: (cfg, cfg)
        with patch("environments.norvale.integrations.company_run.authority.QMS") as qms:
            for rows, expected in (([], False), ([{"name": "QI-1"}], True)):
                qms.return_value.list.return_value = rows
                self.assertIs(authority.inspected(dict(LOT), role="PLANNER"), expected)
        with self.assertRaisesRegex(Pending, "not authorized"):
            authority.inspected(dict(LOT), role="CFO")

    def test_receipt_into_raw_materials_is_marked_go_live(self):
        for kind, warehouse, expected in (("opening", "RM", True), ("opening", "Hold", None), ("incoming", "RM", None)):
            with self.subTest(kind=kind, warehouse=warehouse):
                erp = ledger(quality(), lots=())
                erp.verify_row = lambda *a, **kw: None
                row = dict(batch_no="B", item_code="BULK", qty=1, stock_uom="Nos", name="row", t_warehouse=warehouse)
                erp.receive(dict(items=[row], doctype="Stock Entry", name="SE"), kind, "WAREHOUSE")
                self.assertEqual(erp.state["lots"]["B"].get("go_live"), expected)

    def test_lab_does_not_sample_go_live_stock(self):
        lots = [dict(LOT, item="BULK"), dict(LOT, batch="OLD", go_live=False)]
        erp = ledger(quality(), lots=lots)
        erp.rework_requests = lambda: []
        erp.definition = lambda item: ({}, {"BULK": ("Nos", 1)})
        actor = Demo.__new__(Demo)
        actor.e = SimpleNamespace(company_erp=erp, state={"ops": {"orders": {"o": {"item": "PACK"}}}})
        self.assertEqual(list(actor.eligible_samples()), ["OLD"])

    def test_mes_staging_accepts_stock_pinned_without_a_release(self):
        lot, inp = dict(LOT, qty=4), dict(batch="NV-OPEN", item="BULK", uom="Nos", qty=2, disposition=None)
        work = dict(key="w", request={"key": "w"}, inputs=[inp], dispatch={"source": "east", "id": 1})
        work["scoped_mes_staging"] = True
        flow = SimpleNamespace(state={"work": {"w": work}}, available=lambda *_: (4, None))
        site = OperatingMES("east")
        site.lose_receipt = False
        site.lots[1] = {"product_id": 3}
        engine = SimpleNamespace(company_erp=flow, persist=lambda: None)
        actor = SimpleNamespace(e=engine, mes=site.client("planner"), cfg=dict(company_id=1, stock=8))
        stage(actor, work, inp, lot, 3, 1)
        self.assertEqual(site.staged(1), 2)
