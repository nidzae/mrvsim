"""Satellite overpasses from public TLEs with Skyfield (TDD section 5.1) [skyfield; celestrak].

For each satellite the ground track is propagated at a fixed step over the
simulated year. A facility has an observation opportunity at every track time
whose cross-track distance (point-to-segment distance between consecutive
sub-points, in a local tangent plane) is within half the instrument swath.
Results are cached per satellite per year in ``data/cache/overpasses``.

TLE snapshot
------------
Runs use the pinned TLEs in ``data/fitted/tle/<sensor>.tle`` (fetched from
CelesTrak on 2026-09-30) so that a run is reproducible offline and independent
of the day's element set. Propagating a pinned TLE across a different calendar
year gives realistic revisit statistics and local times but not the true
historical pass times; that is sufficient for an OSSE and is recorded in
DECISION_LOG (2026-09-30, Phase 3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from mrvsim.observe.solar import solar_zenith_deg, year_start_unix

_REPO = Path(__file__).resolve().parents[2]
TLE_DIR = _REPO / "data" / "fitted" / "tle"
CACHE_DIR = _REPO / "data" / "cache" / "overpasses"
EARTH_RADIUS_KM = 6371.0088


@dataclass
class Overpasses:
    """All (facility, time) opportunities for one satellite over one year."""

    sensor_key: str
    year: int
    facility_idx: np.ndarray        # int64
    unix_time_s: np.ndarray         # float64, exact pass time
    hour_idx: np.ndarray            # int64, simulation hour (0..8759)
    cross_track_km: np.ndarray      # float32
    solar_zenith_deg: np.ndarray    # float32 at the facility at pass time
    tle_epoch: str
    step_s: float
    heading_deg: np.ndarray | None = None   # float32, ground-track direction of motion at closest approach, clockwise from north

    def __len__(self) -> int:
        return int(self.facility_idx.shape[0])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, facility_idx=self.facility_idx, unix_time_s=self.unix_time_s, hour_idx=self.hour_idx,
                            cross_track_km=self.cross_track_km, solar_zenith_deg=self.solar_zenith_deg,
                            heading_deg=self.heading_deg if self.heading_deg is not None else np.full(len(self), np.nan, np.float32),
                            meta=np.array([self.sensor_key, str(self.year), self.tle_epoch, str(self.step_s)]))

    @classmethod
    def load(cls, path: Path) -> "Overpasses":
        with np.load(path) as z:
            meta = list(z["meta"])
            return cls(meta[0], int(meta[1]), z["facility_idx"], z["unix_time_s"], z["hour_idx"], z["cross_track_km"],
                       z["solar_zenith_deg"], meta[2], float(meta[3]), z["heading_deg"] if "heading_deg" in z else None)


def read_tle(path: Path) -> tuple[str, str, str]:
    lines = [ln.rstrip("\n") for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) == 2:
        return Path(path).stem, lines[0], lines[1]
    if len(lines) >= 3:
        return lines[0].strip(), lines[1], lines[2]
    raise ValueError(f"{path}: not a TLE file")


MU_EARTH_KM3_S2 = 398600.4418


def tle_altitude_km(tle_path: Path) -> float:
    """Mean orbital altitude from the TLE mean motion (line 2, columns 53-63, rev/day): a = (mu / n^2)^(1/3) - R_E."""
    _, _, l2 = read_tle(tle_path)
    n_rev_day = float(l2[52:63])
    n = 2.0 * np.pi * n_rev_day / 86400.0
    return float((MU_EARTH_KM3_S2 / n**2) ** (1.0 / 3.0) - EARTH_RADIUS_KM)


def ground_reach_km(altitude_km: float, max_off_nadir_deg: float) -> float:
    """Ground distance from the sub-satellite point reachable by pointing ``max_off_nadir_deg`` off nadir (spherical Earth).

    With R the Earth radius and h the altitude, the Earth-central angle is
    gamma = asin((R + h)/R sin(theta)) - theta and the ground distance is R gamma.
    """
    theta = np.deg2rad(max_off_nadir_deg)
    ratio = (EARTH_RADIUS_KM + altitude_km) / EARTH_RADIUS_KM
    arg = np.clip(ratio * np.sin(theta), -1.0, 1.0)
    return float(EARTH_RADIUS_KM * (np.arcsin(arg) - theta))


def tasking_band_km(swath_km: float, max_off_nadir_deg: float | None, altitude_km: float | None) -> float:
    """Cross-track band (full width) within which a facility can be imaged on a pass: swath plus twice the pointing reach."""
    if max_off_nadir_deg is None or altitude_km is None or max_off_nadir_deg <= 0:
        return float(swath_km)
    return float(swath_km + 2.0 * ground_reach_km(altitude_km, max_off_nadir_deg))


def _bearing_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Initial bearing from unit vectors a to b, degrees clockwise from north."""
    lat1 = np.arcsin(np.clip(a[:, 2], -1, 1)); lat2 = np.arcsin(np.clip(b[:, 2], -1, 1))
    lon1 = np.arctan2(a[:, 1], a[:, 0]); lon2 = np.arctan2(b[:, 1], b[:, 0])
    dlon = lon2 - lon1
    y = np.sin(dlon) * np.cos(lat2); x = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    return (np.rad2deg(np.arctan2(y, x)) + 360.0) % 360.0


