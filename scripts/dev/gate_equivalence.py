"""리팩터링 전후 Gate 동작 동일성 검사: 오프라인·결정론적으로 정답 궤적 재생 결과와 판정 입력을 스냅숏으로 남기고 비교한다.

LLM 호출은 없다. ToolSpec 추출기는 툴 응답의 typed 값(normalization.extract_typed_candidates)과 <INFORMATION> 블록에서
요청 하나를 만드는 stub으로, 판정 LLM은 user prompt 해시로 ALLOW/BLOCK을 정하는 stub client로 바꾼다.

사용법:
  python scripts/dev/gate_equivalence.py --out before.json
  python scripts/dev/gate_equivalence.py --out after.json --compare before.json   # 차이가 있으면 exit 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_OUT = ("/private/tmp/claude-501/-Users-marista-Desktop-REPO-policy-2/30f79746-591d-4cf2-9dae-ee2bf249a0ac/"
               "scratchpad/equiv_before.json")
ATTACK = "tool_knowledge"
CONDITIONS = ("no_gate", "gate_static_rules")
MODES = ("audit", "enforce")
TYPED_TO_DATA = {"email": "emails", "iban": "ibans", "url": "urls", "amount": "amounts"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class StubToolSpec:
    """Deterministic ToolSpec: one record with the typed values of the output; an <INFORMATION> block adds one
    request (action = first tool name in the block, targets = typed values in the block, urgency claimed)."""

    def __init__(self, suite_name: str):
        from agentgate.policy_gate.toolspec import ToolSpecExtractor
        from agentgate.scenarios.suites import describe_tools, load_suite
        self.real = ToolSpecExtractor(describe_tools(load_suite(suite_name)))
        self.tool_names = self.real.tool_names
        self.render_hashes: list[str] = []

    @staticmethod
    def _typed(text: str) -> dict:
        from agentgate.core.normalization import extract_typed_candidates
        candidates = extract_typed_candidates(text)
        return {key: sorted(candidates[kind]) for kind, key in TYPED_TO_DATA.items()}

    def extract(self, tool, args, text):
        from types import SimpleNamespace

        from agentgate.policy_gate.toolspec import validate_toolspec
        from agentgate.scenarios.replay import ATTACK_MARKER
        self.render_hashes.append(_sha(self.real.render(tool, args, text)))
        raw = {"records": [{"id": "r1", "kind": "other", "author": None, "data": self._typed(text)}], "requests": []}
        if ATTACK_MARKER in text:
            block = text.split(ATTACK_MARKER, 1)[1].split("</INFORMATION>", 1)[0]
            positions = sorted((block.find(name), name) for name in self.tool_names if name in block)
            raw["requests"].append({"record": "r1", "addressed_to": "assistant",
                                    "action": positions[0][1] if positions else "other",
                                    "claims": {"urgency": True}, "targets": self._typed(block)})
        spec, dropped = validate_toolspec(raw, text, self.tool_names)
        return SimpleNamespace(spec=spec, cached=True, error=None, input_tokens=0, output_tokens=0, latency_ms=0.0,
                               cache_read_input_tokens=0, cache_creation_input_tokens=0, dropped=dropped)


_STUBS: dict[str, StubToolSpec] = {}


class StubJudgeClient:
    model, provider = "stub-model", "anthropic"

    def __init__(self, *args, **kwargs):
        pass

    def complete(self, system, user, max_tokens=4000, cache_system=False, cache_user_prefix=None):
        from agentgate.llm.client import Completion
        answer = "ALLOW\nR10" if int(_sha(system + user)[:2], 16) % 4 else "BLOCK\nR2"
        return Completion(answer, self.model, 1.0, 10, 2)


def _freeze_clock() -> None:
    """AgentDojo stamps sent e-mails and edited files with datetime.now(): fix it so tool outputs repeat exactly."""
    import datetime
    import types

    from agentdojo.default_suites.v1.tools import cloud_drive_client, email_client
    from agentdojo.default_suites.v1.tools import types as tool_types

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 5, 15, 12, 0, 0, tzinfo=tz)

    shim = types.SimpleNamespace(**{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("__")})
    shim.datetime = FrozenDatetime
    email_client.datetime = cloud_drive_client.datetime = shim
    field = tool_types.Email.model_fields["timestamp"]
    field.default_factory = FrozenDatetime.now
    tool_types.Email.model_rebuild(force=True)


def _install_stubs(cache_dir: str) -> None:
    _freeze_clock()
    os.environ["AGENTGATE_JUDGE_PROVIDER"] = "anthropic"
    os.environ["AGENTGATE_JUDGE_MODEL"] = "stub-model"
    from agentgate.policy_gate import assembly
    from agentgate.policy_gate import llm_judge as judge_module

    def toolspec_extractor(suite_name):
        if suite_name not in _STUBS:
            _STUBS[suite_name] = StubToolSpec(suite_name)
        return _STUBS[suite_name]

    assembly.toolspec_extractor = toolspec_extractor
    judge_module.LLMClient = StubJudgeClient
    judge_module.CACHE_DIR = Path(cache_dir)


def _run_job(job):
    import functools

    from agentgate.experiment.conditions import make_runtime
    from agentgate.policy_gate.llm_judge import build_facts, render_system, render_user
    from agentgate.policy_gate.rule_checker import RuleChecker
    from agentgate.scenarios.replay import replay_attack_pair, replay_benign_task
    from agentgate.scenarios.suites import load_suite
    from agentgate.scenarios.task_specs import load_task_spec

    condition, mode, suite_name, user_task_id, injection_task_id = job
    spec = load_task_spec("oracle", suite_name, user_task_id)
    prompt = load_suite(suite_name).get_user_task_by_id(user_task_id).PROMPT
    factory = functools.partial(make_runtime, condition, suite_name=suite_name)
    checker, system = RuleChecker(), render_system(True)
    effects: list[dict] = []
    stub = _STUBS.get(suite_name)
    render_start = len(stub.render_hashes) if stub else 0

    def runtime_factory(functions, *, prompt=prompt, spec, mode):
        runtime = factory(functions, prompt=prompt, spec=spec, mode=mode)
        original = runtime.resolve

        def resolve(call, result):
            if call.tool in runtime.effect_tools and runtime.index is not None:
                record = checker.check(call, runtime.context)
                effects.append({"tool": call.tool, "verdict": record["verdict"], "rule_ids": record["rule_ids"],
                                "reason": record["reason"],
                                "judge_prompt": _sha(system + render_user(build_facts(call, runtime.context, prompt)))})
            return original(call, result)

        runtime.resolve = resolve
        return runtime

    key = f"{condition}|{mode}|{suite_name}|{user_task_id}|{injection_task_id or ''}"
    try:
        if injection_task_id is None:
            run = replay_benign_task(suite_name, user_task_id, spec, mode, runtime_factory=runtime_factory)
        else:
            run = replay_attack_pair(suite_name, user_task_id, injection_task_id, spec, mode, ATTACK,
                                     runtime_factory=runtime_factory)
    except Exception as error:   # e.g. no_gate in enforce mode refuses to run: the refusal is behaviour too
        return key, {"error": f"{type(error).__name__}: {error}", "effects": effects}
    for event in run["trace"]:
        event.pop("decide_ms", None)
    stub = _STUBS.get(suite_name)
    renders = stub.render_hashes[render_start:] if stub else []
    out = {k: run.get(k) for k in ("utility", "security", "security_executed", "reached", "trace")}
    out.update(effects=effects, toolspec_renders=renders)
    return key, json.loads(json.dumps(out, sort_keys=True, default=str))


def _jobs(suites) -> list[tuple]:
    from agentgate.scenarios.suites import load_suite, task_number
    jobs = []
    for suite_name in suites:
        suite = load_suite(suite_name)
        user_ids = sorted(suite.user_tasks, key=task_number)
        injection_ids = sorted(suite.injection_tasks, key=task_number)
        runs = [(c, m) for c in CONDITIONS for m in MODES] + [("gate_llm_judge", "enforce")]
        for condition, mode in runs:
            for user_task_id in user_ids:
                jobs.append((condition, mode, suite_name, user_task_id, None))
                jobs += [(condition, mode, suite_name, user_task_id, i) for i in injection_ids]
    return jobs


def _taskspec_prompts(suites) -> dict:
    from agentgate.llm.taskspec_extractor import ToolInfo, render_extraction_prompt
    from agentgate.scenarios.suites import describe_tools, load_suite
    out = {}
    for suite_name in suites:
        suite = load_suite(suite_name)
        tools = [ToolInfo(**t) for t in describe_tools(suite)]
        for user_task_id, task in suite.user_tasks.items():
            out[f"{suite_name}|{user_task_id}"] = _sha(render_extraction_prompt(task.PROMPT, tools))
    return out


def _init_worker(cache_dir: str):
    _install_stubs(cache_dir)


def _diff(before: dict, after: dict, path: str = "", out: list | None = None, limit: int = 30) -> list:
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                out.append(f"{path}/{key}: {'missing before' if key not in before else 'missing after'}")
            else:
                _diff(before[key], after[key], f"{path}/{key}", out, limit)
    elif before != after:
        out.append(f"{path}: {json.dumps(before, default=str)[:200]} != {json.dumps(after, default=str)[:200]}")
    return out


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":   # k-gram hashes use hash(): fix the seed for identical runs
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, *sys.argv])
    from agentgate import SUITES
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--compare", default=None, help="snapshot to diff against (exit 1 on any difference)")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    args = parser.parse_args()
    jobs = _jobs(args.suites)
    with tempfile.TemporaryDirectory() as cache_dir:
        # the judge disk cache is shared by the workers, so whether a judge record was cached depends on scheduling:
        # the cached flag and the usage numbers that follow from it are dropped below (the answer does not change)
        with mp.get_context("spawn").Pool(args.workers, initializer=_init_worker, initargs=(cache_dir,)) as pool:
            runs = dict(pool.imap(_run_job, jobs, chunksize=8))
    for run in runs.values():
        for event in run.get("trace", []):
            if isinstance(event.get("judge"), dict):
                event["judge"].pop("cached", None)
                if event["judge"].get("judge") == "llm":
                    for key in ("latency_ms", "input_tokens", "output_tokens", "cache_read_input_tokens",
                                "cache_creation_input_tokens"):
                        event["judge"].pop(key, None)
    snapshot = {"runs": runs, "taskspec_prompts": _taskspec_prompts(args.suites)}
    Path(args.out).write_text(json.dumps(snapshot, sort_keys=True, indent=0, ensure_ascii=False))
    print(f"{args.out}: {len(runs)} runs, sha256 {_sha(json.dumps(snapshot, sort_keys=True))[:16]}")
    if args.compare:
        before = json.loads(Path(args.compare).read_text())
        differences = _diff(before, snapshot)
        if differences:
            print(f"DIFFERENT ({len(differences)}+ differences):")
            print("\n".join(differences))
            sys.exit(1)
        print("IDENTICAL")


if __name__ == "__main__":
    main()
