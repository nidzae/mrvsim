"""The seven-stage pipeline (SPEC section 5), each stage writing a table under the run directory."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from mdd.aoi import AreaOfInterest, load_area
from mdd.discover import Asset, assets_frame, discover, user_assets_from_area
from mdd.ingest.carbonmapper import fetch_plumes, fetch_scenes, retrieval_dates
from mdd.looks import build_looks
from mdd.model.dutyfactor import duty_posterior
from mdd.model.rate import rate_from_basin, rate_from_detections
from mdd.model.rollup import AssetDraws, ChainResult, asset_draws, roll_up
from mdd.score import Scorecard, score
from mdd.sensors import SensorLibrary, load_sensors

RUNS = Path(__file__).resolve().parents[1] / "runs"
# Segment-level literature fractions for sub-threshold emissions (SPEC 6c third option): a prior, labelled as such.
# Values are placeholders marked verify; replace with the measurement-informed numbers from EEMDL/Rutherford work.
SUB_THRESHOLD_PRIOR_FRACTION = {"production": 0.004, "gathering": 0.002, "processing": 0.001, "transmission": 0.001, "storage": 0.001, "distribution": 0.003, "lng": 0.001}


@dataclass
class RunConfig:
    area: str
    customer_volume_mmbtu_yr: float
    target_intensity: float = 0.002
    start: str = "2024-08-01"
    end: str | None = None
    n_draws: int = 10_000
    prior: str = "jeffreys"
    seed: int = 1
    sub_threshold: str = "literature_prior"       # supplier | area_flux | literature_prior | none
    include_segments: tuple[str, ...] = ("production", "gathering", "processing", "transmission", "storage")
    confirmed_assets: str | None = None            # CSV of asset_id, confirmed, customer_share, throughput_ch4_kg_yr overrides
    name: str = "run"


@dataclass
class RunResult:
    run_dir: Path
    area: AreaOfInterest
    assets: list[Asset]
    plumes: pd.DataFrame
    scenes: pd.DataFrame
    looks: pd.DataFrame
    attributed: pd.DataFrame
    per_asset: list[dict[str, Any]]
    chain: ChainResult
    scorecard: Scorecard
    meta: dict[str, Any] = field(default_factory=dict)


def _apply_confirmations(assets: list[Asset], path: str | None) -> list[Asset]:
    if not path:
        return assets
    df = pd.read_csv(path)
    by = {str(r.asset_id): r for _, r in df.iterrows()}
    out = []
    for a in assets:
        r = by.get(a.asset_id)
        if r is None:
            continue
        if "confirmed" in df and not bool(r.confirmed):
            continue
        a.confirmed = True
        if "customer_share" in df and pd.notna(r.customer_share):
            a.customer_share = float(r.customer_share)
        if "throughput_ch4_kg_yr" in df and pd.notna(r.throughput_ch4_kg_yr):
            a.throughput_ch4_kg_yr = float(r.throughput_ch4_kg_yr); a.throughput_source = str(r.get("throughput_source", "user")) if "throughput_source" in df else "user"
        out.append(a)
    return out


def allocate_default_shares(assets: list[Asset], delivered_ch4_kg_yr: float, apply: bool) -> list[str]:
    """Default customer shares (SPEC 4b rule 3): within each segment, the customer's methane is spread over the
    assets in proportion to throughput, so that sum(share x throughput) equals the delivered volume. Assets without
    a throughput get share 1 and are flagged. Returns the consistency notes for the report."""
    notes = []
    segs = sorted({a.segment for a in assets})
    for seg in segs:
        members = [a for a in assets if a.segment == seg]
        thr = sum(a.throughput_ch4_kg_yr or 0.0 for a in members)
        if thr <= 0:
            notes.append(f"{seg}: no throughput on any asset; shares left at 1 and intensity cannot be formed for this segment")
            continue
        if apply:
            f = min(delivered_ch4_kg_yr / thr, 1.0)
            for a in members:
                a.customer_share = f if a.throughput_ch4_kg_yr else 1.0
            notes.append(f"{seg}: default allocation, customer share {f:.3f} of every asset's throughput (segment throughput {thr / 1e6:.2f} kt CH4/yr vs {delivered_ch4_kg_yr / 1e6:.2f} kt delivered)")
        else:
            carried = sum((a.customer_share or 0.0) * (a.throughput_ch4_kg_yr or 0.0) for a in members)
            if abs(carried - delivered_ch4_kg_yr) > 0.2 * delivered_ch4_kg_yr:
                notes.append(f"{seg}: WARNING customer shares carry {carried / 1e6:.2f} kt CH4/yr against {delivered_ch4_kg_yr / 1e6:.2f} kt delivered (SPEC 4b rule 3)")
    return notes


def run(cfg: RunConfig, lib: SensorLibrary | None = None) -> RunResult:
    lib = lib or load_sensors()
    rng = np.random.default_rng(cfg.seed)
    run_dir = RUNS / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{cfg.name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(cfg.__dict__, indent=2), encoding="utf-8")

    # 1. area and assets
    area = load_area(cfg.area)
    assets = discover(area, cfg.include_segments) + user_assets_from_area(area)
    assets = _apply_confirmations(assets, cfg.confirmed_assets)
    for a in assets:
        a.confirmed = a.confirmed or cfg.confirmed_assets is None
    delivered_ch4 = cfg.customer_volume_mmbtu_yr * 19.26
    share_notes = allocate_default_shares(assets, delivered_ch4, cfg.confirmed_assets is None)
    af = assets_frame(assets) if assets else gpd.GeoDataFrame()
    if len(af):
        af.to_parquet(run_dir / "assets.parquet")
    if not assets:
        raise ValueError("no assets inside the area; draw a polygon over infrastructure or add points")

    # 2-3. detections and observations
    bbox = area.bbox()
    pad = 0.3                                                  # degrees, so that scenes that cover the edge are found
    start = pd.Timestamp(cfg.start, tz="UTC"); end = pd.Timestamp(cfg.end, tz="UTC") if cfg.end else pd.Timestamp.now(tz="UTC")
    plumes = fetch_plumes((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad))
    scenes = fetch_scenes((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad))
    looks, attributed = build_looks(assets, scenes, plumes, lib, start, end)
    for name, df in (("plumes", plumes), ("scenes", scenes.drop(columns=["footprint"], errors="ignore")), ("looks", looks), ("attributed", attributed)):
        if len(df):
            df.to_parquet(run_dir / f"{name}.parquet")

    # 6-7. per-asset model
    basin_rates = plumes.rate_kg_h.dropna().to_numpy() if not plumes.empty else np.array([])
    draws: list[AssetDraws] = []
    per_asset: list[dict[str, Any]] = []
    for a in assets:
        lk = looks[looks.asset_id == a.asset_id] if not looks.empty else pd.DataFrame()
        clear = lk[lk.clear] if len(lk) else lk
        det = attributed[attributed.asset_id == a.asset_id] if not attributed.empty else pd.DataFrame()
        n_looks = int(len(clear)); n_det = int(det.scene_id.nunique()) if len(det) else 0
        rec: dict[str, Any] = {"asset_id": a.asset_id, "name": a.name, "segment": a.segment, "operator": a.operator, "n_looks": n_looks, "n_detections": n_det,
                               "throughput_ch4_kg_yr": a.throughput_ch4_kg_yr, "throughput_source": a.throughput_source, "customer_share": a.customer_share}
        thr = a.throughput_ch4_kg_yr if a.throughput_ch4_kg_yr else np.nan
        duty_d = rate_d = duty_z = rate_z = None
        attrib_p = 1.0
        if n_looks > 0:
            sensors = clear.sensor.to_numpy(); wind = clear.wind_m_s.to_numpy(float)
            detected = clear.detected.to_numpy(bool)
            if n_det > 0:
                rates = det.rate_kg_h.to_numpy(float); unc = det.rate_unc_kg_h.to_numpy(float)
                sig = np.array([lib[s].log_sigma for s in det.sensor])
                if np.isfinite(rates).any() and np.nanmax(rates) > 0:
                    rate_d = rate_from_detections(rates, unc, sig)
                else:   # detections published without a rate (SPEC stage 4: quantify from enhancement and wind; not yet built): basin prior
                    rate_d = rate_from_basin(basin_rates, lib[str(det.sensor.iloc[0])].detection_range_kg_h()); rec["note"] = "detections carry no emission rate; basin size prior used"
                attrib_p = float(det.attribution_prob.mean())
                qd = rate_d.sample(rng, 200)
                pod = np.stack([lib[s].pod(qd, w) for s, w in zip(sensors, wind)], axis=1)
                duty_d = duty_posterior(detected, pod, cfg.prior)
                rec.update({"rate_median_kg_h": rate_d.median_kg_h(), "duty_p50": float(duty_d.quantile(0.5)), "duty_p95": float(duty_d.quantile(0.95)),
                            "attribution_prob": attrib_p, "duty_p50_uniform": float(duty_posterior(detected, pod, "uniform").quantile(0.5))})
            else:
                rate_z = rate_from_basin(basin_rates, lib[sensors[0]].detection_range_kg_h())
                qz = rate_z.sample(rng, 200)
                pod = np.stack([lib[s].pod(qz, w) for s, w in zip(sensors, wind)], axis=1)
                duty_z = duty_posterior(np.zeros(n_looks), pod, cfg.prior)
                rec.update({"ceiling_duty_p95": float(duty_z.quantile(0.95)), "ceiling_rate_median_kg_h": rate_z.median_kg_h(), "pod_mean": float(pod.mean())})
        else:
            rec["note"] = "no clear look at this asset in the window: large-source ceiling is unbounded (duty factor prior only)"
            rate_z = rate_from_basin(basin_rates, lib["tan"].detection_range_kg_h())
            duty_z = duty_posterior(np.zeros(0), np.zeros((1, 0)), cfg.prior)
        if cfg.sub_threshold == "literature_prior" and np.isfinite(thr):
            sub = SUB_THRESHOLD_PRIOR_FRACTION.get(a.segment, 0.002) * thr * np.exp(rng.normal(0.0, 0.5, cfg.n_draws)); sub_basis = "literature_prior"
        else:
            sub = 0.0; sub_basis = "none"
        rec["sub_threshold_basis"] = sub_basis
        draws.append(asset_draws(rng, a.asset_id, a.segment, a.customer_share, thr if np.isfinite(thr) else 0.0, duty_d, rate_d, duty_z, rate_z, sub, sub_basis, attrib_p, cfg.n_draws, {"name": a.name}))
        per_asset.append(rec)

    # 8. roll-up and score
    chain = roll_up(draws, delivered_ch4)
    card = score(chain, cfg.target_intensity, supplier_data=cfg.sub_threshold == "supplier")
    pd.DataFrame(per_asset).to_parquet(run_dir / "per_asset.parquet")
    meta = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sources": {"carbonmapper": {"retrieved": retrieval_dates()}},
            "window": [str(start), str(end)], "n_assets": len(assets), "n_scenes": int(len(scenes)), "n_plumes": int(len(plumes)), "n_looks": int(len(looks)),
            "n_attributed": int(len(attributed)), "share_notes": share_notes, "prior": cfg.prior, "sub_threshold": cfg.sub_threshold, "wind": "default 3 m/s on every look (reanalysis not attached)"}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_dir / "scorecard.json").write_text(json.dumps(card.to_dict(), indent=2), encoding="utf-8")
    return RunResult(run_dir, area, assets, plumes, scenes, looks, attributed, per_asset, chain, card, meta)
