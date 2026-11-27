"""Independent scheduled native scans. A source failure does not block its peers."""

import argparse
import json
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError

from environments.norvale.integrations.lake.calendar_reader import fetch_caldav_events
from environments.norvale.integrations.lake.mail_reader import fetch_imap_messages
from environments.norvale.integrations.lake.manifest import RUN_START
from environments.norvale.integrations.lake.master_reader import load_master_file
from environments.norvale.integrations.lake.matrix_reader import fetch_matrix_messages, joined_matrix_rooms
from environments.norvale.integrations.qms.sample_reference import (
    ENTITY as SAMPLE_ENTITY,
)
from environments.norvale.integrations.qms.sample_reference import (
    ORIGINS,
    decode,
    decode_registered,
    registered_binding,
)
from environments.norvale.native import company_state

# Containers on the run's fake clock (environment/compose.py), plus the directory authored on business dates.
# QMS, MES, PLM and tickets stamp host wall-clock time, so their own created/modified stamps are ignored.
SIMULATED_CLOCK = frozenset(("erp", "mail", "calendar", "matrix", "master"))


def check_mes_reader(client, cfg):
    """Require the commissioned Odoo instance and its single-company reader."""
    from environments.norvale.integrations.revisions import valid_receiver_marker

    instance, company = cfg["instance"], cfg["reader_company"]
    if (
        not valid_receiver_marker("mes", instance)
        or type(company) is not int
        or company <= 0
        or client.call("mrp.production", "norvale_analytics_identity", company) != instance
    ):
        raise PermissionError("MES analytics reader differs from its commissioned instance or company")


def mes_target(connection):
    return connection["url"].rstrip("/"), connection["db"]


def registered_mes_scan(cfg, product, company, primary):
    execution = cfg["mes"]
    reader = {"reader": execution, "product": product, "company": company}
    if "mes_reader" in cfg:
        reader.update(reader=cfg["mes_reader"], instance=cfg["mes_reader_instance"], reader_company=company)
    if primary and ("instance" in primary or "instance" in reader):
        baseline = primary["reader"]
        same_site = mes_target(execution) == mes_target(baseline)
        same_source = execution["source_id"] == baseline["source_id"]
        if same_site != same_source:
            raise ValueError("MES source and endpoint disagree with the primary reader")
        if same_site and "instance" in primary:
            if (
                company != primary["reader_company"]
                or reader.get("instance", primary["instance"]) != primary["instance"]
                or ("instance" in reader and reader["reader"] != baseline)
            ):
                raise ValueError("Registered MES reader, company or instance differs from its commissioned site")
            if "instance" not in reader:
                reader.update(primary)
    return reader


def scan_people(kind, cfg, root):
    """Read mail, calendars, chat rooms or the staff directory as each identity sees them."""
    from environments.norvale.integrations.lake.boundary import ingest

    def identities():
        env = Path(cfg["env"])
        endpoints = json.loads((env / "endpoints.json").read_text())
        secrets = json.loads((env / "secrets.json").read_text())
        for row in load_master_file(cfg["directory"]):
            yield endpoints, row, row["id"].lower(), secrets[row["id"]]

    # ponytail: every pass re-reads full mailboxes, calendars and rooms; page by date if a long run slows.
    def mail():
        for eps, row, _, secret in identities():
            server = eps["mail"]
            yield from fetch_imap_messages(server["host"], server["imap"], row["email"], secret["password"])

    def calendar():
        for eps, _, user, secret in identities():
            try:
                events = fetch_caldav_events(f"{eps['calendar']}/{user}/calendar/", user, secret["password"])
            except HTTPError as exc:
                if exc.code != 404:  # this identity has no calendar collection yet
                    raise
                continue
            # Each owner holds a separate copy of a shared invitation.
            yield from (dict(event, name=f"{user}/{event['name']}", owner=user) for event in events)

    def matrix():
        for eps, _, _, secret in identities():
            token = secret["chat_token"]
            for room in joined_matrix_rooms(eps["chat"], token):
                yield from fetch_matrix_messages(eps["chat"], token, room, limit=None)

    entity, fetch = {
        "mail": ("Mail Message", mail),
        "calendar": ("Calendar Event", calendar),
        "matrix": ("Matrix Message", matrix),
        "master": ("Directory Account", lambda: load_master_file(cfg["directory"])),
    }[kind]
    ingest(root, entity, lambda: list(fetch()), source=cfg["source"], projection=lambda _, doc: doc)


