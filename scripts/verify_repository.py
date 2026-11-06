"""Verify public submission layout, identical cores, data integrity and local links.

Run from any directory: python scripts/verify_repository.py
Standard library only; no network access and no changes to files.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    reference = ROOT / "labs/lab01/project"
    reference_files = {p.relative_to(reference).as_posix():digest(p)
                       for p in reference.rglob("*") if p.is_file()
                       and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    for lab in range(1,5):
        folder = ROOT/f"labs/lab{lab:02d}"
        assert (folder/"README.md").is_file()
        assert (folder/"report.pdf").read_bytes().startswith(b"%PDF-")
        project = folder/"project"
        for name, expected in reference_files.items():
            assert digest(project/name) == expected, (lab, name)
        results = project/"results"
        manifest = json.loads((results/"manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["source_sha256"].items():
            assert digest(project/name) == expected, (lab, "manifest", name)
        audit = json.loads((results/"quality_audit.json").read_text(encoding="utf-8"))
        assert audit["status"] == "passed" and audit["reproduced_runs"] == 900
        for name, expected in audit["source_sha256"].items():
            assert digest(project/name) == expected, (lab, "audited source", name)
        for name, expected in audit["raw_file_sha256"].items():
            assert digest(results/name) == expected, (lab, "audited CSV", name)
        for work, count in ((2,300),(3,120),(4,480)):
            with (results/f"lab{work}_raw.csv").open(encoding="utf-8-sig",newline="") as stream:
                assert sum(1 for _ in csv.DictReader(stream)) == count
        html = (project/"demo.html").read_text(encoding="utf-8")
        payload = re.search(r'<script id="data" type="application/json">([\s\S]*?)</script>',html)
        assert payload and len(json.loads(payload.group(1))) == 20
        print(f"lab{lab:02d}: report, identical source, 900 data rows, hashes and 20 demo modes verified")
    for path in ROOT.rglob("*.md"):
        for target in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding="utf-8")):
            if target.startswith(("http://","https://","#")):
                continue
            relative = unquote(target.split("#")[0])
            assert (path.parent/relative).exists(), (path, target)
    print("PASS: all four submissions and local Markdown links are complete")


if __name__ == "__main__":
    main()
