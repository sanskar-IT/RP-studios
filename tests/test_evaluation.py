from __future__ import annotations

from services.evaluation import scenario_by_key
from services.evaluation.harness import HARNESS_CAPABILITIES, run
from services.evaluation.scenarios import ALL_SCENARIOS, INVARIANT_KINDS


def test_all_scenarios_declare_known_invariants():
    assert len(ALL_SCENARIOS) == 5
    for scenario in ALL_SCENARIOS:
        assert scenario.characters, scenario.key
        assert scenario.premise and scenario.objective and scenario.location
        for invariant in scenario.invariants:
            assert invariant.key in INVARIANT_KINDS, (scenario.key, invariant.key)
        for index in (0, 7, 30, 99):
            plan = scenario.plan(index, {spec.key: spec.name for spec in scenario.characters})
            assert plan.prose, (scenario.key, index)


def test_unknown_scenario_key_raises():
    import pytest

    with pytest.raises(LookupError):
        scenario_by_key("no_such_scenario")


def test_harness_report_is_machine_readable_and_deterministic():
    from services.evaluation.scenarios import scenario_by_key

    first = run(scenarios=["mystery", "combat"], turn_counts=(10,))
    payload = first.to_dict()
    assert payload["schema"] == "narrative-evaluation/1"
    assert payload["token_estimator"] == "heuristic-v1"
    assert payload["turns_requested"] == [10]
    assert len(payload["runs"]) == 2
    for metrics in payload["runs"]:
        assert metrics["turns"] == 10
        declared = {invariant.key for invariant in scenario_by_key(metrics["scenario"]).invariants}
        assert set(metrics["invariants"]) == declared
        assert metrics["average_context"] > 0
        assert "context_bounded" in metrics["invariants"]
    assert "context_bounded" in payload["runs"][0]["invariants"]
    assert "context_bounded" in payload["runs"][1]["invariants"]
    rendered = first.render()
    assert "Scenario:" in rendered

    second = run(scenarios=["mystery", "combat"], turn_counts=(10,))

    def stable(report):
        """Behaviour, not identifiers: ids and wall-clock timings legitimately differ."""
        view = []
        for metrics in report.to_dict()["runs"]:
            metrics = dict(metrics)
            metrics.pop("samples", None)
            metrics.pop("findings", None)
            metrics.pop("stage_timings_ms", None)
            view.append(metrics)
        return view

    assert [metrics.passed for metrics in second.scenarios] == [
        metrics.passed for metrics in first.scenarios
    ]
    assert stable(second) == stable(first), "the harness must be deterministic"
    assert HARNESS_CAPABILITIES["context_window"] == 8_192
