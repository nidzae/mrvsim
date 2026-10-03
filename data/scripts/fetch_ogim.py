#!/usr/bin/env python
"""Fetch the Oil and Gas Infrastructure Mapping (OGIM) database GeoPackage from Zenodo [ogim].

Writes ``data/raw/ogim/OGIM_v3.0.gpkg`` (3.4 GB, git-ignored) and a provenance JSON. The file is
only needed to rebuild ``data/fitted/site_locations.npz`` with ``build_site_locations.py``; running
the simulator does not need it. Public (CC-BY 4.0), no credentials required. The download resumes
if interrupted. Run:

    .venv/bin/python data/scripts/fetch_ogim.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1] / "raw" / "ogim"
# Zenodo record confirmed live 2026-10-03 (v3.0, published 2026-09-18). [ogim]
RECORD = "22835235"
FILENAME = "OGIM_v3.0.gpkg"
URL = f"https://zenodo.org/api/records/{RECORD}/files/{FILENAME}/content"
MD5 = "99d94e96eefe4d7f561d1a2dbcc1a313"


def md5sum(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / FILENAME
    have = out.stat().st_size if out.exists() else 0
    with requests.get(URL, headers={"Range": f"bytes={have}-"} if have else {}, stream=True, timeout=300) as r:
        if r.status_code == 416:                      # already complete
            pass
        elif r.status_code not in (200, 206):
            raise RuntimeError(f"{URL} -> HTTP {r.status_code}")
        else:
            with out.open("ab" if r.status_code == 206 else "wb") as f:
                for chunk in r.iter_content(1 << 22):
                    f.write(chunk)
    digest = md5sum(out)
    if digest != MD5:
        print(f"md5 mismatch: {digest} != {MD5}", file=sys.stderr)
        return 1
    prov = {"citation_key": "ogim", "url": URL, "zenodo_record": RECORD, "doi": f"10.5281/zenodo.{RECORD}", "md5": digest,
            "bytes": out.stat().st_size, "license": "CC-BY-4.0", "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (RAW / "OGIM_v3.0.provenance.json").write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    print(f"ok: {out} ({prov['bytes'] / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
