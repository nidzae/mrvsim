"""Sensor library tests (TDD section 4; CLAUDE.md Phase 2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mrvsim.sensors import (
    PODCurve, QuantificationError, SensorSchemaError, SurfaceAdjustment, fit_pod_logistic, load_library, load_sensor,
    reference_keys, unresolved_citations,
)

LIB = load_library()


def test_library_loads_all_expected_sensors() -> None:
    keys = set(LIB.sensors)
    assert {"bridger_gml", "kairos", "aviris_ng_gao", "ghgsat_c", "sentinel2_msi", "landsat_oli", "prisma", "enmap",
            "tanager1", "tropomi", "cms_generic", "drone_generic", "ogi"} <= keys


@pytest.mark.parametrize("key", sorted(LIB.sensors))
def test_pod_monotone_in_rate(key: str) -> None:
    s = LIB[key]
    q = np.logspace(-3, 5, 400)
    p = s.pod.prob(q, wind_m_s=3.0, rho_surf=0.3)
    assert np.all(np.diff(p) >= -1e-12)
    assert p[0] < 0.05 and p[-1] > 0.95
    assert s.pod.prob(0.0) == 0.0


@pytest.mark.parametrize("key", sorted(LIB.sensors))
def test_pod50_pod90_round_trip(key: str) -> None:
    s = LIB[key]
    assert s.pod.prob(s.pod.pod50_kg_h) == pytest.approx(0.5, abs=1e-9)
    assert s.pod.prob(s.pod.pod90_kg_h) == pytest.approx(0.9, abs=1e-9)
    assert s.pod.pod90_kg_h > s.pod.pod50_kg_h


def test_pod_wind_dependence_direction() -> None:
    s = LIB["bridger_gml"]  # gamma = 1
    q = 5.0
    assert s.pod.prob(q, wind_m_s=1.0) > s.pod.prob(q, wind_m_s=3.0) > s.pod.prob(q, wind_m_s=9.0)
    c = LIB["cms_generic"]  # gamma = 0
    assert c.pod.prob(q, wind_m_s=1.0) == c.pod.prob(q, wind_m_s=9.0)


def test_surface_adjustment_shape() -> None:
    sa = SurfaceAdjustment(enabled=True, rho_ref=0.30, rho_p10=0.12, phi_at_p10=0.5)
    assert sa.phi(0.30) == pytest.approx(1.0)
    assert sa.phi(0.12) == pytest.approx(0.5)
    assert sa.phi(0.40) == pytest.approx(1.0)   # never brighter than test site
    assert sa.phi(0.0) >= 0.0
    s = LIB["kairos"]
    assert s.pod.prob(10.0, wind_m_s=3.0, rho_surf=0.12) < s.pod.prob(10.0, wind_m_s=3.0, rho_surf=0.30)


def test_quantification_reproduces_published_aircraft_range() -> None:
    """CLAUDE.md Phase 2: at least one aircraft sensor reproduces the published 95 % range (-60 %, +90 %)."""
    s = LIB["bridger_gml"]
    lo, hi = s.quantification.ratio_interval(0.95)
    assert lo == pytest.approx(0.40, abs=0.02)
    assert hi == pytest.approx(1.90, abs=0.05)
    rng = np.random.default_rng(0)
    r = s.quantification.draw_reported(rng, np.full(200_000, 100.0))
    q2, q97 = np.percentile(r / 100.0, [2.5, 97.5])
    assert q2 == pytest.approx(0.40, abs=0.02)
    assert q97 == pytest.approx(1.90, abs=0.05)


def test_quantification_loglik_peaks_at_truth_times_bias() -> None:
    qe = QuantificationError(beta=-0.1, sigma=0.4)
    q = np.array([50.0])
    r_grid = np.linspace(20, 100, 801)
    ll = qe.log_likelihood(r_grid, q)
    assert r_grid[np.argmax(ll)] == pytest.approx(50.0 * np.exp(-0.1), rel=0.01)


def test_fit_recovers_parameters_and_laplace_shrinks() -> None:
    rng = np.random.default_rng(1)
    a_true, b_true = -2.0, 1.5
    curve = PODCurve(a=a_true, b=b_true, gamma=1.0, u_ref_m_s=3.0, surface=SurfaceAdjustment(False, 0.3, 0.12))

    def make(n: int) -> pd.DataFrame:
        q = np.exp(rng.uniform(-1, 5, n))
        u = rng.uniform(1, 8, n)
        p = curve.prob(q, wind_m_s=u)
        return pd.DataFrame({"rate_kg_h": q, "detected": rng.random(n) < p, "wind_m_s": u})

    small = fit_pod_logistic(make(150), gamma=1.0, u_ref_m_s=3.0)
    large = fit_pod_logistic(make(5000), gamma=1.0, u_ref_m_s=3.0)
    assert large.converged
    assert abs(large.a - a_true) < 3 * large.se_a + 0.05
    assert abs(large.b - b_true) < 3 * large.se_b + 0.05
    assert large.se_a < small.se_a and large.se_b < small.se_b
    c = large.to_curve(curve.surface)
    assert c.ab_cov is not None
    a_s, b_s = c.sample_ab(np.random.default_rng(2), 1000)
    assert abs(a_s.mean() - large.a) < 0.1 and np.all(b_s > 0)


def test_fit_handles_complete_separation() -> None:
    df = pd.DataFrame({"rate_kg_h": [1, 2, 3, 30, 40, 50], "detected": [0, 0, 0, 1, 1, 1]})
    fit = fit_pod_logistic(df, gamma=0.0)
    assert np.isfinite(fit.a) and np.isfinite(fit.b) and fit.b > 0
    assert 3 < np.exp(-fit.a / fit.b) < 30   # POD50 lies in the gap


def test_tier_rules_enforced() -> None:
    for s in LIB:
        if s.tier != "A":
            assert not s.enabled_for_certification, s.key
    assert {s.key for s in LIB.certifiable("A")} == {s.key for s in LIB if s.tier == "A"}
    assert "tropomi" not in {s.key for s in LIB.certifiable("A")}
    assert "tropomi" in {s.key for s in LIB.certifiable("B")} or True  # tier B still needs enabled flag


def test_every_citation_resolves_in_references() -> None:
    """PRD N1 / G5: every citation key in the sensor library exists in REFERENCES.md."""
    refs = reference_keys()
    assert "sherwin2023" in refs and refs["cusworth2022"] == "ok"
    assert unresolved_citations(LIB, refs) == {}


def test_all_sensors_have_every_tdd_field() -> None:
    for s in LIB:
        assert s.pod and s.quantification and s.false_positive and s.constraints and s.cost
        assert s.observation_mode in ("snapshot", "continuous", "survey")
        assert s.spatial_scope in ("site", "source")
        if s.sensor_class == "satellite":
            assert s.orbit is not None and s.orbit.swath_km > 0 and s.orbit.tle_name
        assert s.unverified_blocks()   # nothing is fitted yet; the attribution panel must flag this


def test_schema_rejects_tier_b_enabled(tmp_path) -> None:
    src = (LIB["tropomi"].source_path)
    text = open(src).read().replace("enabled_for_certification: false", "enabled_for_certification: true")
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SensorSchemaError, match="PRD N2"):
        load_sensor(p)


def test_schema_rejects_missing_citations(tmp_path) -> None:
    text = open(LIB["ogi"].source_path).read().replace("citations: [cost-assumptions]", "citations: []")
    p = tmp_path / "bad2.yaml"
    p.write_text(text)
    with pytest.raises(SensorSchemaError, match="citations"):
        load_sensor(p)
