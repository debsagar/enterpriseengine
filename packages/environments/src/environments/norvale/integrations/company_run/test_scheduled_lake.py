"""Exercise the ordinary generator's analytical scan and completed-horizon retry."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from environments.norvale import generate
from environments.norvale.integrations.company_run.lake import scheduled_scan
from environments.norvale.integrations.company_run.timing import DEFAULT_TIMING
from environments.norvale.integrations.lake.adapter import Lake
from environments.norvale.integrations.lake.boundary import DISPOSITION_ENTITY, Frappe
from environments.norvale.integrations.lake.calendar_reader import parse_vevents
from environments.norvale.integrations.lake.mail_reader import normalize_message
from environments.norvale.integrations.lake.manifest import snapshot_at
from environments.norvale.integrations.lake.matrix_reader import normalize_event
from environments.norvale.native.company import Engine


class ScheduledLake(TestCase):
    def test_each_source_enters_the_daily_manifest_on_its_first_day(self):
        with TemporaryDirectory() as temp:
            run, archive = Path(temp), Path(temp) / "lake"
            env = run / "env"
            env.mkdir()
            reader = dict(url="http://local", api_key="reader", api_secret="test")
            settings = {
                "erp": dict(source="operations", entities=["Work Order"], reader=reader),
                "qms": dict(source="quality", entities=["Quality Inspection"], reader=reader),
            }
            directory = env / "directory.json"
            account = dict(id="CEO", email="ceo@norvale.test", effective_at="2027-01-02")
            today = [0]
            for kind in ("mail", "calendar", "matrix", "master"):
                settings[kind] = dict(source=kind, env=str(env), directory=str(directory))
            settings["plm"] = dict(source="definitions", reader=reader, parts=[])
            (env / "analytics.json").write_text(json.dumps(settings))
            (env / "planning-report.json").write_text(
                json.dumps(dict(settings=str(env / "analytics.json"), archive=str(archive)))
            )
            (env / "company-sources.json").write_text(
                json.dumps(dict(plm_source="definitions", plm=reader, products={}))
            )
            (env / "endpoints.json").write_text(
                json.dumps(dict(mail=dict(host="mail", imap=143), calendar="http://cal", chat="http://chat"))
            )
            (env / "secrets.json").write_text(json.dumps(dict(CEO=dict(password="p", chat_token="t"))))
            engine = SimpleNamespace(run=run, state={}, persist=Mock(), log=Mock())

            def documents(entity):
                if today[0] == 0 or entity in (DISPOSITION_ENTITY, "Norvale Dispatch Lease"):
                    return []
                at = "2027-01-02 08:00:00"
                rows = [dict(name=entity + "-1", modified=at, planned_start_date=at, report_date="2027-01-02")]
                if entity == "Quality Inspection":
                    rows.append(dict(name="version-only", modified="qms-0003"))
                return rows

            mail = normalize_message(
                b"Message-ID: <1@norvale.test>\r\nDate: Sat, 02 Jan 2027 08:00:00 +0000\r\n\r\nHello\r\n"
            )
            ics = (
                "BEGIN:VEVENT\nUID:1\nDTSTART:20270102T090000Z\nEND:VEVENT\n"
                "BEGIN:VEVENT\nUID:2\nDTSTART:20270230\nEND:VEVENT\n"
            )
            chat = normalize_event(dict(event_id="$1", origin_server_ts=1798876800000, content=dict(body="Hi")))
            module = "environments.norvale.integrations.company_run.lake."
            with (
                patch.object(Frappe, "documents", side_effect=documents),
                patch.object(Frappe, "__init__", return_value=None),
                patch("environments.norvale.integrations.plm.PLM"),
                patch("environments.norvale.integrations.company_run.publications.collect"),
                patch(module + "fetch_imap_messages", side_effect=lambda *a: [mail] * today[0]),
                patch(module + "fetch_caldav_events", side_effect=lambda *a: parse_vevents(ics) if today[0] else []),
                patch(module + "joined_matrix_rooms", return_value=["!room"]),
                patch(module + "fetch_matrix_messages", side_effect=lambda *a, **k: [chat] * today[0]),
            ):
                for day in (0, 1):
                    today[0] = day
                    directory.write_text(json.dumps([account] * day))
                    scheduled_scan(engine, day * 1440 + 1439)
                    self.assertEqual(engine.state["lake_scans"]["errors"], [])
            expected = {
                ("operations", "Work Order"),
                ("quality", "Quality Inspection"),
                ("mail", "Mail Message"),
                ("calendar", "Calendar Event"),
                ("matrix", "Matrix Message"),
                ("master", "Directory Account"),
            }
            # An impossible iCal date and a version string are dated by the day the collector first saw them.
            both = dict.fromkeys(expected, 1) | dict.fromkeys(
                [("calendar", "Calendar Event"), ("quality", "Quality Inspection")], 2
            )
            for date, counts in (("2027-01-01", {}), ("2027-01-02", both)):
                sources = snapshot_at(archive, date)["sources"]
                found = {(s, e): row["count"] for s, entities in sources.items() for e, row in entities.items()}
                self.assertEqual(found, counts, date)
                self.assertNotIn("undated", snapshot_at(archive, date))

    def test_each_day_snapshot_holds_the_version_current_that_day(self):
        with TemporaryDirectory() as temp:
            run = Path(temp)
            env, archive = run / "env", run / "lake"
            env.mkdir()
            reader = dict(url="http://local", api_key="reader", api_secret="test")
            settings = {
                "erp": dict(source="operations", entities=["Sales Order"], reader=reader),
                "qms": dict(source="quality", entities=["Quality Inspection"], reader=reader),
                "plm": dict(source="definitions", reader=reader, parts=[]),
            }
            (env / "analytics.json").write_text(json.dumps(settings))
            (env / "planning-report.json").write_text(
                json.dumps(dict(settings=str(env / "analytics.json"), archive=str(archive)))
            )
            (env / "company-sources.json").write_text(
                json.dumps(dict(plm_source="definitions", plm=reader, products={}))
            )
            engine = SimpleNamespace(run=run, state={}, persist=Mock(), log=Mock())
            native = {}

            def documents(entity):
                return [dict(row) for row in native.get(entity, [])]

            with (
                patch.object(Frappe, "documents", side_effect=documents),
                patch.object(Frappe, "__init__", return_value=None),
                patch("environments.norvale.integrations.plm.PLM"),
                patch("environments.norvale.integrations.company_run.publications.collect"),
            ):
                for day in range(4):
                    if day == 0:
                        # The posting date never changes; QMS stamps wall-clock time or nothing at all.
                        native["Sales Order"] = [
                            dict(
                                name="SO-1", modified="2027-01-01 09:00:00", transaction_date="2027-01-01", status="v1"
                            )
                        ]
                        native["Quality Inspection"] = [
                            dict(name="QI-1", modified="2026-11-26 18:00:00", report_date="2027-01-01", status="v1"),
                            dict(name="QI-2", status="v1"),
                        ]
                    if day == 2:
                        native["Sales Order"][0].update(modified="2027-01-03 10:00:00", status="v2")
                        native["Quality Inspection"][0].update(modified="2026-11-26 18:05:00", status="v2")
                    scheduled_scan(engine, day * 1440 + 1439)
                    self.assertEqual(engine.state["lake_scans"]["errors"], [])

            rows = Lake(archive / "delta", {})._rows()
            self.assertTrue(all(row["business_date"] for row in rows))
            payload = {(r["native_id"], r["content_hash"]): json.loads(r["payload"]) for r in rows}

            def state(date):
                manifest = snapshot_at(archive, date)
                self.assertNotIn("undated", manifest)
                return {
                    name: (payload[name, digest]["status"], manifest["sources"][source][entity]["count"])
                    for source, entities in manifest["sources"].items()
                    for entity, summary in entities.items()
                    for name, digest in summary["records"].items()
                }

            v1 = {"SO-1": ("v1", 1), "QI-1": ("v1", 2), "QI-2": ("v1", 2)}
            v2 = v1 | {"SO-1": ("v2", 1), "QI-1": ("v2", 2)}
            for date, expected in (("01", v1), ("02", v1), ("03", v2), ("04", v2)):
                self.assertEqual(state("2027-01-" + date), expected, date)
            self.assertEqual(
                sorted((r["native_id"], r["business_date"]) for r in rows),
                [
                    ("QI-1", "2027-01-01"),
                    ("QI-1", "2027-01-03"),
                    ("QI-2", "2027-01-01"),
                    ("SO-1", "2027-01-01"),
                    ("SO-1", "2027-01-03"),
                ],
            )

    def test_business_cutoffs_independent_failure_and_resume(self):
        with TemporaryDirectory() as temp:
            run = Path(temp)
            env = run / "env"
            env.mkdir()
            archive = run / "lake"
            settings = env / "analytics.json"
            reader = dict(url="http://local", api_key="reader", api_secret="test")
            settings.write_text(
                json.dumps(
                    {
                        "erp": dict(source="operations", entities=["Work Order"], reader=reader),
                        "qms": dict(source="quality", entities=["Quality Inspection"], reader=reader),
                        "plm": dict(source="definitions", reader=reader, parts=[]),
                    }
                )
            )
            (env / "company-sources.json").write_text(
                json.dumps({"plm_source": "definitions", "plm": reader, "products": {}})
            )
            (env / "planning-report.json").write_text(json.dumps({"settings": str(settings), "archive": str(archive)}))

            def controller():
                e = Engine.__new__(Engine)
                e.run, e.day = run, 0
                e.config = dict(days=1, seed=42, company_operating=True, company_timing=DEFAULT_TIMING, report_every=7)
                e.state = dict(day=0, docs={}, completed=[])
                e.docs = e.state["docs"]
                e.log, e.advance_clock, e.run_additions = Mock(), Mock(), Mock()
                return e

            e = controller()
            e.state["completed"] = [
                key for _, key, _ in e.events() if key not in ("d0000:management", "d0000:native-clock")
            ]
            e.persist()
            recover = False

            def documents(entity):
                if entity in (DISPOSITION_ENTITY, "Norvale Dispatch Lease"):
                    return []
                if entity == "Quality Inspection" and not recover:
                    raise TimeoutError()
                return [dict(name=entity + "-1", modified="2027-01-01 08:00:00")]

            with (
                patch.object(Frappe, "documents", side_effect=documents),
                patch.object(Frappe, "__init__", return_value=None),
                patch("environments.norvale.integrations.plm.PLM"),
                patch("environments.norvale.integrations.company_run.publications.collect") as publications,
            ):
                with patch("environments.norvale.native.company.Engine", return_value=e):
                    generate.main(["--run", temp])
                self.assertEqual(e.state["lake_scans"]["last_success_minute"]["operations"], 1439)
                self.assertNotIn("quality", e.state["lake_scans"]["last_success_minute"])
                self.assertIn(("quality", "ExceptionGroup"), e.state["lake_scans"]["errors"])
                recover = True
                resumed = controller()
                with patch("environments.norvale.native.company.Engine", return_value=resumed):
                    generate.main(["--run", temp])
                    count = publications.call_count
                    generate.main(["--run", temp])
                self.assertEqual(publications.call_count, count)
                self.assertEqual(resumed.state["lake_scans"]["errors"], [])
                self.assertEqual(resumed.state["lake_scans"]["last_success_minute"]["quality"], 1439)
                rows = Lake(archive / "delta", {})._rows()
                self.assertCountEqual([r["source"] for r in rows], ["operations", "quality"])
                self.assertTrue(all(c.kwargs["on"] == "2027-01-01" for c in publications.call_args_list))
