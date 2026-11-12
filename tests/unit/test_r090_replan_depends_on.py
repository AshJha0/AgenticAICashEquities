"""0.9.0 review: a re-plan with dependencies must keep its dependency graph valid after renaming."""

from __future__ import annotations

from ceap.domain.plans import PlanStep, StepType
from ceap.harness.planning import rename_replan_steps


def _step(step_id: str, seq: int, deps: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(id=step_id, sequence=seq, type=StepType.TOOL_CALL, description=step_id, depends_on=deps)


def test_dependencies_between_rerun_steps_follow_the_rename() -> None:
    out = rename_replan_steps([_step("a", 1), _step("b", 2, ("a",))])
    assert [s.id for s in out] == ["replan-a", "replan-b"]
    assert out[1].depends_on == ("replan-a",)


def test_dependency_on_a_step_that_is_not_rerun_is_dropped() -> None:
    # "a" was already satisfied in memory, so only "b" is re-run; its old dependency must not dangle.
    out = rename_replan_steps([_step("b", 2, ("a", "approval"))])
    assert out[0].depends_on == ()


def test_every_dependency_points_at_a_step_in_the_batch() -> None:
    out = rename_replan_steps([_step("x", 1), _step("y", 2, ("x", "gone")), _step("z", 3, ("x", "y"))])
    ids = {s.id for s in out}
    assert all(d in ids for s in out for d in s.depends_on)
