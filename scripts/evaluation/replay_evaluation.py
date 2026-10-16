"""재생 평가(RQ1): AgentDojo 정답(ground truth) 궤적을 게이트로 재생한다. LLM 호출 없음.

사용법: python scripts/evaluation/replay_evaluation.py --spec oracle --mode audit enforce --out results/replay/v1/oracle

출력: <out>/benign_<mode>.jsonl, <out>/attack_<mode>.jsonl, <out>/meta.json
`--spec llm:<model>`은 data/specs/llm/<model>/에 저장된 LLM TaskSpec 추출 결과를 읽는다(LLM을 호출하지 않는다).

v3.1: --gate v31 --escalate judge_v3 | judge_v3_norules(규칙 문서 없이 같은 사실만, ablation)
v3: --gate v3 --escalate block|judge_v3 (ToolSpec은 claude-haiku-5-5, 판정 LLM v3는 AGENTGATE_JUDGE_MODEL)
v2(명세서.md 3절): --gate v2 [--detector piguard|protectai-v2] [--detector-mode full|chunks] --escalate block|judge_v2
`--escalate judge_v2`는 enforce 모드에서만 Trusted LLM v2를 부른다(audit은 게이트 판정만 기록).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import subprocess
import time
from pathlib import Path

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.config import (PROJECT_ROOT, load_policy_config, load_policy_config_v2, load_policy_config_v3,
                              load_policy_config_v31)
from agentgate.evaluation.replay import replay_attack_pair, replay_benign_task
from agentgate.suites import load_suite
from agentgate.task_spec import load_task_spec


_WORKER: dict = {}


def _v3_runtime_kw(v2: dict, mode: str, prompt: str, suite_name: str) -> tuple[dict, dict]:
    """v3: P0+P1–P9+P11+P12, ToolSpec labels on every response, ESCALATE -> Trusted LLM v3 (enforce only)."""
    from agentgate.llm.toolspec_extractor import ToolSpecExtractor
    from agentgate.suites import describe_tools
    if "config" not in _WORKER:
        v31 = v2["gate"] == "v31"
        _WORKER["config"] = load_policy_config_v31() if v31 else load_policy_config_v3()
        _WORKER["toolspec"] = {}
        if v2["escalate"].startswith("judge_v3"):
            from agentgate.llm.trusted_judge_v3 import TrustedJudgeV3
            _WORKER["judge"] = TrustedJudgeV3(
                rules_path=PROJECT_ROOT / "config" / ("policy_rules_v31.yaml" if v31 else "policy_rules_v3.yaml"),
                use_rules=v2["escalate"] != "judge_v3_norules")
    if suite_name not in _WORKER["toolspec"]:
        _WORKER["toolspec"][suite_name] = ToolSpecExtractor(describe_tools(load_suite(suite_name)))
    extractor = _WORKER["toolspec"][suite_name]
    runtime_kw = {"toolspec": lambda tool, args, text: extractor.extract(tool, args, text).spec}
    if v2["escalate"].startswith("judge_v3") and mode == "enforce":
        from agentgate.llm.trusted_judge_v3 import as_runtime_judge
        runtime_kw.update(escalate_action="judge", judge=as_runtime_judge(_WORKER["judge"], prompt))
    else:
        runtime_kw["escalate_action"] = "block" if v2["escalate"].startswith("judge_v3") else v2["escalate"]
    return runtime_kw, _WORKER["config"]


def _v2_runtime_kw(v2: dict, mode: str, prompt: str) -> tuple[dict, dict]:
    """Per-process detector / judge (loaded once per worker) and the v2 engine config."""
    if "config" not in _WORKER:
        _WORKER["config"] = load_policy_config_v2()
        if v2["detector"]:
            import torch

            from agentgate.detector import make_detector
            torch.set_num_threads(1)
            _WORKER["detector"] = make_detector(v2["detector"], v2["detector_mode"])
        if v2["escalate"] == "judge_v2":
            from agentgate.llm.trusted_judge import TrustedJudgeV2
            _WORKER["judge"] = TrustedJudgeV2()
    runtime_kw = {"detector": _WORKER.get("detector")}
    if v2["escalate"] == "judge_v2" and mode == "enforce":
        from agentgate.llm.trusted_judge import as_runtime_judge
        runtime_kw.update(escalate_action="judge", judge=as_runtime_judge(_WORKER["judge"], prompt))
    else:
        runtime_kw["escalate_action"] = v2["escalate"]
    return runtime_kw, _WORKER["config"]


def _run_job(job):
    kind, suite, user_task_id, injection_task_id, spec_kind, mode, disabled, attack, v2 = job
    spec = load_task_spec(spec_kind, suite, user_task_id)
    started = time.perf_counter()
    extra = {}
    if v2:
        prompt = load_suite(suite).get_user_task_by_id(user_task_id).PROMPT
        if v2["gate"] in ("v3", "v31"):
            runtime_kw, engine_config = _v3_runtime_kw(v2, mode, prompt, suite)
        else:
            runtime_kw, engine_config = _v2_runtime_kw(v2, mode, prompt)
        extra = {"runtime_kw": runtime_kw, "engine_config": engine_config}
    if kind == "benign":
        run = replay_benign_task(suite, user_task_id, spec, mode, set(disabled), **extra)
    else:
        run = replay_attack_pair(suite, user_task_id, injection_task_id, spec, mode, set(disabled), attack, **extra)
    run["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    run["spec"] = spec_kind
    for event in run["trace"]:
        event["output_excerpt"] = event["output_excerpt"][:160]
    return run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", default="oracle")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--mode", nargs="*", default=["audit", "enforce"], choices=["audit", "enforce"])
    parser.add_argument("--disable", default="", help="comma separated policy ids, e.g. P1,P2")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    parser.add_argument("--limit", type=int, default=0, help="debug: max user tasks per suite")
    parser.add_argument("--gate", default="v1", choices=["v1", "v2", "v3", "v31"])
    parser.add_argument("--detector", default=None, choices=["piguard", "protectai-v2"])
    parser.add_argument("--detector-mode", default="chunks", choices=["full", "chunks"])
    parser.add_argument("--escalate", default="block", choices=["block", "allow", "judge_v2", "judge_v3",
                                                                         "judge_v3_norules"])
    parser.add_argument("--pairs", default=None, help="file of 'suite user_task injection_task' lines (subset)")
    args = parser.parse_args()
    if args.gate == "v1" and (args.detector or args.escalate != "block"):
        parser.error("--detector / --escalate need --gate v2")
    v2 = ({"gate": args.gate, "detector": args.detector, "detector_mode": args.detector_mode,
           "escalate": args.escalate} if args.gate in ("v2", "v3", "v31") else None)
    pair_filter = None
    if args.pairs:
        pair_filter = {tuple(line.split()[:3]) for line in Path(args.pairs).read_text().splitlines()
                       if line.strip() and not line.startswith("#")}
    disabled = tuple(p for p in args.disable.split(",") if p)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs = {mode: {"benign": [], "attack": []} for mode in args.mode}
    for suite_name in args.suites:
        suite = load_suite(suite_name)
        user_task_ids = sorted(suite.user_tasks, key=lambda x: int(x.split("_")[-1]))
        injection_task_ids = sorted(suite.injection_tasks, key=lambda x: int(x.split("_")[-1]))
        if args.limit:
            user_task_ids = user_task_ids[: args.limit]
        for mode in args.mode:
            for user_task_id in user_task_ids:
                if pair_filter is None or any(p[:2] == (suite_name, user_task_id) for p in pair_filter):
                    jobs[mode]["benign"].append(
                        ("benign", suite_name, user_task_id, None, args.spec, mode, disabled, args.attack, v2))
                for injection_task_id in injection_task_ids:
                    if pair_filter is not None and (suite_name, user_task_id, injection_task_id) not in pair_filter:
                        continue
                    jobs[mode]["attack"].append(
                        ("attack", suite_name, user_task_id, injection_task_id, args.spec, mode, disabled,
                         args.attack, v2))

    started = time.time()
    with mp.Pool(args.workers) as pool:
        for mode in args.mode:
            for kind in ("benign", "attack"):
                path = output_dir / f"{kind}_{mode}.jsonl"
                with path.open("w") as f:
                    for run in pool.imap(_run_job, jobs[mode][kind], chunksize=4):
                        f.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")
                print(f"{path}: {len(jobs[mode][kind])} runs ({time.time() - started:.0f}s)")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except Exception:
        commit = None
    meta = {"benchmark_version": BENCHMARK_VERSION, "spec": args.spec, "suites": args.suites, "modes": args.mode,
            "disabled": list(disabled), "attack": args.attack,
            "policy_config_version": ({"v2": load_policy_config_v2, "v3": load_policy_config_v3,
                                       "v31": load_policy_config_v31}.get(
                args.gate, load_policy_config)())["version"],
            "gate": args.gate, "v2": v2, "pairs": args.pairs,
            "git_commit": commit, "python": platform.python_version(), "elapsed_s": round(time.time() - started, 1)}
    (output_dir / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
