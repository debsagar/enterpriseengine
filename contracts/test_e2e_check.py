import importlib.util
import unittest

from environments.norvale.paths import CHECKOUT

spec = importlib.util.spec_from_file_location("e2e_check", CHECKOUT / "scripts/e2e_check.py")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


class MakeChain(unittest.TestCase):
    def test_release_needs_a_named_batch(self):
        state = {
            "Work Order": {"WO": dict(docstatus=1)},
            "MES Manufacturing Order": {"1": dict(origin="WO")},
            "mrp.production": {"1": dict(erp_work_ref="WO")},
            "Stock Entry": {
                "OUT": dict(docstatus=1, work_order="WO", purpose="Manufacture", items=[dict(batch_no="B")]),
                "OTHER": dict(docstatus=1, purpose="Material Transfer", items=[dict(item_code="X")]),
            },
            "Quality Inspection": {"QI": dict(docstatus=1, batch_no="B")},
            "Norvale QMS Disposition": {"D": dict(inspection="QI", batch_no="B")},
        }
        self.assertEqual(dict(e2e.chains(state)["make"])["Release Stock Entry"], 0)
        state["Stock Entry"]["OTHER"]["items"] = [dict(batch_no="B")]
        self.assertEqual(dict(e2e.chains(state)["make"])["Release Stock Entry"], 1)


if __name__ == "__main__":
    unittest.main()
