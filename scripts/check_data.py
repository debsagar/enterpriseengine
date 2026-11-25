"""Offline integrity, count and link checks for the shipped snapshot. No services."""

import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = root / "data"
manifest = json.loads((data / "manifest.json").read_text())
for name, expected in manifest["sha256"].items():
    assert hashlib.sha256((data / name).read_bytes()).hexdigest() == expected, name
records = json.loads((data / "observable/native-records.json").read_text())
summary = json.loads((data / "summary.json").read_text())
assert len(records["Sales Order"]) == summary["orders"] == 258
assert len(records["Work Order"]) == len(records["Job Card"]) == summary["production_jobs"] == 328
assert len(json.loads((data / "observable/tickets.json").read_text())) == summary["maintenance_jobs"] == 15
assert len(json.loads((data / "observable/mail.json").read_text())) == summary["mail_readbacks"] == 119
assert all(json.loads((data / "checks.json").read_text()).values())
for card in records["Job Card"].values():
    assert card["work_order"] in records["Work Order"], card["name"]
for payment in records["Payment Entry"].values():
    for ref in payment.get("references", []):
        if ref["reference_doctype"] in records:
            assert ref["reference_name"] in records[ref["reference_doctype"]], payment["name"]


# Detect credential fields without printing values.
def audit(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert not re.search(
                r"(^|_)(password|passwd|api_key|api_secret|access_token|refresh_token|secret_key|encryption_key)($|_)",
                key,
                re.I,
            ), key
            assert key != "custom_norvale_operation"
            audit(child)
    elif isinstance(value, list):
        for child in value:
            audit(child)


for path in data.rglob("*.json"):
    audit(json.loads(path.read_text()))
print("PASS: hashes, recorded counts, nine saved checks, job/payment links, credential-field audit")
