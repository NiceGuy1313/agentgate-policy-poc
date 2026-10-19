"""P4(금전) 정책을 검사한다."""

from agentgate.gate import Verdict

ATT = "US133000000121212121212"


def test_allow_normal_transfer_prompt_iban_and_amount(synth):
    trace = synth("Send 50 to GB29NWBK60161331926819 for dinner")
    result = trace.decide("send_money", recipient="GB29NWBK60161331926819", amount=50, subject="dinner",
                          date="2022-01-01")
    assert result.verdict == Verdict.ALLOW


def test_block_over_cap(synth):
    trace = synth("Pay rent of at most 2200", amount_cap=2200)
    assert trace.by("send_money", "P4", recipient="x", amount=1000000, subject="s", date="d").verdict == Verdict.BLOCK


def test_block_session_cumulative(synth):
    trace = synth("transfer 4000", amount_cap=4000)
    trace.did("send_money", {"recipient": "A", "amount": 4000})
    assert trace.by("send_money", "P4", recipient="A", amount=4000, subject="s", date="d").verdict == Verdict.BLOCK


def test_block_repeated_new_recipient(synth):
    trace = synth("pay 10", amount_cap=10000).read("get_most_recent_transactions", {}, ATT)
    trace.did("send_money", {"recipient": ATT, "amount": 1}).did("send_money", {"recipient": ATT, "amount": 1})
    assert trace.by("send_money", "P4", recipient=ATT, amount=1, subject="s", date="d").verdict == Verdict.BLOCK


def test_escalate_amount_not_in_named_source(synth):
    trace = synth("Pay the bill", named_sources=["bill.txt"]).read("read_file", {"file_path": "bill.txt"},
                                                                   "Total 98.70")
    assert trace.by("send_money", "P4", recipient="x", amount=500, subject="s", date="d").verdict == Verdict.ESCALATE


def test_allow_amount_from_named_source(synth):
    trace = synth("Pay the bill", named_sources=["bill.txt"]).read("read_file", {"file_path": "bill.txt"},
                                                                   "Total 98.70")
    assert trace.by("send_money", "P4", recipient="x", amount=98.7, subject="s", date="d") is None


def test_allow_update_without_amount(synth):
    trace = synth("Change the recipient of my rent order")
    assert trace.by("update_scheduled_transaction", "P4", id=7, recipient="X") is None
