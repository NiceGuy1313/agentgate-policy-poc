"""mini-swe-agent gated shell parsing and browser-use conformance in its own venv (LLM-free)."""

import subprocess

import pytest

from agentgate.adapters.mini_swe_agent_adapter import GatedShell, parse_command
from agentgate.config import PROJECT_ROOT
from agentgate.harness import registry


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


BROWSER_USE_PYTHON = registry.entry("browser-use").get("python")


@pytest.mark.skipif(not BROWSER_USE_PYTHON or not (PROJECT_ROOT / BROWSER_USE_PYTHON).exists(),
                    reason="browser-use venv not set up (README)")
def test_browser_use_conformance_in_its_venv():
    """browser-use cannot be imported next to agentgate's dependencies, so its conformance run uses its venv."""
    result = subprocess.run([BROWSER_USE_PYTHON, "-m", "agentgate.harness.conformance", "--framework", "browser-use"],
                            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "8/8 passed" in result.stdout
