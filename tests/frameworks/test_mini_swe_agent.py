"""mini-swe-agent gated shell 해석 테스트(LLM 없음)."""

import pytest

from agentgate.adapters.mini_swe_agent_adapter import GatedShell, parse_command


@pytest.mark.parametrize("line, expected", [
    ("""send_money '{"recipient": "GB29", "amount": 10.5}'""", ("send_money", {"recipient": "GB29", "amount": 10.5})),
    ("get_balance", ("get_balance", {})),
    ("get_most_recent_transactions --n 5", ("get_most_recent_transactions", {"n": 5})),
    ("read_file --file_path bill-december-2023.txt", ("read_file", {"file_path": "bill-december-2023.txt"})),
    ("submit 'Paid the bill.'", ("submit", "Paid the bill.")),
])
def test_parse_command(line, expected):
    assert parse_command(line) == expected


@pytest.mark.parametrize("line", ["send_money '[1, 2]'", "send_money --amount", "send_money amount 5"])
def test_parse_command_rejects_malformed_arguments(line):
    with pytest.raises(ValueError):
        parse_command(line)


class _Bridge:
    tool_names = {"get_balance"}

    def __init__(self):
        self.calls = []

    def invoke(self, tool, args):
        self.calls.append({"tool": tool, "args": args, "error": None})
        return "1000.0"


def test_gated_shell_runs_only_gated_tools_and_submit():
    pytest.importorskip("minisweagent")
    from minisweagent.exceptions import Submitted

    bridge = _Bridge()
    shell = GatedShell(bridge)
    out = shell.execute({"command": "get_balance\nrm -rf /tmp/x"})
    assert out["returncode"] == 127 and out["output"].startswith("1000.0\nrm: command not found")
    assert [c["tool"] for c in bridge.calls] == ["get_balance"]
    with pytest.raises(Submitted) as exit_:
        shell.execute({"command": "submit 'done'"})
    assert exit_.value.messages[0]["extra"]["submission"] == "done"
