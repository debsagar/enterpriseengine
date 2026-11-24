"""Generate a static registry explorer from component declarations."""

import argparse
from html import escape
import json
from pathlib import Path
from shutil import copyfile

from components.registry.catalog import builtin_registry


HERE = Path(__file__).resolve().parent


def schema_rows(schema):
    return "".join(
        f"<li>{escape(name)} <code>{escape(kind)}</code></li>"
        for name, kind in sorted(schema.items())
    )


def section(label, style, rows):
    return (
        f'<section class="{style}"><h3>{label}</h3>'
        + (f"<ul>{rows}</ul>" if rows else "<p>None declared</p>")
        + "</section>"
    )


def build(registry, output):
    """Write the complete site into a directory suitable for static serving."""
    catalog = registry.catalog()
    anchors = {
        entry["type"]: "type-" + entry["type"].encode("utf-8", "surrogatepass").hex()
        for entry in catalog
    }
    navigation = []
    cards = []
    for entry in catalog:
        type_id = entry["type"]
        anchor = anchors[type_id]
        title = escape(type_id)
        navigation.append(f'<li><a href="#{anchor}">{title}</a></li>')
        sections = [
            section(label, "fields", schema_rows(entry[key]))
            for key, label in (
                ("instance_schema", "Configuration fields"),
                ("state_schema", "State fields"),
                ("parameters", "Parameters"),
                ("policies", "Policies"),
            )
        ]
        for key, label in (("emits", "Emitted events"), ("handles", "Handled events")):
            events = "".join(
                f"<li>{escape(name)}"
                + (f"<ul>{schema_rows(schema)}</ul>" if schema else " <code>empty payload</code>")
                + "</li>"
                for name, schema in sorted(entry[key].items())
            )
            sections.append(section(label, key, events))

        relations = []
        for name, target in sorted(entry["relations"].items()):
            link = escape(target)
            if target in anchors:
                link = f'<a href="#{anchors[target]}">{link}</a>'
            else:
                link += " <small>(unregistered)</small>"
            relations.append(f"<li>{escape(name)} → {link}</li>")
        sections.append(section("Relations", "relations", "".join(relations)))
        for key, label in (("requires_adapters", "Required adapters"),
                           ("provides_adapters", "Provided adapters")):
            rows = "".join(f"<li><code>{escape(name)}</code></li>" for name in sorted(entry[key]))
            sections.append(section(label, "adapters", rows))

        cards.append(
            f'<article id="{anchor}"><header><span class="sprite" aria-hidden="true"></span>'
            f'<div><h2>{title}</h2><p>Source · {escape(entry["provenance"])}</p></div></header>'
            f'<div class="details">{"".join(sections)}</div></article>'
        )

    output.mkdir(parents=True, exist_ok=True)
    if output.resolve() != HERE:
        copyfile(HERE / "explorer.css", output / "explorer.css")
    (output / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Component registry</title>
  <link rel="stylesheet" href="explorer.css">
</head>
<body>
  <h1>Component registry</h1>
  <div class="layout">
    <nav aria-label="Registered types">
      <h2>Registered types</h2>
      <ul>{"".join(navigation)}</ul>
    </nav>
    <main>{"".join(cards)}</main>
  </div>
</body>
</html>
""", encoding="utf-8", errors="xmlcharrefreplace",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE)
    parser.add_argument("--extra-descriptors", type=Path, help="JSON array of additional descriptors")
    args = parser.parse_args()
    extras = []
    if args.extra_descriptors:
        extras = json.loads(args.extra_descriptors.read_text(encoding="utf-8"))
        if not isinstance(extras, list):
            parser.error("--extra-descriptors must contain a JSON array")
    build(builtin_registry(extras), args.output)


if __name__ == "__main__":
    main()
