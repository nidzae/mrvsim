"""Observation simulator (TDD section 5): overpasses, deployment plans, gates, detection, logs."""

from mrvsim.observe.deployment import DeploymentPlan, SensorDeployment, build_plan
from mrvsim.observe.overpass import Overpasses, compute_overpasses, overpasses_for
from mrvsim.observe.simulator import CMSLog, ObservationLog, ObservationSet, simulate_observations
from mrvsim.observe.solar import solar_zenith_deg

__all__ = ["CMSLog", "DeploymentPlan", "ObservationLog", "ObservationSet", "Overpasses", "SensorDeployment", "build_plan",
           "compute_overpasses", "overpasses_for", "simulate_observations", "solar_zenith_deg"]
