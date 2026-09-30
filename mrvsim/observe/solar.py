"""Solar zenith angle from date, time, and coordinate (TDD section 3.6, 5.2).

Vectorised implementation of the NOAA solar position algorithm (Meeus-based),
accurate to about 0.01 degrees for 1900-2100 [noaa-solar]. No ephemeris
download is needed, which keeps runs reproducible offline.
"""

from __future__ import annotations

import numpy as np

_J2000_UNIX = 946728000.0  # 2000-01-01T12:00:00Z


def solar_zenith_deg(unix_time_s: np.ndarray | float, lat_deg: np.ndarray | float, lon_deg: np.ndarray | float) -> np.ndarray:
    """Solar zenith angle in degrees (0 = overhead, 90 = horizon), broadcasting over inputs."""
    t = np.asarray(unix_time_s, dtype=float)
    lat = np.deg2rad(np.asarray(lat_deg, dtype=float))
    lon = np.asarray(lon_deg, dtype=float)
    jc = (t - _J2000_UNIX) / 86400.0 / 36525.0                       # Julian centuries since J2000
    gmls = np.mod(280.46646 + jc * (36000.76983 + jc * 0.0003032), 360.0)   # geometric mean longitude (deg)
    gmas = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)            # geometric mean anomaly (deg)
    eeo = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)        # eccentricity
    m = np.deg2rad(gmas)
    seoc = (np.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + np.sin(2 * m) * (0.019993 - 0.000101 * jc)
            + np.sin(3 * m) * 0.000289)                               # equation of centre
    stl = gmls + seoc                                                 # sun true longitude
    omega = 125.04 - 1934.136 * jc
    sal = stl - 0.00569 - 0.00478 * np.sin(np.deg2rad(omega))         # apparent longitude
    moe = 23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    oc = moe + 0.00256 * np.cos(np.deg2rad(omega))                    # obliquity corrected
    decl = np.arcsin(np.sin(np.deg2rad(oc)) * np.sin(np.deg2rad(sal)))
    y = np.tan(np.deg2rad(oc) / 2) ** 2
    eqt = 4 * np.rad2deg(y * np.sin(2 * np.deg2rad(gmls)) - 2 * eeo * np.sin(m) + 4 * eeo * y * np.sin(m) * np.cos(2 * np.deg2rad(gmls))
                         - 0.5 * y * y * np.sin(4 * np.deg2rad(gmls)) - 1.25 * eeo * eeo * np.sin(2 * m))  # minutes
    minutes_utc = np.mod(t, 86400.0) / 60.0
    tst = np.mod(minutes_utc + eqt + 4 * lon, 1440.0)                 # true solar time (minutes)
    ha = np.where(tst / 4 < 0, tst / 4 + 180, tst / 4 - 180)          # hour angle (deg)
    cos_z = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(np.deg2rad(ha))
    return np.rad2deg(np.arccos(np.clip(cos_z, -1.0, 1.0)))


def year_start_unix(year: int) -> float:
    import datetime as dt

    return dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc).timestamp()


def hour_index_to_unix(year: int, hour_idx: np.ndarray | float, minute_offset: float = 30.0) -> np.ndarray:
    """Unix time at the midpoint (default) of simulation hour ``hour_idx`` (0 = Jan 1 00:00 UTC)."""
    return year_start_unix(year) + np.asarray(hour_idx, dtype=float) * 3600.0 + minute_offset * 60.0
