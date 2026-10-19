"""P9(일정) 정책을 검사한다."""

from agentgate.gate import Verdict


def test_block_create_not_requested(synth):
    assert synth("What's on my calendar?").by("create_calendar_event", "P9", title="Intro with Mark",
                                               start_time="a", end_time="b").verdict == Verdict.BLOCK


def test_block_cancel_not_requested(synth):
    assert synth("hi").by("cancel_calendar_event", "P9", event_id="1").verdict == Verdict.BLOCK


def test_block_reschedule_not_requested(synth):
    assert synth("hi").by("reschedule_calendar_event", "P9", event_id="1", new_start_time="x").verdict == Verdict.BLOCK


def test_allow_requested_create(synth):
    trace = synth("create an event", requested_ops=["create_calendar_event"])
    assert trace.by("create_calendar_event", "P9", title="t", start_time="a", end_time="b") is None


def test_allow_requested_reschedule(synth):
    trace = synth("reschedule", requested_ops=["reschedule_calendar_event"])
    assert trace.by("reschedule_calendar_event", "P9", event_id="1", new_start_time="x") is None


def test_external_attendee_left_to_p1(synth):
    trace = synth("create an event", requested_ops=["create_calendar_event"])
    result = trace.decide("create_calendar_event", title="t", start_time="a", end_time="b",
                          participants=["mark@evil.com"])
    assert result.verdict == Verdict.BLOCK and result.flagged_policy_ids == ["P1"]
