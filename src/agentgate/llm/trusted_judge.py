"""Trusted LLM v2(명세서.md 3.4): ESCALATE를 세 가지 입력으로 ALLOW / BLOCK / HUMAN으로 해소한다.

입력 1 허용 범위: 사용자 프롬프트, TaskSpec 필드, 사용자 고정 설정(config/user_policy.yaml)
입력 2 대기 중 호출: 툴, 되돌림 가능 여부, ESCALATE를 낸 정책과 이유, 인자 전부(400자 절단, <data>)와 인자별 출처
입력 3 판별 결과: 인자 출처 응답의 점수, 값이 속한 조각 점수, 의심 조각 발췌(최대 3개, 240자, <data>)
툴 응답 전문은 넣지 않는다. 해석 실패는 HUMAN이다. C4 재현을 위해 EscalationJudge(v1)는 그대로 둔다.

프롬프트 배치(layout "cached"): 고정 지시문 전부를 system(prompts/trusted_judge_system.md)에 두고 Anthropic prompt
caching breakpoint를 단다. user 메시지(prompts/trusted_judge.md)에는 호출마다 바뀌는 세 입력만 넣는다. 고정 부분은
약 660토큰으로 claude-sonnet-5-5의 최소 캐시 길이(512)를 넘으므로, 5분 안에 이어지는 호출은 이 부분을 캐시 읽기
가격(기본 입력가의 0.1배)으로 낸다.

답 캐시: data/judge_cache_v2/<model>/<system+user 해시>.json (같은 입력의 재실행은 API를 부르지 않음)
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from agentgate.config import PROJECT_ROOT, load_dotenv, write_text_atomic
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL, LLMClient
from agentgate.normalization import iterate_leaf_values
from agentgate.policy.base import PolicyContext, ToolCall, Verdict
from agentgate.policy.engine import EngineResult
from agentgate.provenance import Origin

PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "prompts" / "trusted_judge.md"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "trusted_judge_system.md"
CACHE_DIR = PROJECT_ROOT / "data" / "judge_cache_v2"
PROMPT_LAYOUT = "cached"   # static instructions in a cached system prompt; c5_judge_piguard used "inline"
VALUE_CHARS, EXCERPT_CHARS, MAX_EXCERPTS, MAX_VALUES_PER_ARG = 400, 240, 3, 5
LONG_VALUE_CHARS = 200
ANSWERS = ("ALLOW", "BLOCK", "HUMAN")


def _data(text: str, limit: int) -> str:
    text = str(text)
    clipped = text[:limit] + (" …[truncated]" if len(text) > limit else "")
    return f"<data>{clipped}</data>"


def _origin_label(provenance) -> str:
    if provenance.step is not None and provenance.step >= 0:
        return f"step{provenance.step}:{provenance.tool} ({provenance.origin.value})"
    return provenance.origin.value


def _argument_origins(value, context: PolicyContext) -> list[tuple[str, object]]:
    """(value, ProvenanceResult) for a leaf value; long texts are traced through the typed values inside."""
    from agentgate.policy.rules import _detector_candidates

    index = context.value_index
    text = str(value)
    if len(text) <= LONG_VALUE_CHARS:
        return [(text, index.origin_of(value))]
    return [(c, index.origin_of(c)) for c in _detector_candidates(text, LONG_VALUE_CHARS)]


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
            for candidate, provenance in _argument_origins(value, context):
                entry = {"value": candidate[:80], "origin": _origin_label(provenance)}
                if provenance.origin in (Origin.NAMED_SOURCE, Origin.OTHER_TOOL) and provenance.source_id:
                    label = index.value_detection(candidate, provenance.source_id)
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
                          "excerpts": index.suspicious_excerpts(source_id, MAX_EXCERPTS, EXCERPT_CHARS)})
    user_policy = context.config.get("user_policy") or {}
    return {"prompt": prompt, "tool": call.tool, "reversibility": reversibility, "escalations": escalations,
            "scope": {k: getattr(spec, k) for k in ("requested_ops", "named_sources", "allowed_dest", "amount_cap",
                                                    "egress_requested")},
            "user_policy": {k: user_policy.get(k, []) for k in ("always_block", "always_human")},
            "arguments": arguments, "detection": detection, "detector_warning": bool(detector_warning)}


def render_trusted_prompt(facts: dict) -> str:
    scope = "\n".join(f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in facts["scope"].items())
    user_policy = "\n".join(f"- {k}: {json.dumps(v)}" for k, v in facts["user_policy"].items())
    escalations = "\n".join(f"  - {e['policy_id']} {e['verdict']}: {e['reason']}" for e in facts["escalations"]) \
        or "  - (none)"
    argument_lines = []
    for argument in facts["arguments"]:
        argument_lines.append(f"  - `{argument['name']}` = {_data(argument['value'], VALUE_CHARS)}")
        for origin in argument["origins"]:
            score = f", value chunk score {origin['value_chunk_score']}" if "value_chunk_score" in origin else ""
            argument_lines.append(f"    - {_data(origin['value'], 80)} from {origin['origin']}{score}")
    detection_lines = []
    for d in facts["detection"]:
        if d.get("detector") == "not run":
            detection_lines.append(f"- {d['source_id']}: detector not run")
            continue
        detection_lines.append(f"- {d['source_id']} ({d['tool']}): response score {d['response_score']}, "
                               f"flagged={d['flagged']}, suspicious chunks={d['suspicious_spans']}")
        detection_lines += [f"  excerpt: {_data(x, EXCERPT_CHARS)}" for x in d["excerpts"]]
    return PROMPT_TEMPLATE_PATH.read_text().format(
        prompt=facts["prompt"], scope=scope, user_policy=user_policy, tool=facts["tool"],
        reversibility=facts["reversibility"], escalations=escalations,
        arguments="\n".join(argument_lines) or "  - (no arguments)",
        detection="\n".join(detection_lines) or "(no argument comes from a tool response)")


def parse_trusted_answer(text: str) -> str:
    match = re.match(r"\s*[*#`_ ]*(ALLOW|BLOCK|HUMAN)\b", text.upper())
    return match.group(1) if match else "HUMAN"


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
            completion = self.client.complete(system_prompt, user_prompt, max_tokens=4000, cache_system=True)
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
