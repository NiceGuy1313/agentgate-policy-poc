"""호환 shim: 이 모듈은 `agentgate.experiment.live_run_analysis`로 옮겼다. 같은 모듈 객체를 가리키므로 monkeypatch도 그대로 듣는다."""

import sys

from agentgate.experiment import live_run_analysis

sys.modules[__name__] = live_run_analysis