def write_daily_manifest(root, on, failed_sources):
    """Save what the archive holds for business date `on`: the last version of each record dated by then.

    Snapshots without a business date cannot be placed on a day; the manifest names their source and entity.
    """
    from environments.norvale.integrations.lake.adapter import Lake
    from environments.norvale.integrations.lake.manifest import filter_rows_at, write_manifest

    index = Lake(root / "delta", {}).index()
    undated = {f"{row['source']}/{row['entity']}" for row in index if not row.get("business_date")}
    latest = {}
    dated = (row for row in index if row.get("business_date"))
    for row in sorted(filter_rows_at(dated, on), key=lambda r: r["ingested_at"]):
        latest[row["source"], row["entity"], row["native_id"]] = row
    rows = {}
    for (source, entity, _), row in latest.items():
        rows.setdefault((source, entity), []).append(row)
    return write_manifest(root, on, rows, failed_sources=failed_sources, undated=undated)


def scan(source, cfg, root):
    from environments.norvale.integrations.lake.boundary import DISPOSITION_ENTITY, Frappe, ingest, project

    if source in ("mail", "calendar", "matrix", "master"):
        scan_people(source, cfg, root)
    elif source in ("erp", "qms"):
        errors = []
        entities = cfg["entities"]
        if source == "qms":
            entities = dict.fromkeys([*entities, "Norvale Dispatch Lease", DISPOSITION_ENTITY])
            if cfg.get("registered_samples"):
                entities[SAMPLE_ENTITY] = None

        def registered_sample(doc):
            scope = decode_registered(doc)
            if scope and not any(
                all(scope[k] == v for k, v in binding.items()) for binding in cfg["registered_samples"]
            ):
                raise ValueError("Sample reference differs from the registered sources or employees")
            return scope

        for entity in entities:

            def open_reader():
                account = cfg.get("entity_readers", {}).get(entity, cfg["reader"])
                return Frappe(
                    account["url"],
                    account["api_key"],
                    account["api_secret"],
                    account.get("site"),
                    private_origin=cfg.get("compose_origin"),
                )

            def projection(entity, doc):
                if entity == SAMPLE_ENTITY and "registered_inspection" not in doc:
                    scope = decode(doc)
                    expected = dict(cfg["sample_scope"], qms_source=cfg["source"])
                    if any(scope[k] != v for k, v in expected.items()):
                        raise ValueError("Sample reference belongs to a different commissioned source or writer")
                if entity != "BOM":
                    result = project(entity, doc)
                    if source == "erp" and entity in ("Stock Entry", "Work Order"):
                        field = "remarks" if entity == "Stock Entry" else "custom_norvale_definition"
                        try:
                            link = json.loads(doc.get(field) or "{}")
                        except (ValueError, TypeError):
                            link = {}
                        if isinstance(link, dict) and entity == "Stock Entry" and link.get("completion_id"):
                            result.update({k: link[k] for k in ("completion_id", "dispatch_id", "output_lot_id")})
                        if isinstance(link, dict) and entity == "Work Order" and link.get("mes_ack_id"):
                            result.update(
                                plm_part=link["id"],
                                plm_version=link["version"],
                                mes_bom=link["mes_ack_id"],
                                erp_bom=link["erp_ack_id"],
                            )
                    return result
                projected = project(entity, doc)
                result = {k: doc[k] for k in ("name", "modified", "item", "company", "docstatus", "quantity", "uom")}
                for field in ("owner", "receiver_marker"):
                    if field in projected:
                        result[field] = projected[field]
                result["items"] = [{k: row[k] for k in ("item_code", "qty", "uom")} for row in doc["items"]]
                return result

            try:
                if entity == SAMPLE_ENTITY and cfg.get("registered_samples"):

                    def fetch():
                        return open_reader().documents(
                            entity, registered_sample=registered_sample, primary_sample="sample_scope" in cfg
                        )

                    ingest(root, entity, fetch, source=cfg["source"], projection=projection)
                else:
                    ingest(root, entity, source=cfg["source"], projection=projection, open_reader=open_reader)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Native entity scans failed", errors)
    elif source == "mes":
        from environments.norvale.integrations.lake.mes_reader import scan as collect
        from environments.norvale.integrations.lake.mes_reader import scan_labor, scan_staging
        from environments.norvale.integrations.mes.client import Mes

        client = Mes(**cfg["reader"])
        if "instance" in cfg or "reader_company" in cfg:
            check_mes_reader(client, cfg)
        errors = []
        for reader in (collect, scan_labor, scan_staging):
            try:
                reader(root, client, product=cfg.get("product"), company=cfg.get("company"))
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Native MES scans failed", errors)
    elif source == "publication":
        from environments.norvale.integrations.lake.plm_reader import scan_publications
        from environments.norvale.integrations.plm import PLM

        scan_publications(
            root,
            cfg["source"],
            PLM(**cfg["reader"]),
            cfg["families"],
            cfg["on"],
            registered_family=cfg.get("registered_family"),
        )
    elif source == "plm":
        from environments.norvale.integrations.lake.plm_reader import scan_bom
        from environments.norvale.integrations.plm import PLM

        client = PLM(**cfg["reader"])
        parts = (part["pk"] for family in sorted(set(cfg["parts"])) for part in client._family(family))
        scan_bom(root, cfg["source"], client, parts, registered_family=cfg.get("registered_family"))
    else:
        raise ValueError("Unknown source")


