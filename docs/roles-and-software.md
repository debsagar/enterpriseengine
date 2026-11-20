# How Norvale connects employees to software

A shift tells the clock **when** someone can work. An event scope tells the engine **which action** the role can request. A native account determines **whose authority the application enforces**. A business rule determines **whether this particular lot, order or payment is eligible**. All four matter.

## Connect an employee to an ERP site

The [operator registry](../examples/norvale-sales/host-registry.json) binds the role `counter-sales` to a particular ERPNext URL, Frappe site, and native employee. It names the environment variable holding the employee token. The [company extension](../examples/norvale-sales/extension.json) adds a sales desk, a 13:00–14:00 shift, the existing native item and one order event in a single command. Its only granted write scope is `write:warehouse-sales:order`.

To use these files on a disposable commissioned company, replace the example URL, site, employee and native masters with your installed ones. Supply the employee's token privately and choose an event minute in the run's future. Run from the same release as that company:

```sh
export NORVALE_CONNECTIONS_FILE=/absolute/private/path/host-registry.json
python3 generate.py --run /absolute/private/company/business --extend examples/norvale-sales/extension.json
```

The ordinary controller dispatches the event when the clock reaches it. The adapter calls Frappe's logged-user API, compares the authenticated employee with the host binding, looks up the run-scoped operation marker, and creates or reconciles a draft Sales Order. Readback checks the stored order and its owner. A matching scope on a self-declared local role cannot substitute for the commissioned employee. See the [connection validator](../native/software_connections.py), [native sales adapter](../native/incremental_erp.py), and [recurring entry tests](../tests/test_recurring_extensions.py).

## Who can move a manufacturing order forward?

| Decision or handoff | Native system and acting identity | Required evidence |
|---|---|---|
| Approve a revision | InvenTree reviewer and approver | Submitted change, effective date and destination BOM acknowledgments. Existing work keeps its pins. [Revision bridge](../integrations/revisions) |
| Release work to the factory | Planner reads the approved definition; Production dispatches to Odoo | Pinned revision, receiver BOM, allocated inputs and equipment availability. [Authority methods](../integrations/company_run/authority.py) |
| Record a result or release a lot | QMS lab analyst records a measurement; QA exercises disposition authority | Site, lot, quantity, unit, assay, inspection version and approver. [Lab commissioning](REGISTERED-QMS-LAB.md) |
| Submit a shipment | Warehouse obtains a QMS dispatch lease and uses ERPNext | The allocation must match the released lots. Recovery reads the submitted delivery, batch bundle and stock ledger. [ERP handoffs](COMPANY-ERP-CONTRACT.md) |
| Recognize cash | Finance reconciles ERPNext payment records | Linked invoice allocation and bank evidence. A due date alone cannot establish receipt. [Finance verification](../integrations/company_run/verify.py) |
| Collect company history | Scoped source readers ingest into Delta | Records retain source identities and versions. A missing link leaves genealogy unresolved. [Lake boundary](../integrations/lake) |

JEV and model decisions sit upstream of native writes. A recommendation to ship a held lot grants no warehouse lease. The ordinary controller checks the current native evidence before submitting the delivery.

## What you would implement for another company

Reuse the clock, checkpoint, role shifts, extension catalog and write journal. Supply the company's handlers, the software clients they call, and the business-specific evidence for each approval. The sales extension above uses an installed adapter. Adding an unfamiliar application or approval workflow still requires an adapter or handler plus native commissioning. Norvale's manufacturing authority rules live in Python and in the applications' own access controls. There is no single declarative file that can define arbitrary software and safely infer those rules.
