# AutoTwin reference profiles v1

The [manifest](autotwin-v1.json) pins eight XES files across five collections.
Each path is relative to the `data/` directory of an AutoTwin checkout. The
source size and SHA-256 refer to the original file bytes, including the gzip
container for BPI 2017. Each entry records its upstream attribution, projected
case object and timestamp semantics. Read the profiles as counts of projected
trace rows. One original OCEL event can appear in multiple traces, and XES
cannot recover the complete original object relationships.

From the repository root, reproduce and compare all eight profiles with
Python 3.10+ and the original AutoTwin files.

```sh
python3 scripts/validation/profile_autotwin.py --autotwin-root /path/to/autotwin
```

The command checks every byte pin before parsing and fails on a changed source
or profile. The profiler opens its inputs read-only.

Run the same fixture suite CI discovers from the repository root. It uses
the bundled files and a temporary checkout to exercise the CLI's success
and mismatch paths.

```sh
uv run python -m unittest discover -s contracts -p 'test_v1_profiles.py' -v
```

The profile uses one case per unique trace `concept:name`. Each event's
ordering time is its explicit `start_timestamp` when present and its
`time:timestamp` otherwise. Equal instants preserve XES order, including
instants expressed with different UTC offsets. The most frequent variant uses
the activity and recorded lifecycle transition sequence. Equally frequent
variants use the one first encountered in trace order. Tie counts sum the
events beyond the first at each instant within each trace, separately for
ordering time and recorded event timestamp. Adjacent source-time decreases
show where the raw order differs from timestamp order.

Observed case span is the latest event timestamp minus the earliest ordering
time. Explicit interval duration is the event timestamp minus its recorded
start. Both are elapsed seconds with microsecond input resolution. Case
spans can include waiting and nonworking hours, and completion-only spans
start at the first observed event. BPI 2017 lifecycle transitions are counted
as recorded, with no pairing across rows. Empty cases have no measured span.

The six bundled fixture files use the first two traces of their respective
pinned benchmark, order-management and ForgeFlow projections. Their manifest
entries identify the upstream datasets and pin the fixtures. The excerpts
replace case IDs with `example-1` and `example-2` and retain only activity,
lifecycle and timestamp attributes. Full verification checks each fixture's
byte pin and compares its event sequence with the source traces. The gzip
test builds a synthetic log with tied instants and lifecycle transitions.
The BPI source files are needed only for full verification.

This supports offline block K validation. External XES evidence yields a
versioned manifest of aggregate reference profiles and checks of source
identity, ordering and duration semantics. It makes no native application
writes or changes to the running company.
