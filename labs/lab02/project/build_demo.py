"""Build an offline demonstration with embedded real traces; no CDN or server."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
data = {p.stem:json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((ROOT/"results"/"traces").glob("*.json"))}
template = (ROOT/"demo_template.html").read_text(encoding="utf-8")
(ROOT/"demo.html").write_text(template.replace("__DATA__",json.dumps(data, ensure_ascii=False).replace("</", "<\\/")),encoding="utf-8")
print("Built", ROOT/"demo.html", "with", len(data), "traces")
