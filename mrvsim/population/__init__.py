"""Population generator (TDD section 3): strata, sources, temporal process, throughput, conditions."""

from mrvsim.population.generate import Population, generate_population, load_population
from mrvsim.population.priors import PlaceholderPriorsError, PriorSet, load_priors, require_fitted
from mrvsim.population.strata import StrataTable, load_strata
from mrvsim.population.temporal import StatePaths, duty_cycle, simulate_intermittent_states
from mrvsim.population.throughput import Constants

__all__ = [
    "Constants", "PlaceholderPriorsError", "Population", "PriorSet", "StatePaths", "StrataTable",
    "duty_cycle", "generate_population", "load_population", "load_priors", "load_strata", "require_fitted", "simulate_intermittent_states",
]
