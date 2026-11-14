"""0.10.0 item H8 (finding AI-2-11): no path reaches FINALISING unless the critic has run and the
evidence has been validated since the last agent output."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ceap.harness.state_machine import HarnessState, InvalidTransition, StateMachine

OPS = st.one_of(
    st.sampled_from(list(HarnessState)).map(lambda s: ("go", s)),
    st.sampled_from(["critiqued", "validated", "new_work"]).map(lambda m: (m, None)),
)


@settings(max_examples=400, deadline=None)
@given(st.lists(OPS, max_size=40))
def test_no_path_reaches_finalising_without_critic_and_validated_evidence(ops):
    sm = StateMachine()
    for op, target in ops:
        if op == "critiqued":
            sm.mark_critiqued()
        elif op == "validated":
            sm.mark_evidence_validated()
        elif op == "new_work":
            sm.mark_new_work()
        else:
            before = (sm.critic_ran, sm.evidence_validated)
            try:
                sm.transition(target)
            except InvalidTransition:
                continue
            if target is HarnessState.FINALISING:
                assert before == (True, True)
    finalising = [t for t in sm.history if t.to_state is HarnessState.FINALISING]
    assert len(finalising) <= 1


def test_finalising_is_refused_from_executing_without_the_flags():
    sm = StateMachine(HarnessState.EXECUTING)
    try:
        sm.transition(HarnessState.FINALISING)
    except InvalidTransition as exc:
        assert "critic" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("FINALISING reached without the critic")
    sm.mark_critiqued()
    sm.mark_evidence_validated()
    sm.transition(HarnessState.FINALISING)
    assert sm.state is HarnessState.FINALISING
