"""클라우드 환경 점검. 설치, AgentDojo suite 로딩, 네트워크, API 키 유무를 한 번에 확인한다.

사용법: python scripts/cloud/check_env.py      # 키 값은 출력하지 않고 설정 여부만 보인다
"""

from __future__ import annotations

import importlib
import os
import sys
import urllib.request

CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))


def main() -> int:
    sys.path.insert(0, "src")
    for module in ("agentgate", "agentdojo", "fastapi", "torch", "transformers"):
        try:
            importlib.import_module(module)
            record(f"import {module}", True)
        except Exception as exc:
            record(f"import {module}", False, f"{type(exc).__name__}: {exc}")

    try:
        from agentgate import SUITES
        from agentgate.agentdojo.suites import load_suite
        counts = {name: len(load_suite(name).user_tasks) for name in SUITES}
        record("AgentDojo suites", sum(counts.values()) == 97, str(counts))
    except Exception as exc:
        record("AgentDojo suites", False, f"{type(exc).__name__}: {exc}")

    for url in ("https://pypi.org/simple/", "https://huggingface.co/api/models/leolee99/PIGuard",
                "https://download.pytorch.org/whl/cpu/", "https://ollama.com/api/tags",
                "https://api.anthropic.com/"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=10) as response:
                record(f"net {url}", True, str(response.status))
        except urllib.error.HTTPError as exc:   # reachable; auth or method errors still prove egress works
            record(f"net {url}", exc.code < 500, f"HTTP {exc.code}")
        except Exception as exc:
            record(f"net {url}", False, f"{type(exc).__name__}: {exc}")

    for key in ("ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "HF_TOKEN"):
        record(f"env {key}", bool(os.environ.get(key)), "set" if os.environ.get(key) else "missing")

    width = max(len(name) for name, _, _ in CHECKS)
    for name, ok, detail in CHECKS:
        print(f"{'OK  ' if ok else 'FAIL'} {name:<{width}}  {detail}")
    required = [ok for name, ok, _ in CHECKS if name.startswith(("import agentgate", "import agentdojo",
                                                                  "AgentDojo"))]
    return 0 if all(required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
