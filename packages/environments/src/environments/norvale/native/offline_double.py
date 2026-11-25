"""Test stand-in for Company.persist on controller doubles that have no engine.

Production state lives in the engine checkpoint. Doubles built with object.__new__
reload this file to model a restart.
"""

import json


def persist_to_file(company):
    company.run.mkdir(parents=True, exist_ok=True)
    (company.run / "state.json").write_text(json.dumps(company.state, default=str))
