"""Archive maintenance tickets and their comments through the read-only Gitea identity."""

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
    """`client` lists issues with `records()` and one issue's comments with `comments(number)`."""
    issues = []

    def fetch_issues():
        issues.extend(client.records())
        return [issue_document(row) for row in issues]

    def fetch_comments():
        return [comment_document(row, issue["number"]) for issue in issues for row in client.comments(issue["number"])]

    count = ingest(root, "Gitea Issue", fetch_issues, source=source, projection=lambda _, row: row)
    return count + ingest(root, "Gitea Comment", fetch_comments, source=source, projection=lambda _, row: row)
