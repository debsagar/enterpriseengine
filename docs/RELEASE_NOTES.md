# EnterpriseEngine 0.3.0 candidate

Release awaits joint signoff. After the `v0.3.0` tag is published, install it
in your Python environment:

```sh
python -m pip install "enterpriseengine @ git+https://github.com/debsagar/enterpriseengine.git@v0.3.0#subdirectory=packages/engine"
```

- Pull requests and pushes build and test the wheel with Python 3.10 and 3.12.
  The gate runs the public Python example and offline engine tests against the
  installed wheel outside the repository.
- The installed-wheel check seeds a real v0.2.0 checkpoint and confirms that
  the 0.3 candidate rejects it before changing the SQLite file. Fresh 0.3 runs
  can resume normally.

## Start a 0.3 run

Create a fresh run with the 0.3 engine, environment and scenario. Checkpoints,
run manifests and dataset releases require both engine and schema versions.
Artifacts from before 0.3 and artifacts missing either field fail to load.
The original Norvale company remains on its pinned v0.2.0 engine and keeps
its own checkpoint and application records.
