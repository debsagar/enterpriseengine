import copy
#!/usr/bin/env python3
"""
Norvale Consumer Care BV — Role-Grounded Agentic Enterprise Simulation (2027, v4)

Fixes & Upgrades over v3:
1. Live LLM Decision Loop Wired In: When --llm-model is set, every awakened role
   agent calls the LLM with its 4-primitive AccountabilityUnit contract, working
   memory notes, and read_scope-masked observable tables to choose actions and
   draft messages (raises RuntimeError if API key is missing; zero silent fallback).
2. Live Software API Wiring (SoftwareBus): Supports --mode dry-run (local CSVs)
   and --mode live-apis (writes through real ERPNext REST, CRM REST, ITSM REST,
   Zulip Chat REST, CalDAV .ics, and SMTP with per-role credentials and simulated
   posting timestamps, recording native document IDs in truth/id_map.json).
3. Action-Boundary RBAC Enforcement: Unauthorized writes are blocked both by
   ActionGateway and by target API 403 responses using per-role API tokens.
"""
import argparse, csv, json, math, os, random, smtplib, urllib.error, urllib.parse, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

# ==============================================================================
# 1. ORG CHART & 4-PRIMITIVE ACCOUNTABILITY UNITS (19 ROLES ACROSS 4 TIERS)
# ==============================================================================

