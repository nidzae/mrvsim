"""The validation framework itself (fast): refusal on placeholder priors, targets file integrity, perturbation helper."""

from __future__ import annotations

import os

import numpy as np
import pytest

from mrvsim.population import load_priors, load_strata
from mrvsim.population.priors import DEFAULT_PRIORS_PATH, perturb_priors
from mrvsim.sensors.library import reference_keys
from mrvsim.validate import v1_rates, v4_stability, v5_misspecification, v6_transient, v7_calibration
from mrvsim.validate.results import ALLOW_PLACEHOLDER_ENV
from mrvsim.validate.targets import load_targets


def test_targets_file_integrity() -> None:
    tg = load_targets()
    refs = reference_keys()
    for vid in ("V1", "V2", "V3", "V4", "V5", "V6", "V7"):
        assert vid in tg and "pass_rule" in tg[vid] and "citations" in tg[vid]
        for c in tg[vid]["citations"]:
            assert c in refs, (vid, c)
    assert tg["V3"]["intensities"]["haynesville"]["value"] == pytest.approx(0.0079)
    assert tg["V7"]["kappa_range"] == [0.85, 0.95]


def test_refusal_on_placeholder_priors(monkeypatch) -> None:
    monkeypatch.delenv(ALLOW_PLACEHOLDER_ENV, raising=False)
    st = load_strata(); pr = load_priors(DEFAULT_PRIORS_PATH, list(st.basins), list(st.facility_types))
    assert pr.is_placeholder
    for mod in (v1_rates, v4_stability, v5_misspecification, v6_transient, v7_calibration):
        res = mod.run()
        assert res.status == "skipped" and "PLACEHOLDER" in res.reason, (mod.__name__, res.status, res.reason)
        assert res.elapsed_s is None   # refused before any computation


def test_perturb_priors() -> None:
    st = load_strata(); pr = load_priors(DEFAULT_PRIORS_PATH, list(st.basins), list(st.facility_types))
    pp = perturb_priors(pr, alpha=0.3, mu=-0.3, nu_on=0.5)
    a, b = pr.for_cell("permian", "wp_gas"), pp.for_cell("permian", "wp_gas")
    assert b.alpha == pytest.approx(a.alpha + 0.3) and b.mu_0 == pytest.approx(a.mu_0 - 0.3) and b.mu_1 == pytest.approx(a.mu_1 - 0.3)
    assert b.nu_on == pytest.approx(a.nu_on + 0.5) and b.nu_off == a.nu_off
    assert pp.fit_provenance["perturbation"]["alpha"] == 0.3


@pytest.mark.slow
def test_v6_diagnostic_runs_on_placeholder(monkeypatch) -> None:
    monkeypatch.setenv(ALLOW_PLACEHOLDER_ENV, "1")
    res = v6_transient.run(n_events=40)
    assert res.status in ("pass", "fail") and res.diagnostic_only
    assert res.compared["mean_ratio_estimate_over_truth"] < 1.0
