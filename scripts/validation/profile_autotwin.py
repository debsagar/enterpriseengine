"""Reproduce the pinned AutoTwin XES profiles from a local checkout."""

import argparse
import gzip
import json
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import islice
from pathlib import Path
from statistics import median
from xml.etree import ElementTree as ET

MANIFEST = Path(__file__).resolve().parents[2] / "contracts/validation/autotwin-v1.json"
MICROSECOND = timedelta(microseconds=1)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def attributes(element):
    return {child.get("key"): child.get("value") for child in element if child.get("key") is not None}


def traces(path):
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rb") as stream:
        elements = ET.iterparse(stream, events=("start", "end"))
        _, root = next(elements)
        if root.tag.rsplit("}", 1)[-1] != "log":
            raise ValueError("Expected an XES log")
        for phase, element in elements:
            if phase == "end" and element.tag.rsplit("}", 1)[-1] == "trace":
                yield (
                    attributes(element)["concept:name"],
                    [attributes(child) for child in element if child.tag.rsplit("}", 1)[-1] == "event"],
                )
                root.remove(element)


def instant(value):
    timestamp = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
    if timestamp.utcoffset() is None:
        raise ValueError("Expected an explicit timestamp offset")
    return (timestamp - EPOCH) // MICROSECOND


def seconds_summary(values):
    if not values:
        return None
    return {"min": min(values) / 1e6, "median": median(values) / 1e6, "max": max(values) / 1e6}


def profile(path):
    cases = set()
    activities, lifecycle, variants = Counter(), Counter(), Counter()
    spans, durations = [], []
    ties, inversions = Counter(), Counter()
    for case, events in traces(path):
        if not case or case in cases:
            raise ValueError("Missing or duplicate trace concept:name")
        cases.add(case)
        ordering_times, timestamps = [], []
        labels = []
        for event in events:
            activity = event["concept:name"]
            transition = event.get("lifecycle:transition")
            timestamp = instant(event["time:timestamp"])
            start = timestamp
            if "start_timestamp" in event:
                start = instant(event["start_timestamp"])
                if start > timestamp:
                    raise ValueError("Explicit start is later than event timestamp")
                durations.append(timestamp - start)
            activities[activity] += 1
            lifecycle[transition or "(absent)"] += 1
            labels.append((activity, transition))
            ordering_times.append(start)
            timestamps.append(timestamp)

        if events:
            spans.append(max(timestamps) - min(ordering_times))
        # sorted is stable, preserving the source order at equal instants.
        order = sorted(range(len(events)), key=ordering_times.__getitem__)
        variants[tuple(labels[i] for i in order)] += 1
        for key, times in (("ordering_time", ordering_times), ("event_timestamp", timestamps)):
            ties[key] += len(times) - len(set(times))
            inversions[key] += sum(left > right for left, right in zip(times, times[1:]))

    return {
        "cases": len(cases),
        "events": sum(activities.values()),
        "empty_cases": len(cases) - len(spans),
        "activities": dict(activities),
        "lifecycle": dict(lifecycle),
        "variants": len(variants),
        "most_frequent_variant": [
            {"activity": activity, "lifecycle": transition} for activity, transition in variants.most_common(1)[0][0]
        ]
        if variants
        else [],
        "most_frequent_variant_cases": max(variants.values(), default=0),
        "equal_time_events_after_first": dict(ties),
        "adjacent_source_time_decreases": dict(inversions),
        "observed_case_span_seconds": seconds_summary(spans),
        "explicit_intervals": len(durations),
        "explicit_interval_seconds": seconds_summary(durations),
    }


def source_pin(path):
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def verify_sources(root, manifest):
    for relative, reference in manifest["references"].items():
        actual = source_pin(root / relative)
        if actual != reference["source"]:
            raise ValueError(f"Source pin mismatch: {relative}")


def verify_fixture(source, fixture):
    path = MANIFEST.parent / fixture["path"]
    if source_pin(path) != fixture["source"]:
        raise ValueError(f"Fixture pin mismatch: {fixture['path']}")
    event_keys = {"concept:name", "lifecycle:transition", "start_timestamp", "time:timestamp"}
    with closing(traces(source)) as source_traces:
        expected = [
            (
                f"example-{index}",
                [{key: value for key, value in event.items() if key in event_keys} for event in events],
            )
            for index, (_, events) in enumerate(islice(source_traces, 2), start=1)
        ]
    if len(expected) != 2 or list(traces(path)) != expected:
        raise ValueError(f"Fixture excerpt mismatch: {fixture['path']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autotwin-root", type=Path, required=True, help="AutoTwin checkout containing data/")
    args = parser.parse_args(argv)
    root = args.autotwin_root / "data"
    try:
        manifest = json.loads(MANIFEST.read_text())
        verify_sources(root, manifest)
        for relative, reference in manifest["references"].items():
            source = root / relative
            if "fixture" in reference:
                verify_fixture(source, reference["fixture"])
            if profile(source) != reference["profile"]:
                raise ValueError(f"Profile mismatch: {relative}")
            print(f"Verified {relative}", flush=True)
    except (ValueError, OSError, ET.ParseError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
