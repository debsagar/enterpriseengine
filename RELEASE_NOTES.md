# EnterpriseEngine 0.3.0 candidate

Release awaits joint signoff. The public install instructions continue to point
to `v0.2.0` until the candidate is approved and tagged.

- Pull requests and pushes build and test the wheel with Python 3.10 and 3.12.
  The gate runs the public Python example and offline engine tests against the
  installed wheel outside the repository.
- The upgrade check builds the previous wheel from the `v0.2.0` Git revision.
  It verifies that completed state and history survive a package upgrade and
  that in-flight and queued work finish once at their original business times.
- When the obligation suites and example are present in the candidate checkout,
  the gate also exercises them with the installed wheel. Warehouse and service
  SLA clients run through their documented pause and resume cutoffs.

Run the same gate locally from a checkout of the committed candidate revision.
This includes a local integration commit containing the engine and client
changes for release verification.

```sh
PYTHON=python3.10 bash scripts/check_release.sh
PYTHON=python3.12 bash scripts/check_release.sh
```

The gate prints the wheel SHA-256. GitHub and the configured Python index supply
the previous release source and build dependencies. Runtime checks use local
fixtures and SQLite.
