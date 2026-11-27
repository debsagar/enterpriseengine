"""CFO / accounts payable: supplier invoices checked against purchase order and goods receipt ("three-way match")."""

import unittest

from environments.norvale.integrations.company_run.test_behaviour_roles import Fake, line
from environments.norvale.native import behaviour_roles as roles

CASE = dict(key="invoice:PINV-1", record="PINV-1")


class API:
    """Fake ERP: documents by (doctype, name). Records hold/unblock calls and applies them."""

    def __init__(self, *docs):
        self.docs = {(d["doctype"], d["name"]): d for d in docs}
        self.calls = []

    def get(self, doctype, name):
        return dict(self.docs[doctype, name])

    def list(self, doctype, *args, **kwargs):
        return [dict(d) for (kind, _), d in self.docs.items() if kind == doctype]

    def call(self, method, args):
        self.calls.append(args["method"])
        self.docs["Purchase Invoice", "PINV-1"]["on_hold"] = int(args["method"] == "block_invoice")


def invoice(name="PINV-1", rows=None, supplier="SUP-A", total=None, **over):
    rows = rows or [dict(line(10), purchase_order="PO-1", purchase_receipt="PR-1")]
    total = sum(r["amount"] for r in rows) if total is None else total
    return dict(
        doctype="Purchase Invoice", name=name, supplier=supplier, grand_total=total, items=rows, on_hold=0, **over
    )


def order(name="PO-1", total=100, supplier="SUP-A", rows=None):
    return dict(doctype="Purchase Order", name=name, supplier=supplier, grand_total=total, items=rows or [line(10)])


def receipt(qty=10, name="PR-1"):
    return dict(doctype="Purchase Receipt", name=name, items=[line(qty)])


def review(*docs):
    """Run evidence -> facts -> offline rule for PINV-1."""
    engine = Fake()
    engine.api["FINANCE"] = API(*docs)
    engine.rows["Purchase Invoice"] = [d for d in docs if d["doctype"] == "Purchase Invoice"]
    evidence = roles.invoice_evidence(engine, CASE)
    evidence["facts"] = roles.invoice_facts(evidence, engine.date())
    return engine, evidence, {f["flag"] for f in evidence["facts"]["flags"]}, roles.invoice_rule(evidence)[0]


class Finance(unittest.TestCase):
    def test_1_clean_three_way_match_is_approved(self):
        """PO, receipt and invoice agree, so it is approved. SOP: https://ramp.com/blog/accounts-payable/3-way-match"""
        _, _, flags, action = review(invoice(), order(), receipt())
        self.assertEqual((flags, action), (set(), "approve"))

    def test_2_billed_more_than_received_is_held(self):
        """Billing goods not received (0 or 60 of 100) blocks the invoice.
        SOP: https://docs.oracle.com/cd/A60725_05/html/comnls/us/ap/point04.htm
        """
        for got in (0, 60):
            with self.subTest(received=got):
                rows = [dict(line(100), purchase_order="PO-1", purchase_receipt="PR-1")]
                engine, evidence, flags, action = review(invoice(rows=rows), order(total=1000), receipt(got))
                self.assertEqual((flags, action), ({"billed_over_received"}, "hold"))
                roles.invoice_hold(engine, CASE, evidence, dict(reason="short receipt"))
                self.assertEqual(engine.api["FINANCE"].calls, ["block_invoice"])

    def test_3_billed_over_order_total_is_held(self):
        """An invoice of 1050 against a 1000 order is held.
        SOP: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/three-way-matching-policies
        """
        _, _, flags, action = review(invoice(total=1050), order(total=1000), receipt())
        self.assertEqual((flags, action), ({"billed_over_order"}, "hold"))

    def test_4_invoice_without_po_asks_the_buyer(self):
        """A non-PO invoice becomes a question to BUYER. SOP: https://ramp.com/blog/accounts-payable/3-way-match"""
        rows = [dict(line(10), purchase_receipt="PR-1")]
        engine, evidence, flags, action = review(invoice(rows=rows), receipt())
        self.assertEqual((flags, action), ({"no_purchase_order"}, "request_info"))
        roles.invoice_ask(engine, CASE, evidence, dict(message="Which PO covers PINV-1?"))
        self.assertEqual(engine.mails, [("ask:invoice:PINV-1", "CFO", "BUYER")])

    def test_5_supplier_mismatch_asks_for_information(self):
        """PO and invoice naming different suppliers is queried.
        SOP: https://ramp.com/blog/accounts-payable/3-way-match
        """
        _, _, flags, action = review(invoice(supplier="SUP-B"), order(), receipt())
        self.assertEqual((flags, action), ({"supplier_mismatch"}, "request_info"))

    def test_6_held_invoice_is_released_once_goods_arrive(self):
        """A held invoice whose receipt now matches is approved and unblocked. SOP: none in the spec ([Judgment])."""
        held = {**invoice(), "on_hold": 1}
        engine, evidence, flags, action = review(held, order(), receipt())
        self.assertEqual((flags, action), (set(), "approve"))
        roles.invoice_approve(engine, CASE, evidence, dict(reason="receipt posted"))
        self.assertEqual(engine.api["FINANCE"].calls, ["unblock_invoice"])
        self.assertEqual(engine.api["FINANCE"].get("Purchase Invoice", "PINV-1")["on_hold"], 0)


class FinanceGaps(unittest.TestCase):
    """Spec "wrong or missing" items."""

    # GAP (spec section 3; issue #119 to-do): invoice_evidence reads only orders[0] (behaviour_roles.py:185).
    @unittest.expectedFailure
    def test_invoice_over_two_orders_is_not_flagged(self):
        """An invoice of 100 covering two 50 orders matches. SOP: none in the spec ([Judgment])."""
        rows = [dict(line(5), purchase_order=po, purchase_receipt="PR-1") for po in ("PO-1", "PO-2")]
        _, _, flags, action = review(invoice(rows=rows), order(total=50), order("PO-2", total=50), receipt())
        self.assertEqual((flags, action), (set(), "approve"))

    # GAP (spec section 3; issue #119 to-do): unit price (rate) is never compared with the order.
    @unittest.expectedFailure
    def test_unit_price_above_order_is_not_approved(self):
        """10 units billed at 12 against an order rate of 10 is not approved, even under the order total.
        SOP: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/three-way-matching-policies
        """
        rows = [dict(line(10, rate=12), purchase_order="PO-1", purchase_receipt="PR-1")]
        action = review(invoice(rows=rows), order(total=200, rows=[line(20)]), receipt())[3]
        self.assertNotEqual(action, "approve")

    # GAP (spec section 3; issue #119 to-do): no tolerance, 0.5% over the order already holds the invoice.
    @unittest.expectedFailure
    def test_half_percent_over_order_is_within_tolerance(self):
        """An invoice 5 over a 1000 order (tax/freight tolerance) is approved.
        SOP: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/three-way-matching-policies
        """
        self.assertEqual(review(invoice(total=1005), order(total=1000), receipt())[3], "approve")

    # GAP (spec section 3; issue #119 to-do): no duplicate-invoice check on supplier + bill_no.
    @unittest.expectedFailure
    def test_duplicate_supplier_bill_is_not_approved(self):
        """A second invoice with the same supplier and bill number is not approved.
        SOP: https://ramp.com/blog/accounts-payable/3-way-match
        """
        docs = (invoice(bill_no="B-77"), invoice("PINV-0", bill_no="B-77"), order(total=200), receipt(20))
        self.assertNotEqual(review(*docs)[3], "approve")
