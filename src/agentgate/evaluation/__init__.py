"""Offline evaluation helpers: ground-truth replay, attack trajectories, pair sampling and metrics."""

from agentgate.evaluation.attack_trajectories import plan_attack_calls
from agentgate.evaluation.live_run_analysis import ATTACKER_DESTINATIONS
from agentgate.evaluation.metrics import TYPE_NAMES, mcnemar_exact, wilson
from agentgate.evaluation.pairs import stratified_pairs
from agentgate.evaluation.replay import replay_attack_pair, replay_benign_task

__all__ = ["ATTACKER_DESTINATIONS", "TYPE_NAMES", "mcnemar_exact", "plan_attack_calls", "replay_attack_pair",
           "replay_benign_task", "stratified_pairs", "wilson"]
