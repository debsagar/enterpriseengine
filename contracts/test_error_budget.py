"""Pending errors are labelled expected or bug, and a day over the bug limit stops the run (R10)."""

import unittest
from unittest.mock import patch
from urllib.error import URLError

from environments.norvale.native.company import Engine
from environments.norvale.native.company_erp import CompanyERP, ErrorBudgetExceeded, Pending


def operating_day(*failures, config=None):
    """Raise each (operation, exception) through the ordinary retry wrapper, then close day 0."""
    company = Engine.__new__(Engine)
    company.config = dict(company_operating=True, **(config or {}))
    company.state, company.day, company.event, logs = {}, 0, "d0000:inspections", []
    company.persist, company.log = lambda: None, lambda kind, **data: logs.append((kind, data))
    company.company_erp = CompanyERP.__new__(CompanyERP)
    company.company_erp.e = company
    for operation, failure in failures:

        def action(failure=failure):
            raise failure

        company.company_erp.attempt(operation, "QA", action)
    with patch("environments.norvale.integrations.company_run.lake.scheduled_scan"):
        company.run_phase(0, "d0000:native-clock", lambda: None)
    return logs


def wrapped():
    try:
        try:
            raise KeyError("family")
        except KeyError as cause:
            raise Pending("Handoff pending") from cause
    except Pending as exc:
        return exc


class ErrorBudget(unittest.TestCase):
    def test_labels_and_counts_only_bugs(self):
        logs = operating_day(
            ("dispatch:SO-1", Pending("Awaits QMS release")),
            ("settle:INV-1", Pending("No submitted bank statement")),
            ("sync:LOT-1", URLError("HTTP 500 (NameError)")),
            ("mes-start:W-1", wrapped()),
            ("sample-definition:SKU-1", Pending("No registered PLM family")),
        )
        labels = [data["error_class"] for kind, data in logs if kind == "company_erp_pending"]
        self.assertEqual(labels, ["expected", "expected", "bug", "bug", "bug"])
        budget = next(data for kind, data in logs if kind == "error_budget")
        self.assertEqual((budget["bug_errors"], budget["limit"]), (3, 5))
        self.assertEqual(budget["kinds"], {"sync": 1, "mes-start": 1, "sample-definition": 1})

    def test_stops_after_a_day_over_the_limit(self):
        bugs = [("sync:LOT-1", URLError("down"))] * 6
        with self.assertRaisesRegex(ErrorBudgetExceeded, "sync x6"):
            operating_day(*bugs)
        operating_day(*bugs, config=dict(daily_bug_limit=6))

    def test_expected_waits_never_stop_the_run(self):
        operating_day(*[("dispatch:SO-1", Pending("Awaits QMS release"))] * 100, config=dict(daily_bug_limit=0))


if __name__ == "__main__":
    unittest.main()
