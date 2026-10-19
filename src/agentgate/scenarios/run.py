"""호환 shim: 시나리오 CLI는 `agentgate.experiment.cli`로 옮겼다. `python -m agentgate.scenarios.run`도 그대로 돈다."""

import sys

from agentgate.experiment import cli

if __name__ == "__main__":
    cli.main()
else:
    sys.modules[__name__] = cli