@contextmanager
def observing(on, kind):
    """Date the snapshots this scan appends. `on` is None outside a company run."""
    from environments.norvale.integrations.lake.adapter import observation

    token = observation.set(None if on is None else (on, kind in SIMULATED_CLOCK))
    try:
        yield
    finally:
        observation.reset(token)


def scan_cycle(config, root, run=None, *, qms_sources=None, business_day=None, unresolved_profiles=None):
    from environments.norvale.native.production_profiles import profile, quality_target, registered

    sources = set()
    errors = []
    on = None
    if run:
        if business_day is None:
            business_day = company_state.load(run)["day"]
        on = (date.fromisoformat(RUN_START) + timedelta(days=business_day)).isoformat()
    primary_mes = config.get("mes")
    mes_mismatch = False
    if run:
        path = run / "env/company-sources.json"
        if path.is_file():
            bindings = json.loads(path.read_text())
            commissioned = bindings.get("mes_analytics")
            if commissioned is not None:
                reader = commissioned["reader"]
                execution = bindings.get("mes", {})
                mes_mismatch = (
                    commissioned != primary_mes
                    or any(execution.get(key) != reader[key] for key in ("url", "db", "source_id"))
                    or bindings.get("company_id") != commissioned["reader_company"]
                )
                if mes_mismatch:
                    errors.append((reader["source_id"], "Commissioned MES analytics binding changed"))
                primary_mes = commissioned

    def attempt(kind, cfg, *, primary=False):
        identity = cfg.get("source") or cfg["reader"]["source_id"]
        sources.add(identity)
        try:
            if primary and kind == "plm" and run:
                from .publications import families, publication_source

                bindings = json.loads((run / "env/company-sources.json").read_text())
                publication_source(bindings, cfg)
                cfg = dict(cfg, parts=families(bindings))
            with observing(on, kind):
                scan(kind, cfg, root)
            if kind == "qms" and qms_sources is not None:
                qms_sources.add(identity)
        except Exception as exc:
            errors.append((identity, type(exc).__name__))

    if not run:
        for kind, cfg in config.items():
            attempt(kind, cfg, primary=True)
        return sources, errors

    primary = config["qms"]
    qms = {primary["source"]: dict(primary)}
    targets = {primary["source"]: quality_target({"qms_reader": primary["reader"]})}
    profiles = []
    # Reload the committed catalog every pass so additions need no reader restart.
    for system, spec in registered(run).items():
        try:
            _, cfg = profile(run, system, spec)
        except Exception as exc:
            errors.append((system, type(exc).__name__))
            if unresolved_profiles is not None:
                unresolved_profiles.add(system)
            continue
        profiles.append(cfg)
        identity, target = cfg["qms_source"], quality_target(cfg)
        qms_cfg = qms.setdefault(
            identity,
            dict(
                reader=cfg["qms_reader"],
                source=identity,
                entities=[e for e in primary["entities"] if e != SAMPLE_ENTITY],
            ),
        )
        targets.setdefault(identity, target)
        if targets[identity] != target:
            errors.append((identity, "QMS source identifies multiple sites"))
        if lab := cfg.get("recurring_lab"):
            erp = config["erp"]
            erp_target = (cfg["erp"]["base"].rstrip("/"), cfg["erp"]["site"])
            readers = [erp.get("entity_readers", {}).get(entity, erp["reader"]) for entity in ORIGINS]
            if any((reader["url"].rstrip("/"), reader.get("site")) != erp_target for reader in readers):
                errors.append((identity, "Registered sample ERP site differs from scheduled ERP reader"))
                continue
            qms_cfg.setdefault("registered_samples", []).extend(
                registered_binding(erp["source"], cfg, item) for item in lab["assays"]
            )
    for identity, target in targets.items():
        if list(targets.values()).count(target) > 1:
            errors.append((identity, "QMS site identifies multiple sources"))
    failed_qms = {identity for identity, _ in errors}

    for kind, cfg in config.items():
        if kind == "qms":
            for identity, settings in qms.items():
                if identity not in failed_qms:
                    attempt(kind, settings)
        elif kind != "mes" or not mes_mismatch:
            attempt(kind, cfg, primary=True)
    try:
        from .publications import collect

        with observing(on, "publication"):
            collect(run, root, reader=config["plm"], on=on)
    except Exception as exc:
        errors.append((config["plm"]["source"], type(exc).__name__))
    for cfg in profiles:
        try:
            targets = {
                (m["mes"]["output"]["item"], m["mes"]["settings"]["company_id"])
                for m in cfg["mapping"].values()
                if m["erp"]["output"]["item"] == cfg["item"]
            }
            if not targets:
                raise ValueError("No MES output matches the registered ERP item")
        except Exception as exc:
            errors.extend(
                (identity, type(exc).__name__)
                for identity in (cfg["mes"]["source_id"], cfg["qms_source"], cfg["plm_source"])
            )
            continue
        for product, company in sorted(targets):
            identity = cfg["mes"]["source_id"]
            try:
                if mes_mismatch and identity == primary_mes["reader"]["source_id"]:
                    continue
                settings = registered_mes_scan(cfg, product, company, primary_mes)
            except (KeyError, ValueError) as exc:
                errors.append((identity, type(exc).__name__))
                continue
            attempt("mes", settings)
        primary = config["plm"]
        reader = cfg["plm"]
        same_source = cfg["plm_source"] == primary["source"]
        same_site = reader["url"].rstrip("/") == primary["reader"]["url"].rstrip("/")
        if same_source != same_site:
            errors.append((cfg["plm_source"], "PLM source and endpoint disagree with the primary reader"))
            continue
        if same_source:
            reader = primary["reader"]
        attempt(
            "plm",
            {
                "reader": reader,
                "source": cfg["plm_source"],
                "parts": [cfg["family"]],
                "registered_family": cfg["family"],
            },
        )
        attempt(
            "publication",
            {
                "reader": cfg["plm"],
                "source": cfg["plm_source"],
                "families": [cfg["family"]],
                "registered_family": cfg["family"],
                "on": on,
            },
        )
    try:
        write_daily_manifest(root, on, {identity for identity, _ in errors})
    except Exception as exc:
        errors.append(("manifest", type(exc).__name__))
    return sources, errors


