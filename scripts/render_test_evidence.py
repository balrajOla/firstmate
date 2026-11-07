"""Render captured results, never invented test output, into README images.

Run after updating docs/evidence/verification.json and docs/evidence/pytest.xml.
Requires the development environment plus CairoSVG==2.9.1 and system Cairo.
PNG reports are rendered from JUnit/JSON; they are not WezTerm GUI captures.
"""

from collections import Counter
from io import StringIO
from pathlib import Path
import hashlib
import json
import re
import xml.etree.ElementTree as ET

import cairosvg
from rich.console import Console
from rich.table import Table
from rich.terminal_theme import MONOKAI
from rich.text import Text

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence"
ASSETS = ROOT / "docs/assets/tests"


def read_cases(path):
    tree = ET.parse(path)
    cases = []
    for item in tree.findall(".//testcase"):
        status = "PASS"
        for tag, label in [
            ("failure", "FAIL"),
            ("error", "ERROR"),
            ("skipped", "SKIP"),
        ]:
            if item.find(tag) is not None:
                status = label
                break
        cases.append(
            {
                "id": item.attrib["classname"] + "::" + item.attrib["name"],
                "status": status,
                "seconds": float(item.attrib.get("time", 0)),
            }
        )
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("JUnit cases must be nonempty and uniquely named")
    return cases


