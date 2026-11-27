"""Every entity in the business chains is read by the collector's ordinary scan cycle."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from environments.norvale.integrations.company_run.lake import scan_cycle
from environments.norvale.integrations.lake.adapter import Lake
from environments.norvale.integrations.lake.boundary import Frappe
from environments.norvale.integrations.lake.erp_scope import ERP_ENTITIES
from environments.norvale.integrations.lake.flows import FLOWS, QMS_ENTITIES

ROWS = {
    "mrp.production": dict(
        id=7, name="WH/MO/00007", create_date="2026-11-01 00:00:00", write_date="2026-11-01 00:00:00"
    ),
    "mrp.bom": dict(id=3, code="PLM-R1", create_date="2026-11-01 00:00:00", write_date="2026-11-01 00:00:00"),
}


class Plant:
    source_id = "plant"

    def __init__(self, **_):
        pass

    def call(self, model, method, *args, **kwargs):
        if method == "search_read" and model in ROWS and "norvale_rework_of" not in str(args):
            return [dict(ROWS[model])]
        return {} if method == "fields_get" else []


class Desk:
    def __init__(self, *_, **__):
        pass

    def records(self):
        stamp = "2026-11-01T00:00:00Z"
        return [dict(number=1, title="Filler fault", state="open", created_at=stamp, updated_at=stamp)]

    def comments(self, number):
        stamp = "2026-11-01T00:00:00Z"
        return [dict(id=4, body="Checked", user=dict(login="technician"), created_at=stamp, updated_at=stamp)]


class FlowCoverage(TestCase):
    def test_scan_cycle_reads_every_flow_entity(self):
        with TemporaryDirectory() as temp:
            run, archive = Path(temp), Path(temp) / "lake"
            env = run / "env"
            env.mkdir()
            reader = dict(url="http://local", api_key="reader", api_secret="test")
            (env / "company-sources.json").write_text(
                json.dumps(dict(plm_source="definitions", plm=reader, products={}))
            )
            (env / "maintenance-tokens.json").write_text(json.dumps(dict(reader="token")))
            (env / "secrets.json").write_text(json.dumps(dict(CEO=dict(password="p", chat_token="t"))))
            (env / "endpoints.json").write_text(
                json.dumps(dict(mail=dict(host="mail", imap=143), calendar="http://cal", chat="http://chat"))
            )
            directory = env / "directory.json"
            directory.write_text(json.dumps([dict(id="CEO", email="ceo@norvale.test", effective_at="2027-01-01")]))
            config = dict(
                erp=dict(source="operations", entities=list(ERP_ENTITIES), reader=reader),
                qms=dict(source="quality", entities=list(QMS_ENTITIES), reader=reader),
                mes=dict(reader=dict(source_id="plant")),
                plm=dict(source="definitions", reader=reader, parts=[]),
                tickets=dict(source="tickets", run=str(run)),
            )
            for kind in ("mail", "calendar", "matrix", "master"):
                config[kind] = dict(source=kind, env=str(env), directory=str(directory))
            people = "environments.norvale.integrations.company_run.lake."
            with (
                patch(people + "fetch_imap_messages", return_value=[]),
                patch(people + "fetch_caldav_events", return_value=[]),
                patch(people + "joined_matrix_rooms", return_value=[]),
                patch.object(Frappe, "__init__", return_value=None),
                patch.object(Frappe, "documents", return_value=[]),
                patch("environments.norvale.integrations.mes.client.Mes", Plant),
                patch("environments.norvale.integrations.plm.PLM"),
                patch("environments.norvale.integrations.company_run.publications.PLM"),
                patch("environments.norvale.native.systems_v2.Tickets", Desk),
            ):
                _, errors = scan_cycle(config, archive, run, business_day=0)
            self.assertEqual(errors, [])
            scanned = {entry["entity"] for entry in json.loads((archive / "state.json").read_text()).values()}
            self.assertEqual({entity for chain in FLOWS.values() for entity in chain} - scanned, set())
            rows = {(row["entity"], row["native_id"]): row for row in Lake(archive / "delta", {})._rows()}
            order = rows["MES Manufacturing Order", "7"]
            self.assertEqual(json.loads(order["payload"])["reference"], "WH/MO/00007")
            self.assertEqual(
                {key[0] for key in rows},
                {"MES Manufacturing Order", "MES BOM", "Gitea Issue", "Gitea Comment", "Directory Account"},
            )
            # Wall-clock sources are dated by the day observed.
            self.assertEqual({row["business_date"] for row in rows.values()}, {"2027-01-01"})
