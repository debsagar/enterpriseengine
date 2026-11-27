"""The record chains Norvale's business logic follows across its software, as lake entity names.

Each listed entity is on the normal path of its chain. Exception-path records (non-conformances, quality
actions, PLM changes, maintenance tickets) are collected too but a run can finish without any.
"""

FLOWS = {
    "order_to_cash": (
        "Customer",
        "Sales Order",
        "Delivery Note",
        "Norvale Dispatch Lease",
        "Sales Invoice",
        "Payment Entry",
        "Bank Transaction",
        "GL Entry",
    ),
    "procure_to_pay": (
        "Supplier",
        "Supplier Quotation",
        "Purchase Order",
        "Purchase Receipt",
        "Quality Inspection",
        "Purchase Invoice",
        "Payment Entry",
        "Bank Transaction",
        "GL Entry",
    ),
    "make": (
        "Item",
        "BOM",
        "Work Order",
        "MES Manufacturing Order",
        "mrp.production",
        "mrp.workcenter.productivity",
        "Quality Inspection",
        "Norvale QMS Disposition",
        "Stock Entry",
        "Batch",
        "Serial and Batch Bundle",
        "Stock Ledger Entry",
    ),
    "design": ("InvenTree BOM", "InvenTree Publication", "BOM", "MES BOM"),
    "people_and_masters": (
        "Item",
        "Customer",
        "Supplier",
        "Employee",
        "Warehouse",
        "Directory Account",
        "Mail Message",
    ),
}

QMS_ENTITIES = (
    "Quality Inspection",
    "Quality Inspection Workflow",
    "Norvale Dispatch Lease",
    "Quality Action",
    "Non Conformance",
)