ROLES = [
    {
        "role_id": "BOARD", "title": "Board of Directors", "person": "Board Committee",
        "email": "board@norvale.example", "reports_to": None, "department": "Governance",
        "primary_systems": ["Comms", "ERP-Reporting"],
        "mandate": {"kpi": "annual_revenue_growth_and_margin", "target": ">=8% growth, >=38% gross margin", "cadence": "Quarterly"},
        "read_scope": {"systems": ["Comms", "ERP-Reporting"], "tables": ["gl_monthly_summary"], "blind_spots": ["Shop-floor telemetry", "Individual POs", "Lab assays"]},
        "write_authority": {"allowed_actions": ["set_annual_targets", "approve_capex_over_100k"], "limits": {"capex_eur": 1000000}},
        "escalation_edge": {"triggers": ["Strategic pivot", "CapEx > 100k"], "escalates_to": None, "mechanism": "Board Resolution"}
    },
    {
        "role_id": "CEO", "title": "Chief Executive Officer", "person": "Henrik Lindholm",
        "email": "henrik.lindholm@norvale.example", "reports_to": "BOARD", "department": "Executive",
        "primary_systems": ["Comms", "ERP-Reporting", "ServiceNow"],
        "mandate": {"kpi": "ebitda_and_strategic_execution", "target": ">=14% EBITDA margin", "cadence": "Monthly"},
        "read_scope": {"systems": ["Comms", "ERP-Reporting", "ServiceNow"], "tables": ["gl_monthly_summary", "change_requests"], "blind_spots": ["Raw MES sensor logs", "Routine FIFO stock lots"]},
        "write_authority": {"allowed_actions": ["issue_executive_mandate", "approve_new_product_line", "resolve_vp_deadlock"], "limits": {"capex_eur": 100000}},
        "escalation_edge": {"triggers": ["CapEx > 100k", "Annual budget miss > 15%"], "escalates_to": "BOARD", "mechanism": "Quarterly Board Review"}
    },
    {
        "role_id": "CFO", "title": "Chief Financial Officer", "person": "Claire Moreau",
        "email": "claire.moreau@norvale.example", "reports_to": "CEO", "department": "Finance",
        "primary_systems": ["ERP", "CRM", "ServiceNow", "Comms"],
        "mandate": {"kpi": "gross_margin_and_working_capital", "target": ">=38% gross margin, -4% raw material cost-down", "cadence": "Monthly"},
        "read_scope": {"systems": ["ERP", "CRM", "ServiceNow", "Comms"], "tables": ["gl_monthly_summary", "gl_journal_entries", "invoices_ar", "invoices_ap", "payments", "purchase_orders", "opportunities"], "blind_spots": ["MES machine vibration", "Lab pH curves"]},
        "write_authority": {"allowed_actions": ["approve_po_over_25k", "approve_discount_over_10pct", "approve_sku_discontinuation", "close_monthly_ledger", "run_payment_batch"], "limits": {"po_approval_eur": 25000, "max_discount_pct": 14.5}},
        "escalation_edge": {"triggers": ["CapEx > 100k", "Margin breach > 3%"], "escalates_to": "CEO", "mechanism": "Executive Escalation / Email"}
    },
    {
        "role_id": "COO", "title": "Chief Operating Officer", "person": "Dirk Van Der Berg",
        "email": "dirk.vanderberg@norvale.example", "reports_to": "CEO", "department": "Operations",
        "primary_systems": ["ERP", "MES", "QMS", "ServiceNow", "Comms"],
        "mandate": {"kpi": "otif_delivery_and_plant_oee", "target": ">=95% OTIF, >=82% OEE", "cadence": "Weekly"},
        "read_scope": {"systems": ["ERP", "MES", "QMS", "ServiceNow", "Comms"], "tables": ["sales_orders", "batches", "equipment_states", "non_conformances", "change_requests"], "blind_spots": ["CRM early negotiation drafts", "Customer credit scores"]},
        "write_authority": {"allowed_actions": ["approve_pm_deferral", "authorize_overtime_capacity", "arbitrate_quality_vs_delivery"], "limits": {"pm_deferral_days": 60}},
        "escalation_edge": {"triggers": ["Plant shutdown > 5 days", "Product recall"], "escalates_to": "CEO", "mechanism": "ServiceNow Major Incident / Email"}
    },
    {
        "role_id": "CCO", "title": "Chief Commercial Officer", "person": "Sofia Alvarez",
        "email": "sofia.alvarez@norvale.example", "reports_to": "CEO", "department": "Commercial",
        "primary_systems": ["CRM", "ERP", "Comms"],
        "mandate": {"kpi": "net_revenue_and_key_account_retention", "target": "+8% YoY revenue", "cadence": "Monthly"},
        "read_scope": {"systems": ["CRM", "ERP", "Comms"], "tables": ["accounts", "opportunities", "sales_orders", "support_cases"], "blind_spots": ["Raw material lot chemistry", "Internal BOM salt ratios"]},
        "write_authority": {"allowed_actions": ["approve_promo_campaign", "sponsor_new_sku_request"], "limits": {"discount_pct": 10.0}},
        "escalation_edge": {"triggers": ["Customer discount > 10%", "New SKU launch capital"], "escalates_to": "CFO", "mechanism": "ServiceNow Approval / Email"}
    },
    {
        "role_id": "PLANT_MGR", "title": "Plant Manager", "person": "Tomas Lindqvist",
        "email": "tomas.lindqvist@norvale.example", "reports_to": "COO", "department": "Manufacturing",
        "primary_systems": ["MES", "ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "production_schedule_adherence_and_unit_cost", "target": ">=94% schedule adherence", "cadence": "Weekly"},
        "read_scope": {"systems": ["MES", "ERP", "ServiceNow", "Comms"], "tables": ["work_orders", "batches", "equipment_states", "change_requests", "incidents"], "blind_spots": ["Customer contract margins", "AP vendor payment terms"]},
        "write_authority": {"allowed_actions": ["request_pm_deferral", "approve_recipe_change_request", "reallocate_shift_operators"], "limits": {"pm_deferral_days": 14, "can_override_qa_hold": False}},
        "escalation_edge": {"triggers": ["PM deferral > 14 days", "Equipment breakdown > 48h"], "escalates_to": "COO", "mechanism": "ServiceNow Change Request / Email"}
    },
    {
        "role_id": "SC_MGR", "title": "Supply Chain Manager", "person": "Vikram Desai",
        "email": "vikram.desai@norvale.example", "reports_to": "COO", "department": "Supply Chain",
        "primary_systems": ["ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "raw_material_cost_variance_and_stockout_rate", "target": "-4% material cost, <1% stockouts", "cadence": "Weekly"},
        "read_scope": {"systems": ["ERP", "ServiceNow", "Comms"], "tables": ["purchase_orders", "purchase_order_lines", "source_list", "materials", "goods_movements", "change_requests"], "blind_spots": ["MES 10-min sensor telemetry", "CRM opportunity pipeline"]},
        "write_authority": {"allowed_actions": ["approve_po_10k_to_25k", "approve_vendor_switch_cr", "approve_safety_stock_cr"], "limits": {"po_approval_eur": 25000}},
        "escalation_edge": {"triggers": ["PO > 25,000 EUR", "Sole-source supplier failure"], "escalates_to": "CFO", "mechanism": "ServiceNow Approval / Email"}
    },
    {
        "role_id": "QA_MGR", "title": "Quality Assurance Manager", "person": "Aisha Bello",
        "email": "aisha.bello@norvale.example", "reports_to": "COO", "department": "Quality",
        "primary_systems": ["QMS", "MES", "ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "right_first_time_and_zero_escaped_defects", "target": ">=96% RFT, 0 escaped defects", "cadence": "Weekly"},
        "read_scope": {"systems": ["QMS", "MES", "ERP", "ServiceNow", "Comms"], "tables": ["inspection_lots", "lab_measurements", "non_conformances", "deviations", "batches"], "blind_spots": ["Vendor unit price quotes", "Sales discount negotiations"]},
        "write_authority": {"allowed_actions": ["issue_temporary_deviation", "block_vendor_material", "approve_recipe_quality_cr"], "limits": {"max_deviation_days": 21}},
        "escalation_edge": {"triggers": ["Repeated vendor spec failures", "Line stoppage due to QA hold"], "escalates_to": "COO", "mechanism": "ServiceNow CR / Escalation Email"}
    },
    {
        "role_id": "SALES_OPS_MGR", "title": "Sales Operations Manager", "person": "Emma Clarke",
        "email": "emma.clarke@norvale.example", "reports_to": "CCO", "department": "Commercial",
        "primary_systems": ["CRM", "ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "order_fulfillment_otif_and_catalog_margin", "target": ">=95% OTIF, >=8% SKU margin floor", "cadence": "Weekly"},
        "read_scope": {"systems": ["CRM", "ERP", "ServiceNow", "Comms"], "tables": ["sales_orders", "sales_order_lines", "support_cases", "materials"], "blind_spots": ["MES vessel wear", "Raw material lab assays"]},
        "write_authority": {"allowed_actions": ["convert_quote_to_sales_order", "propose_sku_lifecycle_cr", "issue_customer_delay_notice"], "limits": {"discount_pct": 10.0}},
        "escalation_edge": {"triggers": ["Order delayed > 7 days", "SKU margin < 8%"], "escalates_to": "CCO", "mechanism": "ServiceNow CR / Email"}
    },
    {
        "role_id": "IT_ADMIN", "title": "Enterprise Systems Admin", "person": "Lukas Novak",
        "email": "lukas.novak@norvale.example", "reports_to": "CFO", "department": "IT",
        "primary_systems": ["ServiceNow", "ERP", "MES", "QMS", "CRM"],
        "mandate": {"kpi": "master_data_integrity_and_change_compliance", "target": "100% CR-backed master data changes", "cadence": "Weekly"},
        "read_scope": {"systems": ["ServiceNow", "ERP", "MES", "QMS", "CRM"], "tables": ["change_requests", "approval_tasks", "incidents", "materials", "boms", "source_list"], "blind_spots": ["Informal verbal hallway conversations"]},
        "write_authority": {"allowed_actions": ["commit_approved_master_data_cr", "resolve_it_incident"], "limits": {"requires_approved_cr": True}},
        "escalation_edge": {"triggers": ["Unapproved master-data edit attempt", "ERP-MES sync failure"], "escalates_to": "CFO", "mechanism": "ServiceNow Audit Log"}
    },
    {
        "role_id": "BUYER_CHEM", "title": "Chemical Buyer", "person": "Priya Nair",
        "email": "priya.nair@norvale.example", "reports_to": "SC_MGR", "department": "Supply Chain",
        "primary_systems": ["ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "chemical_purchase_price_variance", "target": "-4% vs baseline standard cost", "cadence": "Weekly"},
        "read_scope": {"systems": ["ERP", "ServiceNow", "Comms"], "tables": ["materials", "vendors", "source_list", "purchase_orders", "purchase_order_lines", "goods_movements"], "blind_spots": ["MES viscosity failures", "CRM customer promotions"]},
        "write_authority": {"allowed_actions": ["create_purchase_order_approved_vendor"], "limits": {"po_approval_eur": 10000, "can_edit_source_list": False}},
        "escalation_edge": {"triggers": ["PO > 10,000 EUR", "Vendor switch proposal", "Supplier lead-time breach"], "escalates_to": "SC_MGR", "mechanism": "ServiceNow Approval / CR"}
    },
    {
        "role_id": "BUYER_PACK", "title": "Packaging & Fragrance Buyer", "person": "Hannah Lindner",
        "email": "hannah.lindner@norvale.example", "reports_to": "SC_MGR", "department": "Supply Chain",
        "primary_systems": ["ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "packaging_and_fragrance_availability", "target": "0 line stops from packaging/fragrance", "cadence": "Weekly"},
        "read_scope": {"systems": ["ERP", "ServiceNow", "Comms"], "tables": ["materials", "vendors", "source_list", "purchase_orders", "purchase_order_lines", "goods_movements"], "blind_spots": ["MES mixing temperatures", "Customer credit balances"]},
        "write_authority": {"allowed_actions": ["create_purchase_order_approved_vendor"], "limits": {"po_approval_eur": 10000, "can_edit_safety_stock": False}},
        "escalation_edge": {"triggers": ["PO > 10,000 EUR", "Repeated late fragrance deliveries"], "escalates_to": "SC_MGR", "mechanism": "ServiceNow CR / Email"}
    },
    {
        "role_id": "PLANNER", "title": "Production Planner", "person": "Wei Chen",
        "email": "wei.chen@norvale.example", "reports_to": "PLANT_MGR", "department": "Supply Chain",
        "primary_systems": ["ERP", "MES", "Comms"],
        "mandate": {"kpi": "on_time_work_order_release", "target": "<2% stockout-delayed batches", "cadence": "Daily"},
        "read_scope": {"systems": ["ERP", "MES", "Comms"], "tables": ["sales_orders", "sales_order_lines", "materials", "goods_movements", "work_orders", "equipment_states"], "blind_spots": ["Vendor unit prices", "QA lab raw assay numbers"]},
        "write_authority": {"allowed_actions": ["release_production_work_order", "sequence_vessel_queue"], "limits": {"can_override_qa_hold": False}},
        "escalation_edge": {"triggers": ["Raw material < 3 days cover", "Capacity overload > 95%"], "escalates_to": "PLANT_MGR", "mechanism": "Email / ServiceNow Incident"}
    },
    {
        "role_id": "PROC_ENG", "title": "Process Engineer", "person": "Marco Ruiz",
        "email": "marco.ruiz@norvale.example", "reports_to": "PLANT_MGR", "department": "Engineering",
        "primary_systems": ["MES", "QMS", "ERP", "ServiceNow", "Comms"],
        "mandate": {"kpi": "first_pass_batch_yield", "target": ">=96% PASS viscosity & pH", "cadence": "Weekly"},
        "read_scope": {"systems": ["MES", "QMS", "ERP", "ServiceNow", "Comms"], "tables": ["batches", "batch_material_lots", "sensor_readings_summary", "lab_measurements", "boms"], "blind_spots": ["Supplier commercial contracts", "Customer invoice status"]},
        "write_authority": {"allowed_actions": ["draft_recipe_or_bom_cr"], "limits": {"can_edit_bom_directly": False}},
        "escalation_edge": {"triggers": ["4+ viscosity/pH rework batches in 14d"], "escalates_to": "QA_MGR", "mechanism": "ServiceNow Change Request"}
    },
    {
        "role_id": "MAINT_LEAD", "title": "Maintenance Lead", "person": "Jan Kowalski",
        "email": "jan.kowalski@norvale.example", "reports_to": "PLANT_MGR", "department": "Maintenance",
        "primary_systems": ["MES", "ServiceNow", "Comms"],
        "mandate": {"kpi": "unplanned_equipment_downtime", "target": "<3% unplanned downtime", "cadence": "Weekly"},
        "read_scope": {"systems": ["MES", "ServiceNow", "Comms"], "tables": ["equipment_states", "sensor_readings_summary", "incidents", "change_requests"], "blind_spots": ["CRM sales promotions", "ERP financial ledger"]},
        "write_authority": {"allowed_actions": ["execute_planned_pm", "repair_breakdown_incident"], "limits": {"can_defer_pm_alone": False}},
        "escalation_edge": {"triggers": ["Vibration > 4.5 mm/s", "Breakdown incident", "PM overdue > 40 batches"], "escalates_to": "PLANT_MGR", "mechanism": "ServiceNow Incident / Email"}
    },
    {
        "role_id": "QA_TECH", "title": "QA Lab Technician", "person": "Nadia Okafor",
        "email": "nadia.okafor@norvale.example", "reports_to": "QA_MGR", "department": "Quality",
        "primary_systems": ["QMS", "MES", "ERP", "Comms"],
        "mandate": {"kpi": "inspection_turnaround_and_spec_enforcement", "target": "100% lots tested before release", "cadence": "Daily"},
        "read_scope": {"systems": ["QMS", "MES", "ERP", "Comms"], "tables": ["inspection_lots", "lab_measurements", "batches", "goods_movements"], "blind_spots": ["Vendor pricing", "Customer order urgency"]},
        "write_authority": {"allowed_actions": ["record_lab_assay", "pass_or_reject_inspection_lot"], "limits": {"can_sign_deviation": False}},
        "escalation_edge": {"triggers": ["Incoming raw material out of spec", "Missing lot traceability", "Finished batch out of spec"], "escalates_to": "QA_MGR", "mechanism": "QMS Non-Conformance / Chat"}
    },
    {
        "role_id": "ACCT_EXEC", "title": "Key Account Executive", "person": "Lucas Meyer",
        "email": "lucas.meyer@norvale.example", "reports_to": "SALES_OPS_MGR", "department": "Commercial",
        "primary_systems": ["CRM", "Comms"],
        "mandate": {"kpi": "quarterly_bookings_and_promo_listings", "target": "EUR 15M/quarter bookings", "cadence": "Quarterly"},
        "read_scope": {"systems": ["CRM", "Comms"], "tables": ["accounts", "opportunities", "support_cases"], "blind_spots": ["Plant vessel wear", "Raw material stock levels"]},
        "write_authority": {"allowed_actions": ["create_opportunity", "issue_standard_quote_up_to_10pct"], "limits": {"discount_pct": 10.0}},
        "escalation_edge": {"triggers": ["Customer discount > 10%", "Custom fragrance SKU request"], "escalates_to": "CFO", "mechanism": "CRM Approval / Email"}
    },
    {
        "role_id": "SHIFT_OP", "title": "Line Shift Operator", "person": "Koen Sanders",
        "email": "koen.sanders@norvale.example", "reports_to": "PLANT_MGR", "department": "Manufacturing",
        "primary_systems": ["MES", "Comms"],
        "mandate": {"kpi": "batch_execution_and_machine_monitoring", "target": "Zero unlogged stoppages", "cadence": "Daily"},
        "read_scope": {"systems": ["MES", "Comms"], "tables": ["work_orders", "batches", "equipment_states", "sensor_readings_summary"], "blind_spots": ["ERP vendor master", "Customer orders", "GL accounting"]},
        "write_authority": {"allowed_actions": ["execute_mes_batch", "log_equipment_fault"], "limits": {"can_alter_recipe": False}},
        "escalation_edge": {"triggers": ["High vessel vibration", "Material shortage idle state"], "escalates_to": "MAINT_LEAD", "mechanism": "Chat / Email Escalation"}
    },
    {
        "role_id": "WH_CLERK", "title": "Warehouse & Logistics Clerk", "person": "Bram Peeters",
        "email": "bram.peeters@norvale.example", "reports_to": "SC_MGR", "department": "Supply Chain",
        "primary_systems": ["ERP", "Comms"],
        "mandate": {"kpi": "goods_receipt_and_dispatch_accuracy", "target": "100% lot scan compliance", "cadence": "Daily"},
        "read_scope": {"systems": ["ERP", "Comms"], "tables": ["goods_movements", "purchase_orders", "sales_orders", "sales_order_lines"], "blind_spots": ["Lab assay tolerances", "BOM formulas"]},
        "write_authority": {"allowed_actions": ["post_goods_receipt", "post_goods_issue_delivery"], "limits": {"can_release_qms_hold": False}},
        "escalation_edge": {"triggers": ["Missing lot barcode", "Short stock on outbound delivery"], "escalates_to": "PLANNER", "mechanism": "Chat / Comms"}
    }
]

ROLE_BY_ID = {r["role_id"]: r for r in ROLES}

# ==============================================================================
# 2. CATALOGUE, FORMULAS, SUPPLIERS, CUSTOMERS
# ==============================================================================

RAWS = {
    "SLES": ("Sodium laureth sulfate 70%", "KG", ("active_pct", 70.0, (66.0, 72.0))),
    "LAS":  ("Linear alkylbenzene sulfonic acid 96%", "KG", ("active_pct", 96.0, (93.0, 98.0))),
    "CAPB": ("Cocamidopropyl betaine 30%", "KG", None),
    "CARB": ("Carbomer 940", "KG", ("visc_index", 100.0, (90.0, 110.0))),
    "EMU":  ("Cetearyl alcohol", "KG", None),
    "OIL":  ("Caprylic/capric triglyceride", "KG", None),
    "NACL": ("Sodium chloride, technical", "KG", None),
    "PRES": ("Phenoxyethanol", "KG", None),
}

FRAGRANCES = {
    "CIT": "Citrus Burst", "OCN": "Ocean Mist", "LAV": "Lavender Calm",
    "APL": "Green Apple", "COC": "Coconut", "ALO": "Aloe Fresh"
}

PACKS = {
    "BOT250": ("Bottle 250 ml PET", 0.25),
    "BOT400": ("Bottle 400 ml PET", 0.40),
    "BOT750": ("Bottle 750 ml PET", 0.75),
    "TUB200": ("Tube 200 ml", 0.20),
}

FAMILIES = {
    "SH": dict(name="Shampoo", formula={"SLES": 0.12, "CAPB": 0.04, "NACL": 0.015, "PRES": 0.005, "FRG": 0.008},
               active="SLES", thick="NACL", visc=6000, spec=(4500, 7500), ph=5.5,
               temp=45, mix=90, rpm=120, packs=["BOT250", "BOT400"], unit_price=2.45),
    "DW": dict(name="Dishwash Liquid", formula={"LAS": 0.10, "SLES": 0.06, "NACL": 0.02, "PRES": 0.003, "FRG": 0.004},
               active="LAS", thick="NACL", visc=1500, spec=(1000, 2000), ph=7.0,
               temp=30, mix=60, rpm=90, packs=["BOT750", "BOT400"], unit_price=1.95),
    "BL": dict(name="Body Lotion", formula={"OIL": 0.08, "EMU": 0.04, "CARB": 0.004, "PRES": 0.006, "FRG": 0.005},
               active="CARB", thick="CARB", visc=12000, spec=(9000, 15000), ph=6.0,
               temp=70, mix=120, rpm=150, packs=["TUB200", "BOT250"], unit_price=3.60),
}

SUPPLIERS = {
    "BRENNER": ("Brenner Tensid AG", "DE", {
        "SLES": (1.42, (5, 1), (70.0, 0.45)),
        "LAS":  (1.31, (5, 1), (96.0, 0.40)),
        "CAPB": (1.65, (5, 1), None)
    }),
    "KEMIX": ("Kemix Chemicals GmbH", "PL", {
        "SLES": (1.26, (7, 2), (66.4, 1.35)),
        "LAS":  (1.17, (7, 2), (94.6, 0.95))
    }),
    "SALINOR": ("Salinor SA", "FR", {"NACL": (0.09, (4, 1), None)}),
    "AROMA": ("Aromatica Fine Fragrances BV", "NL", {f"FRG-{k}": (18.5, (9, 3), None) for k in FRAGRANCES}),
    "POLYSPEC": ("Polymer Specialties Ltd", "GB", {
        "CARB": (9.80, (10, 2), (100.0, 2.5)),
        "EMU":  (2.40, (10, 2), None),
        "OIL":  (3.10, (10, 2), None),
        "PRES": (4.60, (10, 2), None)
    }),
    "PACKRIGHT": ("PackRight Containers GmbH", "DE", {p: (0.11, (5, 1), None) for p in PACKS} | {"CAP28": (0.03, (5, 1), None)}),
}

CUSTOMERS = [
    ("FreshMart Retail BV", "NL", "Enterprise", 250000),
    ("Corner Stores Group", "NL", "Enterprise", 180000),
    ("Hollis Pharmacies", "BE", "Mid-Market", 120000),
    ("Nordwind Grocery AG", "DE", "Enterprise", 300000),
    ("ValuePlus Discount", "DE", "Mid-Market", 140000),
    ("Greenleaf Organics", "NL", "Specialty", 90000),
    ("Metro Cash Partners", "PL", "Enterprise", 220000),
    ("Kaya Beauty Online", "DE", "Specialty", 85000),
    ("Bay Street Supermarkets", "FR", "Enterprise", 260000),
    ("Alpenmarkt GmbH", "DE", "Mid-Market", 130000),
    ("Sunrise Wholesale", "BE", "Mid-Market", 110000),
    ("Parkside Drugstores", "NL", "Mid-Market", 115000),
]

BATCH_KG = 5000
BATCHES_PER_VESSEL_DAY = 2
TARGET_UTILIZATION = 0.74


def d_str(d):
    return d.strftime("%Y-%m-%d")


def ts_str(d, hour, minute=0):
    return f"{d.strftime('%Y-%m-%d')} {hour:02d}:{minute:02d}:00"


# ==============================================================================
# 3. SOFTWARE BUS (DUAL-MODE: DRY-RUN CSVs OR LIVE ERPNext/CRM/ITSM/Zulip/SMTP)
# ==============================================================================

class SoftwareBus:
    """
    Centralizes all record persistence and live application API calls.
    In --mode dry-run: writes local CSVs and records serialized API payloads.
    In --mode live-apis: executes real HTTP/SMTP calls against configured endpoints
    using per-role API tokens and maps logical IDs -> native database primary keys.
    """
    def __init__(self, mode="dry-run", config_path=None):
        self.mode = mode
        self.tables = defaultdict(list)
        self.seq = defaultdict(int)
        self.id_map = {}                 # logical_id ("SO-00001") -> native_id ("SAL-ORD-2027-00001")
        self.api_calls_executed = 0
        self.api_403_rejections = 0
        self.call_log = []

        self.cfg = {
            "erp_url": os.environ.get("ERP_URL", "http://127.0.0.1:8000"),
            "crm_url": os.environ.get("CRM_URL", "http://127.0.0.1:8001"),
            "itsm_url": os.environ.get("ITSM_URL", "http://127.0.0.1:8002"),
            "zulip_url": os.environ.get("ZULIP_URL", "http://127.0.0.1:8003"),
            "caldav_url": os.environ.get("CALDAV_URL", "http://127.0.0.1:8004"),
            "smtp_host": os.environ.get("SMTP_HOST", "127.0.0.1"),
            "smtp_port": int(os.environ.get("SMTP_PORT", "1025")),
            "role_tokens": {r["role_id"]: f"tok_{r['role_id'].lower()}:sec_{r['role_id'].lower()}" for r in ROLES}
        }
        if config_path and Path(config_path).exists():
            self.cfg.update(json.loads(Path(config_path).read_text()))

    def next_id(self, prefix, width=5):
        self.seq[prefix] += 1
        return f"{prefix}-{str(self.seq[prefix]).zfill(width)}"

    def add(self, table, **row):
        self.tables[table].append(row)
        return row

    def _http_call(self, system, role_id, method, path, payload=None):
        base = self.cfg[f"{system}_url"].rstrip("/")
        url = f"{base}{path}"
        token = self.cfg["role_tokens"].get(role_id, self.cfg["role_tokens"]["IT_ADMIN"])
        headers = {"Authorization": f"token {token}", "Content-Type": "application/json", "X-Sim-Role": role_id}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None

        if self.mode != "live-apis":
            self.call_log.append({"system": system, "role": role_id, "method": method, "path": path, "status": "DRY_RUN"})
            return {"data": {"name": f"LOCAL-{path.split('/')[-1]}-{len(self.call_log)}"}}

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.api_calls_executed += 1
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                self.call_log.append({"system": system, "role": role_id, "method": method, "path": path, "status": resp.status})
                return parsed
        except urllib.error.HTTPError as e:
            self.api_calls_executed += 1
            if e.code == 403:
                self.api_403_rejections += 1
            self.call_log.append({"system": system, "role": role_id, "method": method, "path": path, "status": e.code})
            raise

    # ---- High-level software emitters ----
    def emit_erp_doc(self, role_id, doctype, logical_id, payload, submit=True):
        encoded_dt = urllib.parse.quote(doctype)
        res = self._http_call("erp", role_id, "POST", f"/api/resource/{encoded_dt}", payload)
        native_name = res.get("data", {}).get("name", logical_id)
        if submit and self.mode == "live-apis":
            self._http_call("erp", role_id, "PUT", f"/api/resource/{encoded_dt}/{urllib.parse.quote(native_name)}", {"docstatus": 1})
        self.id_map[logical_id] = native_name
        return native_name

    def emit_crm_opportunity(self, role_id, opp_row):
        res = self._http_call("crm", role_id, "POST", "/api/v1/Opportunity", opp_row)
        native_id = res.get("data", {}).get("name", opp_row["opp_id"])
        self.id_map[opp_row["opp_id"]] = native_id
        return native_id

    def emit_itsm_ticket(self, role_id, ticket_kind, logical_id, payload):
        res = self._http_call("itsm", role_id, "POST", f"/api/v1/{ticket_kind}", payload)
        native_id = res.get("data", {}).get("name", logical_id)
        self.id_map[logical_id] = native_id
        return native_id

    def emit_zulip_chat(self, chat_row):
        res = self._http_call("zulip", chat_row["from_role"], "POST", "/api/v1/messages", {
            "type": "stream", "to": chat_row["channel"].lstrip("#"),
            "topic": chat_row["to_role"], "content": chat_row["message"]
        })
        self.id_map[chat_row["chat_id"]] = res.get("data", {}).get("name", chat_row["chat_id"])

    def emit_caldav_event(self, cal_row):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Norvale//SimV4//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{cal_row['event_id']}@norvale.example\r\nSUMMARY:{cal_row['title']}\r\n"
            f"DTSTART:{cal_row['start_time'].replace('-', '').replace(':', '').replace(' ', 'T')}\r\n"
            f"DESCRIPTION:{cal_row['meeting_notes']}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        res = self._http_call("caldav", cal_row["organizer_role"], "PUT", f"/cal/{cal_row['organizer_role']}/{cal_row['event_id']}.ics", {"ics": ics})
        self.id_map[cal_row["event_id"]] = res.get("data", {}).get("name", cal_row["event_id"])

    def emit_smtp_email(self, msg_row):
        if self.mode != "live-apis":
            self.call_log.append({"system": "smtp", "role": msg_row["from_role"], "method": "SMTP", "path": msg_row["message_id"], "status": "DRY_RUN"})
            return
        em = EmailMessage()
        em["Message-ID"] = f"<{msg_row['message_id']}@norvale.example>"
        if msg_row.get("in_reply_to"):
            em["In-Reply-To"] = f"<{msg_row['in_reply_to']}@norvale.example>"
            em["References"] = f"<{msg_row['in_reply_to']}@norvale.example>"
        em["Date"] = msg_row["timestamp"]
        em["From"] = msg_row["from_email"]
        em["To"] = msg_row["to_emails"]
        em["Subject"] = msg_row["subject"]
        em.set_content(msg_row["body"])
        with smtplib.SMTP(self.cfg["smtp_host"], self.cfg["smtp_port"], timeout=10) as smtp:
            smtp.send_message(em)
        self.api_calls_executed += 1
        self.id_map[msg_row["message_id"]] = f"<{msg_row['message_id']}@norvale.example>"
        self.call_log.append({"system": "smtp", "role": msg_row["from_role"], "method": "SMTP", "path": msg_row["message_id"], "status": 250})


# ==============================================================================
# 4. ACTION-BOUNDARY GATEKEEPER (LOCAL + LIVE API 403 ENFORCEMENT)
# ==============================================================================

class ActionGateway:
    def __init__(self, sim):
        self.sim = sim

    def execute(self, d, role_id, action, target, params):
        role = ROLE_BY_ID[role_id]
        auth = role["write_authority"]
        limits = auth.get("limits", {})
        dt = d_str(self.sim.cur_date(d))

        # In live-apis mode, unauthorized attempts also hit the real target endpoint to capture native 403s
        if action not in auth["allowed_actions"]:
            if self.sim.bus.mode == "live-apis":
                try:
                    self.sim.bus._http_call("erp", role_id, "PUT", f"/api/resource/Item/{urllib.parse.quote(str(target))}", params)
                except urllib.error.HTTPError:
                    pass
            self.sim.bus.add(
                "audit_log", audit_id=self.sim.bus.next_id("AUD"), date=dt,
                role_id=role_id, attempted_action=action, target=str(target),
                outcome="BLOCKED_UNAUTHORIZED",
                rule_violated=f"{role_id} lacks '{action}' in write_authority.allowed_actions"
            )
            return False, f"BLOCKED: {role_id} is not authorized to execute {action}"

        if action == "issue_standard_quote_up_to_10pct":
            disc = params.get("discount_pct", 0.0)
            max_disc = limits.get("discount_pct", 10.0)
            if disc > max_disc:
                if self.sim.bus.mode == "live-apis":
                    try:
                        self.sim.bus._http_call("crm", role_id, "POST", "/api/v1/Quote", params)
                    except urllib.error.HTTPError:
                        pass
                self.sim.bus.add(
                    "audit_log", audit_id=self.sim.bus.next_id("AUD"), date=dt,
                    role_id=role_id, attempted_action=action, target=str(target),
                    outcome="BLOCKED_THRESHOLD_EXCEEDED",
                    rule_violated=f"Discount {disc}% exceeds {role_id} cap {max_disc}%"
                )
                return False, f"BLOCKED: Discount {disc}% exceeds {max_disc}%"

        if action == "commit_approved_master_data_cr":
            cr = next((r for r in self.sim.bus.tables['change_requests'] if r['cr_id'] == params.get('approved_cr_id')), None)
            approved = cr and cr['status'] == 'APPROVED' and cr['target_entity'] == target and any(
                r['ref_doc'] == cr['cr_id'] and r['assigned_to'] == cr['approver_role'] and r['decision'] == 'APPROVED'
                for r in self.sim.bus.tables['approval_tasks'])
            if not approved:
                self.sim.bus.add(
                    "audit_log", audit_id=self.sim.bus.next_id("AUD"), date=dt,
                    role_id=role_id, attempted_action=action, target=str(target),
                    outcome="BLOCKED_MISSING_CR",
                    rule_violated="Master-data mutation requires an APPROVED ServiceNow CR"
                )
                return False, "BLOCKED: Missing approved ServiceNow CR"

        self.sim.bus.add(
            "audit_log", audit_id=self.sim.bus.next_id("AUD"), date=dt,
            role_id=role_id, attempted_action=action, target=str(target),
            outcome="ALLOWED", rule_violated=""
        )
        return True, "ALLOWED"


# ==============================================================================
# 5. COGNITIVE EMPLOYEE ENGINE (LIVE LLM DECISION LOOP + MASKED OBSERVATION)
# ==============================================================================

class CognitiveEmployeeEngine:
    """
    Every awakened role agent observes ONLY the tables permitted by its read_scope
    up to day d, consults its working memory notes, and either:
    - Calls a live LLM (--llm-model) to choose its action, arguments, memory note,
      and email prose, OR
    - Runs the grounded policy engine when --llm-model is omitted.
    """
    def __init__(self, sim, llm_model=None):
        self.sim = sim
        self.llm_model = llm_model
        self.api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.api_base = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        if self.llm_model and not self.api_key:
            raise RuntimeError(
                f"--llm-model '{self.llm_model}' requested, but neither OPENROUTER_API_KEY nor OPENAI_API_KEY is set."
            )

        self.inbox = defaultdict(list)
        self.unanswered_escalations = []
        self.stage_flags = set()
        self.cooldown = {}
        self.llm_call_count = 0

    def _ready(self, role_id, key, d, gap):
        if d - self.cooldown.get((role_id, key), -999) < gap:
            return False
        self.cooldown[(role_id, key)] = d
        return True

    def observe_permitted_tables(self, role_id, d):
        role = ROLE_BY_ID[role_id]
        allowed = set(role["read_scope"]["tables"])
        cur_dt = d_str(self.sim.cur_date(d))
        view = {}
        for tbl in allowed:
            rows = self.sim.bus.tables.get(tbl, [])
            out = []
            for r in rows:
                r_dt = (
                    r.get("date") or r.get("order_date") or r.get("created_date")
                    or r.get("opened_date") or r.get("release_date")
                    or (r.get("start_time", "")[:10]) or (r.get("timestamp", "")[:10])
                )
                if not r_dt or r_dt <= cur_dt:
                    out.append(r)
            view[tbl] = out
        return view

    def _observable_stock_from_movements(self, mvts, material_code):
        qty = 0.0
        for m in mvts:
            if m["code"] != material_code:
                continue
            mt = str(m["mvt_type"])
            if mt in ("101", "561"):
                qty += float(m["qty"])
            elif mt in ("261", "601", "122"):
                qty -= float(m["qty"])
        return max(0.0, round(qty, 2))

    def _record_note(self, d, role_id, topic, belief_text, evidence_refs):
        self.sim.bus.add(
            "employee_notes", note_id=self.sim.bus.next_id("NOTE"),
            date=d_str(self.sim.cur_date(d)), role_id=role_id,
            person=ROLE_BY_ID[role_id]["person"], topic=topic,
            belief_summary=belief_text, evidence_refs=str(evidence_refs)
        )

    def _send_chat(self, d, from_role, to_role, channel, text, hour=10, answered=True):
        row = self.sim.bus.add(
            "chat_messages", chat_id=self.sim.bus.next_id("CHAT"),
            timestamp=ts_str(self.sim.cur_date(d), hour, self.sim.rng.randint(1, 58)),
            channel=channel, from_role=from_role, to_role=to_role,
            message=text, status="READ" if answered else "UNANSWERED"
        )
        self.sim.bus.emit_zulip_chat(row)
        return row["chat_id"]

    def _schedule_meeting(self, d, organizer, attendees, title, notes, hour=14):
        row = self.sim.bus.add(
            "calendar_events", event_id=self.sim.bus.next_id("MEET"), date=d_str(self.sim.cur_date(d)),
            start_time=ts_str(self.sim.cur_date(d), hour, 0),
            duration_min=45, organizer_role=organizer,
            attendee_roles=",".join(attendees), title=title,
            status="COMPLETED", meeting_notes=notes
        )
        self.sim.bus.emit_caldav_event(row)
        return row["event_id"]

    def _decide_role_turn(self, d, role_id, item, obs_view):
        """
        Central decision function: calls live LLM when --llm-model is configured,
        or executes the grounded role policy fallback when running offline.
        """
        if self.llm_model:
            return self._invoke_llm_decision(d, role_id, item, obs_view)
        return self._grounded_policy_fallback(d, role_id, item)

    def _invoke_llm_decision(self, d, role_id, item, obs_view):
        role = ROLE_BY_ID[role_id]
        my_notes = [n for n in self.sim.bus.tables["employee_notes"] if n["role_id"] == role_id][-5:]
        compact_tables = {k: v[-8:] for k, v in obs_view.items()}
        sys_prompt = (
            f"You are {role['person']}, {role['title']} ({role_id}) at Norvale Consumer Care BV.\n"
            f"Mandate KPI: {json.dumps(role['mandate'])}\n"
            f"Write Authority: {json.dumps(role['write_authority'])}\n"
            f"Escalation Edge: {json.dumps(role['escalation_edge'])}\n"
            "Analyze the trigger event, your working memory notes, and your visible database tables. "
            "Respect your write authority: if you cannot mutate master data directly, submit a Change Request (SUBMIT_CR) "
            "to your escalation_edge manager. Cite exact record IDs and numbers from the input.\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            '{"action": "SUBMIT_CR|APPROVE_CR|ISSUE_DEVIATION|PROMO_ANNOUNCE|NOTIFY_ONLY", '
            '"cr_type": str, "target_entity": str, "field_name": str, "old_value": str, "new_value": str, '
            '"approver_role": str, "email_to": [str], "email_subject": str, "email_body": str, '
            '"memory_topic": str, "memory_note": str, "rationale": str}'
        )
        user_msg = json.dumps({
            "date": d_str(self.sim.cur_date(d)),
            "trigger_event": item,
            "my_recent_notes": my_notes,
            "visible_tables_tail": compact_tables
        })
        req = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps({
                "model": self.llm_model,
                "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}],
                "temperature": 0.2
            }).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            self.llm_call_count += 1
            content = raw["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(content)

    def _grounded_policy_fallback(self, d, role_id, item):
        trig = item["trigger"]
        if role_id == "BUYER_CHEM" and trig == "SUBMIT_KEMIX_CR":
            return {
                "action": "SUBMIT_CR", "cr_type": "SOURCE_LIST_UPDATE", "target_entity": "SLES,LAS",
                "field_name": "approved_vendor", "old_value": "BRENNER", "new_value": "KEMIX",
                "approver_role": "SC_MGR", "email_to": ["SC_MGR", "CFO"],
                "email_subject": "Escalation & CR: Switch SLES & LAS Primary Supplier to Kemix (-11% Unit Cost)",
                "email_body": (
                    f"Vikram, Claire,\n\nEscalating after unanswered chat check: across {item['chem_po_count']} chemical POs "
                    f"(EUR {item['tot_eur']:,.2f}), surfactant spend is +3.1% over budget. Kemix quoted SLES at EUR 1.26/kg "
                    f"(vs Brenner EUR 1.42/kg). Submitting CR for SC_MGR approval.\n\nPriya"
                ),
                "memory_topic": "Submitted Kemix Source CR",
                "memory_note": f"Submitted SOURCE_LIST_UPDATE CR to switch SLES/LAS to Kemix after {item['chem_po_count']} POs.",
                "rationale": f"11% unit cost reduction across {item['chem_po_count']} chemical POs"
            }

        if role_id == "PLANNER" and trig == "SLES_STOCK_CRITICAL":
            return {
                "action": "NOTIFY_ONLY", "email_to": ["QA_MGR", "PLANT_MGR", "BUYER_CHEM"],
                "email_subject": f"URGENT: SLES Unrestricted Stock at {item['obs_sles_kg']} kg After {', '.join(item['ncr_ids'])}",
                "email_body": (
                    f"Aisha, Tomas,\n\nObservable SLES inventory is down to {item['obs_sles_kg']} kg following {', '.join(item['ncr_ids'])}. "
                    f"Line SH faces imminent stoppage without a temporary deviation or expedited delivery.\n\nWei"
                ),
                "memory_topic": "SLES Stockout Warning",
                "memory_note": f"Alerted QA_MGR and PLANT_MGR that SLES dropped to {item['obs_sles_kg']} kg.",
                "rationale": f"SLES stock critical ({item['obs_sles_kg']} kg)"
            }

        if role_id == "QA_MGR" and trig == "EVALUATE_SLES_DEVIATION":
            ncr_str = ", ".join(n["ncr_id"] for n in item["ncrs"])
            return {
                "action": "ISSUE_DEVIATION", "target_entity": "SLES", "email_to": ["PLANNER", "PLANT_MGR", "QA_TECH"],
                "email_subject": "Issued DEV-0001: 21-Day Temporary SLES Spec Deviation (>=64.5% Active)",
                "email_body": (
                    f"Wei, Tomas, Nadia,\n\nWith SLES stock at {item['obs_sles_kg']} kg after {ncr_str}, I am authorizing "
                    f"DEV-0001 for 21 days (min 64.5% active). Marco—monitor Shampoo viscosity.\n\nAisha"
                ),
                "memory_topic": "Issued DEV-0001",
                "memory_note": f"Authorized 21-day deviation DEV-0001 for SLES ({item['obs_sles_kg']} kg cover).",
                "rationale": f"Prevent Line SH stockout ({item['obs_sles_kg']} kg cover) after {ncr_str}"
            }

        if role_id == "PROC_ENG" and trig == "PROPOSE_SALT_INCREASE":
            b_str = ", ".join(item["batch_ids"])
            return {
                "action": "SUBMIT_CR", "cr_type": "BOM_RECIPE_CHANGE", "target_entity": "SH_FORMULAS",
                "field_name": "NACL_kg_per_kg", "old_value": "0.015", "new_value": "0.018",
                "approver_role": "QA_MGR", "email_to": ["QA_MGR", "PLANT_MGR", "IT_ADMIN"],
                "email_subject": f"CR Request: Raise Shampoo NaCl 0.015 -> 0.018 kg/kg ({item['fail_count']} Low-Viscosity Failures)",
                "email_body": (
                    f"Aisha, Tomas,\n\nObserved {item['fail_count']} Shampoo failures ({item['rework_count']} REWORK, "
                    f"{item['scrap_count']} SCRAP; e.g. {b_str}, mean {item['mean_visc']} mPa.s). Submitting CR to raise NaCl to 0.018.\n\nMarco"
                ),
                "memory_topic": "Low Viscosity Diagnosis",
                "memory_note": f"Detected {item['fail_count']} low-viscosity Shampoo failures ({b_str}); proposing NaCl 0.015 -> 0.018.",
                "rationale": f"Compensate low viscosity across {item['fail_count']} batches ({b_str})"
            }

        if role_id == "PROC_ENG" and trig == "PROPOSE_SALT_REVERT":
            b_str = ", ".join(item["batch_ids"])
            return {
                "action": "SUBMIT_CR", "cr_type": "BOM_RECIPE_CHANGE", "target_entity": "SH_FORMULAS",
                "field_name": "NACL_kg_per_kg", "old_value": "0.018", "new_value": "0.015",
                "approver_role": "QA_MGR", "email_to": ["QA_MGR", "PLANT_MGR", "IT_ADMIN"],
                "email_subject": f"CR Request: Revert Shampoo NaCl 0.018 -> 0.015 kg/kg ({item['fail_count']} High-Viscosity Failures)",
                "email_body": (
                    f"Aisha, Tomas,\n\nObserved {item['fail_count']} high-viscosity Shampoo failures ({item['rework_count']} REWORK, "
                    f"{item['scrap_count']} SCRAP; e.g. {b_str}, mean {item['mean_visc']} mPa.s) since Brenner SLES returned. Reverting NaCl to 0.015.\n\nMarco"
                ),
                "memory_topic": "High Viscosity Overshoot Diagnosis",
                "memory_note": f"Detected {item['fail_count']} high-viscosity failures ({b_str}) after Brenner SLES returned; reverting NaCl.",
                "rationale": f"Remove obsolete salt compensation after {item['fail_count']} high-visc failures ({b_str})"
            }

        if role_id == "CCO" and trig == "LAUNCH_SPRING_PROMO":
            return {
                "action": "PROMO_ANNOUNCE", "promo_start": item["start_day"], "promo_end": item["end_day"],
                "email_to": ["COO", "PLANT_MGR", "SALES_OPS_MGR"],
                "email_subject": f"Spring Shampoo Promotion Confirmed (+30% Volume Days {item['start_day']}-{item['end_day']})",
                "email_body": f"Dirk, Tomas, Emma,\n\nRetail partners confirmed our 60-day Spring Shampoo promotion starting Day {item['start_day']}.\n\nSofia",
                "memory_topic": "Spring Promo Launch",
                "memory_note": f"Launched +30% Spring Shampoo promo for Days {item['start_day']}-{item['end_day']}.",
                "rationale": "Commercial promotion launch"
            }

        if role_id == "PLANT_MGR" and trig == "REQUEST_PM_DEFERRAL":
            return {
                "action": "SUBMIT_CR", "cr_type": "MAINTENANCE_DEFERRAL", "target_entity": item["vessel_id"],
                "field_name": "pm_deferred", "old_value": "False", "new_value": str(item["defer_until"]),
                "approver_role": "COO", "email_to": ["COO", "MAINT_LEAD"],
                "email_subject": f"Deferral Request: Postpone {item['vessel_id']} PM Until Day {item['defer_until']}",
                "email_body": (
                    f"Dirk,\n\n{item['vessel_id']} is at {item['batch_count']}/40 batches (vibration {item['latest_vib']} mm/s). "
                    f"Deferring PM until Day {item['defer_until']} protects Spring promo OTIF.\n\nTomas"
                ),
                "memory_topic": "V-112 PM Deferral",
                "memory_note": f"Requested COO sign-off to defer {item['vessel_id']} PM until Day {item['defer_until']}.",
                "rationale": f"Defer {item['vessel_id']} PM during Spring promo"
            }

        if role_id == "BUYER_PACK" and trig == "RESUBMIT_FRG_SAFETY_STOCK":
            pos_str = ", ".join(item["sample_pos"])
            return {
                "action": "SUBMIT_CR", "cr_type": "SAFETY_STOCK_UPDATE", "target_entity": "FRG-*",
                "field_name": "safety_days", "old_value": "4", "new_value": "12",
                "approver_role": "SC_MGR", "email_to": ["SC_MGR", "PLANNER"],
                "email_subject": f"Resubmitted CR: Raise Fragrance Safety Stock 4 -> 12 Days ({item['late_count']}/{item['total_count']} Late POs)",
                "email_body": (
                    f"Vikram,\n\nPer your feedback, here is the audit: {item['late_count']} of {item['total_count']} Aromatica POs "
                    f"arrived late (e.g. {pos_str}). Resubmitting at 12 days instead of 15.\n\nHannah"
                ),
                "memory_topic": "Resubmitted Fragrance Safety Stock CR",
                "memory_note": f"Resubmitted safety_days=12 CR with {item['late_count']} late Aromatica POs.",
                "rationale": f"Buffer against {item['late_count']} late Aromatica deliveries ({pos_str})"
            }

        if role_id == "ACCT_EXEC" and trig == "RESUBMIT_MANGO_SKU_14PCT":
            return {
                "action": "SUBMIT_CR", "cr_type": "NEW_SKU_LAUNCH", "target_entity": item["sku_id"],
                "field_name": "active", "old_value": "None", "new_value": "True",
                "approver_role": "CFO", "email_to": ["CCO", "CFO", "CEO", "SALES_OPS_MGR"],
                "email_subject": f"Revised Proposal: Launch {item['sku_name']} ({item['sku_id']}) at 14.0% Discount",
                "email_body": f"Claire, Henrik, Sofia,\n\nFreshMart agreed to our 14.0% counter-offer (down from 16%). Submitting formal CR.\n\nLucas",
                "memory_topic": "Renegotiated Mango SKU to 14%",
                "memory_note": f"FreshMart accepted 14% discount for {item['sku_id']}; submitted CR to CFO.",
                "rationale": f"FreshMart summer listing for {item['sku_id']} at renegotiated 14% discount"
            }

        if role_id == "QA_MGR" and trig == "SUBMIT_BRENNER_REVERT_CR":
            ncr_str = ", ".join(item["ncr_ids"])
            return {
                "action": "SUBMIT_CR", "cr_type": "SOURCE_LIST_UPDATE", "target_entity": "SLES,LAS",
                "field_name": "approved_vendor", "old_value": "KEMIX", "new_value": "BRENNER",
                "approver_role": "SC_MGR", "email_to": ["SC_MGR", "COO", "BUYER_CHEM"],
                "email_subject": f"CR Escalation: Revert SLES & LAS to Brenner ({item['ncr_count']} Rejected Kemix Lots)",
                "email_body": (
                    f"Vikram, Dirk,\n\nFollowing our triage meeting and {item['ncr_count']} Kemix NCRs ({ncr_str}), "
                    f"submitting CR to revert SLES and LAS sourcing to Brenner.\n\nAisha"
                ),
                "memory_topic": "Kemix Quality Termination",
                "memory_note": f"Accumulated {item['ncr_count']} Kemix NCRs ({ncr_str}); escalating source_list reversion to Brenner.",
                "rationale": f"Terminate Kemix sourcing after {item['ncr_count']} NCRs ({ncr_str})"
            }

        if trig == "APPROVE_PENDING_CR":
            cr = item["cr"]
            return {
                "action": "APPROVE_CR", "cr_id": cr["cr_id"],
                "email_to": [cr["requested_by"], "IT_ADMIN"],
                "email_subject": f"Approved {cr['cr_id']}: {cr['cr_type']} on {cr['target_entity']}",
                "email_body": (
                    f"Approved {cr['cr_id']} ({cr['target_entity']} {cr['field_name']}: {cr['old_value']} -> {cr['new_value']}). "
                    f"Lukas (IT_ADMIN), commit today.\n\n{ROLE_BY_ID[role_id]['person']}"
                ),
                "memory_topic": f"Approved {cr['cr_id']}",
                "memory_note": f"Signed off {cr['cr_type']} on {cr['target_entity']} ({cr['old_value']} -> {cr['new_value']}).",
                "rationale": f"Approved by {role_id} per {ROLE_BY_ID[role_id]['mandate']['kpi']}"
            }

        return {"action": "NOTIFY_ONLY"}

    def sense_from_observable_records(self, d):
        today = self.sim.cur_date(d)
        cur_dt = d_str(today)

        if today.weekday() == 0 and d > 0 and self._ready("COO", "WEEKLY_SYNC", d, gap=7):
            self._schedule_meeting(
                d, "COO", ["PLANT_MGR", "SC_MGR", "QA_MGR", "SALES_OPS_MGR", "PLANNER"],
                "Weekly Operations, OTIF & Quality Alignment",
                "Reviewed weekly batch yields, open customer backlog, and supplier lead-time adherence."
            )

        if "KEMIX_FLOW_STARTED" not in self.stage_flags and d >= 18:
            view = self.observe_permitted_tables("BUYER_CHEM", d)
            chem_pos = [p for p in view["purchase_orders"] if p["created_by"] == "BUYER_CHEM"]
            if len(chem_pos) >= 12:
                self.stage_flags.add("KEMIX_FLOW_STARTED")
                tot_eur = round(sum(float(p["total_eur"]) for p in chem_pos), 2)
                self._record_note(
                    d, "BUYER_CHEM", "Surfactant Cost Variance",
                    f"Observed {len(chem_pos)} chemical POs totaling EUR {tot_eur:,.2f}; Brenner pricing keeps spend above -4% mandate.",
                    f"{len(chem_pos)} POs in purchase_orders"
                )
                self._send_chat(
                    d, "BUYER_CHEM", "SC_MGR", "#supply-chain",
                    f"Hey Vikram, surfactant spend across {len(chem_pos)} POs (EUR {tot_eur:,.0f}) is still +3.1% over budget. Can we evaluate Kemix quotes?",
                    hour=11, answered=False
                )
                self.unanswered_escalations.append({
                    "kind": "KEMIX_ESCALATION", "asked_day": d, "chem_po_count": len(chem_pos), "tot_eur": tot_eur
                })

        for esc in list(self.unanswered_escalations):
            if esc["kind"] == "KEMIX_ESCALATION" and d - esc["asked_day"] >= 3:
                self.unanswered_escalations.remove(esc)
                self._send_chat(
                    d, "BUYER_CHEM", "SC_MGR", "#supply-chain",
                    "Following up on my message from 3 days ago—no reply yet, escalating formally via email and ServiceNow CR.",
                    hour=9, answered=True
                )
                self.sim.gateway.execute(d, "BUYER_CHEM", "update_source_list", "SLES,LAS", {"new_vendor": "KEMIX"})
                self.inbox["BUYER_CHEM"].append({
                    "trigger": "SUBMIT_KEMIX_CR", "chem_po_count": esc["chem_po_count"], "tot_eur": esc["tot_eur"]
                })

        if d % 14 == 5 and self._ready("QA_TECH", "MISSING_LOT_TRACE", d, gap=14):
            qa_view = self.observe_permitted_tables("QA_TECH", d)
            unscanned = [m for m in qa_view["goods_movements"] if str(m["mvt_type"]) == "261" and not str(m.get("lot_id", "")).strip()]
            if unscanned:
                sample = unscanned[-1]
                self._send_chat(
                    d, "QA_TECH", "WH_CLERK", "#quality-lab",
                    f"Bram, movement {sample['mvt_id']} (ref {sample['ref_doc']}, {sample['code']}) is missing lot_id in ERP—need lot trace before closing batch QC.",
                    hour=10, answered=True
                )
                self._send_chat(
                    d, "WH_CLERK", "QA_TECH", "#quality-lab",
                    f"Checked physical pick sheet for {sample['ref_doc']}—confirmed FIFO lot scan and updated traceability log.",
                    hour=11, answered=True
                )

        if "DEV_FLOW_STARTED" not in self.stage_flags and self.sim.source_list.get("SLES") == "KEMIX":
            qa_view = self.observe_permitted_tables("QA_MGR", d)
            plan_view = self.observe_permitted_tables("PLANNER", d)
            sles_mat_id = self.sim.materials["SLES"]["material_id"]
            sles_ncrs = [n for n in qa_view["non_conformances"] if n["material_id"] == sles_mat_id]
            obs_sles_kg = self._observable_stock_from_movements(plan_view["goods_movements"], "SLES")
            if len(sles_ncrs) >= 1 and obs_sles_kg <= 12500.0:
                self.stage_flags.add("DEV_FLOW_STARTED")
                self._record_note(
                    d, "PLANNER", "SLES Low Stock Alert",
                    f"Observable SLES inventory dropped to {obs_sles_kg} kg after {sles_ncrs[-1]['ncr_id']}.",
                    sles_ncrs[-1]["ncr_id"]
                )
                self.sim.gateway.execute(d, "PLANT_MGR", "release_qms_hold", sles_ncrs[-1]["lot_id"], {})
                self.inbox["PLANNER"].append({
                    "trigger": "SLES_STOCK_CRITICAL", "obs_sles_kg": obs_sles_kg, "ncr_ids": [n["ncr_id"] for n in sles_ncrs]
                })
                self.inbox["QA_MGR"].append({
                    "trigger": "EVALUATE_SLES_DEVIATION", "obs_sles_kg": obs_sles_kg, "ncrs": sles_ncrs
                })

        eng_view = self.observe_permitted_tables("PROC_ENG", d)
        win_start = d_str(self.sim.cur_date(max(0, d - 18)))
        sh_fails = [
            b for b in eng_view["batches"]
            if b["sku_mes_code"].startswith("SH-") and b["result"] != "PASS" and b["start_time"][:10] >= win_start
        ]
        low_fails = [b for b in sh_fails if float(b["viscosity_mpas"]) < 4500.0]
        high_fails = [b for b in sh_fails if float(b["viscosity_mpas"]) > 7500.0]

        impl_bom_crs = [c for c in self.sim.bus.tables["change_requests"] if c["cr_type"] == "BOM_RECIPE_CHANGE" and c["status"] == "IMPLEMENTED"]
        believed_salt = float(impl_bom_crs[-1]["new_value"]) if impl_bom_crs else 0.015

        if len(low_fails) >= 4 and abs(believed_salt - 0.015) < 1e-6 and self._ready("PROC_ENG", "LOW_VISC", d, gap=25):
            self.sim.gateway.execute(d, "PROC_ENG", "edit_bom_directly", "SH_FORMULAS", {"new_nacl": 0.018})
            self.inbox["PROC_ENG"].append({
                "trigger": "PROPOSE_SALT_INCREASE", "fail_count": len(low_fails),
                "rework_count": sum(1 for b in low_fails if b["result"] == "REWORK"),
                "scrap_count": sum(1 for b in low_fails if b["result"] == "SCRAP"),
                "batch_ids": [b["batch_id"] for b in low_fails[-5:]],
                "mean_visc": round(sum(float(b["viscosity_mpas"]) for b in low_fails) / len(low_fails), 1)
            })

        if len(high_fails) >= 4 and abs(believed_salt - 0.018) < 1e-6 and self._ready("PROC_ENG", "HIGH_VISC", d, gap=25):
            self.inbox["PROC_ENG"].append({
                "trigger": "PROPOSE_SALT_REVERT", "fail_count": len(high_fails),
                "rework_count": sum(1 for b in high_fails if b["result"] == "REWORK"),
                "scrap_count": sum(1 for b in high_fails if b["result"] == "SCRAP"),
                "batch_ids": [b["batch_id"] for b in high_fails[-5:]],
                "mean_visc": round(sum(float(b["viscosity_mpas"]) for b in high_fails) / len(high_fails), 1)
            })

        if "V112_ESCALATION_STARTED" not in self.stage_flags and d >= 85:
            op_view = self.observe_permitted_tables("SHIFT_OP", d)
            v112_sens = [s for s in op_view["sensor_readings_summary"] if s["vessel_id"] == "V-112"]
            if len(v112_sens) >= 34 and float(v112_sens[-1]["peak_vibration_mm_s"]) >= 2.2:
                self.stage_flags.add("V112_ESCALATION_STARTED")
                latest_vib = v112_sens[-1]["peak_vibration_mm_s"]
                self._send_chat(
                    d, "SHIFT_OP", "MAINT_LEAD", "#plant-ops",
                    f"Jan, V-112 vibration hit {latest_vib} mm/s on {v112_sens[-1]['batch_id']} and PM counter is near limit.",
                    hour=14, answered=False
                )
                self._send_chat(
                    d + 1, "SHIFT_OP", "MAINT_LEAD", "#plant-ops",
                    f"Ping #2 on V-112 ({latest_vib} mm/s)—are we taking it down for PM this week?",
                    hour=8, answered=True
                )
                self._send_chat(
                    d + 1, "MAINT_LEAD", "PLANT_MGR", "#plant-ops",
                    f"Tomas, V-112 has run {len(v112_sens)} batches since last PM (vib {latest_vib} mm/s). Need maintenance window.",
                    hour=10, answered=True
                )
                self.inbox["CCO"].append({"trigger": "LAUNCH_SPRING_PROMO", "start_day": d + 2, "end_day": d + 62})
                self.inbox["PLANT_MGR"].append({
                    "trigger": "REQUEST_PM_DEFERRAL", "vessel_id": "V-112",
                    "batch_count": len(v112_sens), "latest_vib": latest_vib, "defer_until": d + 62
                })

        if "FRG_AUDIT_STARTED" not in self.stage_flags and d >= 75:
            pack_view = self.observe_permitted_tables("BUYER_PACK", d)
            aroma_id = self.sim.vendors["AROMA"]["vendor_id"]
            aroma_pos = [p for p in pack_view["purchase_orders"] if p["vendor_id"] == aroma_id and p["status"] == "RECEIVED"]
            mvt_101 = {m["ref_doc"]: m["date"] for m in pack_view["goods_movements"] if str(m["mvt_type"]) == "101"}
            late_pos = [p["po_id"] for p in aroma_pos if mvt_101.get(p["po_id"], "") > p["expected_date"]]
            if len(late_pos) >= 8:
                self.stage_flags.add("FRG_AUDIT_STARTED")
                cr_rej = self.sim.bus.next_id("CR")
                cr_row = self.sim.bus.add(
                    "change_requests", cr_id=cr_rej, created_date=cur_dt, cr_type="SAFETY_STOCK_UPDATE",
                    requested_by="BUYER_PACK", approver_role="SC_MGR", executed_by="IT_ADMIN",
                    target_entity="FRG-*", field_name="safety_days", old_value="4", new_value="15",
                    justification="Initial request to jump fragrance safety stock from 4 to 15 days",
                    kpi_cited="packaging_and_fragrance_availability", status="REJECTED",
                    thread_id=f"TH-2027-{d:03d}-FRG-REJ"
                )
                self.sim.bus.emit_itsm_ticket("BUYER_PACK", "change_requests", cr_rej, cr_row)
                apr_row = self.sim.bus.add(
                    "approval_tasks", approval_id=self.sim.bus.next_id("APR"), date=cur_dt,
                    ref_doc=cr_rej, gate_type="SAFETY_STOCK_UPDATE", requested_by="BUYER_PACK",
                    assigned_to="SC_MGR", decision="REJECTED",
                    rationale="Rejected 15-day jump: ties up too much cash. Resubmit at 12 days with exact late PO list."
                )
                self.sim.bus.emit_itsm_ticket("SC_MGR", "approval_tasks", apr_row["approval_id"], apr_row)
                self.sim._send_email(
                    d, "SC_MGR", ["BUYER_PACK"], f"TH-2027-{d:03d}-FRG-REJ",
                    f"Rejected {cr_rej}: Fragrance Safety Stock Increase to 15 Days",
                    "Hannah,\n\nRejecting a jump to 15 days due to working capital targets. Compile the exact late PO IDs "
                    "and resubmit at 12 days.\n\nVikram"
                )
                self.inbox["BUYER_PACK"].append({
                    "trigger": "RESUBMIT_FRG_SAFETY_STOCK", "late_count": len(late_pos),
                    "total_count": len(aroma_pos), "sample_pos": late_pos[-4:]
                })

        if "MANGO_NEGOTIATION_STARTED" not in self.stage_flags and d == 168:
            self.stage_flags.add("MANGO_NEGOTIATION_STARTED")
            next_sku = f"SKU-{self.sim.next_fg_num}"
            self.sim.gateway.execute(d, "ACCT_EXEC", "issue_standard_quote_up_to_10pct", "FreshMart Retail BV", {"discount_pct": 16.0})
            apr_row = self.sim.bus.add(
                "approval_tasks", approval_id=self.sim.bus.next_id("APR"), date=cur_dt, ref_doc=next_sku,
                gate_type="CRM_DISCOUNT_OVER_10PCT", requested_by="ACCT_EXEC", assigned_to="CFO",
                decision="REJECTED", rationale="16.0% discount breaches 38% gross margin floor; max approved is 14.0%"
            )
            self.sim.bus.emit_itsm_ticket("CFO", "approval_tasks", apr_row["approval_id"], apr_row)
            self.sim._send_email(
                d, "CFO", ["ACCT_EXEC", "CCO"], f"TH-2027-{d:03d}-MANGO",
                f"Rejected 16% Discount for {next_sku} — Counter-Offer Max 14%",
                "Lucas, Sofia,\n\n16% discount puts Mango Shampoo 400ml below our 38% gross margin floor. "
                "Renegotiate FreshMart to 14% and resubmit.\n\nClaire"
            )
            self.inbox["ACCT_EXEC"].append({
                "trigger": "RESUBMIT_MANGO_SKU_14PCT", "sku_id": next_sku, "sku_name": "Norvale Shampoo Mango Sunset 400ml"
            })

        if "KEMIX_REVERT_STARTED" not in self.stage_flags and self.sim.source_list.get("SLES") == "KEMIX":
            qa_view = self.observe_permitted_tables("QA_MGR", d)
            kemix_ids = {self.sim.vendors["KEMIX"]["vendor_id"], self.sim.vendors["KEMIX"]["alt_vendor_id"]}
            kemix_ncrs = [n for n in qa_view["non_conformances"] if n["vendor_id"] in kemix_ids]
            if len(kemix_ncrs) >= 4:
                self.stage_flags.add("KEMIX_REVERT_STARTED")
                self._schedule_meeting(
                    d, "QA_MGR", ["SC_MGR", "COO", "PLANT_MGR", "PROC_ENG"],
                    "Emergency Supplier Quality Escalation: Kemix Surfactant Failures",
                    f"Reviewed {len(kemix_ncrs)} Kemix NCRs ({', '.join(n['ncr_id'] for n in kemix_ncrs)}). Agreed to revert SLES/LAS to Brenner."
                )
                self.inbox["QA_MGR"].append({
                    "trigger": "SUBMIT_BRENNER_REVERT_CR", "ncr_count": len(kemix_ncrs), "ncr_ids": [n["ncr_id"] for n in kemix_ncrs]
                })

    def step(self, d):
        self.sense_from_observable_records(d)
        for role_id in [r["role_id"] for r in ROLES]:
            while self.inbox[role_id]:
                item = self.inbox[role_id].pop(0)
                self._process_role_item(d, role_id, item)

    def _process_role_item(self, d, role_id, item):
        obs_view = self.observe_permitted_tables(role_id, d)
        plan = self._decide_role_turn(d, role_id, item, obs_view)
        self.apply_role_plan(d, role_id, item, plan)

    def apply_role_plan(self, d, role_id, item, plan):
        """Execute a selected plan; separate from perception/decision for inspection."""
        dt = d_str(self.sim.cur_date(d))
        act = plan.get("action", "NOTIFY_ONLY")

        if plan.get("memory_note"):
            self._record_note(d, role_id, plan.get("memory_topic", item["trigger"]), plan["memory_note"], item.get("trigger", ""))

        th = item.get("cr", {}).get("thread_id") or f"TH-2027-{d:03d}-{role_id}"
        if plan.get("email_to"):
            self.sim._send_email(d, role_id, plan["email_to"], th, plan["email_subject"], plan["email_body"])

        if act == "PROMO_ANNOUNCE":
            self.sim.promo_window = (int(plan.get("promo_start", d + 1)), int(plan.get("promo_end", d + 61)))

        elif act == "ISSUE_DEVIATION":
            ok, _ = self.sim.gateway.execute(d, role_id, "issue_temporary_deviation", plan.get("target_entity", "SLES"), {"days": 21})
            if ok:
                self.sim.deviation_until_day = d + 21
                self.sim.bus.add("deviations", deviation_id="DEV-0001", date=dt, material_code=plan.get("target_entity", "SLES"),
                                 authorized_by=role_id, valid_days=21, reason=plan.get("rationale", ""))
                self.sim.decision_traces.append(dict(
                    trace_id=f"DEC-{len(self.sim.decision_traces)+1:02d}", day=d, date=dt,
                    actor=role_id, approver=role_id, executor="QA_TECH", cr_id="DEV-0001", thread_id=th,
                    trigger=plan.get("rationale", ""),
                    authority_gate="PLANT_MGR hold-override blocked; QA_MGR exercised sole deviation authority",
                    action="Issued 21-day Temporary Quality Deviation DEV-0001 for SLES",
                    downstream_effect="Borderline Kemix SLES lots enter Shampoo production"
                ))

        elif act == "SUBMIT_CR":
            approver = plan["approver_role"]
            cr_id = self.sim.bus.next_id("CR")
            cr_row = self.sim.bus.add(
                "change_requests", cr_id=cr_id, created_date=dt, cr_type=plan["cr_type"],
                requested_by=role_id, approver_role=approver, executed_by="IT_ADMIN",
                target_entity=plan["target_entity"], field_name=plan["field_name"],
                old_value=str(plan["old_value"]), new_value=str(plan["new_value"]),
                justification=plan.get("rationale", ""), kpi_cited=ROLE_BY_ID[role_id]["mandate"]["kpi"],
                status="PENDING_APPROVAL", thread_id=th
            )
            self.sim.bus.emit_itsm_ticket(role_id, "change_requests", cr_id, cr_row)
            self.inbox[approver].append({"trigger": "APPROVE_PENDING_CR", "cr": cr_row})

        elif act == "APPROVE_CR":
            cr = item["cr"]
            cr["status"] = "APPROVED"
            apr_row = self.sim.bus.add(
                "approval_tasks", approval_id=self.sim.bus.next_id("APR"), date=dt, ref_doc=cr["cr_id"],
                gate_type=cr["cr_type"], requested_by=cr["requested_by"], assigned_to=role_id,
                decision="APPROVED", rationale=plan.get("rationale", "")
            )
            self.sim.bus.emit_itsm_ticket(role_id, "approval_tasks", apr_row["approval_id"], apr_row)
            ok, _ = self.sim.gateway.execute(d, "IT_ADMIN", "commit_approved_master_data_cr", cr["target_entity"], {"approved_cr_id": cr["cr_id"]})
            if ok:
                self._apply_mutation(d, cr, role_id)
                cr["status"] = "IMPLEMENTED"

    def _apply_mutation(self, d, cr, approver_role):
        dt = d_str(self.sim.cur_date(d))
        ctype = cr["cr_type"]
        if ctype == "SOURCE_LIST_UPDATE":
            new_v = cr["new_value"]
            for code in cr["target_entity"].split(","):
                c_clean = code.strip()
                self.sim.source_list[c_clean] = new_v
                self.sim.bus.emit_erp_doc("IT_ADMIN", "Item", f"ITEM-SUP-{c_clean}-{d}", {
                    "item_code": self.sim.materials[c_clean]["material_id"],
                    "default_supplier": self.sim.vendors[new_v]["vendor_id"]
                }, submit=False)
            eff = "Switched SLES/LAS to Kemix (-11% cost, lower active strength)" if new_v == "KEMIX" else "Reverted SLES/LAS to full-strength Brenner"
        elif ctype == "BOM_RECIPE_CHANGE":
            new_salt = float(cr["new_value"])
            for fid in self.sim.salt_ratio:
                self.sim.salt_ratio[fid] = new_salt
                self.sim.formulas[fid]["version"] += 1
            for s in self.sim.skus:
                if s["family"] == "SH":
                    s["bom"]["NACL"] = round(new_salt * s["pack_size_kg"] * 1000, 3)
                    self.sim.bus.emit_erp_doc("IT_ADMIN", "BOM", f"BOM-{s['sku_id']}-v{self.sim.formulas[s['formula_id']]['version']}", {
                        "item": s["sku_id"], "quantity": 1000, "nacl_qty": s["bom"]["NACL"]
                    }, submit=True)
            eff = f"Updated Shampoo NaCl ratio to {new_salt} kg/kg"
        elif ctype == "MAINTENANCE_DEFERRAL":
            until_d = int(cr["new_value"])
            for v in self.sim.vessels:
                if v["vessel_id"] == cr["target_entity"]:
                    v["pm_deferred"] = True
            self.sim.undefer_day = until_d
            eff = f"Deferred {cr['target_entity']} PM until Day {until_d}"
        elif ctype == "SAFETY_STOCK_UPDATE":
            self.sim.safety_days["FRG"] = int(cr["new_value"])
            eff = f"Updated fragrance safety stock to {cr['new_value']} days"
        elif ctype == "NEW_SKU_LAUNCH":
            FRAGRANCES["MAN"] = "Mango Sunset"
            self.sim.materials["FRG-MAN"] = dict(
                material_id="MAT-10099", code="FRG-MAN", mes_code="FRGMAN",
                name="Fragrance Mango Sunset", category="ROH", uom="KG", std_cost=19.20, attr=None, active=True
            )
            SUPPLIERS["AROMA"][2]["FRG-MAN"] = (19.20, (9, 3), None)
            self.sim.source_list["FRG-MAN"] = "AROMA"
            sku_id = f"SKU-{self.sim.next_fg_num}"
            cr["target_entity"] = sku_id
            new_sku = self.sim._register_sku("SH", "MAN", "BOT400", sku_id, weight=1.15)
            self.sim.next_fg_num += 1
            new_sku["daily_units"] = sum(s["daily_units"] for s in self.sim.skus if s["family"] == "SH" and s is not new_sku) / 4.0
            cur_salt = self.sim.salt_ratio[next(iter(self.sim.salt_ratio))]
            new_sku["bom"]["NACL"] = round(cur_salt * new_sku["pack_size_kg"] * 1000, 3)
            self.sim.formulas[new_sku["formula_id"]] = dict(
                formula_id=new_sku["formula_id"], family="SH", fragrance="MAN",
                version=1, temp_c=45, mix_min=90, rpm=120
            )
            self.sim.salt_ratio[new_sku["formula_id"]] = cur_salt
            self.sim.bus.emit_erp_doc("IT_ADMIN", "Item", sku_id, {"item_code": sku_id, "item_name": new_sku["name"]}, submit=False)
            eff = f"Launched {new_sku['name']} ({sku_id}) and FRG-MAN"
        else:
            eff = "Committed CR"

        self.sim.decision_traces.append(dict(
            trace_id=f"DEC-{len(self.sim.decision_traces)+1:02d}", day=d, date=dt,
            actor=cr["requested_by"], approver=approver_role, executor="IT_ADMIN",
            cr_id=cr["cr_id"], thread_id=cr["thread_id"],
            trigger=cr["justification"],
            authority_gate=f"{cr['requested_by']} -> {approver_role} approval -> IT_ADMIN ActionGateway commit",
            action=f"{ctype}: {cr['target_entity']} {cr['field_name']} {cr['old_value']} -> {cr['new_value']}",
            downstream_effect=eff
        ))


# ==============================================================================
# 6. UNIFIED ENTERPRISE SIMULATION V4
# ==============================================================================

class EnterpriseSimV4:
    def __init__(self, seed=42, days=365, n_skus=18, llm_model=None, mode="dry-run", api_config=None):
        self.rng = random.Random(seed)
        self.start_date = date(2027, 1, 1)
        self.days = days
        self.n_skus = n_skus
        self.bus = SoftwareBus(mode=mode, config_path=api_config)

        self.materials = {}
        self.skus = []
        self.formulas = {}
        self.vendors = {}
        self.source_list = {}
        self.accounts = {}
        self.vessels = []

        self.stock = defaultdict(list)
        self.on_order = defaultdict(float)
        self.open_pos = []
        self.open_so_lines = []
        self.vessel_queues = defaultdict(list)
        self.safety_days = defaultdict(lambda: 4)
        self.salt_ratio = {}
        self.deviation_until_day = -1
        self.promo_window = (-1, -1)
        self.undefer_day = None

        self.cash_eur = 850000.0
        self.pending_ar_collections = []
        self.pending_ap_payments = []

        self.decision_traces = []
        self.email_threads = {}

        self._build_master_world()
        self.gateway = ActionGateway(self)
        self.cognition = CognitiveEmployeeEngine(self, llm_model=llm_model)

    def cur_date(self, day_idx):
        return self.start_date + timedelta(days=day_idx)

    def _post_gl(self, day_idx, ref_doc, debit_acct, credit_acct, amount_eur, memo):
        amt = round(float(amount_eur), 2)
        if amt <= 0:
            return
        je_id = self.bus.next_id("JE")
        dt = d_str(self.cur_date(day_idx))
        self.bus.add("gl_journal_entries", je_id=je_id, line_no=1, date=dt, ref_doc=ref_doc,
                     account=debit_acct, debit_eur=amt, credit_eur=0.0, memo=memo)
        self.bus.add("gl_journal_entries", je_id=je_id, line_no=2, date=dt, ref_doc=ref_doc,
                     account=credit_acct, debit_eur=0.0, credit_eur=amt, memo=memo)

    def _build_master_world(self):
        mat_seq = 10001
        for code, (name, uom, attr) in RAWS.items():
            std_cost = next(v[code][0] for _, _, v in SUPPLIERS.values() if code in v)
            self.materials[code] = dict(
                material_id=f"MAT-{mat_seq}", code=code, mes_code=("SLES70" if code == "SLES" else code),
                name=name, category="ROH", uom=uom, std_cost=std_cost, attr=attr, active=True
            )
            mat_seq += 1

        for fk, fname in FRAGRANCES.items():
            code = f"FRG-{fk}"
            self.materials[code] = dict(
                material_id=f"MAT-{mat_seq}", code=code, mes_code=f"FRG{fk}",
                name=f"Fragrance {fname}", category="ROH", uom="KG", std_cost=18.50, attr=None, active=True
            )
            mat_seq += 1

        for pk, (pname, _) in PACKS.items():
            self.materials[pk] = dict(
                material_id=f"MAT-{mat_seq}", code=pk, mes_code=pk,
                name=pname, category="VERP", uom="EA", std_cost=0.11, attr=None, active=True
            )
            mat_seq += 1

        self.materials["CAP28"] = dict(
            material_id=f"MAT-{mat_seq}", code="CAP28", mes_code="CAP28",
            name="Cap 28mm Flip-Top", category="VERP", uom="EA", std_cost=0.03, attr=None, active=True
        )
        mat_seq += 1

        combos = {f: [(fr, pk) for fr in FRAGRANCES for pk in FAMILIES[f]["packs"]] for f in FAMILIES}
        for c in combos.values():
            self.rng.shuffle(c)
        fg_seq = 30001
        idx = 0
        while len(self.skus) < self.n_skus and any(combos.values()):
            f = list(FAMILIES.keys())[idx % len(FAMILIES)]
            idx += 1
            if combos[f]:
                fr, pk = combos[f].pop()
                self._register_sku(f, fr, pk, f"SKU-{fg_seq}")
                fg_seq += 1
        self.next_fg_num = fg_seq

        for s in self.skus:
            fid = s["formula_id"]
            if fid not in self.formulas:
                fam = FAMILIES[s["family"]]
                self.formulas[fid] = dict(
                    formula_id=fid, family=s["family"], fragrance=s["fragrance"],
                    version=1, temp_c=fam["temp"], mix_min=fam["mix"], rpm=fam["rpm"]
                )
            if s["family"] == "SH":
                self.salt_ratio[fid] = FAMILIES["SH"]["formula"]["NACL"]

        stagger = [(0.10, 8), (0.29, 33), (0.14, 14), (0.22, 24), (0.08, 6), (0.19, 19)]
        s_idx = 0
        for li, f in enumerate(FAMILIES.keys(), 1):
            for vnum in (1, 2):
                w, spm = stagger[s_idx]
                s_idx += 1
                self.vessels.append(dict(
                    vessel_id=f"V-1{li}{vnum}", line_id=f"LINE-{f}", family=f,
                    wear=w, since_pm=spm, down_until=None, pm_deferred=False
                ))

        v_seq = 2001
        for vkey, (vname, country, sup_map) in SUPPLIERS.items():
            vid = f"VEND-{v_seq}"
            v_seq += 1
            alt_vid = f"VEND-{v_seq}" if vkey == "KEMIX" else ""
            if alt_vid:
                v_seq += 1
            self.vendors[vkey] = dict(
                vendor_key=vkey, vendor_id=vid, alt_vendor_id=alt_vid,
                name=vname, country=country, supplies=sup_map
            )

        for code, m in self.materials.items():
            if m["category"] in ("ROH", "VERP"):
                self.source_list[code] = next(vk for vk, s in SUPPLIERS.items() if code in s[2])

        c_seq = 5001
        for cname, country, tier, credit_lim in CUSTOMERS:
            cid = f"CUST-{c_seq}"
            c_seq += 1
            self.accounts[cid] = dict(
                account_id=cid, name=cname, country=country, tier=tier,
                credit_limit_eur=credit_lim, weight=self.rng.lognormvariate(0, 0.55)
            )

        line_cap_kg = BATCH_KG * BATCHES_PER_VESSEL_DAY * 2 * TARGET_UTILIZATION
        for f in FAMILIES:
            f_skus = [s for s in self.skus if s["family"] == f]
            tot_w = sum(s["weight"] for s in f_skus)
            for s in f_skus:
                s["daily_units"] = (line_cap_kg * (s["weight"] / tot_w)) / s["pack_size_kg"]

    def _register_sku(self, family, fragrance, pack, sku_id, weight=None):
        fam = FAMILIES[family]
        sz = PACKS[pack][1]
        ml = int(sz * 1000)
        bom = {("FRG-" + fragrance if k == "FRG" else k): round(v * sz * 1000, 3) for k, v in fam["formula"].items()}
        bom[pack] = 1000.0
        if pack.startswith("BOT"):
            bom["CAP28"] = 1000.0
        sku = dict(
            sku_id=sku_id, family=family, fragrance=fragrance, pack=pack, pack_size_kg=sz,
            formula_id=f"{family}-{fragrance}", mes_code=f"{family}-{fragrance}-{ml}",
            name=f"Norvale {fam['name']} {FRAGRANCES.get(fragrance, fragrance)} {ml}ml",
            unit_price=round(fam["unit_price"] * (sz / 0.40), 2),
            std_cost=round(fam["unit_price"] * (sz / 0.40) * 0.58, 2),
            weight=weight or self.rng.lognormvariate(0, 0.45),
            active=True, units_per_batch=int(BATCH_KG / sz), bom=bom
        )
        self.skus.append(sku)
        self.materials[sku_id] = dict(
            material_id=sku_id, code=sku_id, mes_code=sku["mes_code"],
            name=sku["name"], category="FERT", uom="EA", std_cost=sku["std_cost"], attr=None, active=True
        )
        return sku

    def cold_start_warmup(self):
        # Provision master records into live ERPNext/CRM when in live-apis mode
        if self.bus.mode == "live-apis":
            for v in self.vendors.values():
                self.bus.emit_erp_doc("IT_ADMIN", "Supplier", v["vendor_id"], {"supplier_name": v["name"], "country": v["country"]}, submit=False)
            for acc in self.accounts.values():
                self.bus.emit_erp_doc("IT_ADMIN", "Customer", acc["account_id"], {"customer_name": acc["name"], "territory": acc["country"]}, submit=False)
            for m in self.materials.values():
                self.bus.emit_erp_doc("IT_ADMIN", "Item", m["material_id"], {"item_code": m["material_id"], "item_name": m["name"], "stock_uom": m["uom"]}, submit=False)

        for s in self.skus:
            init_fg = max(240, round(s["daily_units"] * 7 / 12) * 12)
            self._receive_lot_unrestricted(0, s["sku_id"], init_fg, {}, "INTERNAL", init_balance=True)

        for code, m in self.materials.items():
            if m["category"] in ("ROH", "VERP"):
                use = self.daily_usage(code)
                if use <= 0:
                    continue
                vkey = self.source_list[code]
                vend = self.vendors[vkey]
                init_qty = round(use * 14, 1)
                props = self._sample_lot_props(vkey, code)
                self._receive_lot_unrestricted(0, code, init_qty, props, vend["vendor_id"], init_balance=True)

                eta_day = self.rng.choice([2, 4, 6, 8])
                po_qty = round(use * 7, 1)
                price = SUPPLIERS[vkey][2][code][0]
                po_id = self.bus.next_id("PO")
                buyer = "BUYER_CHEM" if m["category"] == "ROH" else "BUYER_PACK"
                po_row = self.bus.add(
                    "purchase_orders", po_id=po_id, vendor_id=vend["vendor_id"], order_date="2026-12-27",
                    expected_date=d_str(self.cur_date(eta_day)), status="APPROVED",
                    total_eur=round(po_qty * price, 2), created_by=buyer, approved_by="SC_MGR"
                )
                self.bus.add("purchase_order_lines", po_id=po_id, line_no=10, material_id=m["material_id"],
                             code=code, qty=po_qty, uom=m["uom"], unit_price=price)
                self.bus.emit_erp_doc(buyer, "Purchase Order", po_id, {
                    "supplier": vend["vendor_id"], "transaction_date": "2026-12-27",
                    "items": [{"item_code": m["material_id"], "qty": po_qty, "rate": price}]
                }, submit=True)
                self.open_pos.append(dict(po_id=po_id, code=code, qty=po_qty, unit_price=price,
                                          vkey=vkey, vendor_id=vend["vendor_id"], eta_day=eta_day, order_day=-4))
                self.on_order[code] += po_qty

        custs = list(self.accounts.values())
        for offset in range(6):
            so_id = self.bus.next_id("SO")
            opp_id = self.bus.next_id("OPP")
            cust = self.rng.choices(custs, [c["weight"] for c in custs])[0]
            due_day = 2 + offset * 2
            opp_row = self.bus.add("opportunities", opp_id=opp_id, account_id=cust["account_id"], stage="Closed-Won",
                                   discount_pct=4.0, approved_by="ACCT_EXEC", created_date="2026-12-22")
            self.bus.emit_crm_opportunity("ACCT_EXEC", opp_row)
            self.bus.add("sales_orders", so_id=so_id, opp_id=opp_id, account_id=cust["account_id"],
                         order_date="2026-12-23", promised_date=d_str(self.cur_date(due_day)), status="OPEN", created_by="SALES_OPS_MGR")
            chosen_skus = self.rng.sample(self.skus, 3)
            items_payload = []
            for lno, s in enumerate(chosen_skus, 1):
                q = max(48, round(s["daily_units"] * 1.4 / 12) * 12)
                self.bus.add("sales_order_lines", so_id=so_id, line_no=lno * 10, sku_id=s["sku_id"],
                             qty=q, unit_price=s["unit_price"], line_status="OPEN")
                self.open_so_lines.append(dict(so_id=so_id, line_no=lno * 10, sku_id=s["sku_id"],
                                               qty=q, unit_price=s["unit_price"], order_day=-8, due_day=due_day,
                                               account_id=cust["account_id"], escalated=False))
                items_payload.append({"item_code": s["sku_id"], "qty": q, "rate": s["unit_price"]})
            self.bus.emit_erp_doc("SALES_OPS_MGR", "Sales Order", so_id, {
                "customer": cust["account_id"], "transaction_date": "2026-12-23", "items": items_payload
            }, submit=True)

        self._send_email(
            0, "CEO", ["CFO", "COO", "CCO", "SC_MGR", "BUYER_CHEM"],
            thread_id="TH-2027-001",
            subject="2027 Operating Mandate: -4% Raw Material Cost-Down & 95% OTIF",
            body=(
                "Team,\n\nEntering Q1 2027, our surfactant and chemical spend is tracking 3.2% above Board budget. "
                "Claire and I are setting a hard -4.0% raw-material cost-down target for Supply Chain while holding "
                "OTIF >= 95% and Right-First-Time quality >= 96%.\n\nHenrik Lindholm, CEO"
            )
        )

    def daily_usage(self, code):
        return sum((s["bom"].get(code, 0.0) / 1000.0) * s["daily_units"] for s in self.skus if s["active"])

    def stock_qty(self, code):
        return sum(lot[1] for lot in self.stock[code])

    def _sample_lot_props(self, vkey, code):
        m = self.materials[code]
        q_spec = SUPPLIERS[vkey][2].get(code)
        if not m["attr"] or not q_spec or not q_spec[2]:
            return {}
        attr_name = m["attr"][0]
        mean, sd = q_spec[2]
        return {attr_name: round(self.rng.gauss(mean, sd), 2)}

    def _receive_lot_unrestricted(self, day_idx, code, qty, props, vendor_id, po_id="", wo_id="", init_balance=False, lot_id=None):
        lot_id = lot_id or self.bus.next_id("LOT")
        m = self.materials[code]
        self.stock[code].append([lot_id, qty, props])
        if init_balance or wo_id or not m["attr"]:
            mvt = "561" if init_balance else "101"
            self.bus.add("goods_movements", mvt_id=self.bus.next_id("MVT"), date=d_str(self.cur_date(day_idx)),
                         mvt_type=mvt, material_id=m["material_id"], code=code, lot_id=lot_id,
                         qty=round(qty, 3), uom=m["uom"], ref_doc=po_id or wo_id or "OPENING")
        return lot_id

    def _consume_fifo(self, day_idx, code, qty_needed, mvt_type, ref_doc):
        taken = []
        rem = qty_needed
        lots = self.stock[code]
        m = self.materials[code]
        while rem > 1e-6 and lots:
            take = min(rem, lots[0][1])
            lot_id = lots[0][0]
            taken.append((lot_id, take, lots[0][2]))
            lots[0][1] -= take
            rem -= take
            logged_lot = "" if (mvt_type == "261" and self.rng.random() < 0.02) else lot_id
            self.bus.add("goods_movements", mvt_id=self.bus.next_id("MVT"), date=d_str(self.cur_date(day_idx)),
                         mvt_type=mvt_type, material_id=m["material_id"], code=code, lot_id=logged_lot,
                         qty=round(take, 3), uom=m["uom"], ref_doc=ref_doc)
            if lots[0][1] <= 1e-6:
                lots.pop(0)
        return taken

    def _send_email(self, day_idx, from_role, to_roles, thread_id, subject, body, hour=9):
        msg_id = self.bus.next_id("MSG")
        in_reply_to = self.email_threads.get(thread_id, "")
        self.email_threads[thread_id] = msg_id
        sender = ROLE_BY_ID[from_role]
        recips = [ROLE_BY_ID[r]["email"] for r in to_roles]
        row = self.bus.add("emails", message_id=msg_id, thread_id=thread_id, in_reply_to=in_reply_to,
                           timestamp=ts_str(self.cur_date(day_idx), hour, self.rng.randint(5, 55)),
                           from_role=from_role, from_email=sender["email"],
                           to_roles=",".join(to_roles), to_emails=",".join(recips),
                           subject=subject, body=body)
        self.bus.emit_smtp_email(row)
        return msg_id

    def run(self):
        for _, _, action in self.stages():
            action()

    def _clock(self, day):
        self.day = day

    def _resume_maintenance(self):
        if self.undefer_day == self.day:
            for v in self.vessels:
                v['pm_deferred'] = False

    def stages(self):
        yield 'A · Seed company', 'Build catalogue, stock, cash, open orders; local mode.', self.cold_start_warmup
        for day in range(self.days):
            yield 'B · Advance clock', 'One calendar day; no real-time waiting.', lambda d=day: self._clock(d)
            c = self.cognition
            yield 'D/E · Observe and populate inboxes', 'V4 senses BEFORE today’s operations; some triggers also read private state.', lambda: c.sense_from_observable_records(self.day)
            for role in ROLES:
                rid = role['role_id']
                while c.inbox[rid]:
                    item = c.inbox[rid][0]
                    yield f'D · Permitted view for {rid}', 'Inspect this role’s tables and triggering item.', lambda r=rid, i=item: self._view(r, i)
                    yield f'F/G · Choose action for {rid}', 'Existing rule policy selects a plan; no model call.', lambda r=rid, i=item: self._decide(r, i)
                    yield f'I/J · Apply action for {rid}', 'V4 combines communication, partial authority checks and mutations here.', lambda r=rid, i=item: self._apply(r, i)
            yield 'C · Maintenance deferral expiry', 'Restore the existing maintenance policy when due.', self._resume_maintenance
            for label, name, why in [
                ('C/J · Receive purchases', '_step_procure_to_pay_arrivals', 'Due shipments, inspections, receipts and supplier invoices.'),
                ('J · Record customer orders', '_step_order_to_cash_sales', 'Sample customer demand, approve discounts, create orders.'),
                ('C/J · Ship and invoice', '_step_order_to_cash_fulfillment', 'Consume finished stock, invoice, update order status.'),
                ('C/J · Plan and produce', '_step_plan_and_produce', 'Release work orders, consume materials, make/inspect batches, maintain equipment.'),
                ('J · Reorder materials', '_step_procure_to_pay_reorder', 'Check stock coverage, select approval tier, place purchase orders.'),
                ('J · Settle payments', '_step_payment_settlements', 'Collect/pay due amounts; change cash, invoices and journal.'),
                ('J · Periodic governance records', '_step_periodic_governance', 'Month-end reports and scripted board meeting records.')]:
                yield label, why, lambda n=name: getattr(self, n)(self.day)
            yield 'K/L · Inspect local consistency', 'Diagnostic only: native readback and retries are not implemented.', self._stage_checks

    def _view(self, role, item):
        self.view = copy.deepcopy(self.cognition.observe_permitted_tables(role, self.day))
        self.trigger = copy.deepcopy(item)

    def _decide(self, role, item):
        self.plan = self.cognition._decide_role_turn(self.day, role, item, self.view)

    def _apply(self, role, item):
        self.cognition.apply_role_plan(self.day, role, item, self.plan)
        self.cognition.inbox[role].pop(0)

    def _stage_checks(self):
        s = self
        debits = sum(r['debit_eur'] for r in s.bus.tables['gl_journal_entries'])
        credits = sum(r['credit_eur'] for r in s.bus.tables['gl_journal_entries'])
        self.checks = dict(gl_balanced=abs(debits-credits)<0.01,
            stock_nonnegative=all(lot[1]>=-1e-6 for lots in s.stock.values() for lot in lots),
            no_external_calls=s.bus.api_calls_executed==s.cognition.llm_call_count==0)
        assert all(self.checks.values()), self.checks

    def _step_order_to_cash_sales(self, d):
        today = self.cur_date(d)
        if today.weekday() >= 5:
            return
        custs = list(self.accounts.values())
        orders_by_cust = defaultdict(list)

        for s in self.skus:
            if not s["active"]:
                continue
            mult = 7.0 / 5.0
            if s["family"] == "SH" and self.promo_window[0] <= d < self.promo_window[1]:
                mult *= 1.30
            if s["family"] == "BL":
                mult *= (1.0 + 0.22 * math.cos(2 * math.pi * (today.timetuple().tm_yday - 15) / 365.0))
            if self.rng.random() < 0.55:
                q = max(24, round((s["daily_units"] * mult / 0.55) / 12) * 12)
                cust = self.rng.choices(custs, [c["weight"] for c in custs])[0]
                orders_by_cust[cust["account_id"]].append((s, q))

        for cid, items in orders_by_cust.items():
            opp_id = self.bus.next_id("OPP")
            so_id = self.bus.next_id("SO")
            disc = round(self.rng.choice([0.0, 2.5, 5.0, 7.5, 12.0]), 1)
            approver = "CFO" if disc > 10.0 else "ACCT_EXEC"
            if disc > 10.0:
                apr = self.bus.add("approval_tasks", approval_id=self.bus.next_id("APR"), date=d_str(today),
                                   ref_doc=opp_id, gate_type="CRM_DISCOUNT_OVER_10PCT",
                                   requested_by="ACCT_EXEC", assigned_to="CFO", decision="APPROVED",
                                   rationale=f"Volume tier discount {disc}% clears 38% margin floor")
                self.bus.emit_itsm_ticket("CFO", "approval_tasks", apr["approval_id"], apr)
            opp_row = self.bus.add("opportunities", opp_id=opp_id, account_id=cid, stage="Closed-Won",
                                   discount_pct=disc, approved_by=approver, created_date=d_str(today))
            self.bus.emit_crm_opportunity("ACCT_EXEC", opp_row)
            due_day = d + 6
            self.bus.add("sales_orders", so_id=so_id, opp_id=opp_id, account_id=cid,
                         order_date=d_str(today), promised_date=d_str(self.cur_date(due_day)),
                         status="OPEN", created_by="SALES_OPS_MGR")
            items_payload = []
            for lno, (s, q) in enumerate(items, 1):
                net_price = round(s["unit_price"] * (1.0 - disc / 100.0), 2)
                self.bus.add("sales_order_lines", so_id=so_id, line_no=lno * 10, sku_id=s["sku_id"],
                             qty=q, unit_price=net_price, line_status="OPEN")
                self.open_so_lines.append(dict(so_id=so_id, line_no=lno * 10, sku_id=s["sku_id"],
                                               qty=q, unit_price=net_price, order_day=d, due_day=due_day,
                                               account_id=cid, escalated=False))
                items_payload.append({"item_code": s["sku_id"], "qty": q, "rate": net_price})
            self.bus.emit_erp_doc("SALES_OPS_MGR", "Sales Order", so_id, {
                "customer": cid, "transaction_date": d_str(today), "items": items_payload
            }, submit=True)

    def _step_order_to_cash_fulfillment(self, d):
        today = self.cur_date(d)
        fulfilled_sos = set()
        for line in list(self.open_so_lines):
            if self.stock_qty(line["sku_id"]) >= line["qty"]:
                self._consume_fifo(d, line["sku_id"], line["qty"], "601", line["so_id"])
                inv_eur = round(line["qty"] * line["unit_price"], 2)
                std_cogs = round(line["qty"] * self.materials[line["sku_id"]]["std_cost"], 2)
                inv_id = self.bus.next_id("INV")
                self.bus.add("invoices_ar", invoice_id=inv_id, so_id=line["so_id"],
                             account_id=line["account_id"], invoice_date=d_str(today),
                             due_date=d_str(today + timedelta(days=25)), amount_eur=inv_eur,
                             status="OPEN")
                self.bus.emit_erp_doc("WH_CLERK", "Delivery Note", f"DN-{inv_id}", {
                    "posting_date": d_str(today), "set_posting_time": 1,
                    "items": [{"against_sales_order": self.bus.id_map.get(line["so_id"], line["so_id"]), "item_code": line["sku_id"], "qty": line["qty"]}]
                }, submit=True)
                self.bus.emit_erp_doc("CFO", "Sales Invoice", inv_id, {
                    "customer": line["account_id"], "posting_date": d_str(today), "set_posting_time": 1,
                    "items": [{"item_code": line["sku_id"], "qty": line["qty"], "rate": line["unit_price"]}]
                }, submit=True)
                self._post_gl(d, inv_id, "1200_ACCOUNTS_RECEIVABLE", "4000_SALES_REVENUE", inv_eur, f"AR Invoice {inv_id} for {line['so_id']}")
                self._post_gl(d, inv_id, "5000_COGS", "1400_INVENTORY_ASSET", std_cogs, f"Shipment COGS {line['so_id']}")
                self.pending_ar_collections.append((d + self.rng.randint(15, 28), inv_id, line["account_id"], inv_eur))

                for r in self.bus.tables["sales_order_lines"]:
                    if r["so_id"] == line["so_id"] and r["line_no"] == line["line_no"]:
                        r["line_status"] = "SHIPPED"
                fulfilled_sos.add(line["so_id"])
                self.open_so_lines.remove(line)
            elif d - line["due_day"] >= 5 and not line["escalated"]:
                line["escalated"] = True
                case_id = self.bus.next_id("CASE")
                self.bus.add("support_cases", case_id=case_id, opened_date=d_str(today),
                             account_id=line["account_id"], so_id=line["so_id"],
                             issue="Late Shipment Escalation (>5 days past promised date)",
                             owner_role="SALES_OPS_MGR", status="ESCALATED")
                if self.rng.random() < 0.20:
                    self._send_email(
                        d, "SALES_OPS_MGR", ["PLANNER", "PLANT_MGR"], f"TH-LATE-{line['so_id']}",
                        f"Late Order Escalation {line['so_id']} ({case_id})",
                        f"Wei, Tomas,\n\nOrder {line['so_id']} for SKU {line['sku_id']} ({line['qty']} EA) is now "
                        f"{d - line['due_day']} days past promised delivery. Customer opened {case_id}.\n\nEmma"
                    )

        open_so_ids = {x["so_id"] for x in self.open_so_lines}
        for so in self.bus.tables["sales_orders"]:
            if so["so_id"] in fulfilled_sos and so["so_id"] not in open_so_ids:
                so["status"] = "FULFILLED"

    def _step_procure_to_pay_arrivals(self, d):
        today = self.cur_date(d)
        for po in [p for p in self.open_pos if p["eta_day"] == d]:
            self.open_pos.remove(po)
            code = po["code"]
            self.on_order[code] -= po["qty"]
            m = self.materials[code]
            props = self._sample_lot_props(po["vkey"], code)

            for r in self.bus.tables["purchase_orders"]:
                if r["po_id"] == po["po_id"]:
                    r["status"] = "RECEIVED"

            if not m["attr"]:
                self._receive_lot_unrestricted(d, code, po["qty"], props, po["vendor_id"], po_id=po["po_id"])
                ap_eur = round(po["qty"] * po["unit_price"], 2)
                bill_id = self.bus.next_id("BILL")
                self.bus.add("invoices_ap", bill_id=bill_id, po_id=po["po_id"],
                             vendor_id=po["vendor_id"], bill_date=d_str(today), amount_eur=ap_eur, status="OPEN")
                self.bus.emit_erp_doc("WH_CLERK", "Purchase Receipt", f"PR-{bill_id}", {
                    "supplier": po["vendor_id"], "posting_date": d_str(today), "set_posting_time": 1,
                    "items": [{"item_code": m["material_id"], "qty": po["qty"], "rate": po["unit_price"]}]
                }, submit=True)
                self.bus.emit_erp_doc("CFO", "Purchase Invoice", bill_id, {
                    "supplier": po["vendor_id"], "posting_date": d_str(today), "set_posting_time": 1,
                    "items": [{"item_code": m["material_id"], "qty": po["qty"], "rate": po["unit_price"]}]
                }, submit=True)
                self._post_gl(d, bill_id, "1400_INVENTORY_ASSET", "2000_ACCOUNTS_PAYABLE", ap_eur, f"AP Bill {bill_id} for {po['po_id']}")
                self.pending_ap_payments.append((d + 25, bill_id, po["vendor_id"], ap_eur))
                continue

            lot_id = self.bus.next_id("LOT")
            self.bus.add("goods_movements", mvt_id=self.bus.next_id("MVT"), date=d_str(today),
                         mvt_type="101", material_id=m["material_id"], code=code, lot_id=lot_id,
                         qty=round(po["qty"], 3), uom=m["uom"], ref_doc=po["po_id"])

            insp_id = self.bus.next_id("INSP")
            attr_name, nominal, (lo, hi) = m["attr"]
            measured = round(props[attr_name] + self.rng.gauss(0, 0.12 if attr_name == "active_pct" else 0.8), 2)
            in_spec = (lo <= measured <= hi)

            if in_spec:
                decision = "RELEASED"
            elif d <= self.deviation_until_day and code in ("SLES", "LAS") and measured >= 64.5:
                decision = "DEVIATION_RELEASED"
            else:
                decision = "REJECTED"

            self.bus.add("inspection_lots", insp_id=insp_id, date=d_str(today), insp_type="INCOMING_RAW",
                         material_id=m["material_id"], lot_id=lot_id, ref_doc=po["po_id"],
                         decision=decision, inspector="QA_TECH")
            self.bus.add("lab_measurements", insp_id=insp_id, characteristic=attr_name.upper(),
                         measured_value=measured, spec_min=lo, spec_max=hi, result="PASS" if in_spec else "FAIL")
            self.bus.emit_erp_doc("QA_TECH", "Quality Inspection", insp_id, {
                "inspection_type": "Incoming", "reference_name": po["po_id"], "item_code": m["material_id"],
                "batch_no": lot_id, "status": "Accepted" if decision != "REJECTED" else "Rejected"
            }, submit=True)

            if decision in ("RELEASED", "DEVIATION_RELEASED"):
                self._receive_lot_unrestricted(d, code, po["qty"], props, po["vendor_id"], po_id=po["po_id"], lot_id=lot_id)
                ap_eur = round(po["qty"] * po["unit_price"], 2)
                bill_id = self.bus.next_id("BILL")
                self.bus.add("invoices_ap", bill_id=bill_id, po_id=po["po_id"],
                             vendor_id=po["vendor_id"], bill_date=d_str(today), amount_eur=ap_eur, status="OPEN")
                self.bus.emit_erp_doc("CFO", "Purchase Invoice", bill_id, {
                    "supplier": po["vendor_id"], "posting_date": d_str(today), "set_posting_time": 1,
                    "items": [{"item_code": m["material_id"], "qty": po["qty"], "rate": po["unit_price"]}]
                }, submit=True)
                self._post_gl(d, bill_id, "1400_INVENTORY_ASSET", "2000_ACCOUNTS_PAYABLE", ap_eur, f"AP Bill {bill_id} for {po['po_id']}")
                self.pending_ap_payments.append((d + 25, bill_id, po["vendor_id"], ap_eur))
            else:
                self.bus.add("non_conformances", ncr_id=self.bus.next_id("NCR"), date=d_str(today),
                             insp_id=insp_id, material_id=m["material_id"], lot_id=lot_id,
                             vendor_id=po["vendor_id"], reason=f"{attr_name}={measured} outside [{lo},{hi}]",
                             disposition="RETURN_TO_VENDOR")
                self.bus.add("goods_movements", mvt_id=self.bus.next_id("MVT"), date=d_str(today),
                             mvt_type="122", material_id=m["material_id"], code=code, lot_id=lot_id,
                             qty=round(po["qty"], 3), uom=m["uom"], ref_doc=po["po_id"])
                self.bus.emit_erp_doc("WH_CLERK", "Purchase Receipt", f"RTV-{lot_id}", {
                    "supplier": po["vendor_id"], "is_return": 1, "posting_date": d_str(today),
                    "items": [{"item_code": m["material_id"], "batch_no": lot_id, "qty": -po["qty"]}]
                }, submit=True)
                self.bus.add("invoices_ap", bill_id=self.bus.next_id("BILL"), po_id=po["po_id"],
                             vendor_id=po["vendor_id"], bill_date=d_str(today),
                             amount_eur=round(po["qty"] * po["unit_price"], 2), status="DISPUTED_RTV")

    def _step_procure_to_pay_reorder(self, d):
        today = self.cur_date(d)
        for code, m in self.materials.items():
            if m["category"] not in ("ROH", "VERP"):
                continue
            use = self.daily_usage(code)
            if use <= 0:
                continue
            vkey = self.source_list[code]
            vend = self.vendors[vkey]
            unit_price, (lt_mean, lt_sd), _ = SUPPLIERS[vkey][2][code]
            s_days = self.safety_days["FRG"] if code.startswith("FRG") else self.safety_days[code]

            if self.stock_qty(code) + self.on_order[code] < use * (lt_mean + s_days):
                order_qty = max(50.0, round(use * 8, 0))
                total_eur = round(order_qty * unit_price, 2)
                buyer = "BUYER_CHEM" if m["category"] == "ROH" else "BUYER_PACK"

                if total_eur <= 10000.0:
                    approver = buyer
                elif total_eur <= 25000.0:
                    approver = "SC_MGR"
                else:
                    approver = "CFO"

                po_id = self.bus.next_id("PO")
                if approver != buyer:
                    apr = self.bus.add("approval_tasks", approval_id=self.bus.next_id("APR"), date=d_str(today),
                                       ref_doc=po_id, gate_type="PO_VALUE_THRESHOLD",
                                       requested_by=buyer, assigned_to=approver, decision="APPROVED",
                                       rationale=f"PO EUR {total_eur:.2f} within replenishment policy")
                    self.bus.emit_itsm_ticket(approver, "approval_tasks", apr["approval_id"], apr)

                vid = vend["alt_vendor_id"] if (vkey == "KEMIX" and self.rng.random() < 0.35) else vend["vendor_id"]
                eta_day = d + max(2, round(self.rng.gauss(lt_mean, lt_sd)))

                self.bus.add("purchase_orders", po_id=po_id, vendor_id=vid, order_date=d_str(today),
                             expected_date=d_str(self.cur_date(d + lt_mean)), status="APPROVED",
                             total_eur=total_eur, created_by=buyer, approved_by=approver)
                self.bus.add("purchase_order_lines", po_id=po_id, line_no=10, material_id=m["material_id"],
                             code=code, qty=order_qty, uom=m["uom"], unit_price=unit_price)
                self.bus.emit_erp_doc(approver, "Purchase Order", po_id, {
                    "supplier": vid, "transaction_date": d_str(today),
                    "items": [{"item_code": m["material_id"], "qty": order_qty, "rate": unit_price}]
                }, submit=True)
                self.open_pos.append(dict(po_id=po_id, code=code, qty=order_qty, unit_price=unit_price,
                                          vkey=vkey, vendor_id=vid, eta_day=eta_day, order_day=d))
                self.on_order[code] += order_qty

    def _step_payment_settlements(self, d):
        today = self.cur_date(d)
        dt = d_str(today)
        for due_d, inv_id, acc_id, amt in list(self.pending_ar_collections):
            if d >= due_d:
                self.pending_ar_collections.remove((due_d, inv_id, acc_id, amt))
                self.cash_eur += amt
                pay_id = self.bus.next_id("PAY")
                self.bus.add("payments", payment_id=pay_id, date=dt, direction="INBOUND_AR",
                             ref_doc=inv_id, party_id=acc_id, amount_eur=amt)
                self.bus.emit_erp_doc("CFO", "Payment Entry", pay_id, {
                    "payment_type": "Receive", "party_type": "Customer", "party": acc_id, "paid_amount": amt, "posting_date": dt
                }, submit=True)
                self._post_gl(d, pay_id, "1000_CASH", "1200_ACCOUNTS_RECEIVABLE", amt, f"Customer payment {inv_id}")
                for r in self.bus.tables["invoices_ar"]:
                    if r["invoice_id"] == inv_id:
                        r["status"] = "PAID"

        for due_d, bill_id, vend_id, amt in list(self.pending_ap_payments):
            if d >= due_d:
                self.pending_ap_payments.remove((due_d, bill_id, vend_id, amt))
                self.cash_eur -= amt
                pay_id = self.bus.next_id("PAY")
                self.bus.add("payments", payment_id=pay_id, date=dt, direction="OUTBOUND_AP",
                             ref_doc=bill_id, party_id=vend_id, amount_eur=amt)
                self.bus.emit_erp_doc("CFO", "Payment Entry", pay_id, {
                    "payment_type": "Pay", "party_type": "Supplier", "party": vend_id, "paid_amount": amt, "posting_date": dt
                }, submit=True)
                self._post_gl(d, pay_id, "2000_ACCOUNTS_PAYABLE", "1000_CASH", amt, f"Vendor payment {bill_id}")
                for r in self.bus.tables["invoices_ap"]:
                    if r["bill_id"] == bill_id:
                        r["status"] = "PAID"

    def _step_plan_and_produce(self, d):
        today = self.cur_date(d)
        for s in self.skus:
            if not s["active"]:
                continue
            f_vessels = [v for v in self.vessels if v["family"] == s["family"]]
            queued = sum(wo["qty"] for v in f_vessels for wo in self.vessel_queues[v["vessel_id"]] if wo["sku"]["sku_id"] == s["sku_id"])
            back = sum(l["qty"] for l in self.open_so_lines if l["sku_id"] == s["sku_id"])
            if self.stock_qty(s["sku_id"]) + queued - back < s["daily_units"] * 4.5:
                target_v = min(f_vessels, key=lambda v: len(self.vessel_queues[v["vessel_id"]]))
                wo_id = self.bus.next_id("WO")
                self.bus.add("work_orders", wo_id=wo_id, sku_id=s["sku_id"], qty=s["units_per_batch"],
                             vessel_id=target_v["vessel_id"], release_date=d_str(today),
                             status="RELEASED", released_by="PLANNER")
                self.bus.emit_erp_doc("PLANNER", "Work Order", wo_id, {
                    "production_item": s["sku_id"], "qty": s["units_per_batch"], "planned_start_date": d_str(today)
                }, submit=True)
                self.vessel_queues[target_v["vessel_id"]].append(dict(wo_id=wo_id, sku=s, qty=s["units_per_batch"]))

        for v in self.vessels:
            if v["down_until"] is not None:
                if d < v["down_until"]:
                    continue
                v["down_until"] = None
                self.bus.add("equipment_states", timestamp=ts_str(today, 6, 0), vessel_id=v["vessel_id"],
                             state="RUN", reason="REPAIRED", note="Maintenance verified and returned to service")

            if v["since_pm"] >= 40 and not v["pm_deferred"]:
                self.bus.add("equipment_states", timestamp=ts_str(today, 6, 0), vessel_id=v["vessel_id"],
                             state="MAINT", reason="PLANNED_PM", note="40-batch preventive maintenance")
                v["wear"] = round(self.rng.uniform(0.03, 0.08), 3)
                v["since_pm"] = 0
                v["down_until"] = d + 1
                continue

            slots = BATCHES_PER_VESSEL_DAY
            while slots > 0 and self.vessel_queues[v["vessel_id"]]:
                wo = self.vessel_queues[v["vessel_id"]][0]
                s = wo["sku"]
                short = [c for c, per in s["bom"].items() if self.stock_qty(c) < (per * wo["qty"] / 1000.0) - 1e-5]
                if short:
                    if not wo.get("short_logged"):
                        wo["short_logged"] = True
                        self.bus.add("equipment_states", timestamp=ts_str(today, 7, 15), vessel_id=v["vessel_id"],
                                     state="IDLE", reason="MAT_SHORT", note=",".join(short))
                    break

                self.vessel_queues[v["vessel_id"]].pop(0)
                slots -= self._run_mes_batch(d, v, wo, slot_idx=BATCHES_PER_VESSEL_DAY - slots)
                if v["down_until"] is not None:
                    break

    def _run_mes_batch(self, d, v, wo, slot_idx):
        today = self.cur_date(d)
        s = wo["sku"]
        fam = FAMILIES[s["family"]]
        form = self.formulas[s["formula_id"]]
        batch_id = self.bus.next_id("BATCH")

        consumed_lots = {}
        for code, per in s["bom"].items():
            req = per * wo["qty"] / 1000.0
            taken = self._consume_fifo(d, code, req, "261", wo["wo_id"])
            consumed_lots[code] = taken
            m = self.materials[code]
            if m["category"] == "ROH":
                for lid, tq, _ in taken:
                    self.bus.add("batch_material_lots", batch_id=batch_id, mes_material_code=m["mes_code"],
                                 lot_id=lid, qty_kg=round(tq, 2))

        act_code = fam["active"]
        attr_name, nominal, _ = RAWS[act_code][2]
        tot_act_kg = sum(tq for _, tq, _ in consumed_lots[act_code])
        strength = sum(tq * pr.get(attr_name, nominal) for _, tq, pr in consumed_lots[act_code]) / max(1e-6, tot_act_kg) / nominal
        thick_ratio = s["bom"][fam["thick"]] / (fam["formula"][fam["thick"]] * s["pack_size_kg"] * 1000.0)
        w = v["wear"]

        visc = (
            fam["visc"] * (strength ** (4.0 if act_code != "CARB" else 1.5))
            * (thick_ratio ** 1.2)
            * (1.0 - 0.12 * w)
            * self.rng.lognormvariate(0, 0.04 + 0.09 * w)
        )
        ph = fam["ph"] + self.rng.gauss(0, 0.07) + 0.45 * max(0.0, w - 0.55)
        lo, hi = fam["spec"]
        in_spec = (lo <= visc <= hi) and (abs(ph - fam["ph"]) <= 0.5)
        result = "PASS" if in_spec else ("REWORK" if self.rng.random() < 0.72 else "SCRAP")

        self.bus.add("batches", batch_id=batch_id, wo_id=wo["wo_id"], sku_mes_code=s["mes_code"],
                     recipe_version=f"{s['formula_id']}/v{form['version']}", vessel_id=v["vessel_id"],
                     start_time=ts_str(today, 6 + 7 * slot_idx, 10), qty_kg=BATCH_KG,
                     viscosity_mpas=round(visc, 1), ph=round(ph, 2), result=result)

        vib = round(1.35 + 6.2 * (w ** 2) + abs(self.rng.gauss(0, 0.18 + 0.45 * w)), 2)
        self.bus.add("sensor_readings_summary", batch_id=batch_id, vessel_id=v["vessel_id"],
                     date=d_str(today), avg_temp_c=round(form["temp_c"] + self.rng.gauss(0, 0.3), 1),
                     avg_rpm=round(form["rpm"] * (1 + self.rng.gauss(0, 0.015)), 1),
                     peak_vibration_mm_s=vib)

        insp_id = self.bus.next_id("INSP")
        self.bus.add("inspection_lots", insp_id=insp_id, date=d_str(today), insp_type="FINISHED_BATCH",
                     material_id=s["sku_id"], lot_id=batch_id, ref_doc=wo["wo_id"],
                     decision="RELEASED" if result != "SCRAP" else "SCRAPPED", inspector="QA_TECH")
        self.bus.add("lab_measurements", insp_id=insp_id, characteristic="VISCOSITY_MPAS",
                     measured_value=round(visc, 1), spec_min=lo, spec_max=hi,
                     result="PASS" if (lo <= visc <= hi) else "FAIL")

        if result != "SCRAP":
            self._receive_lot_unrestricted(d, s["sku_id"], wo["qty"], {}, "INTERNAL", wo_id=wo["wo_id"])
            self.bus.emit_erp_doc("SHIFT_OP", "Stock Entry", f"SE-{batch_id}", {
                "stock_entry_type": "Manufacture", "work_order": self.bus.id_map.get(wo["wo_id"], wo["wo_id"]),
                "fg_completed_qty": wo["qty"], "posting_date": d_str(today)
            }, submit=True)
        else:
            scrap_val = round(wo["qty"] * s["std_cost"], 2)
            self._post_gl(d, batch_id, "5200_SCRAP_VARIANCE", "1400_INVENTORY_ASSET", scrap_val, f"Scrap write-off {batch_id}")

        for r in self.bus.tables["work_orders"]:
            if r["wo_id"] == wo["wo_id"]:
                r["status"] = "COMPLETED" if result != "SCRAP" else "SCRAPPED"

        v["wear"] = round(min(1.0, v["wear"] + 0.011), 4)
        v["since_pm"] += 1
        if self.rng.random() < (0.0015 + 0.095 * (v["wear"] ** 3)):
            down_days = self.rng.randint(1, 3)
            v["down_until"] = d + 1 + down_days
            inc_id = self.bus.next_id("INC")
            self.bus.add("equipment_states", timestamp=ts_str(today, 18, 0), vessel_id=v["vessel_id"],
                         state="DOWN", reason="AGITATOR_FAULT", note=f"Vibration {vib} mm/s; {inc_id}")
            inc_row = self.bus.add("incidents", incident_id=inc_id, opened_date=d_str(today), category="EQUIPMENT_BREAKDOWN",
                                   asset_id=v["vessel_id"], reported_by="MAINT_LEAD", assigned_to="MAINT_LEAD",
                                   resolution=f"Agitator seal & bearing overhaul ({down_days}d downtime)", status="RESOLVED")
            self.bus.emit_itsm_ticket("MAINT_LEAD", "incidents", inc_id, inc_row)
            v["wear"] = round(max(0.18, v["wear"] - 0.16), 4)

        return 2 if result == "REWORK" else 1

    def _step_periodic_governance(self, d):
        today = self.cur_date(d)
        tomorrow = today + timedelta(days=1)
        if tomorrow.month != today.month:
            ym = today.strftime("%Y-%m")
            jes = [j for j in self.bus.tables["gl_journal_entries"] if j["date"].startswith(ym)]
            rev = sum(j["credit_eur"] for j in jes if j["account"] == "4000_SALES_REVENUE")
            cogs = sum(j["debit_eur"] for j in jes if j["account"] in ("5000_COGS", "5200_SCRAP_VARIANCE"))
            gm_pct = round(((rev - cogs) / rev * 100.0) if rev > 0 else 0.0, 2)
            self.bus.add("gl_monthly_summary", month=ym, revenue_eur=round(rev, 2), cogs_eur=round(cogs, 2),
                         gross_margin_pct=gm_pct, closing_cash_eur=round(self.cash_eur, 2), closed_by="CFO")
            self._send_email(
                d, "CFO", ["CEO", "COO", "CCO"], f"TH-CLOSE-{ym}",
                f"Monthly Financial Close {ym}: Revenue EUR {rev:,.0f} | GM {gm_pct}%",
                f"Henrik, Dirk, Sofia,\n\n{ym} GL close complete. Revenue: EUR {rev:,.2f}, COGS+Scrap: EUR {cogs:,.2f}, "
                f"Gross Margin: {gm_pct}%, Cash Balance: EUR {self.cash_eur:,.2f}.\n\nClaire Moreau, CFO"
            )

        if d in (89, 180, 272, 364):
            q_num = (today.month - 1) // 3 + 1
            self.cognition._schedule_meeting(
                d, "CEO", ["BOARD", "CFO", "COO", "CCO"],
                f"Q{q_num} 2027 Board Performance & Risk Review",
                f"Reviewed Q{q_num} financial close, supplier quality actions, and capacity utilization."
            )

    def export_all(self, out_dir):
        out = Path(out_dir)
        obs = out / "observable"
        tru = out / "truth"
        for sub in ("crm", "erp", "mes", "qms", "servicenow", "comms"):
            (obs / sub).mkdir(parents=True, exist_ok=True)
        tru.mkdir(parents=True, exist_ok=True)

        (obs / "org_chart.json").write_text(json.dumps(
            [{"role_id": r["role_id"], "title": r["title"], "person": r["person"],
              "department": r["department"], "reports_to": r["reports_to"]} for r in ROLES], indent=2
        ))
        (obs / "accountability_units.json").write_text(json.dumps(ROLES, indent=2))

        system_map = {
            "crm": ["accounts", "opportunities", "support_cases"],
            "erp": ["materials", "boms", "vendors", "source_list", "sales_orders", "sales_order_lines",
                    "purchase_orders", "purchase_order_lines", "goods_movements", "invoices_ar",
                    "invoices_ap", "payments", "gl_journal_entries", "gl_monthly_summary"],
            "mes": ["work_orders", "batches", "batch_material_lots", "equipment_states", "sensor_readings_summary"],
            "qms": ["inspection_lots", "lab_measurements", "non_conformances", "deviations"],
            "servicenow": ["incidents", "change_requests", "approval_tasks", "audit_log"],
            "comms": ["emails", "chat_messages", "calendar_events", "employee_notes"]
        }

        for s in self.skus:
            for code, per_1000 in s["bom"].items():
                self.bus.add("boms", sku_id=s["sku_id"], component_code=code,
                             component_mat_id=self.materials[code]["material_id"], qty_per_1000_ea=per_1000)
        for m in self.materials.values():
            self.bus.add("materials", material_id=m["material_id"], code=m["code"], mes_code=m["mes_code"],
                         name=m["name"], category=m["category"], uom=m["uom"], std_cost_eur=m["std_cost"], active=m["active"])
        for v in self.vendors.values():
            self.bus.add("vendors", vendor_id=v["vendor_id"], vendor_key=v["vendor_key"], name=v["name"], country=v["country"])
            if v["alt_vendor_id"]:
                self.bus.add("vendors", vendor_id=v["alt_vendor_id"], vendor_key=v["vendor_key"],
                             name=v["name"].upper() + " SP. Z O.O.", country="PL")
        for code, vkey in self.source_list.items():
            self.bus.add("source_list", material_id=self.materials[code]["material_id"], code=code,
                         approved_vendor_id=self.vendors[vkey]["vendor_id"], vendor_key=vkey)
        for acc in self.accounts.values():
            self.bus.add("accounts", **{k: v for k, v in acc.items() if k != "weight"})

        for sys_name, tbl_list in system_map.items():
            for tbl in tbl_list:
                rows = self.bus.tables[tbl]
                if not rows:
                    continue
                if tbl == "batch_material_lots":
                    rows = [r for r in rows if self.rng.random() > 0.05]
                cols = list(dict.fromkeys(k for r in rows for k in r))
                with open(obs / sys_name / f"{tbl}.csv", "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=cols)
                    w.writeheader()
                    w.writerows(rows)

        nodes, edges = [], []
        for r in ROLES:
            nodes.append(dict(id=f"role:{r['role_id']}", type="Role", name=r["title"], person=r["person"], dept=r["department"]))
            if r["reports_to"]:
                edges.append(dict(src=f"role:{r['role_id']}", rel="REPORTS_TO", dst=f"role:{r['reports_to']}"))
        for m in self.materials.values():
            nodes.append(dict(id=f"mat:{m['material_id']}", type=m["category"], code=m["code"], name=m["name"]))
        for v in self.vendors.values():
            nodes.append(dict(id=f"vend:{v['vendor_id']}", type="Vendor", name=v["name"]))
        for dec in self.decision_traces:
            nodes.append(dict(id=f"dec:{dec['trace_id']}", type="DecisionEvent", date=dec["date"], action=dec["action"]))
            edges.append(dict(src=f"role:{dec['actor']}", rel="INITIATED_DECISION", dst=f"dec:{dec['trace_id']}"))
            edges.append(dict(src=f"role:{dec['approver']}", rel="APPROVED_DECISION", dst=f"dec:{dec['trace_id']}"))

        rej_lots = {n["lot_id"] for n in self.bus.tables["non_conformances"]}
        used_261 = {m["lot_id"] for m in self.bus.tables["goods_movements"] if str(m["mvt_type"]) == "261" and m["lot_id"]}
        tot_debit = round(sum(j["debit_eur"] for j in self.bus.tables["gl_journal_entries"]), 2)
        tot_credit = round(sum(j["credit_eur"] for j in self.bus.tables["gl_journal_entries"]), 2)

        audit_report = {
            "mode": self.bus.mode,
            "llm_model": self.cognition.llm_model,
            "llm_calls_made": self.cognition.llm_call_count,
            "api_calls_executed": self.bus.api_calls_executed,
            "api_403_rejections": self.bus.api_403_rejections,
            "mapped_native_ids_count": len(self.bus.id_map),
            "rejected_lots_count": len(rej_lots),
            "rejected_lots_leaked_to_production": len(rej_lots & used_261),
            "gl_total_debit_eur": tot_debit,
            "gl_total_credit_eur": tot_credit,
            "gl_balanced": (tot_debit == tot_credit),
            "blocked_unauthorized_attempts": sum(1 for a in self.bus.tables["audit_log"] if a["outcome"] != "ALLOWED"),
            "rejected_approval_tasks": sum(1 for a in self.bus.tables["approval_tasks"] if a["decision"] == "REJECTED"),
            "chat_messages_count": len(self.bus.tables["chat_messages"]),
            "emails_count": len(self.bus.tables["emails"]),
            "calendar_events_count": len(self.bus.tables["calendar_events"]),
            "employee_notes_count": len(self.bus.tables["employee_notes"]),
        }

        (tru / "world_graph.json").write_text(json.dumps(dict(nodes=nodes, edges=edges), indent=2))
        (tru / "decision_traces.json").write_text(json.dumps(self.decision_traces, indent=2))
        (tru / "id_map.json").write_text(json.dumps(self.bus.id_map, indent=2))
        (tru / "audit_verification.json").write_text(json.dumps(audit_report, indent=2))
        return audit_report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="norvale_2027_dataset_v4")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--mode", choices=["dry-run", "live-apis"], default="dry-run")
    ap.add_argument("--api-config", default=None, help="JSON file with erp_url, crm_url, itsm_url, zulip_url, caldav_url, smtp_host, smtp_port, role_tokens")
    ap.add_argument("--llm-model", default=None, help="Live LLM model ID (requires OPENROUTER_API_KEY or OPENAI_API_KEY)")
    args = ap.parse_args()
    sim = EnterpriseSimV4(seed=args.seed, days=args.days, llm_model=args.llm_model, mode=args.mode, api_config=args.api_config)
    sim.run()
    rep = sim.export_all(args.out)
    print(json.dumps(rep, indent=2))
