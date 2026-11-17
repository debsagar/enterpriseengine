Project state lives in PROJECT-LOG.jsonl (append-only event log). Replay it at session start via the project-log skill; append events as you work.

Use offline checks from README.md for packaging/code changes. Do not run generation or connect to live services unless requested. Never commit private runs, tokens, raw SQL, mail archives, or environment secrets.
