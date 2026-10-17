"""v3 결과를 results/v3/RESULTS.md로 만든다. 수치는 모두 실행 기록에서 계산한다(손으로 고치지 않는다).

세 조건: Gate 없음(C0) / Gate + 판정 LLM(C9n) / Gate + 판정 LLM + 정책 문서(C9). 정책 엔진은 없다.
선행: check_enforcement.py, summarize_live.py, live_significance.py를 같은 --run에 실행
사용법: python scripts/v3/summarize_v3.py --run results/v3/live/<run>
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from agentgate.agentdojo.live_run_analysis import live_run_files
from agentgate.agentdojo.metrics import mcnemar_exact
from agentgate.config import PROJECT_ROOT, load_dotenv
from agentgate.v3.toolspec import DEFAULT_MODEL as TOOLSPEC_DEFAULT_MODEL

V3_DIR = PROJECT_ROOT / "results" / "v3"
NAMES = {"C0": "Gate 없음", "C9n": "Gate + 판정 LLM", "C9": "Gate + 판정 LLM + 정책 문서"}
METRICS = {"ASR": ("attack", "security"), "Utility": ("benign", "utility"), "공격 중 Utility": ("attack", "utility")}
SIG_KEYS = {"ASR": "asr", "Utility": "utility", "공격 중 Utility": "utility_under_attack"}

FLOW = """```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant TS as TaskSpec 추출기<br/>({spec_model})
    participant AG as 에이전트<br/>({agent})
    participant GT as Gate
    participant JD as 판정 LLM<br/>({judge_model})
    participant TL as 툴<br/>(AgentDojo 환경)
    participant TO as ToolSpec 추출기<br/>({toolspec_model})

    U->>TS: 프롬프트 + 툴 목록
    TS-->>GT: TaskSpec (사용자가 허락한 범위)
    U->>AG: 프롬프트
    loop 에이전트가 끝낼 때까지
        AG->>GT: 툴 호출 (툴, 인자)
        alt C0 또는 읽기 호출
            GT->>TL: 그대로 실행
        else C9n · C9의 effect 호출 (상태 변경 · 외부 전송 · URL 방문)
            GT->>JD: TaskSpec + 지금까지의 ToolSpec + 대기 중 호출<br/>(C9는 정책 문서도)
            JD-->>GT: ALLOW 또는 BLOCK
            alt ALLOW
                GT->>TL: 실행
            else BLOCK
                GT-->>AG: 차단 메시지
            end
        end
        TL-->>AG: 툴 응답 (공격 실행이면 주입 문구 포함)
        TL->>TO: 툴 응답 원문
        TO-->>GT: ToolSpec (레코드 · 응답 속 요청)
    end
    AG-->>U: 최종 답변
```"""

COMPONENTS = """**TaskSpec 추출기** (`{spec_model}`): 사용자가 허락한 범위를 구조화한다.
- 입력: 사용자 프롬프트, 툴 목록(이름·설명). 툴 응답은 보지 않는다.
- 출력: TaskSpec. 요청한 행동(`requested_ops`), 사용자가 지목한 자료(`named_sources`), 허용 목적지(`allowed_dest`), 금액 상한(`amount_cap`), 전송 요청 여부(`egress_requested`), 지시 수행을 위임한 자료(`delegated_sources`).

**에이전트** (`{agent}`): 평가 대상이다.
- 입력: 사용자 프롬프트, 툴 응답 또는 차단 메시지.
- 출력: 툴 호출(툴 이름, 인자).

**Gate**: 툴 호출이 실행되기 직전의 관문이다. 규칙으로 판정하지 않는다.
- 입력: 툴 호출.
- 출력: 읽기 호출은 그대로 실행한다. effect 호출(상태 변경·외부 전송·URL 방문)은 판정 LLM의 답대로 실행하거나 차단한다.

**판정 LLM** (`{judge_model}`, C9n·C9): effect 호출을 실행할지 정한다.
- 입력: 사용자 프롬프트와 TaskSpec, 지금까지 받은 툴 응답의 ToolSpec, 대기 중 호출(툴, 인자). C9는 정책 문서(`config/v3/policy.yaml`, R1–R10)를 함께 받는다. 툴 응답 원문은 받지 않는다.
- 출력: ALLOW 또는 BLOCK과 이유 한 문장.

**툴** (AgentDojo v1.2.2 환경):
- 입력: 실행이 허용된 툴 호출.
- 출력: 툴 응답. 공격 실행에서는 응답 안에 주입 문구가 들어 있다.

