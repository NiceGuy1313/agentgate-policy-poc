"""Trusted LLM v2: ESCALATE를 ALLOW / BLOCK / HUMAN으로 해소한다. HUMAN은 BLOCK으로 집행한다.

입력 1 허용 범위: TaskSpec(사용자 요청에서 추출)과 사용자 고정 설정(config/v2/user_policy.yaml의 always_block,
       always_human). 사용자 프롬프트 원문과 ESCALATE 이유는 넣지 않는다.
입력 2 대기 중 호출: 툴, 되돌림 가능 여부, 인자(400자 절단, <data>)와 인자별 출처
입력 3 판별 결과: 인자 출처 응답의 점수, 값이 속한 창 점수, 의심 구간 발췌(최대 3개, 240자, <data>)
툴 응답 전문은 넣지 않는다. 해석 실패는 HUMAN이다.

고정 지시문은 system(prompts/v2/trusted_judge_system.md)에 두고 Anthropic prompt caching breakpoint를 단다.
답 캐시: data/v2/cache/judge/<model>/<system+user 해시>.json (같은 입력의 재실행은 API를 부르지 않음)
"""

from __future__ import annotations

import hashlib
import json
import os

from agentgate.config import PROJECT_ROOT, load_dotenv, write_text_atomic
from agentgate.gate.base import PolicyContext, ToolCall, Verdict
from agentgate.gate.engine import EngineResult
from agentgate.gate.normalization import iterate_leaf_values
from agentgate.gate.provenance import Origin
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL, LLMClient
from agentgate.llm.judge_io import argument_origins, data_block, origin_label, parse_trusted_answer
from agentgate.v2.labels import suspicious_excerpts, value_detection

PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "prompts" / "v2" / "trusted_judge.md"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "v2" / "trusted_judge_system.md"
CACHE_DIR = PROJECT_ROOT / "data" / "v2" / "cache" / "judge"
PROMPT_LAYOUT = "cached"   # static instructions in a cached system prompt; c5_judge_piguard used "inline"
VALUE_CHARS, EXCERPT_CHARS, MAX_EXCERPTS, MAX_VALUES_PER_ARG = 400, 240, 3, 5


def build_trusted_facts(call: ToolCall, result: EngineResult, context: PolicyContext, prompt: str) -> dict:
    index, spec = context.value_index, context.spec
    reversibility = ("irreversible" if call.tool in context.config.get("reversibility", {}).get("irreversible", [])
                     else "reversible")
    escalations = [{"policy_id": d.policy_id, "verdict": d.verdict.value, "reason": d.reason}
                   for d in result.decisions if d.verdict != Verdict.ALLOW]
    arguments, sources, detector_warning = [], {}, False
    for name, raw in call.args.items():
        origins = []
        for value in list(iterate_leaf_values(raw))[:MAX_VALUES_PER_ARG]:
            for candidate, provenance in argument_origins(value, context):
                entry = {"value": candidate[:80], "origin": origin_label(provenance)}
                if provenance.origin in (Origin.NAMED_SOURCE, Origin.OTHER_TOOL) and provenance.source_id:
                    label = value_detection(index, candidate, provenance.source_id)
                    if label is not None:
                        entry["value_chunk_score"] = label["value_score"]
                        detector_warning |= label["flagged"] or label["in_suspicious_span"]
                        sources.setdefault(provenance.source_id, label)
                    elif provenance.source_id not in sources:
                        sources[provenance.source_id] = None
                origins.append(entry)
        arguments.append({"name": name, "value": json.dumps(raw, ensure_ascii=False, default=str)
                          if not isinstance(raw, str) else raw, "origins": origins})
    detection = []
    for source_id, label in sources.items():
        if label is None:
            detection.append({"source_id": source_id, "detector": "not run"})
            continue
        detection.append({**{k: label[k] for k in ("source_id", "tool", "response_score", "flagged",
                                                     "suspicious_spans")},
                          "excerpts": suspicious_excerpts(index, source_id, MAX_EXCERPTS, EXCERPT_CHARS)})
    user_policy = context.config.get("user_policy") or {}
    return {"prompt": prompt, "tool": call.tool, "reversibility": reversibility, "escalations": escalations,
            "scope": {k: getattr(spec, k) for k in ("requested_ops", "named_sources", "allowed_dest", "amount_cap",
                                                    "egress_requested")},
            "user_policy": {k: user_policy.get(k, []) for k in ("always_block", "always_human")},
            "arguments": arguments, "detection": detection, "detector_warning": bool(detector_warning)}


