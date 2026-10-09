"""Command line: ``python -m mdd.cli run --area path --volume 1e6 [--target 0.002] [--start 2024-08-01]``."""

from __future__ import annotations

import argparse
import sys

from mdd.pipeline import RunConfig, run
from mdd.report import write_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mdd", description="Supply-chain methane due diligence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="score an area of interest")
    r.add_argument("--area", required=True, help="KML/KMZ/GeoJSON/CSV file, or pasted 'lat, lon, label' lines")
    r.add_argument("--volume", type=float, required=True, help="customer volume, MMBtu per year")
    r.add_argument("--target", type=float, default=0.002, help="target methane intensity (fraction; 0.002 = 0.2 %%)")
    r.add_argument("--start", default="2024-08-01"); r.add_argument("--end", default=None)
    r.add_argument("--draws", type=int, default=10_000); r.add_argument("--prior", default="jeffreys", choices=["jeffreys", "uniform"])
    r.add_argument("--sub-threshold", default="literature_prior", choices=["literature_prior", "none", "supplier"])
    r.add_argument("--confirmed", default=None, help="CSV of confirmed assets with customer_share and throughput overrides")
    r.add_argument("--name", default="run")
    d = sub.add_parser("discover", help="list the assets found inside an area, for confirmation")
    d.add_argument("--area", required=True); d.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "discover":
        from mdd.aoi import load_area
        from mdd.discover import assets_frame, discover, user_assets_from_area

        area = load_area(a.area)
        assets = discover(area) + user_assets_from_area(area)
        df = assets_frame(assets).drop(columns=["geometry"]) if assets else None
        if df is None:
            print("no assets found"); return 1
        df["confirmed"] = True
        if a.out:
            df.to_csv(a.out, index=False); print(f"{len(df)} assets written to {a.out}; edit confirmed / customer_share / throughput_ch4_kg_yr and pass with --confirmed")
        else:
            print(df.to_string())
        return 0
    cfg = RunConfig(area=a.area, customer_volume_mmbtu_yr=a.volume, target_intensity=a.target, start=a.start, end=a.end, n_draws=a.draws, prior=a.prior,
                    sub_threshold=a.sub_threshold, confirmed_assets=a.confirmed, name=a.name)
    res = run(cfg)
    p = write_report(res)
    c = res.scorecard
    print(f"{c.grade}: assured delivered intensity {c.assured_intensity * 100:.3f} % (target {c.target * 100:.2f} %), measured floor {c.measured_floor * 100:.3f} %, "
          f"evidence {c.evidence_share * 100:.0f} %; {res.meta['n_assets']} assets, {res.meta['n_looks']} looks, {res.meta['n_attributed']} attributed plume rows")
    print(f"report: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
