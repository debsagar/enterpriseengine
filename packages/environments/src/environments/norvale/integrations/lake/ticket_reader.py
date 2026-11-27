"""Archive maintenance tickets and their comments through the read-only Gitea identity."""

from datetime import datetime, timedelta

if __package__:
    from .boundary import ingest
else:
    from environments.norvale.integrations.lake.boundary import ingest


def _common(row):
    return dict(
        body=row.get("body") or "",
        user=(row.get("user") or {}).get("login"),
        creation=row["created_at"],
        modified=row["updated_at"],
    )


def issue_document(row):
    return dict(
        _common(row),
        name=str(row["number"]),
        title=row["title"],
        state=row["state"],
        closed_at=row.get("closed_at"),
        labels=[label["name"] for label in row.get("labels") or []],
    )


def comment_document(row, issue):
    return dict(_common(row), name=str(row["id"]), issue=str(issue))


def scan(root, source, client):
    """Read repository issues and comments by their native update time."""

    def fetch(cursor, query, project):
        latest = cursor.get("updated_at")
        # Gitea timestamps have second precision. Replay the boundary second to
        # include concurrent writes tied with the previous high watermark.
        since = (datetime.fromisoformat(latest) - timedelta(seconds=1)).isoformat() if latest else None
        for row in query(since=since):
            stamp = row["updated_at"]
            if not latest or stamp > latest:
                latest = stamp
            yield project(row)
        if latest:
            cursor["updated_at"] = latest

    def comments(cursor):
        return fetch(
            cursor,
            client.changed_comments,
            lambda row: comment_document(row, int(row["issue_url"].rsplit("/", 1)[-1])),
        )

    return sum(
        ingest(root, entity, source=source, projection=lambda _, row: row, fetch_incremental=read)
        for entity, read in (
            ("Gitea Issue", lambda cursor: fetch(cursor, client.records, issue_document)),
            ("Gitea Comment", comments),
        )
    )
