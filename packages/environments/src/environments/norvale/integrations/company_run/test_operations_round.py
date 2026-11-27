"""The daily operations round writes its chat posts and review invite once per business day."""

import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from environments.norvale.integrations.lake.calendar_reader import parse_vevents
from environments.norvale.native import operations_round
from environments.norvale.walkthrough import ROLES


class OperationsRound(TestCase):
    def test_posts_records_and_tomorrows_invite_once_a_day(self):
        with TemporaryDirectory() as temp:
            run = Path(temp)
            (run / "env").mkdir()
            (run / "env/secrets.json").write_text(json.dumps({r["role_id"]: {} for r in ROLES}))
            engine = SimpleNamespace(run=run, state={}, eps={}, day=0, persist=Mock(), log=Mock())
            engine.dated = lambda day: (date(2027, 1, 1) + timedelta(days=day)).isoformat()
            engine.date = lambda: engine.dated(engine.day)
            engine.observe = lambda role, doctype, filters, fields: [{"name": doctype + "-1"}]
            with patch.object(operations_round, "Comms") as comms:
                client = comms.return_value
                client.create_room.return_value = "!room"
                operations_round.run(engine)
                operations_round.run(engine)
                engine.day = 1
                operations_round.run(engine)
            client.create_room.assert_called_once()
            self.assertEqual(client.join.call_count, 4)
            posts = client.send_chat.call_args_list
            self.assertEqual(len(posts), 6)
            self.assertEqual(posts[0].args, ("!room", "2027-01-01 Open work orders: Work Order-1"))
            self.assertEqual(len({call.kwargs["transaction"] for call in posts}), 6)
            self.assertEqual(client.put_event.call_count, 6)
            (event,) = parse_vevents(client.put_event.call_args_list[0].args[1])
            self.assertEqual((event["dtstamp"], event["dtstart"]), ("20270101T120000Z", "20270102T083000Z"))
            self.assertEqual(len(event["attendees"]), 3)