def _unit_xyz(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    lat, lon = np.deg2rad(lat_deg), np.deg2rad(lon_deg)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def ground_track(tle_path: Path, year: int, step_s: float = 20.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """Sub-satellite (lat, lon) at ``step_s`` intervals over the year. Returns (unix_time, lat, lon, epoch)."""
    from skyfield.api import EarthSatellite, load, wgs84

    name, l1, l2 = read_tle(tle_path)
    ts = load.timescale()
    sat = EarthSatellite(l1, l2, name, ts)
    t0 = year_start_unix(year)
    n = int(np.ceil(365.0 * 86400.0 / step_s)) + 1
    unix = t0 + np.arange(n) * step_s
    lat = np.empty(n); lon = np.empty(n)
    chunk = 200_000
    for lo in range(0, n, chunk):
        hi = min(lo + chunk, n)
        tt = ts.utc(1970, 1, 1, 0, 0, unix[lo:hi])   # seconds since epoch passed as the seconds argument
        sp = wgs84.subpoint_of(sat.at(tt))
        lat[lo:hi] = sp.latitude.degrees
        lon[lo:hi] = sp.longitude.degrees
    return unix, lat, lon, f"{l1[18:32].strip()}"


def compute_overpasses(
    sensor_key: str, tle_path: Path, year: int, fac_lat: np.ndarray, fac_lon: np.ndarray, swath_km: float,
    step_s: float = 20.0,
) -> Overpasses:
    """Swath test of every facility against the year's ground track.

    The propagation step adapts to the swath: wide swaths (TROPOMI) tolerate a
    coarse step because the point-to-segment test interpolates between
    sub-points; narrow swaths need a fine step to bound the coarse search radius.
    """
    step_s = float(np.clip(swath_km / 4.0 / 7.7, step_s, 180.0))
    unix, lat, lon, epoch = ground_track(tle_path, year, step_s)
    track = _unit_xyz(lat, lon)                       # (N, 3)
    fac = _unit_xyz(np.asarray(fac_lat, float), np.asarray(fac_lon, float))   # (F, 3)
    # Coarse search radius: half swath plus half the along-track spacing (chord on unit sphere).
    seg_km = 7.7 * step_s                              # LEO ground speed ~7.7 km/s upper bound
    radius_km = swath_km / 2.0 + seg_km / 2.0 + 5.0
    tree = cKDTree(track)
    hits = tree.query_ball_point(fac, r=2 * np.sin(radius_km / EARTH_RADIUS_KM / 2.0))
    f_idx = np.concatenate([np.full(len(h), i, dtype=np.int64) for i, h in enumerate(hits)]) if len(hits) else np.array([], np.int64)
    t_idx = np.concatenate([np.asarray(h, dtype=np.int64) for h in hits]) if len(hits) else np.array([], np.int64)
    if f_idx.size == 0:
        return Overpasses(sensor_key, year, f_idx, np.array([]), np.array([], np.int64), np.array([], np.float32),
                          np.array([], np.float32), epoch, step_s, heading_deg=np.array([], np.float32))
    # Exact cross-track distance: point-to-segment for the segment [t_idx, t_idx+1] and [t_idx-1, t_idx]; take the min.
    d_km = np.full(f_idx.shape, np.inf)
    t_at = np.empty(f_idx.shape)
    head = np.zeros(f_idx.shape)
    for a_idx, b_idx in ((t_idx, np.minimum(t_idx + 1, len(track) - 1)), (np.maximum(t_idx - 1, 0), t_idx)):
        a, b, p = track[a_idx], track[b_idx], fac[f_idx]
        ab = b - a
        ab2 = np.einsum("ij,ij->i", ab, ab)
        s = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.maximum(ab2, 1e-18), 0.0, 1.0)
        q = a + s[:, None] * ab
        chord = np.linalg.norm(p - q, axis=1)
        dist = 2 * EARTH_RADIUS_KM * np.arcsin(np.clip(chord / 2, 0, 1))
        better = dist < d_km
        d_km = np.where(better, dist, d_km)
        t_at = np.where(better, unix[a_idx] + s * (unix[b_idx] - unix[a_idx]), t_at)
        head = np.where(better, _bearing_deg(a, b), head)
    keep = d_km <= swath_km / 2.0
    f_idx, t_at, d_km, head = f_idx[keep], t_at[keep], d_km[keep], head[keep]
    # One opportunity per pass: collapse hits of the same facility within 10 minutes to the closest approach.
    order = np.lexsort((t_at, f_idx))
    f_idx, t_at, d_km, head = f_idx[order], t_at[order], d_km[order], head[order]
    new_pass = np.ones(f_idx.shape, dtype=bool)
    new_pass[1:] = (f_idx[1:] != f_idx[:-1]) | (np.diff(t_at) > 600.0)
    pass_id = np.cumsum(new_pass) - 1
    # closest approach within each pass, vectorised: sort by (pass, distance) and take the first of each pass
    order2 = np.lexsort((d_km, pass_id))
    first = np.unique(pass_id[order2], return_index=True)[1]
    sel = order2[first]
    f_idx, t_at, d_km, head = f_idx[sel], t_at[sel], d_km[sel], head[sel]
    hour = ((t_at - year_start_unix(year)) // 3600.0).astype(np.int64)
    ok = (hour >= 0) & (hour < 8760)
    f_idx, t_at, d_km, hour, head = f_idx[ok], t_at[ok], d_km[ok], hour[ok], head[ok]
    sza = solar_zenith_deg(t_at, np.asarray(fac_lat)[f_idx], np.asarray(fac_lon)[f_idx])
    return Overpasses(sensor_key, year, f_idx, t_at, hour, d_km.astype(np.float32), sza.astype(np.float32), epoch, step_s,
                      heading_deg=head.astype(np.float32))


def _cache_key(sensor_key: str, year: int, fac_lat: np.ndarray, fac_lon: np.ndarray, swath_km: float, step_s: float, tle_path: Path) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(fac_lat, dtype=np.float64).tobytes())
    h.update(np.ascontiguousarray(fac_lon, dtype=np.float64).tobytes())
    h.update(f"v2|{sensor_key}|{year}|{swath_km}|{step_s}|{Path(tle_path).read_text()}".encode())   # v2: heading column
    return h.hexdigest()[:16]


def overpasses_for(
    sensor_key: str, year: int, fac_lat: np.ndarray, fac_lon: np.ndarray, swath_km: float,
    tle_path: Path | None = None, step_s: float = 20.0, cache_dir: Path | None = CACHE_DIR,
) -> Overpasses:
    """Cached overpass computation ("cache per satellite per year", CLAUDE.md Phase 3).

    The cache key also covers the facility coordinates and the TLE text, so a
    different population or a refreshed TLE never reuses a stale cache.
    """
    tle_path = tle_path or (TLE_DIR / f"{sensor_key}.tle")
    if not tle_path.exists():
        raise FileNotFoundError(f"no pinned TLE for {sensor_key} at {tle_path}; see data/fitted/tle/provenance.json")
    if cache_dir is not None:
        key = _cache_key(sensor_key, year, fac_lat, fac_lon, swath_km, step_s, tle_path)
        path = Path(cache_dir) / f"{sensor_key}_{year}_{key}.npz"
        if path.exists():
            return Overpasses.load(path)
        ov = compute_overpasses(sensor_key, tle_path, year, fac_lat, fac_lon, swath_km, step_s)
        ov.save(path)
        return ov
    return compute_overpasses(sensor_key, tle_path, year, fac_lat, fac_lon, swath_km, step_s)