def render_trusted_prompt(facts: dict) -> str:
    scope = "\n".join(f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in facts["scope"].items())
    user_policy = "\n".join(f"- {k}: {json.dumps(v)}" for k, v in facts["user_policy"].items())
    argument_lines = []
    for argument in facts["arguments"]:
        argument_lines.append(f"  - `{argument['name']}` = {data_block(argument['value'], VALUE_CHARS)}")
        for origin in argument["origins"]:
            score = f", value chunk score {origin['value_chunk_score']}" if "value_chunk_score" in origin else ""
            argument_lines.append(f"    - {data_block(origin['value'], 80)} from {origin['origin']}{score}")
    detection_lines = []
    for d in facts["detection"]:
        if d.get("detector") == "not run":
            detection_lines.append(f"- {d['source_id']}: detector not run")
            continue
        detection_lines.append(f"- {d['source_id']} ({d['tool']}): response score {d['response_score']}, "
                               f"flagged={d['flagged']}, suspicious chunks={d['suspicious_spans']}")
        detection_lines += [f"  excerpt: {data_block(x, EXCERPT_CHARS)}" for x in d["excerpts"]]
    return PROMPT_TEMPLATE_PATH.read_text().format(
        scope=scope, user_policy=user_policy, tool=facts["tool"], reversibility=facts["reversibility"],
        arguments="\n".join(argument_lines) or "  - (no arguments)",
        detection="\n".join(detection_lines) or "(no argument comes from a tool response)")


class TrustedJudgeV2:
    """Model: AGENTGATE_JUDGE_MODEL (default claude-sonnet-5-5). The API client is created on a cache miss only."""

    def __init__(self, client: LLMClient | None = None, model: str | None = None):
        load_dotenv()
        self._client = client
        self.model = client.model if client else (model or os.environ.get("AGENTGATE_JUDGE_MODEL")
                                                  or DEFAULT_ANTHROPIC_MODEL)
        self.provider = os.environ.get("AGENTGATE_JUDGE_PROVIDER") or "anthropic"

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient(provider=self.provider, model=self.model)
        return self._client

    def __call__(self, call: ToolCall, result: EngineResult, context: PolicyContext, prompt: str) -> dict:
        facts = build_trusted_facts(call, result, context, prompt)
        base = {"judge": "v2", "model": self.model, "layout": PROMPT_LAYOUT, "detector_warning": facts["detector_warning"],
                "reversibility": facts["reversibility"]}
        if any(d.evidence.get("force_human") for d in result.decisions if d.verdict == Verdict.ESCALATE):
            return {**base, "answer": None, "final": "HUMAN", "verdict": "BLOCK", "human": True,
                    "by": "P0 always_human", "latency_ms": 0.0, "cached": True}
        system_prompt = SYSTEM_PROMPT_PATH.read_text()
        user_prompt = render_trusted_prompt(facts)
        key = hashlib.sha256((self.model + system_prompt + user_prompt).encode()).hexdigest()[:24]
        path = CACHE_DIR / self.model.replace("/", "_").replace(":", "_") / f"{key}.json"
        if path.exists():
            record = {**json.loads(path.read_text()), "cached": True}
        else:
            # Input 1 (TaskSpec + fixed settings) repeats for every escalation of a task: cache it after the system
            prefix = user_prompt.split("## Input 2", 1)[0] if "## Input 2" in user_prompt else None
            completion = self.client.complete(system_prompt, user_prompt, max_tokens=4000, cache_system=True,
                                              cache_user_prefix=prefix)
            record = {"answer": parse_trusted_answer(completion.text), "text": completion.text.strip()[:400],
                      "latency_ms": completion.latency_ms, "input_tokens": completion.input_tokens,
                      "output_tokens": completion.output_tokens, "served_model": completion.served_model,
                      "cache_read_input_tokens": completion.cache_read_input_tokens,
                      "cache_creation_input_tokens": completion.cache_creation_input_tokens,
                      "layout": PROMPT_LAYOUT, "cached": False}
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(path, json.dumps({k: v for k, v in record.items() if k != "cached"}, indent=1))
        final = record["answer"]
        return {**base, "answer": record["answer"], "final": final, "verdict": "ALLOW" if final == "ALLOW" else "BLOCK",
                "human": final == "HUMAN", "text": record["text"][:200], "cached": record["cached"],
                "latency_ms": 0.0 if record["cached"] else record["latency_ms"],
                "input_tokens": 0 if record["cached"] else record["input_tokens"],
                "output_tokens": 0 if record["cached"] else record["output_tokens"],
                "cache_read_input_tokens": 0 if record["cached"] else record.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": 0 if record["cached"] else record.get("cache_creation_input_tokens", 0)}


def as_runtime_judge(judge: TrustedJudgeV2, prompt: str):
    return lambda call, result, context: judge(call, result, context, prompt)
