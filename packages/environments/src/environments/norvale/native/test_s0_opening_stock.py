"""Offline tests proving opening stock is available before day-0 production while respecting QMS holds."""

from types import SimpleNamespace

from environments.norvale.native.company_erp import CompanyERP


def _make_erp(disposition_fn, rm_qty=100.0, hold_qty=0.0):
    e = SimpleNamespace(
        wh={"rm": "Raw Materials - NCC", "fg": "Finished Goods - NCC", "hold": "Quarantine - NCC"},
        date=lambda: "2027-01-01",
        state={"lot_flow": {"lots": {}, "synced": {}, "pending": {}, "work": {}, "moves": {}}},
    )
    erp = CompanyERP.__new__(CompanyERP)
    erp.e = e
    erp.authority = SimpleNamespace(disposition=disposition_fn)
    erp.balance = lambda lot, warehouse, role: rm_qty if warehouse == "Raw Materials - NCC" else hold_qty
    erp.used = lambda lot: 0.0
    return erp


def test_opening_lot_available_without_qms_disposition():
    erp = _make_erp(lambda lot, as_of, role: None, rm_qty=100.0, hold_qty=0.0)
    lot = {"batch": "NV-OPEN-1", "item": "CARB", "qty": 100.0, "uom": "Kg", "kind": "opening"}
    avail, disp = erp.available(lot, "PLANNER")
    assert avail == 100.0
    assert disp is None


def test_opening_lot_blocked_when_qms_hold_or_quarantined():
    hold_disp = {
        "id": "QI-1",
        "version": "1",
        "approver": "qa@norvale.test",
        "evidence": "QI-1",
        "batch": "NV-OPEN-2",
        "item": "CARB",
        "uom": "Kg",
        "status": "hold",
        "qty": 0.0,
        "authorized": True,
    }
    erp_hold = _make_erp(lambda lot, as_of, role: hold_disp, rm_qty=100.0, hold_qty=0.0)
    lot = {"batch": "NV-OPEN-2", "item": "CARB", "qty": 100.0, "uom": "Kg", "kind": "opening"}
    avail, disp = erp_hold.available(lot, "PLANNER")
    assert avail == 0
    assert disp == hold_disp

    erp_quarantine = _make_erp(lambda lot, as_of, role: None, rm_qty=0.0, hold_qty=100.0)
    avail_q, _ = erp_quarantine.available(lot, "PLANNER")
    assert avail_q == 0
