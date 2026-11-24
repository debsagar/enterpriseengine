# EnterpriseEngine 0.3.0 candidate

Release awaits joint signoff. After the `v0.3.0` tag is published, install it
in your Python environment:

```sh
python -m pip install "enterpriseengine @ git+https://github.com/debsagar/enterpriseengine.git@v0.3.0#subdirectory=packages/engine"
```

- Pull requests and pushes build and test the wheel with Python 3.10 and 3.12.
  The gate runs the public Python example and offline engine tests against the
  installed wheel outside the repository.
- The upgrade check builds the previous wheel from the `v0.2.0` Git revision.
  It verifies that completed state and history survive a package upgrade and
  that in-flight and queued work finish once at their original business times.
- Both jobs exercise the obligation suites and run the warehouse and restoration
  examples through their documented pause and resume cutoffs. The
  managed-services consumer regression suite runs on Python 3.12, which
  supports its unittest fixture.

## Roll back an upgrade

Before upgrading, stop the runner and save a copy of its run directory,
including the checkpoint and operation journal. To roll back, stop the 0.3.0
runner and keep its run directory separately as 0.3.0 history. Restore the
pre-upgrade copy to a separate run directory and use it with a Python
environment pinned to the previous release:

```sh
python -m pip install "enterpriseengine @ git+https://github.com/debsagar/enterpriseengine.git@v0.2.0"
```

This restores the saved checkpoint. Reconcile native application records
created after the backup separately.

## Run the release gate

Run the same gate locally from a checkout of the committed candidate revision.

```sh
PYTHON=python3.10 bash scripts/check_release.sh
PYTHON=python3.12 bash scripts/check_release.sh
```

The gate prints the wheel SHA-256. GitHub and the configured Python index supply
the previous release source and build dependencies. Runtime checks use local
fixtures and SQLite.
