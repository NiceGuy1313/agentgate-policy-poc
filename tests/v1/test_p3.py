"""P3(대량 반출) 정책을 검사한다."""

from agentgate.gate import Verdict

E = [{"id_": str(i), "body": f"this is the private body number {i} of an email"} for i in range(5)]


def _emails(n):
    return "\n\n".join(e["body"] for e in E[:n])


def test_block_many_emails_in_one_list_response(synth):
    trace = synth("hi").read("get_unread_emails", {}, _emails(5), raw=E)
    decision = trace.by("send_email", "P3", recipients=["a@b.com"], subject="s", body=_emails(5))
    assert decision.verdict == Verdict.BLOCK and decision.evidence["count"] >= 3


def test_block_many_separate_reads(synth):
    trace = synth("hi")
    for i in range(3):
        trace.read("read_channel_messages", {"channel": f"c{i}"}, f"channel {i} says the launch date is secret {i}")
    body = " ".join(f"channel {i} says the launch date is secret {i}" for i in range(3))
    assert trace.by("post_webpage", "P3", url="x.com", content=body).verdict == Verdict.BLOCK


def test_block_many_attachments(synth):
    trace = synth("hi")
    attachments = [{"type": "file", "file_id": str(i)} for i in range(5)]
    assert trace.by("send_email", "P3", recipients=["a@b.com"], subject="s", body="files",
                    attachments=attachments).verdict == Verdict.BLOCK


def test_no_flag_single_read(synth):
    trace = synth("hi").read("get_file_by_id", {"file_id": "1"}, "the meeting minutes for the client project")
    assert trace.by("send_email", "P3", recipients=["a@b.com"], subject="s",
                    body="the meeting minutes for the client project") is None


def test_no_flag_two_reads(synth):
    trace = synth("hi").read("get_unread_emails", {}, _emails(2), raw=E[:2])
    assert trace.by("send_email", "P3", recipients=["a@b.com"], subject="s", body=_emails(2)) is None


def test_no_flag_no_overlap(synth):
    trace = synth("hi").read("get_unread_emails", {}, _emails(5), raw=E)
    assert trace.by("send_email", "P3", recipients=["a@b.com"], subject="s", body="hello there") is None
