"""Native ERP users used to read commercial records into the private archive."""

ERP_ANALYTICS_ROLES = {
    "Sales Order": "SALES",
    "Purchase Order": "BUYER",
    "Purchase Receipt": "BUYER",
    "Sales Invoice": "FINANCE",
    "Purchase Invoice": "FINANCE",
    "Payment Entry": "FINANCE",
    "Bank Transaction": "FINANCE",
    "GL Entry": "FINANCE",
    "Stock Ledger Entry": "WAREHOUSE",
}

# Masters, people and the other records the company writes, each with a native role that may read it.
ERP_RECORD_ROLES = {
    "Item": "IT_ADMIN",
    "Customer": "SALES",
    "Supplier": "BUYER",
    "Employee": "IT_ADMIN",
    "Batch": "IT_ADMIN",
    "Warehouse": "WAREHOUSE",
    "Supplier Quotation": "BUYER",
    "Journal Entry": "FINANCE",
    "ToDo": "IT_ADMIN",
}

ERP_ENTITY_ROLES = {"BOM": "PLANNER", "Work Order": "PLANNER", **ERP_ANALYTICS_ROLES, **ERP_RECORD_ROLES}
# Stock Entry, Serial and Batch Bundle and Delivery Note use the QA reader.
ERP_ENTITIES = (
    "BOM",
    "Work Order",
    "Stock Entry",
    "Serial and Batch Bundle",
    "Delivery Note",
    *ERP_ANALYTICS_ROLES,
    *ERP_RECORD_ROLES,
)