**ToolSpec 추출기** (`{toolspec_model}`): 툴 응답을 판정 LLM이 읽을 JSON으로 바꾼다.
- 입력: 툴 응답 원문, 그 응답을 만든 호출, 툴 목록.
- 출력: ToolSpec. 레코드(종류·작성자)와 응답 속 요청(누구에게 하는 요청인지, 어떤 툴로 수행되는지, 어떤 값을 지목하는지). 원문에 없는 값은 코드로 걸러낸다."""


def paired(runs: list[dict], a: str, b: str, kind: str, field: str) -> dict:
    """Same (suite, user task, injection task) in conditions a and b: rate difference a−b and exact McNemar p."""
    def key(r):
        return r["suite"], r["user_task"], r["injection_task"]

    def keep(r):
        return not r.get("error") and (r["injection_task"] is None) == (kind == "benign")
    by_a = {key(r): bool(r[field]) for r in runs if r["condition"] == a and keep(r)}
    by_b = {key(r): bool(r[field]) for r in runs if r["condition"] == b and keep(r)}
    keys = by_a.keys() & by_b.keys()
    a_only = sum(by_a[k] and not by_b[k] for k in keys)
    b_only = sum(by_b[k] and not by_a[k] for k in keys)
    return {"diff": (a_only - b_only) / len(keys) if keys else None, "p": mcnemar_exact(b_only, a_only)}


def diff_text(d: dict) -> str:
    if d["diff"] is None:
        return "-"
    return f"{100 * d['diff']:+.1f}%p (p={d['p']:.3g}, {'유의' if d['p'] < 0.05 else '유의하지 않음'})"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", default=str(V3_DIR / "RESULTS.md"))
    parser.add_argument("--toolspec", default=str(V3_DIR / "toolspec" / "summary.json"),
                        help="scripts/v3/toolspec_evaluation.py summary (section skipped if missing)")
    args = parser.parse_args()
    run_dir = Path(args.run).resolve()
    summary = json.loads((run_dir / "summary.json").read_text())
    significance = json.loads((run_dir / "significance.json").read_text())
    enforcement = json.loads((run_dir / "enforcement_check.json").read_text())
    runs = [json.loads(p.read_text()) for p in live_run_files(run_dir)]
    conditions = [c for c in NAMES if c in summary["conditions"]]
    first = summary["conditions"][conditions[0]]
    load_dotenv()
    names = {"agent": summary["agent_model"],
             "spec_model": next((r["spec_meta"]["source"].removeprefix("llm:") for r in runs
                                 if (r.get("spec_meta") or {}).get("source")), "-"),
             "toolspec_model": os.environ.get("AGENTGATE_TOOLSPEC_MODEL") or TOOLSPEC_DEFAULT_MODEL,
             "judge_model": next((j["model"] for r in runs for e in r["trace"] if (j := e.get("judge"))), "-")}

    def sig(c, metric):
        return significance["conditions"][c][SIG_KEYS[metric]]

    def rate(c, metric):
        return f"{100 * sig(c, metric)['rate']:.1f}%"

    def vs_c0(c, metric):
        p = sig(c, metric).get("paired_vs_C0")
        return diff_text({"diff": p["diff"], "p": p["p_mcnemar"]}) if p else "-"

    doc_effect = {m: paired(runs, "C9", "C9n", kind, field) for m, (kind, field) in METRICS.items()}

    lines = ["# v3 결과: Gate 없음 / Gate + 판정 LLM / Gate + 판정 LLM + 정책 문서", "",
             "> `scripts/v3/summarize_v3.py`가 live 실행 기록에서 생성한다. 손으로 고치지 않는다. "
             "실행 기록(json)은 요약 후 지웠다.", "",
             "## 한눈에 보기", "",
             f"- 공격 성공률(ASR): Gate 없음 {rate('C0', 'ASR')} → Gate + 판정 LLM {rate('C9n', 'ASR')} → "
             f"Gate + 판정 LLM + 정책 문서 {rate('C9', 'ASR')}.",
             f"- 판정 LLM을 넣으면(C9n − C0): ASR {vs_c0('C9n', 'ASR')}, 정상 Utility {vs_c0('C9n', 'Utility')}, "
             f"공격 중 Utility {vs_c0('C9n', '공격 중 Utility')}.",
             f"- 정책 문서를 읽히면(C9 − C9n): ASR {diff_text(doc_effect['ASR'])}, 정상 Utility "
             f"{diff_text(doc_effect['Utility'])}, 공격 중 Utility {diff_text(doc_effect['공격 중 Utility'])}.", "",
             "## 실험 흐름", "", FLOW.format(**names), "",
             "## 구성 요소", "", COMPONENTS.format(**names), "",
             "## 비교한 조건", ""]
    lines += [f"- **{c}** {NAMES[c]}" for c in conditions]
    lines += ["", f"에이전트 `{names['agent']}`, 공격 `{summary['attack']}`, 조건마다 공격 없는 실행 "
                  f"{first['benign_runs']}건과 공격 실행 {first['attack_runs']}건이다.", "",
              "## 결과", "",
              "- **ASR**: 공격 실행 중 공격자 목표가 달성된 비율. 낮을수록 좋다.",
              "- **Utility**: 공격 없는 실행에서 사용자 작업을 끝낸 비율.",
              "- **공격 중 Utility**: 공격 실행에서도 사용자 작업을 끝낸 비율.", "",
              "| 조건 | ASR | Utility | 공격 중 Utility |", "|---|---|---|---|"]
    for c in conditions:
        cells = [f"{100 * sig(c, m)['rate']:.1f}% ({sig(c, m)['k']}/{sig(c, m)['n']})" for m in METRICS]
        lines.append(f"| {c} {NAMES[c]} | " + " | ".join(cells) + " |")
    lines += ["", "같은 (사용자 작업, 공격) 짝끼리 비교했다. 차이는 앞 조건 − 뒤 조건, p는 exact McNemar다.", ""]
    for c in conditions[1:]:
        lines.append(f"**{c} − C0** ({NAMES[c]} 대 Gate 없음)")
        lines += [f"- {m} {vs_c0(c, m)}" for m in METRICS] + [""]
    lines.append("**C9 − C9n** (정책 문서를 읽었을 때 대 안 읽었을 때)")
    lines += [f"- {m} {diff_text(doc_effect[m])}" for m in METRICS] + [""]
    lines.append("95% 신뢰구간(Wilson):")
    for m in METRICS:
        lines.append(f"- {m}: " + ", ".join(
            f"{c} [{100 * sig(c, m)['ci95'][0]:.1f}, {100 * sig(c, m)['ci95'][1]:.1f}]" for c in conditions))
    lines += ["", "## 자세히", "", "### 판정 LLM의 판단", ""]
    for c in conditions[1:]:
        judges = [j for r in runs if r["condition"] == c for e in r["trace"] if (j := e.get("judge"))]
        finals = Counter(j["final"] for j in judges)
        lines.append(f"- **{c}**: effect 호출 {len(judges)}회 판정, ALLOW {finals.get('ALLOW', 0)}, "
                     f"BLOCK {finals.get('BLOCK', 0)}")
    lines += ["", "### 정상 작업이 실패한 이유", "", "공격 없는 실행 중 실패한 것을 나눴다. "
              "\"차단 없이 실패\"는 에이전트가 스스로 작업을 끝내지 못한 경우다.", ""]
    for c in conditions:
        lost = Counter()
        for r in runs:
            if r["condition"] != c or r["injection_task"] is not None or r["utility"] or r.get("error"):
                continue
            lost["판정 LLM 차단" if any(e.get("blocked") for e in r["trace"]) else "차단 없이 실패"] += 1
        lines.append(f"- **{c}** {sum(lost.values())}건: " + ", ".join(f"{k} {v}" for k, v in lost.most_common()))
    lines += ["", "### 공격이 성공한 경로", "",
              "\"툴 호출 없이 성공(유형 H)\"은 에이전트가 답변 텍스트만으로 공격 목표를 달성한 경우로, 툴 호출 Gate로는 막을 수 "
              "없다.", ""]
    for c in conditions[1:]:
        paths = summary["conditions"][c]["successful_attacks_by_class"]
        lines.append(f"- **{c}** {sum(paths.values())}건: " + (", ".join(f"{k} {v}" for k, v in sorted(paths.items()))
                                                             or "-"))
    toolspec_path = Path(args.toolspec)
    if toolspec_path.exists():
        a = json.loads(toolspec_path.read_text())["all"]

        def ratio(m):
            return f"{100 * m['rate']:.1f}% ({m['n']}/{m['of']})"
        lines += ["", "### ToolSpec 추출 품질", "",
                  f"정답 궤적의 툴 응답(`{summary['attack']}`, 중복 제거)을 추출한 결과다(`scripts/v3/toolspec_evaluation.py`).",
                  f"- 주입 응답 {a['injected_responses']}개: AI 대상 요청 추출 {ratio(a['injected_detected_strict'])}, "
                  f"그 요청의 툴이 공격 목표 툴과 일치 {ratio(a['injected_goal_action_match'])}",
                  f"- 정상 응답 {a['benign_responses']}개: AI 대상 요청 오탐 {ratio(a['benign_flagged_strict'])}, "
                  f"요청이 하나라도 있다고 본 비율 {ratio(a['benign_flagged_any'])}"]
    lines += ["", "### 비용과 집행 무결성", ""]
    for c in conditions[1:]:
        judges = [j for r in runs if r["condition"] == c for e in r["trace"] if (j := e.get("judge"))]
        ts = Counter()
        for run in runs:
            if run["condition"] == c:
                ts.update({k: v for k, v in (run.get("toolspec") or {}).items() if isinstance(v, (int, float))})
        lines.append(f"- **{c}** 판정 LLM API {sum(not j['cached'] for j in judges)}회(입력 "
                     f"{sum(j['input_tokens'] for j in judges):,}, 캐시 읽기 "
                     f"{sum(j['cache_read_input_tokens'] for j in judges):,}, 출력 "
                     f"{sum(j['output_tokens'] for j in judges):,} 토큰), ToolSpec API {int(ts['api_calls'])}회(입력 "
                     f"{int(ts['input_tokens']):,}, 캐시 읽기 {int(ts['cache_read_input_tokens']):,}, 출력 "
                     f"{int(ts['output_tokens']):,} 토큰)")
    lines.append(f"- 집행 무결성: 실행 {enforcement.get('runs', 0):,}건, 차단된 호출이 실행된 위반 "
                 f"{enforcement.get('violations', '-')}건, 오류 {summary.get('n_errors', '-')}건")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