def scheduled_scan(engine, minute):
    """Record the business observation cutoff separately from native scan timestamps."""
    binding = engine.run / "env/planning-report.json"
    if not binding.is_file():
        return
    status = engine.state.setdefault("lake_scans", {})
    if status.get("at") == minute and not status.get("errors"):
        return
    unresolved_profiles = set()
    try:
        paths = json.loads(binding.read_text())
        config = json.loads(Path(paths["settings"]).read_text())
        sources, errors = scan_cycle(
            config,
            Path(paths["archive"]),
            engine.run,
            business_day=minute // 1440,
            unresolved_profiles=unresolved_profiles,
        )
    except Exception as exc:
        sources, errors = set(), [("collector", type(exc).__name__)]
    failed = {source for source, _ in errors}
    last = status.setdefault("last_success_minute", {})
    # Until every profile is resolved, a missing scope could belong to any source.
    if not unresolved_profiles:
        for source in sources - failed:
            last[source] = minute
    status.update(at=minute, errors=errors, unresolved_profiles=sorted(unresolved_profiles))
    engine.persist()
    engine.log("lake_scan", business_minute=minute, last_success_minute=last, errors=errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("settings", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--run", type=Path, help="Recurring run whose committed production additions to ingest")
    args = parser.parse_args()
    while True:
        config = json.loads(args.settings.read_text())
        _, errors = scan_cycle(config, args.root, args.run)
        for identity, error in errors:
            print(identity, error, flush=True)
        if not args.interval:
            raise SystemExit(int(bool(errors)))
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
