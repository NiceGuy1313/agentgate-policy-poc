"""P5(삭제) 정책을 검사한다."""

from agentgate.policy import Verdict


class Sent:
    def __init__(self, i):
        self.id_ = i


def test_block_deleting_email_sent_by_agent(synth):
    trace = synth("hi").did("send_email", {"recipients": ["a@b.com"]}, raw=Sent("34"))
    assert trace.by("delete_email", "P5", email_id="34").verdict == Verdict.BLOCK


def test_block_deleting_any_of_several_sent(synth):
    trace = synth("hi").did("send_email", {}, raw=Sent("40")).did("send_email", {}, raw=Sent("41"))
    assert trace.by("delete_email", "P5", email_id="41").verdict == Verdict.BLOCK


def test_escalate_file_not_in_prompt(synth):
    trace = synth("tidy up").read("list_files", {}, [{"id_": "13"}])
    assert trace.by("delete_file", "P5", file_id="13").verdict == Verdict.ESCALATE


def test_escalate_unseen_file(synth):
    assert synth("hi").by("delete_file", "P5", file_id="99").verdict == Verdict.ESCALATE


def test_allow_deleting_received_email(synth):
    trace = synth("hi").did("send_email", {}, raw=Sent("40"))
    assert trace.by("delete_email", "P5", email_id="7") is None


def test_allow_file_named_in_prompt(synth):
    assert synth("Delete the file with ID 'f-123'").by("delete_file", "P5", file_id="f-123") is None


def test_allow_delete_email_without_sends(synth):
    assert synth("delete spam").by("delete_email", "P5", email_id="3") is None
