#!/usr/bin/env python3
"""Parse trivy-results/<service>{,.secrets}.json and write a Markdown summary to trivy-results/<service>.md.

Adapted from alpenlabs/blockscout-fe .github/scripts/render_trivy_summary.py;
the service name comes from the SERVICE environment variable.
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

SERVICE = os.environ.get("SERVICE", "unknown")
IMAGE_REF = os.environ.get("IMAGE_REF", "unknown")
IMAGE_TAG = os.environ.get("IMAGE_TAG", "unknown")

INPUTS = [Path(f"trivy-results/{SERVICE}.json"), Path(f"trivy-results/{SERVICE}.secrets.json")]
OUTPUT = Path(f"trivy-results/{SERVICE}.md")

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1}


def main():
    present = [p for p in INPUTS if p.exists()]
    if not present:
        print(f"::warning::No Trivy JSON found at {[str(p) for p in INPUTS]}, skipping summary", file=sys.stderr)
        return

    results = []
    for path in present:
        results += json.loads(path.read_text()).get("Results", [])

    findings = []
    for result in results:
        target = result.get("Target", "")
        for v in result.get("Vulnerabilities") or []:
            findings.append({
                "severity": v.get("Severity", ""),
                "kind": "vuln",
                "id": v.get("VulnerabilityID", ""),
                "pkg": v.get("PkgName", ""),
                "installed": v.get("InstalledVersion", ""),
                "fixed": v.get("FixedVersion", "") or "—",
                "title": v.get("Title", ""),
                "target": target,
            })
        for s in result.get("Secrets") or []:
            findings.append({
                "severity": s.get("Severity", ""),
                "kind": "secret",
                "id": s.get("RuleID", ""),
                "pkg": "—",
                "installed": "—",
                "fixed": "—",
                "title": s.get("Title", ""),
                "target": target,
            })

    lines = [f"### Trivy scan — {SERVICE} `{IMAGE_TAG}`", ""]

    if not findings:
        lines.append(f":white_check_mark: No HIGH or CRITICAL findings in `{IMAGE_REF}`.")
    else:
        counts = Counter(f["severity"] for f in findings)
        lines += [
            f":x: **{len(findings)} HIGH/CRITICAL finding(s)** in `{IMAGE_REF}`.",
            "",
            "| CRITICAL | HIGH |",
            "|---|---|",
            f"| {counts.get('CRITICAL', 0)} | {counts.get('HIGH', 0)} |",
            "",
            "| Severity | Type | ID | Package | Installed | Fixed | Target | Summary |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for f in sorted(findings, key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["pkg"], x["id"])):
            title = (f["title"][:57] + "…") if len(f["title"]) > 60 else f["title"]
            lines.append(
                f"| {f['severity']} | {f['kind']} | {f['id']} | `{f['pkg']}` "
                f"| {f['installed']} | {f['fixed']} | `{f['target']}` | {title} |"
            )

    OUTPUT.write_text("\n".join(lines) + "\n")
    print(f"Written: {OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
