"""subprocess worker: stdin으로 RunRequest(JSON)를 받아 이벤트와 최종 레코드를 JSON lines로 내보낸다."""

from __future__ import annotations

import json
import sys

from agentgate.config import load_dotenv
from agentgate.harness.events import EventSink
from agentgate.harness.runner import RunRequest, run_scenario


def main() -> None:
    load_dotenv()
    request = RunRequest.from_dict(json.loads(sys.stdin.read()))
    out = sys.stdout
    sys.stdout = sys.stderr   # framework prints must not corrupt the JSON-lines channel
    sink = EventSink()

    def forward(event):
        out.write(json.dumps({"type": "event", "event": event.as_dict()}, ensure_ascii=False, default=str) + "\n")
        out.flush()

    sink.subscribe(forward)
    record = run_scenario(request, sink)
    out.write(json.dumps({"type": "record", "record": record}, ensure_ascii=False, default=str) + "\n")
    out.flush()


if __name__ == "__main__":
    main()