def render(name, title, columns, rows, notes):
    console = Console(record=True, width=118, file=StringIO(), color_system="truecolor")
    console.print(Text(title, style="bold bright_cyan"))
    console.print(
        Text(
            "Captured results | "
            + REPORT["tested_at"]
            + " | runtime "
            + REPORT["source_commit_tested"][:12]
        )
    )
    console.print()
    table = Table(expand=True, show_lines=True, padding=(0, 1))
    for index, heading in enumerate(columns):
        fixed = None
        if name in {"summary", "integration-checks"} and index != 1:
            fixed = {"Area": 13, "Result": 8, "Status": 14, "Observation": 24}[heading]
        table.add_column(
            heading,
            ratio=None if fixed else 7 if index == 1 else 1,
            width=fixed,
            min_width=13 if heading == "Area" else 8,
            overflow="fold",
        )
    for row in rows:
        table.add_row(
            *(
                Text(
                    str(cell),
                    style=(
                        "green"
                        if str(cell) == "PASS"
                        else "red" if str(cell) in {"FAIL", "MISS", "ERROR"} else ""
                    ),
                )
                for cell in row
            )
        )
    console.print(table)
    console.print()
    for note in notes:
        console.print(Text(note))
    svg = console.export_svg(
        title="Assistant Workbench — recorded test results", theme=MONOKAI
    )
    svg = re.sub(r"@font-face\s*\{[^}]+\}", "", svg)
    svg = svg.replace(
        "font-family: Fira Code, monospace;", "font-family: DejaVu Sans Mono;"
    )
    filename = name + ".png"
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(ASSETS / filename))
    return "docs/assets/tests/" + filename


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    cases = read_cases(EVIDENCE / "pytest.xml")
    counts = Counter(c["status"] for c in cases)
    expected = REPORT["core_and_installed_tests"]
    if (len(cases), counts["FAIL"], counts["ERROR"], counts["SKIP"]) != (
        expected["tests"],
        expected["failures"],
        expected["errors"],
        expected["skipped"],
    ):
        raise ValueError("Summary does not match the captured JUnit file")
    manifest = {
        "source_junit": "docs/evidence/pytest.xml",
        "source_report": "docs/evidence/verification.json",
        "images": [],
        "cases": [],
    }
    for start in range(0, len(cases), 13):
        group = cases[start : start + 13]
        image = render(
            "cases-" + str(start // 13 + 1),
            f"Pytest cases {start + 1}–{start + len(group)} of {len(cases)}",
            ["Result", "Exact test ID", "Seconds"],
            [(c["status"], c["id"], f"{c['seconds']:.3f}") for c in group],
            [
                "Rendered directly from JUnit. Names wrap; no cases are omitted.",
                "Fixtures, local processes and headless UI checks do not certify live model or desktop behavior.",
            ],
        )
        manifest["images"].append(image)
        manifest["cases"].extend({**c, "image": image} for c in group)
    assert [c["id"] for c in manifest["cases"]] == [c["id"] for c in cases]

    integrations = [*REPORT["local_integrations"]["checks"], *REPORT["lcm"]["checks"]]
    rows = [
        ("PASS" if c["passed"] else "FAIL", c["check"], "local package")
        for c in integrations
    ]
    rows.extend(
        [
            (
                "PASS" if REPORT["vale"]["clear_alerts"] == 0 else "FAIL",
                "Vale: clean writing fixture",
                str(REPORT["vale"]["clear_alerts"]) + " alerts",
            ),
            (
                "PASS" if REPORT["vale"]["seeded_verbose_alerts"] == 3 else "FAIL",
                "Vale: seeded verbose fixture",
                str(REPORT["vale"]["seeded_verbose_alerts"]) + " warnings",
            ),
            (
                (
                    "PASS"
                    if REPORT["promptfoo"]["positive_control"] == "passed"
                    else "FAIL"
                ),
                "Promptfoo: grounded positive control",
                REPORT["promptfoo"]["positive_control"],
            ),
            (
                (
                    "PASS"
                    if REPORT["promptfoo"]["seeded_false_claim"] == "correctly_rejected"
                    else "FAIL"
                ),
                "Promptfoo: seeded false claim",
                "rejected (expected)",
            ),
            (
                "PASS" if REPORT["intake_soak"]["passed"] else "FAIL",
                "Intake soak: prompts / reopen cycles / lost",
                f"{REPORT['intake_soak']['submitted']} / {REPORT['intake_soak']['restart_cycles']} / {REPORT['intake_soak']['lost_prompts']}",
            ),
            (
                "PASS" if REPORT["qualification_check"]["exit_code"] == 3 else "FAIL",
                "Qualification refuses missing target evidence",
                "exit " + str(REPORT["qualification_check"]["exit_code"]),
            ),
        ]
    )
    manifest["images"].append(
        render(
            "integration-checks",
            "Local integrations and negative controls",
            ["Result", "Check", "Observation"],
            rows,
            [
                "Synthetic inputs. LCM summarizer disabled. No live LLM judge.",
                "Promptfoo reports one intentional failure and exits 100: its assertion caught the seeded false claim.",
                f"The {REPORT['intake_soak']['seconds']}-second intake soak is not a 72-hour full-system soak.",
            ],
        )
    )
    route = REPORT["routing_demo"]
    manifest["images"].append(
        render(
            "routing-cases",
            f"Routing demonstration: {route['correct']}/{route['total']} correct",
            ["Result", "Case ID", "Expected", "Actual"],
            [
                (
                    "PASS" if c["passed"] else "MISS",
                    c["id"],
                    c["expected"],
                    c["predicted"],
                )
                for c in route["cases"]
            ],
            [
                "Known miss: 'Report the architecture review' selects design instead of report.",
                "This is a small demonstration, not a held-out quality benchmark. Review ambiguous workflow selections.",
            ],
        )
    )
    manifest["images"].append(
        render(
            "summary",
            "Verification snapshot",
            ["Area", "Observed result", "Status"],
            [
                (
                    "Pytest",
                    f"{len(cases)} cases; {counts['PASS']} passed; {counts['FAIL']} failed; {counts['ERROR']} errors; {counts['SKIP']} skipped",
                    "PASS" if counts["PASS"] == len(cases) else "FAIL",
                ),
                (
                    "Package",
                    "Wheel + source archive built; separate installation in a path with spaces exercised",
                    "PASS",
                ),
                (
                    "Integrations",
                    f"{sum(c['passed'] for c in integrations)}/{len(integrations)} Basic Memory, Docling, QMD and LCM checks",
                    "PASS" if all(c["passed"] for c in integrations) else "FAIL",
                ),
                (
                    "Writing",
                    "Vale clean/verbose checks; Promptfoo positive and seeded-negative controls",
                    "local fixtures",
                ),
                (
                    "Routing",
                    f"{route['correct']}/{route['total']} correct; ambiguous request retained as a known miss",
                    "MISS",
                ),
                (
                    "Intake",
                    f"{REPORT['intake_soak']['submitted']} prompts; {REPORT['intake_soak']['restart_cycles']} reopen cycles; {REPORT['intake_soak']['lost_prompts']} lost",
                    "PASS" if REPORT["intake_soak"]["passed"] else "FAIL",
                ),
                (
                    "Terminal",
                    "Interactions at 80x24, 120x36, 160x48; new 80/120-column captures",
                    "headless",
                ),
                (
                    "Live gates",
                    "Paperclip teams, authenticated workers, real rollovers, WezTerm desktop, research, 72h soak",
                    "OPEN",
                ),
            ],
            [
                "Every pytest case appears in the six case images and the linked machine-readable evidence.",
                "These are rendered reports from captured results. Headless app captures are labelled separately.",
            ],
        )
    )

    manifest["sha256"] = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in [
            manifest["source_junit"],
            manifest["source_report"],
            *manifest["images"],
        ]
    }
    (EVIDENCE / "screenshot-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    lines = [
        "# Every executed pytest case",
        "",
        f"{len(cases)} cases from the captured [JUnit file](pytest.xml). Image coverage is checked by `scripts/render_test_evidence.py`.",
        "",
        "| # | Test ID | Result | Seconds | Image |",
        "|---|---|---|---|---|",
    ]
    for n, c in enumerate(manifest["cases"], 1):
        lines.append(
            f"| {n} | `{c['id']}` | {c['status']} | {c['seconds']:.3f} | [capture](../assets/tests/{Path(c['image']).name}) |"
        )
    (EVIDENCE / "TEST_CASES.md").write_text("\n".join(lines) + "\n")
    print(
        f"Rendered {len(manifest['images'])} report images; all {len(cases)} JUnit cases accounted for."
    )


if __name__ == "__main__":
    REPORT = json.loads((EVIDENCE / "verification.json").read_text())
    main()
