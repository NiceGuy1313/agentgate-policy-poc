"""판정 LLM v3의 조항 선택(트리거 인덱스) 범위 점검. LLM 호출 없음(--count-tokens만 토큰 수 API 사용).

effect 툴 분류마다, 정책 엔진이 아무 정책도 걸지 않았을 때(ALLOW)와 ToolSpec 신호가 하나씩 있을 때
user 프롬프트에 들어가는 judge 조항(J*)을 센다. ESCALATE만 판정할 때는 policy:<P-id> 트리거가 항상 하나 이상 있지만,
모든 effect 호출을 판정하면 엔진이 ALLOW한 호출은 tool_class·signal 트리거만으로 조항을 고른다.

    python scripts/v3/rule_coverage.py [--gate v31] [--count-tokens]
"""

from __future__ import annotations

import argparse
import json

from agentgate.config import load_config_yaml
from agentgate.v3 import load_rules

SIGNALS = ["instructed", "delegated", "from_named_source", "unseen_value"]


def selected(rules: list[dict], triggers: set[str]) -> list[str]:
    return [r["id"] for r in rules if r["layer"] == "judge" and set(r["triggers"]) & triggers]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", default="v31")
    parser.add_argument("--count-tokens", action="store_true", help="count rule-document tokens with the API")
    args = parser.parse_args()
    rules = load_rules(args.gate)
    effect = load_config_yaml("agentdojo/tool_roles.yaml")["effect"]
    judge_rules = [r["id"] for r in rules if r["layer"] == "judge"]
    rows = []
    for tool_class in effect:
        base = {"always", f"tool_class:{tool_class}"}
        no_signal = selected(rules, base)
        rows.append({"tool_class": tool_class, "engine_allow_no_signal": no_signal,
                     **{f"+{s}": selected(rules, base | {f"signal:{s}"}) for s in SIGNALS}})
    print(f"judge rules: {judge_rules}")
    print(f"{'tool_class':<14} {'no signal':<14} " + " ".join(f"{'+' + s:<20}" for s in SIGNALS))
    for row in rows:
        print(f"{row['tool_class']:<14} {','.join(row['engine_allow_no_signal']):<14} "
              + " ".join(f"{','.join(row['+' + s]):<20}" for s in SIGNALS))
    only_default = [r["tool_class"] for r in rows if r["engine_allow_no_signal"] == ["J7"]]
    print(f"classes that get only J7 when the engine allows and ToolSpec gives no signal: {only_default}")
    text = "\n".join(f"{r['id']} [{r['layer']}] {r['title']}: {' '.join(r['text'].split())}" for r in rules)
    print(f"full rule document: {len(rules)} rules, {len(text)} chars")
    if args.count_tokens:
        import anthropic

        from agentgate.config import load_dotenv
        load_dotenv()
        client = anthropic.Anthropic()
        for model in ("claude-sonnet-5-5", "claude-haiku-5-5"):
            count = client.messages.count_tokens(model=model, messages=[{"role": "user", "content": text}])
            print(f"full rule document tokens ({model}): {count.input_tokens}")
    print(json.dumps({"gate": args.gate, "rows": rows, "only_default": only_default, "chars": len(text)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
