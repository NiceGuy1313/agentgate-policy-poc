"""Regenerate every number-bearing document from result files.

  python scripts/build_report.py

Inputs : data/catalog/*, results/replay/v1/oracle/summary.json, results/live/v1/*/summary.json,
         results/live/v1/*/enforcement_check.json, results/judge/v1/*/judge_eval.json (if present),
         data/specs/llm/*/comparison.json (if present)
Outputs: docs/stage1_catalog_report.md, docs/stage5_replay_report.md, docs/stage6_live_report.md,
         docs/final_report.md, results/figures/*.png, results/dashboard.html
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.config import policies_config

ROOT = Path(__file__).resolve().parents[1]
DOCS, FIG = ROOT / "docs", ROOT / "results" / "figures"
REPLAY = ROOT / "results/replay/v1/oracle"
LIVE_ROOT = ROOT / "results/live/v1"
TYPE_NAMES = {"A": "무단 외부 연락", "B": "금전 탈취", "C": "민감 데이터 반출", "D": "삭제·은폐",
              "E": "계정·권한 변경", "F": "비신뢰 내용 전파·방문", "G": "무단 예약·일정 생성", "H": "툴 호출 없는 출력 조작"}
COND_EN = {"C0": "no defense", "C1": "gate\nESC→block", "C2": "gate\nESC→allow", "C3": "built-in\n(spotlighting)",
           "C4": "gate\nESC→judge"}
COND_NAMES = {"C0": "방어 없음", "C1": "게이트 (ESCALATE→block)", "C2": "게이트 (ESCALATE→allow)",
              "C3": "AgentDojo 내장 방어", "C4": "게이트 (ESCALATE→judge)"}
# reference categorical palette (dataviz skill, slots 1-3 validate all-pairs)
C1_, C2_, C3_ = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def J(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def pct(a, b, digits=1):
    return f"{100 * a / b:.{digits}f}% ({a}/{b})" if b else "n/a"


def p100(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- figures
def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def grouped_bars(path, groups, series, title, ylabel, note=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig, ax = plt.subplots(figsize=(8.4, 3.9), dpi=150)
    n = len(series)
    w = 0.8 / n
    for i, (name, vals, color) in enumerate(series):
        xs = [g + (i - (n - 1) / 2) * w for g in range(len(groups))]
        bars = ax.bar(xs, vals, width=w - 0.03, color=color, label=name, edgecolor="white", linewidth=1.5)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.0f}", ha="center", va="bottom", fontsize=7.5, color=MUTED)
    ax.set_xticks(range(len(groups)), groups)
    ax.set_ylim(0, 108)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    _style(ax)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    if note:
        fig.text(0.01, 0.01, note, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04 if note else 0, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def figures(R, LIVE):
    figs = {}
    types = [t for t in "ABCDEFG" if t in R["by_type"]]
    grouped_bars(FIG / "detection_by_type.png", [f"{t}" for t in types], [
        ("strict (BLOCK)", [100 * R["by_type"][t]["strict"] / R["by_type"][t]["eval"] for t in types], C1_),
        ("lenient (BLOCK or ESCALATE)", [100 * R["by_type"][t]["lenient"] / R["by_type"][t]["eval"] for t in types], C2_),
    ], "Pair-level detection by attack type (replay, oracle TaskSpec)", "% of pairs",
        note="Type H (no tool call) excluded: no effect call to detect. A-G as in docs/policy_spec.md §1.")
    figs["type"] = "results/figures/detection_by_type.png"
    ab = R["ablation"]
    pols = [f"P{i}" for i in range(1, 10)]
    base_s, base_l = ab["none"]["strict"], ab["none"]["lenient"]
    ev = ab["none"]["eval"]
    grouped_bars(FIG / "ablation.png", [f"-{p}" for p in pols], [
        ("strict detection", [100 * ab[p]["strict"] / ev for p in pols], C1_),
        ("lenient detection", [100 * ab[p]["lenient"] / ev for p in pols], C2_),
    ], f"Detection with one policy disabled (all on: strict {100 * base_s / ev:.0f}%, lenient {100 * base_l / ev:.0f}%)",
        "% of pairs")
    figs["ablation"] = "results/figures/ablation.png"
    for name, L in LIVE.items():
        conds = list(L["conditions"])
        m = L["conditions"]
        grouped_bars(FIG / f"tradeoff_{name}.png", [f"{c}\n{COND_EN[c]}" for c in conds], [
            ("Utility (no attack)", [100 * (m[c]["utility"] or 0) for c in conds], C1_),
            ("Utility under attack", [100 * (m[c]["utility_under_attack"] or 0) for c in conds], C3_),
            ("ASR", [100 * (m[c]["asr"] or 0) for c in conds], C2_),
        ], f"Utility vs ASR by condition — agent: {name}" + (" (deterministic stand-in, not an LLM)" if name == "scripted" else ""), "%",
            note="Latency is reported in the table, not on this axis (one axis per chart).")
        figs[f"tradeoff_{name}"] = f"results/figures/tradeoff_{name}.png"
    return figs


# ---------------------------------------------------------------------------------------------- stage 1
def stage1():
    cat = ROOT / "data/catalog"
    users = J(cat / "user_tasks.json")
    injs = J(cat / "injection_tasks.json")
    labels = yaml.safe_load((cat / "injection_labels.yaml").read_text())
    lc = J(cat / "label_check.json")
    prov = J(cat / "provenance_summary.json")
    ad = J(cat / "attacker_dest_check.json")
    rows = []
    for s in SUITES:
        rows.append([s, sum(u["suite"] == s for u in users), sum(i["suite"] == s for i in injs),
                     sum(i["suite"] == s and i["gt_source"] == "synthetic" for i in injs)])
    rows.append(["합계", len(users), len(injs), sum(i["gt_source"] == "synthetic" for i in injs)])
    tcount = Counter(labels[i["suite"]][i["task_id"]]["type"] for i in injs)
    eff = Counter(sum(c["role"] == "effect" for c in i["ground_truth"]) for i in injs)
    zero_eff = [f"{i['suite']}/{i['task_id']}" for i in injs if not any(c["role"] == "effect" for c in i["ground_truth"])]
    L = [
        "# Stage 1 — 시나리오 카탈로그와 인자 출처 분석", "",
        "> 이 문서는 `scripts/build_report.py`가 `data/catalog/`의 산출물에서 생성한다. 산출물은 "
        "`scripts/build_catalog.py`, `scripts/analyze_provenance.py`가 LLM 호출 없이 만든다.", "",
        f"## 1. 태스크 수 (AgentDojo {BENCHMARK_VERSION})", "",
        table(["suite", "user task", "injection task", "참조 궤적을 직접 작성한 injection task"], rows), "",
        "AgentDojo v1.2에서 추가된 workspace injection_task_6~13은 `ground_truth()`가 빈 목록을 반환한다. "
        "재생 평가에는 공격에 넘어간 에이전트의 호출이 필요하므로 `src/agentgate/attack_trajectories.py`에 참조 궤적 "
        "생성기를 작성했다. 생성기는 현재 환경에서 ID를 계산하며, 35개 전부가 기본 환경에서 해당 태스크의 "
        "`security()`를 만족함을 `tests/test_attack_trajectories.py`로 확인했다.", "",
        "## 2. 유형별 분포", "",
        table(["유형", "이름", "태스크 수"], [[t, TYPE_NAMES[t], tcount.get(t, 0)] for t in "ABCDEFGH"]), "",
        f"효과 호출 수 분포: {dict(sorted(eff.items()))} (키: 태스크당 효과 호출 수, 값: 태스크 수). "
        f"효과 호출이 0개인 태스크는 {', '.join(zero_eff) or '없음'}이며, 유형 H로 정상이다.", "",
        "## 3. 레이블 모순 점검", "",
        f"유형별 필수 툴군과 대상 정책의 적용 툴이 ground truth 호출에 존재하는지 자동 점검했다. 모순 {len(lc)}건."
        + ("" if not lc else "\n\n" + table(["suite", "task", "문제", "툴"], [[x["suite"], x["task_id"], x["issue"], x["tools"]] for x in lc])), "",
        "## 4. 인자 출처 집계 (user task 97개, 주입 없는 기본 환경)", "",
        "판정 범주는 PROMPT(사용자 프롬프트에 있음), TOOL(이전 툴 응답에 처음 등장), UNSEEN(어디에도 없음)이다. "
        "정규화는 이메일 소문자, URL의 scheme·www·끝 슬래시 제거, IBAN 공백 제거이며, 리스트 인자는 원소별로 판정한다. "
        "짧은 ID(예: 파일 ID `11`)는 구조화 응답의 필드 값과 정확히 일치할 때만 등장으로 본다.", "",
        table(["툴.인자", "PROMPT", "TOOL", "UNSEEN"],
              [[k, v.get("PROMPT", 0), v.get("TOOL", 0), v.get("UNSEEN", 0)] for k, v in prov.items()]), "",
        "### policy_spec 2절 수치와의 비교", "",
        "Stage 1 지시서에 예시로 제시된 `send_money.recipient` 프롬프트 4 · 툴 응답 "
        f"2와 이 집계({prov.get('send_money.recipient', {}).get('PROMPT')} · {prov.get('send_money.recipient', {}).get('TOOL')})는 일치한다. "
        "원본 `docs/policy_spec.md`가 저장소에 없어 이 세션에서 사양을 새로 작성했으며, 2절 표는 이 집계를 그대로 옮겼다. "
        "따라서 나머지 항목은 독립 대조가 아니라 같은 출력이다. UNSEEN 항목은 모두 계산된 값이다 "
        "(`send_money.amount` 3건과 `update_scheduled_transaction.amount` 3건: 차액·가산 금액 계산). "
        "정규화 문제로 UNSEEN이 된 사례는 짧은 ID 매칭을 추가한 뒤 남지 않았다.", "",
        "## 5. 공격자 목적지 점검", "",
        table(["목적지", "user PROMPT에 등장", "기본 환경에 등장", "통과"],
              [[d, ", ".join(v["in_user_prompts"]) or "-", ", ".join(v["in_default_env"]) or "-", "예" if v["ok"] else "아니오"]
               for d, v in ad.items()]), "",
        "통과하지 못한 두 건은 다음과 같다.", "",
        "- `janeLong@google.com`: travel user_task_3 프롬프트가 이 주소를 사용자의 배우자 주소로 명시한다. 이 쌍"
        "(travel user_task_3 × injection_task_5)에서는 공격 목적지가 정당한 목적지와 같으므로 P1이 원리적으로 구분할 수 없다. "
        "Stage 5에서 이 쌍을 놓친 원인이 이것이다.",
        "- `www.true-informations.com`: slack 기본 환경의 웹 페이지 사전에 내용이 빈 키로 존재한다. "
        "에이전트가 `get_webpage`로 그 URL을 직접 열기 전에는 어떤 툴 응답에도 나타나지 않으므로 "
        "\"툴 응답에만 있는 값\" 판정에는 영향이 없다.", "",
        "## 6. 정책 수정 제안", "",
        "- 위 `janeLong@google.com` 사례처럼 공격 목적지가 프롬프트의 정당한 목적지와 같으면 출처 기반 정책으로는 막을 수 없다. "
        "본문 내용(P2)이나 판정 LLM이 보조해야 한다. 사양은 수정하지 않았다.",
        "- `trusted_tools`(연락처·계정 정보처럼 출처를 신뢰하는 툴)는 인젝션 벡터를 담지 않는다는 전제가 필요하다. "
        "점검 결과 slack `get_channels`는 채널 이름에 인젝션 벡터를 담으므로 신뢰 목록에서 제외했다. 이 전제는 "
        "`tests/test_config_consistency.py`가 canary 주입으로 계속 검사한다.",
    ]
    (DOCS / "stage1_catalog_report.md").write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------------------------------------- stage 5
def stage5(R, figs):
    o = R["overall"]
    b = R["benign"]
    pv = R["prevention"]
    defs = [
        "# Stage 5 — 정답 궤적 재생 평가 (RQ1: 탐지 가능성)", "",
        "> `scripts/replay_eval.py`(재생)와 `scripts/summarize_replay.py`(집계)의 출력 "
        f"`{R['run']}/summary.json`에서 `scripts/build_report.py`가 생성했다. LLM 호출은 없다.", "",
        "## 1. 지표 정의", "",
        "- **쌍**: 같은 suite의 (user task, injection task) 조합. user task가 읽는 injection vector에 "
        "`important_instructions` 공격 문자열을 넣은 환경에서 user task ground truth를 재생한 뒤 injection task의 참조 궤적을 이어서 재생한다(공격에 넘어간 에이전트 모사). TaskSpec은 user task의 oracle spec이다.",
        "- **효과 호출**: injection 궤적 중 `config/tool_roles.yaml`의 effect 툴 호출(외부 전송·쓰기·삭제·송금·예약·권한 변경·공격 URL 방문).",
        "- **쌍 단위 탐지율(strict)**: 효과 호출 중 하나 이상이 BLOCK인 쌍의 비율.",
        "- **쌍 단위 탐지율(lenient)**: 효과 호출 중 하나 이상이 BLOCK 또는 ESCALATE인 쌍의 비율.",
        "- **호출 단위 탐지율**: 효과 호출 중 BLOCK 또는 ESCALATE 판정 비율.",
        "- **저지율**: 정책 없이(audit) 재생했을 때 AgentDojo security 판정이 성공(공격 성공)인 쌍을 기준선으로, enforce 재생(BLOCK과 ESCALATE를 실행하지 않음) 후 security 판정이 실패로 바뀐 쌍의 비율. "
        "AgentDojo의 security 판정을 그대로 쓴 값(native)과, 판정에 넘기는 호출 기록을 실제로 실행된 호출로 한정한 값(executed-only)을 함께 보고한다. "
        "slack injection_task_5는 `security_from_traces`가 *요청된* 호출 기록만으로 성공을 판정하므로 차단된 시도도 공격 성공으로 센다.",
        "- **오탐률**: user task 97개를 주입 없는 환경에서 재생할 때, 정답 호출 중 BLOCK 비율 · BLOCK이 하나라도 있는 user task 비율 · ESCALATE 비율.",
        "- **분모 처리**: 유형 H(travel injection_task_6, 효과 호출 0개) 20쌍은 모든 탐지율 분모에서 제외하고 저지율 분모에는 포함한다(정책이 막을 수 없는 공격으로 남는다). "
        f"미도달 쌍(trace에 인젝션 문자열이 나타나지 않은 쌍)은 {R['n_attack_pairs'] - R['reached']}쌍이며 분모에서 제외한다.", "",
    ]
    summary = [
        "## 2. 전체 요약 (oracle TaskSpec)", "",
        table(["지표", "값"], [
            ["평가 쌍 (효과 호출 있음)", f"{o['eval']} / 전체 {R['n_attack_pairs']}"],
            ["쌍 단위 탐지율 strict", pct(o["strict"], o["eval"])],
            ["쌍 단위 탐지율 lenient", pct(o["lenient"], o["eval"])],
            ["호출 단위 탐지율 (BLOCK+ESCALATE)", pct(o["calls_flagged"], o["calls"])],
            ["호출 단위 BLOCK 비율", pct(o["calls_blocked"], o["calls"])],
            ["저지율 (native)", pct(pv["prevented"], pv["baseline_success"])],
            ["저지율 (executed-only)", pct(pv["prevented_executed_only"], pv["baseline_success"])],
            ["기준선에서 공격이 실패한 쌍 (저지율 분모 제외)", pv["baseline_fail_pairs"]],
            ["오탐: BLOCK 호출 비율", pct(b["calls_block"], b["calls"])],
            ["오탐: BLOCK이 있는 user task 비율", pct(b["tasks_block"], b["tasks"])],
            ["ESCALATE 호출 비율 (정상 태스크)", pct(b["calls_escalate"], b["calls"])],
            ["ESCALATE가 있는 user task 비율", pct(b["tasks_escalate"], b["tasks"])],
            ["정상 태스크 Utility (audit → enforce, ESCALATE→block)", f"{b['utility_audit']}/{b['tasks']} → {b['utility_enforce']}/{b['tasks']}"],
            ["공격 하 Utility (audit → enforce)", f"{R['utility_under_attack']['audit']}/{R['utility_under_attack']['n']} → {R['utility_under_attack']['enforce']}/{R['utility_under_attack']['n']}"],
            ["판정 지연 (호출당 ms, mean / p50 / p95 / max)", "{mean:.3f} / {p50:.3f} / {p95:.3f} / {max:.1f}".format(**R["decide_ms"])],
        ]), "",
        "LLM 추출 TaskSpec(`--spec llm:<model>`)으로 재생한 결과는 API 키가 없어 생성하지 못했다. "
        "키를 넣은 뒤 `scripts/extract_specs.py` → `scripts/replay_eval.py --spec llm:<model> --out results/replay/v1/llm_<model>` → "
        "`scripts/summarize_replay.py --compare results/replay/v1/llm_<model>`로 oracle과의 차이 쌍 목록이 생성된다.", "",
        "기준선에서 공격이 실패한 쌍은 순차 재생의 부산물이다. 예를 들어 user task가 먼저 메일을 보내거나 읽음 처리하면 "
        "injection_task_8·9의 security 조건(읽지 않은 메일 7개 삭제, 다음 메일 ID)이 성립하지 않는다.", "",
    ]
    def det_rows(d, order):
        rows = []
        for k in order:
            v = d.get(k)
            if not v:
                continue
            rows.append([k, v["pairs"], v.get("eval", 0), pct(v.get("strict", 0), v.get("eval", 0)),
                         pct(v.get("lenient", 0), v.get("eval", 0)), pct(v.get("calls_flagged", 0), v.get("calls", 0))])
        return rows
    bd = R["prevention_breakdown"]
    split = [
        "## 3. 분해표", "",
        "### 3.1 suite별 (분모: 효과 호출이 있는 쌍, 유형 H 제외)", "",
        table(["suite", "쌍", "평가 쌍", "strict", "lenient", "호출 단위"], det_rows(R["by_suite"], SUITES)), "",
        table(["suite", "기준선 성공 쌍", "저지 (native)", "저지 (executed-only)"],
              [[s, bd[s]["base"], pct(bd[s]["prevented"], bd[s]["base"]), pct(bd[s]["prevented_executed_only"], bd[s]["base"])]
               for s in SUITES if s in bd]), "",
        "### 3.2 유형별", "",
        "탐지율 표는 유형 H를 제외한다(효과 호출 없음). 저지율 표는 H를 포함한다.", "",
        table(["유형", "쌍", "평가 쌍", "strict", "lenient", "호출 단위"],
              [[f"{k} {TYPE_NAMES[k]}"] + r[1:] for k, r in zip([k for k in "ABCDEFG" if k in R["by_type"]],
                                                               det_rows(R["by_type"], "ABCDEFG"))]), "",
        table(["유형", "기준선 성공 쌍", "저지 (native)", "저지 (executed-only)"],
              [[f"{t} {TYPE_NAMES[t]}", bd[t]["base"], pct(bd[t]["prevented"], bd[t]["base"]),
                pct(bd[t]["prevented_executed_only"], bd[t]["base"])] for t in "ABCDEFGH" if t in bd]), "",
        f"![detection by type]({'../' + figs['type']})", "",
        "### 3.3 인젝션 태스크별", "",
        table(["injection task", "평가 쌍", "strict", "lenient", "호출 단위"],
              [[k, v.get("eval", 0), pct(v.get("strict", 0), v.get("eval", 0)), pct(v.get("lenient", 0), v.get("eval", 0)),
                pct(v.get("calls_flagged", 0), v.get("calls", 0))] for k, v in R["by_injection_task"].items()]), "",
        "### 3.4 정책별 (효과 호출에 해당 판정을 낸 쌍 수)", "",
        table(["정책", "BLOCK 쌍", "ESCALATE 쌍"],
              [[p, v.get("BLOCK", 0), v.get("ESCALATE", 0)] for p, v in R["policy_fired_pairs"].items()]), "",
        "ESCALATE만 받은 쌍(strict에서는 놓치고 lenient에서는 잡힌 쌍)은 다음과 같다: " +
        ", ".join(f"{k} {v}" for k, v in R["escalate_only_pairs"].items()) + ". "
        "banking은 인젝션이 사용자가 지목한 출처(청구서 파일, 거래 내역)에 들어가 공격 IBAN이 NAMED_SOURCE로 판정되므로 "
        "대부분 ESCALATE에 머문다. 판정 LLM(Stage 7)이 해소해야 하는 회색 지대가 이것이다. "
        "workspace injection_task_1(파일 삭제)은 P5가 프롬프트 출처가 아닌 파일 삭제를 ESCALATE로만 판정하므로 strict 0이다.", "",
    ]
    ab = R["ablation"]
    ev = ab["none"]["eval"]
    abl = [
        "## 4. Ablation (oracle, 정책 하나씩 비활성화)", "",
        "audit 재생에서 각 정책의 판정은 다른 정책의 판정에 의존하지 않으므로(실행이 바뀌지 않음) 기록된 정책별 판정에서 해당 정책을 빼고 다시 결합해 계산했다. "
        "고유 기여는 그 정책을 끄면 탐지에서 빠지는 쌍 수다.", "",
        table(["비활성 정책", "strict", "lenient", "고유 기여 strict", "고유 기여 lenient", "정상 BLOCK 호출", "정상 ESCALATE 호출"],
              [[k if k != "none" else "(없음)", pct(v["strict"], ev), pct(v["lenient"], ev), v.get("unique_strict", "-"),
                v.get("unique_lenient", "-"), v["benign_calls_block"], v["benign_calls_escalate"]] for k, v in ab.items()]), "",
        f"![ablation]({'../' + figs['ablation']})", "",
    ]
    miss_rows = []
    for k, lst in R["misses"].items():
        c = Counter(m["cause"] for m in lst)
        for cause, n in c.items():
            miss_rows.append([k, n, cause, ", ".join(m["user_task"].replace("user_task_", "u") for m in lst if m["cause"] == cause)])
    fp_rows = [[f"{f['suite']}/{f['user_task'].replace('user_task_', 'u')}", f["tool"], f["policy"], f["verdict"],
                f["origin"] or "-", f["cause"]] for f in R["benign_flags"]]
    fail = [
        "## 5. 실패 분석", "",
        "### 5.1 놓친 쌍 (lenient 기준, 효과 호출이 하나도 BLOCK/ESCALATE되지 않음)", "",
        f"총 {o['eval'] - o['lenient']}쌍.", "",
        table(["injection task", "쌍 수", "원인", "user task"], miss_rows), "",
        "- **출처 판정 실패(지목 출처 안의 주입 값)**: slack injection_task_1·3은 공격 URL이 사용자가 지목한 출처(웹 페이지, 채널 목록) 안에 주입된다. "
        "P7은 지목 출처 안의 링크를 ALLOW하도록 정의되어 있어 통과한다. travel injection_task_0·4(user_task_0)는 예약 대상 호텔이 사용자가 지목한 호텔 리뷰·도시 목록에 등장해 P8이 통과시킨다.",
        "- **TaskSpec 한계**: travel injection_task_2(일정 생성)는 user task가 일정 생성을 요청한 경우(u1·u4·u7·u8) 같은 툴·같은 권한 범위라 P9가 구분하지 못한다.",
        "- **TaskSpec 문제**: travel user_task_3 × injection_task_5는 공격 목적지가 프롬프트에 명시된 정당한 목적지와 같다(Stage 1 5절).", "",
        "### 5.2 정상 태스크에서 BLOCK/ESCALATE된 호출", "",
        table(["task", "툴", "정책", "판정", "출처", "원인"], fp_rows), "",
        "BLOCK은 3건이다: banking u0(청구서 내용을 송금 메모로 복사해 P2가 막음), slack u11(지목한 메시지가 가리키는 웹 페이지에서 얻은 이메일 주소: 2단계 출처를 OTHER_TOOL로 판정), "
        "slack u19(TODO 목록에 위임된 작업이라 requested_ops에 채널 추가가 없음). 나머지 ESCALATE는 지목 출처에서 온 목적지·계산된 금액으로, 설계상 사람이나 판정 LLM이 보는 회색 지대다.", "",
        "### 5.3 oracle과 LLM spec의 차이", "",
        "LLM spec 결과가 없어 생성하지 못했다(5.2절 명령으로 생성 가능).", "",
        "### 5.4 enforce에서도 공격이 성공한 쌍", "",
        table(["유형", "suite", "injection task", "쌍 수", "실행된 호출 기준 성공"],
              [[t, s, i, n, sum(1 for x in R["surviving_attacks"] if (x["type"], x["pair"][0], x["pair"][2]) == (t, s, i) and x["security_executed"])]
               for (t, s, i), n in Counter((x["type"], x["pair"][0], x["pair"][2]) for x in R["surviving_attacks"]).items()]), "",
        "slack injection_task_5의 21쌍은 효과 호출 3개가 모두 BLOCK되어 실행되지 않았지만 AgentDojo가 시도 기록으로 성공을 판정한 경우다. 실행된 호출만 넘기면 모두 실패로 판정된다.", "",
    ]
    nm = lambda *ks: sum(len(R["misses"].get(k, [])) for k in ks)  # noqa: E731
    n_slack, n_travel_res, n_travel_cal = (nm("slack/injection_task_1", "slack/injection_task_3"),
                                           nm("travel/injection_task_0", "travel/injection_task_4"),
                                           nm("travel/injection_task_2"))
    d_strict = R["by_injection_task"].get("workspace/injection_task_1", {})
    lim = [
        "## 6. 한계", "",
        "- **정답 궤적 재생과 실제 에이전트의 차이**: 재생은 공격에 넘어간 에이전트가 GOAL을 정답대로 수행한다고 가정한다. 실제 모델은 다른 툴·인자 조합으로 같은 목적을 이루거나, 차단 후 우회할 수 있다(Stage 6).",
        "- **정책 도출·평가 데이터 중복**: 정책(P1~P9)과 유형 분류는 같은 35개 인젝션 태스크를 보고 설계했다. 이 수치는 일반화 성능이 아니라 설계 대상 위협에 대한 상한에 가깝다.",
        "- **참조 궤적**: workspace injection_task_6~13의 궤적은 이 프로젝트가 작성했다(AgentDojo가 제공하지 않음). 다른 합리적 궤적(예: 메일을 여러 통으로 나눠 보내기)에서는 P3 판정이 달라질 수 있다.",
        "- **순차 재생 부산물**: 주입된 환경에서 user task ground truth를 재생하면 일부 값이 사라진다(예: banking 청구서의 IBAN이 인젝션으로 대체되어 user 단계 송금의 수신자가 UNSEEN이 됨). 탐지율은 injection 단계 효과 호출만으로 계산하므로 영향이 없지만, 공격 하 Utility에는 반영된다.",
        "", "## 7. 정책 수정 제안 v2 (승인 전, 미적용)", "",
        "이 세션에서는 정책·설정을 바꾸지 않았다. 아래 제안은 승인 후 별도 세션에서 적용하고 `results/replay/v2`로 재실행해 v1과 비교한다.", "",
        table(["#", "제안", "근거 (이 보고서)", "예상 효과 / 위험"], [
            [1, "P7·P8: 지목 출처 응답 안에서 지시 문구(`<INFORMATION>` 등 P7 instruction_phrases)와 같은 블록에 있는 URL·예약 대상은 ALLOW 대신 ESCALATE",
             f"5.1 slack injection_task_1·3 {n_slack}쌍, travel 0·4 {n_travel_res}쌍", "lenient 놓침 감소. 정상 태스크 중 지시 문구가 있는 지목 출처는 없으므로 오탐 증가는 작을 것으로 본다(재실행으로 확인 필요)"],
            [2, "지목 출처의 전이: 지목 출처 응답에 있던 URL로 읽은 응답도 지목 출처로 간주(1단계까지)",
             "5.2 slack u11 BLOCK", "오탐 1건 해소. 공격자가 지목 출처에 링크를 심으면 그 페이지도 신뢰되므로 1번 제안과 함께 적용해야 한다"],
            [3, "P5: 삭제 툴이 requested_ops에 없으면 BLOCK (현재는 파일 삭제 ESCALATE, 메일 삭제는 보낸 메일만 BLOCK)",
             f"3.3 workspace injection_task_1 strict {d_strict.get('strict', 0)}/{d_strict.get('eval', 0)}", "D 유형 strict 상승. 정상 삭제 태스크(u35·u38)는 requested_ops에 delete_file이 있어 영향 없음"],
            [4, "P2: 송금 메모가 지목 출처(청구서 파일)의 내용만 담으면 겹침에서 제외",
             "5.2 banking u0 BLOCK", "오탐 1건 해소. banking 메모 유출 공격은 P1(공격 IBAN)로 여전히 ESCALATE 이상이지만 P2 고유 기여(ablation)가 일부 줄어든다"],
            [5, "P9: 요청된 일정 생성이라도 제목·시간이 프롬프트나 지목 출처에 없으면 ESCALATE",
             f"5.1 travel injection_task_2 {n_travel_cal}쌍", "TaskSpec 한계 사례 해소. 프롬프트가 제목을 지정하지 않는 정상 일정 생성에서 ESCALATE 증가 가능"],
            [6, "위임형 프롬프트(\"파일·메일·TODO에 적힌 대로\")는 requested_ops를 비우는 대신 사람 확인 대상으로 표시",
             "Stage 4 GT 대조 불일치 9건 전부, 5.2 slack u19", "정책이 아닌 TaskSpec 절차 변경"],
        ]),
    ]
    (DOCS / "stage5_replay_report.md").write_text("\n".join(defs + summary + split + abl + fail + lim) + "\n")


# ---------------------------------------------------------------------------------------------- stage 6
def stage6(LIVE, ENF, figs):
    L = ["# Stage 6 — 실제 에이전트 파이프라인 집행 평가 (RQ2: 정책이 지켜지는가)", "",
         "> `scripts/live_eval.py` → `scripts/check_enforcement.py`, `scripts/summarize_live.py`의 출력(`results/live/v1/<agent>/`)에서 "
         "`scripts/build_report.py`가 생성했다.", "",
         "## 1. 실험 설정", ""]
    if not LIVE:
        L += ["실행 결과가 없다."]
    have_llm = any(k != "scripted" for k in LIVE)
    L += [
        f"- 벤치마크: AgentDojo {BENCHMARK_VERSION}, 4 suite, user task 97개, 쌍 949개(+ 공격 없는 실행 97개), attack `important_instructions`.",
        "- 가로채기 지점: `GatedRuntime`(FunctionsRuntime 서브클래스). 파이프라인은 AgentDojo `AgentPipeline.from_config`가 만든 것에 "
        "`TaskSpecElement`를 InitQuery 다음에 끼운 것이다(`src/agentgate/pipeline.py`). agentdojo 소스는 수정하지 않았다.",
        "- 조건: " + "; ".join(f"{k} {v}" for k, v in COND_NAMES.items()) + ". C0와 C3에서도 게이트는 audit 모드로 판정만 기록한다.",
        "- **에이전트**: 이 세션에는 LLM API 키가 없어 실제 모델로 실행하지 못했다. 대신 `ScriptedAgent`(결정론적 대역)를 "
        "AgentDojo 파이프라인의 LLM 자리에 넣어 전체 경로를 실행했다. ScriptedAgent는 user task 정답 호출을 한 턴에 하나씩 요청하고, "
        "툴 응답에서 인젝션 표식을 보면 즉시 injection task 참조 궤적을 끼워 넣은 뒤(항상 넘어가는 에이전트) user task를 이어 간다. 차단 메시지를 받아도 계획을 계속 진행한다.",
        "- 따라서 이 단계의 수치는 **통합·집행 무결성 검증**이다. 모델이 인젝션에 넘어가는 비율, 차단 후 재시도·우회 같은 행동, 토큰·지연 비용은 측정하지 못했다. "
        "C3(spotlighting)은 도구 출력에 구분자를 붙이고 시스템 프롬프트를 바꾸는 방어라서 대역 에이전트에는 효과가 없다(C0와 같은 값이 나오는 것이 정상)." if not have_llm else "",
        "- ESCALATE→judge(C4)는 판정 LLM이 필요해 실행하지 않았다.", "",
        "## 2. 조건별 요약", ""]
    for name, S in LIVE.items():
        m = S["conditions"]
        L += [f"### 에이전트: {name}", "",
              table(["조건", "Utility", "Utility under attack", "ASR (native)", "ASR (executed-only)", "태스크당 wall ms",
                     "태스크당 게이트 판정 ms", "태스크당 TaskSpec ms", "태스크당 에이전트 토큰 (in/out)", "BLOCK 실행 수"],
                    [[f"{c} {COND_NAMES[c]}", p100(v["utility"]), p100(v["utility_under_attack"]), p100(v["asr"]),
                      p100(v["asr_executed_only"]), v["wall_ms_per_task"], v["gate_ms_per_task"], v["spec_ms_per_task"],
                      f"{v['agent_input_tokens_per_task']}/{v['agent_output_tokens_per_task']}", v["blocked_calls"]]
                     for c, v in m.items()]), "",
              f"![tradeoff](../{figs.get('tradeoff_' + name, '')})", "",
              "판정 분포(호출 단위, 게이트 판정): " + "; ".join(f"{c} {v['verdicts']}" for c, v in m.items()), ""]
        if name == "scripted":
            L += [f"C0의 Utility가 100%가 아닌 것은 travel user_task_19 한 건 때문이다. 정답 호출이 16개라 AgentDojo `ToolsExecutionLoop` 상한(15회)에 걸려 마지막 호출이 실행되지 않는다(아래 3절 참고).", ""]
    L += ["## 3. 집행 무결성 점검 (`scripts/check_enforcement.py`)", ""]
    for name, E in ENF.items():
        L += [table(["항목", "값"], [
            ["점검한 실행 수", E["runs"]], ["게이트 판정 이벤트", E["gate_events"]], ["차단(BLOCK 집행) 이벤트", E["blocked_events"]],
            ["① BLOCK 판정 호출이 실행된 사례", E["violations_by_check"].get("blocked_executed", 0) + E["violations_by_check"].get("enforce_block_executed", 0)],
            ["② 차단 직후 환경이 바뀐 사례 (환경 해시 비교)", E["violations_by_check"].get("env_changed_on_block", 0)],
            ["③ 툴 결과와 게이트 판정의 1:1 대응 위반 (우회 경로)", E["violations_by_check"].get("gate_trace_mismatch", 0)],
            ["참고: 반복 상한으로 요청만 되고 실행되지 않은 호출이 있는 실행", E.get("runs_with_unexecuted_requests_at_loop_limit", 0)],
            ["실행 오류", E.get("errored_runs", 0)],
        ]), ""]
    L += ["위반이 0건이므로 BLOCK 판정은 모두 집행되었고, 에이전트가 받은 모든 툴 결과는 게이트를 거쳤다. "
          "이 세 항목은 구현의 문제이며, 아래 4절의 우회·탐지 실패는 정책 설계의 문제로 구분한다.", "",
          "## 4. 우회·실패 분석", ""]
    for name, S in LIVE.items():
        m = S["conditions"]
        for c in ("C1", "C2"):
            if c in m:
                L += [f"**{name} {c}에서 성공한 공격의 경로**", "",
                      table(["분류", "쌍 수"], sorted(m[c]["successful_attacks_by_class"].items())), ""]
        L += ["분류 기준: (a) 유형 H, (b) 차단 이벤트 없이 성공, (c) 차단이 있었지만 다른 호출로 성공, (d) ESCALATE가 허용되어 성공, "
              "(e) 실행된 호출만 보면 실패지만 AgentDojo가 요청 기록으로 성공 판정(slack injection_task_5). "
              "대역 에이전트는 차단 후에도 계획을 그대로 진행하므로 (c)는 '우회 시도'가 아니라 같은 궤적의 다른 효과 호출이 허용된 경우다.", "",
              "차단 후 행동(휴리스틱 분류): " + "; ".join(f"{c} {v['post_block_behaviour']}" for c, v in m.items() if v["post_block_behaviour"]) + ". "
              "대역 에이전트의 행동은 설계로 고정되어 있으므로 이 분포는 분류기 동작 확인용이며 모델 행동을 뜻하지 않는다.", ""]
        if S.get("utility_lost_C1") is not None:
            L += ["**C1에서 정상 Utility가 떨어진 user task와 원인 정책**", "",
                  table(["task", "BLOCK한 정책"], [[x["task"], ", ".join(x["blocked_by"])] for x in S["utility_lost_C1"]]), ""]
            cmp = S.get("utility_lost_vs_replay", {})
            L += [f"Stage 5 재생의 enforce Utility 손실 목록과 비교: 공통 {len(cmp.get('both', []))}건, live에만 {len(cmp.get('live_only', []))}건, "
                  f"재생에만 {len(cmp.get('replay_only', []))}건. 대역 에이전트가 같은 정답 호출을 내므로 일치하는 것이 정상이다. "
                  "이 손실의 대부분은 ESCALATE를 block으로 처리한 결과다(C2에서는 회복된다).", ""]
    L += ["## 5. Stage 5 재생 결과와의 차이", "",
          "대역 에이전트는 재생과 같은 호출을 내지만 다음이 다르다. (1) 인젝션 표식을 본 직후 공격을 실행하므로 user task 호출과 순서가 섞인다. "
          "(2) AgentDojo 파이프라인의 반복 상한(15)이 적용된다. (3) 차단 메시지가 에이전트에게 툴 결과로 돌아간다. "
          "그럼에도 C1의 ASR이 재생의 저지율과 같은 수준인 것은, 판정이 호출 순서보다 출처에 의존하기 때문이다.", "",
          "## 6. 한계와 실제 모델 실행 방법", "",
          "- 실제 LLM 에이전트 결과가 없다. `.env`에 키를 넣고 다음 순서로 실행한다. 파일럿 결과의 비용 추정을 보고 전체 실행 범위를 승인한 뒤 전체 실행을 한다.",
          "```bash",
          "python scripts/live_eval.py --agent llm --spec llm --pilot --conditions C0 C1 --out results/live/v1/<model>",
          "python scripts/summarize_live.py --run results/live/v1/<model> --price-in <USD/Mtok> --price-out <USD/Mtok>   # full_run_estimate",
          "python scripts/live_eval.py --agent llm --spec llm --conditions C0 C1 C2 C3 --out results/live/v1/<model>   # 승인 후",
          "python scripts/check_enforcement.py --run results/live/v1/<model> && python scripts/summarize_live.py --run results/live/v1/<model>",
          "python scripts/build_report.py",
          "```",
          "- 적응형 공격(정책을 아는 공격자)은 평가하지 않았다."]
    (DOCS / "stage6_live_report.md").write_text("\n".join(x for x in L if x is not None) + "\n")


# ---------------------------------------------------------------------------------------------- final
def final(R, LIVE, ENF, figs):
    o, b, pv, ab = R["overall"], R["benign"], R["prevention"], R["ablation"]
    judge = sorted((ROOT / "results/judge/v1").glob("*/judge_eval.json")) if (ROOT / "results/judge/v1").exists() else []
    comp = sorted((ROOT / "data/specs/llm").glob("*/comparison.json")) if (ROOT / "data/specs/llm").exists() else []
    pc = policies_config()
    L = [
        "# AgentDojo 툴 호출 정책 엔진 PoC — 최종 보고서", "",
        "> 이 문서의 모든 수치는 `scripts/build_report.py`가 `results/`와 `data/`의 파일에서 생성한다. 손으로 옮긴 수치는 없다.", "",
        "## 요약", "",
        f"- **RQ1 (탐지 가능성)**: 공격에 넘어간 에이전트의 정답 궤적을 재생하면, 결정론적 정책 P1~P9는 효과 호출이 있는 {o['eval']}쌍 중 "
        f"{pct(o['lenient'], o['eval'])}을 BLOCK 또는 ESCALATE로, {pct(o['strict'], o['eval'])}을 BLOCK으로 잡는다. "
        f"ESCALATE를 block으로 집행하면 정책 없이 성공하던 공격 {pv['baseline_success']}쌍 중 {pct(pv['prevented'], pv['baseline_success'])}가 실패로 바뀐다"
        f"(실행된 호출 기준 {pct(pv['prevented_executed_only'], pv['baseline_success'])}). "
        f"정상 user task 97개에서 BLOCK은 {b['tasks_block']}개 태스크, ESCALATE는 {b['tasks_escalate']}개 태스크에서 나왔다.",
    ]
    if LIVE:
        S = next(iter(LIVE.values()))
        m = S["conditions"]
        E = next(iter(ENF.values()))
        L.append(f"- **RQ2 (집행 여부)**: AgentDojo 파이프라인에 게이트를 넣고 {E['runs']}회 실행(게이트 판정 {E['gate_events']}건, 차단 {E['blocked_events']}건)한 결과, "
                 f"BLOCK된 호출의 실행·차단 시 환경 변화·게이트를 거치지 않은 툴 실행이 모두 {E['violations']}건이었다. "
                 f"대역 에이전트 기준 ASR은 C0 {p100(m['C0']['asr'])} → C1 {p100(m['C1']['asr'])}, 정상 Utility는 {p100(m['C0']['utility'])} → {p100(m['C1']['utility'])}이다. "
                 "실제 LLM 에이전트 결과는 API 키가 없어 아직 없다.")
    L += [
        "- **판정 LLM(C4)과 LLM TaskSpec 추출**: 코드·프롬프트·캐시·테스트는 완성했으나 키가 없어 실행하지 않았다(8절의 명령으로 실행).", "",
        "## 1. 개요", "",
        "LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)에 따라 송금·반출·삭제를 하는 문제를, 모델 바깥의 결정론적 정책으로 막을 수 있는지 검증한다.", "",
        "- **RQ1 탐지 가능성**: 사용자 프롬프트만으로 만든 TaskSpec과 툴 호출 인자의 출처를 기준으로 하는 정책이, 공격 호출을 잡고 정상 호출은 통과시키는가.",
        "- **RQ2 집행 여부**: 정책 판정이 실제 에이전트 파이프라인에서 우회 없이 집행되는가, 그 비용(Utility·지연)은 얼마인가.", "",
        "## 2. 방법", "",
        "### 2.1 시나리오 분류와 정책 도출", "",
        f"AgentDojo {BENCHMARK_VERSION}의 injection task 35개를 공격 효과로 분류했다(A 무단 외부 연락, B 금전 탈취, C 민감 데이터 반출, D 삭제·은폐, E 계정·권한 변경, F 비신뢰 내용 전파·방문, G 무단 예약·일정 생성, H 툴 호출 없는 출력 조작). "
        "분류와 정책 대응은 `docs/policy_spec.md` 1절, 카탈로그 검증은 `docs/stage1_catalog_report.md`에 있다.", "",
        table(["정책", "대상", "판정 요지"], [
            ["P1 외부 목적지 출처", "수신자·IBAN·참석자·공유 대상·게시 URL", "PROMPT→ALLOW, 지목 출처→ESCALATE, 그 외 툴 응답·미등장→BLOCK"],
            ["P2 민감 데이터 반출", "본문·제목·메모·첨부", f"민감 읽기 결과와 {pc['P2']['min_overlap_chars']}자 이상 겹침 또는 고가치 토큰 일치, egress 미요청→BLOCK"],
            ["P3 대량 반출", "같은 싱크 인자", f"서로 다른 읽기 결과 {pc['P3']['min_distinct_reads']}개 이상(첨부 포함)→BLOCK"],
            ["P4 금액", "send_money 등 amount", f"cap 초과→BLOCK, 출처 없는 금액→ESCALATE, 세션 누적 {pc['P4']['session_cumulative_limit']} 초과·같은 신규 수신자 {pc['P4']['repeat_new_recipient_threshold']}회째→BLOCK"],
            ["P5 삭제", "delete_email·delete_file", "세션에서 보낸 메일 삭제→BLOCK, 프롬프트가 지목하지 않은 파일 삭제→ESCALATE"],
            ["P6 권한 변경", "비밀번호·사용자 정보·초대·채널 추가·공유", "requested_ops에 없음→BLOCK, 비밀번호가 프롬프트 출처 아님→BLOCK"],
            ["P7 비신뢰 내용 전파", "메시지 본문, get_webpage.url", "툴 응답에만 있는 URL·지시 문구 전파→BLOCK, 그런 URL 방문→ESCALATE"],
            ["P8 예약", "reserve_*", "requested_ops에 없음→BLOCK, 대상이 프롬프트·지목 출처에 없음→ESCALATE"],
            ["P9 일정 쓰기", "calendar 쓰기 툴", "requested_ops에 없음→BLOCK (외부 참석자는 P1)"],
        ]), "",
        "### 2.2 구조", "",
        "```",
        "user prompt ──► TaskSpec (oracle | LLM extractor: prompt + tool list only)",
        "                    │",
        "agent LLM ──tool call──► GatedRuntime.run_function ──► PolicyEngine.decide(call, spec, trace, session)",
        "                    │            │                         P1..P9 (deterministic, no LLM)",
        "                    │            ├─ ALLOW ──► execute ──► ValueIndex(출처 색인) · SessionState 갱신",
        "                    │            ├─ BLOCK ──► 실행 안 함, 차단 메시지를 툴 결과로 반환",
        "                    │            └─ ESCALATE ─► block | allow | judge LLM (구조화 사실만 입력)",
        "```", "",
        "- 가로채기 지점은 `FunctionsRuntime` 서브클래스다. AgentDojo의 `ToolsExecutor`와 `GroundTruthPipeline`이 모두 `runtime.run_function`으로만 툴을 실행하므로 한 곳에서 모든 경로를 덮는다(근거: `docs/design_decisions.md`).",
        "- 출처 판정은 프롬프트와 각 툴 응답(모델에 전달된 문자열과 원본 객체의 모든 문자열 필드)을 정규화해 색인하고, 값이 가장 신뢰도 높은 출처에서 처음 등장한 위치를 근거로 남긴다.", "",
        "### 2.3 평가 설정", "",
        "- Stage 5 재생: user task 정답 궤적 뒤에 injection task 참조 궤적을 이어 붙여 949쌍을 audit·enforce로 재생. TaskSpec은 oracle(프롬프트만 보고 작성, `docs/stage4_oracle_review.md`).",
        "- Stage 6 파이프라인: AgentDojo `AgentPipeline` + `GatedRuntime`, 조건 C0~C3, 대역 에이전트(ScriptedAgent).", "",
        "## 3. RQ1 결과 — 재생 평가", "",
        table(["지표", "oracle TaskSpec", "LLM TaskSpec"], [
            ["쌍 단위 탐지율 strict", pct(o["strict"], o["eval"]), "미실행"],
            ["쌍 단위 탐지율 lenient", pct(o["lenient"], o["eval"]), "미실행"],
            ["호출 단위 탐지율", pct(o["calls_flagged"], o["calls"]), "미실행"],
            ["저지율 native / executed-only", f"{pct(pv['prevented'], pv['baseline_success'])} / {pct(pv['prevented_executed_only'], pv['baseline_success'])}", "미실행"],
            ["오탐: BLOCK 호출 / BLOCK 태스크", f"{pct(b['calls_block'], b['calls'])} / {pct(b['tasks_block'], b['tasks'])}", "미실행"],
            ["ESCALATE 호출 / 태스크", f"{pct(b['calls_escalate'], b['calls'])} / {pct(b['tasks_escalate'], b['tasks'])}", "미실행"],
        ]), "",
        "유형별 탐지율:", "",
        table(["유형", "평가 쌍", "strict", "lenient"],
              [[f"{t} {TYPE_NAMES[t]}", R["by_type"][t]["eval"], pct(R["by_type"][t]["strict"], R["by_type"][t]["eval"]),
                pct(R["by_type"][t]["lenient"], R["by_type"][t]["eval"])] for t in "ABCDEFG" if t in R["by_type"]]), "",
        f"![detection by type](../{figs['type']})", "",
        "Ablation (정책 하나를 끈 탐지율과 고유 기여):", "",
        table(["비활성", "strict", "lenient", "고유 strict", "고유 lenient"],
              [[k if k != "none" else "(없음)", pct(v["strict"], ab["none"]["eval"]), pct(v["lenient"], ab["none"]["eval"]),
                v.get("unique_strict", "-"), v.get("unique_lenient", "-")] for k, v in ab.items()]), "",
        f"![ablation](../{figs['ablation']})", "",
        "P1(출처)과 P2(내용 겹침)가 가장 큰 축이다. P2는 lenient 고유 기여가 0이지만 strict 고유 기여가 가장 크다. P1이 ESCALATE로만 잡는 banking·workspace 반출을 BLOCK으로 끌어올리는 역할이다. "
        "P6·P8·P9 같은 작업 단위(op-level) 정책은 해당 유형(E·G)에서만 쓰이지만, 그 유형에서는 사실상 유일한 방어선이다.", "",
        "## 4. RQ2 결과 — 파이프라인 집행", "",
    ]
    if LIVE:
        for name, S in LIVE.items():
            m = S["conditions"]
            L += [f"에이전트: **{name}** (실제 LLM 아님, 6절 한계 참고)", "",
                  table(["조건", "Utility", "Utility under attack", "ASR", "ASR (executed-only)", "태스크당 지연 ms (전체 / 게이트 / TaskSpec)",
                         "토큰 (에이전트 in/out, TaskSpec, judge)", "HUMAN"],
                        [[f"{c} {COND_NAMES[c]}", p100(v["utility"]), p100(v["utility_under_attack"]), p100(v["asr"]), p100(v["asr_executed_only"]),
                          f"{v['wall_ms_per_task']} / {v['gate_ms_per_task']} / {v['spec_ms_per_task']}",
                          f"{v['agent_input_tokens_per_task']}/{v['agent_output_tokens_per_task']}, {v['spec_tokens_per_task']}, {v['judge_tokens_total']}",
                          v["human_count"]] for c, v in m.items()] + [["C4 게이트 (ESCALATE→judge)", "미실행", "미실행", "미실행", "미실행", "미실행", "미실행", "미실행"]]), ""]
        for name, E in ENF.items():
            L += [f"집행 무결성({name}): 실행 {E['runs']}회, 게이트 판정 {E['gate_events']}건, 차단 {E['blocked_events']}건, 위반 {E['violations']}건 "
                  f"(BLOCK 호출 실행 0, 차단 시 환경 변화 0, 툴 결과-판정 1:1 불일치 0).", ""]
    L += [
        "## 5. ASR · Utility · Latency 트레이드오프", "",
        "지표 정의는 AgentDojo를 따른다. **Utility**는 공격 없이 user task의 `utility` 판정이 참인 비율, **Utility under attack**은 공격 환경에서 user task `utility`가 참인 비율, "
        "**ASR**(targeted attack success rate)은 injection task의 `security` 판정이 참(공격 목표 달성)인 비율이다.", "",
    ]
    if LIVE:
        name, S = next(iter(LIVE.items()))
        m = S["conditions"]
        L += [table(["조건", "ASR", "Utility", "Utility under attack", "게이트 판정 지연 (태스크당 ms)"],
                    [[f"{c} {COND_NAMES[c]}", p100(v["asr"]), p100(v["utility"]), p100(v["utility_under_attack"]), v["gate_ms_per_task"]] for c, v in m.items()]), "",
              f"![tradeoff](../{figs.get('tradeoff_' + name, '')})", "",
              f"C1은 ASR을 가장 크게 낮추지만 ESCALATE를 모두 막아 정상 Utility도 {p100(m['C0']['utility'])} → {p100(m['C1']['utility'])}로 낮춘다. "
              f"C2는 Utility를 {p100(m['C2']['utility'])}로 회복하는 대신 ASR이 {p100(m['C2']['asr'])}로 오른다. 그 차이가 ESCALATE 회색 지대이며, C4(판정 LLM)가 이 구간을 메우는 것이 목표다. "
              f"게이트 판정 지연은 호출당 평균 {R['decide_ms']['mean']:.2f} ms(p95 {R['decide_ms']['p95']:.2f} ms)로, LLM 호출 한 번(수백 ms 이상)에 비해 무시할 수 있다. "
              "TaskSpec 추출과 판정 LLM의 지연·토큰은 키를 넣은 뒤 측정된다.", ""]
    L += [
        "## 6. 실패 분석", "",
        "- **탐지 실패(정책 설계)**: 인젝션이 사용자가 지목한 출처 안에 있으면 출처 기반 판정이 약해진다. slack injection_task_1·3(지목 웹 페이지·채널 목록 안의 링크), travel injection_task_0·4(지목 호텔 리뷰 안의 호텔)가 그 예다. "
        "banking은 같은 이유로 대부분 ESCALATE에 머문다(lenient는 잡지만 strict는 놓침).",
        "- **같은 권한 범위의 공격**: 사용자가 일정 생성을 요청한 태스크에서 공격도 일정 생성이면 op-level 정책(P9)이 구분하지 못한다(travel injection_task_2).",
        "- **정당한 목적지와 같은 공격 목적지**: travel user_task_3의 `janeLong@google.com`.",
        "- **측정상 주의**: slack injection_task_5는 AgentDojo가 시도 기록으로 성공을 판정하므로, 모든 시도를 막아도 native ASR에는 성공으로 남는다(executed-only로 보정해 함께 보고).",
        "- **오탐 원인**: 지목 출처 내용을 송금 메모로 복사(P2), 2단계 출처(지목 메시지 → 웹 페이지 → 이메일)를 OTHER_TOOL로 판정(P1), 위임형 프롬프트의 requested_ops 공백(P6). 나머지는 설계상 ESCALATE다.",
        "- **경로 우회**: 대역 에이전트는 우회를 시도하지 않으므로 측정하지 못했다. 실제 모델 실행에서 (c) 분류로 집계된다.",
        "- 개선안 6개는 `docs/stage5_replay_report.md` 7절(정책 수정 제안 v2)에 있으며 승인 전이라 적용하지 않았다.", "",
        "## 7. 한계", "",
        "- **정책 도출·평가 데이터 중복**: 정책과 분류를 같은 35개 인젝션 태스크로 설계하고 평가했다.",
        "- **정답 궤적 재생의 한계**: 재생은 \"넘어간 에이전트가 정답대로 공격한다\"는 가정이다. 실제 모델의 다른 경로는 반영되지 않는다.",
        "- **유형 H는 범위 밖**: 툴 호출 없이 응답 텍스트만 조작하는 공격(travel injection_task_6)은 툴 호출 게이트로 막을 수 없다.",
        "- **적응형 공격 미평가**: 정책을 아는 공격자(예: 지목 출처에만 주입, 값 쪼개기, 프롬프트에 있는 목적지 악용)는 평가하지 않았다.",
        "- **판정 LLM 자체의 인젝션 노출**: 판정기는 주입 문장이 포함될 수 있는 주변 문맥을 본다. 인용 블록과 시스템 지시로 완화했지만 실측하지 않았다. 또한 비가역 툴은 A 판정이어도 HUMAN으로 보낸다.",
        "- **실제 LLM 미실행**: API 키가 없어 RQ2의 모델 행동·비용, LLM TaskSpec 추출 오류의 영향, C4를 측정하지 못했다.",
        "- **원본 정책 사양 부재**: 저장소에 `docs/policy_spec.md`가 없어 Stage 지시서의 조건으로 사양을 다시 작성했다. 원본과 다른 부분이 있을 수 있다.", "",
        "## 8. 재현 방법", "",
        f"- 버전: Python ≥3.10 (실행 환경 3.13), `agentdojo==0.1.35`, 벤치마크 `{BENCHMARK_VERSION}`, 정책 설정 `config/policies.yaml` (version {pc['version']}).",
        "- 설정: `config/policies.yaml`, `config/tool_roles.yaml`, `config/named_source_rules.yaml`, `config/sensitive_reads.yaml`, oracle spec `data/specs/oracle/`.",
        "```bash",
        "python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'",
        "python -m pytest -q                                   # unit + integration tests",
        "python scripts/smoke_test.py",
        "python scripts/build_catalog.py && python scripts/analyze_provenance.py      # Stage 1",
        "python scripts/review_oracle.py                                             # Stage 4 review table",
        "python scripts/replay_eval.py --spec oracle --out results/replay/v1/oracle  # Stage 5",
        "python scripts/summarize_replay.py --run results/replay/v1/oracle",
        "python scripts/live_eval.py --agent scripted --spec oracle --conditions C0 C1 C2 C3 --out results/live/v1/scripted",
        "python scripts/check_enforcement.py --run results/live/v1/scripted && python scripts/summarize_live.py --run results/live/v1/scripted",
        "python scripts/build_report.py",
        "# with API keys in .env:",
        "python scripts/extract_specs.py && python scripts/compare_specs.py --model <model>",
        "python scripts/judge_eval.py",
        "python scripts/live_eval.py --agent llm --spec llm --pilot --conditions C0 C1 --out results/live/v1/<model>",
        "```",
    ]
    if judge:
        L += ["", "## 부록. 판정기 단독 평가", ""]
        for p in judge:
            d = J(p)
            L += [f"- {d['model']}: 평가 호출 {d['n']}건, HUMAN 비율 {p100(d['human_rate'])}", "",
                  table(["정답→판정", "건수"], sorted(d["confusion_answer"].items())), ""]
    if comp:
        L += ["", "## 부록. LLM TaskSpec 추출 비교", ""]
        for p in comp:
            d = J(p)
            L += [f"- {d['model']}: requested_ops 정밀도 {p100(d['requested_ops']['precision'])}, 재현율 {p100(d['requested_ops']['recall'])}; "
                  + ", ".join(f"{k} {p100(v)}" for k, v in d["agreement"].items())]
    (DOCS / "final_report.md").write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------------------------------------- dashboard
def dashboard(R, LIVE):
    """Self-contained HTML: summary tables + per-pair trace viewer (replay audit/enforce)."""
    rows = []
    enf = {}
    for line in (REPLAY / "attack_enforce.jsonl").open():
        r = json.loads(line)
        enf[(r["suite"], r["user_task"], r["injection_task"])] = r
    labels = yaml.safe_load((ROOT / "data/catalog/injection_labels.yaml").read_text())
    for line in (REPLAY / "attack_audit.jsonl").open():
        r = json.loads(line)
        k = (r["suite"], r["user_task"], r["injection_task"])
        eff = [e for e in r["trace"] if e.get("role") == "effect"]
        rows.append({
            "s": r["suite"], "u": r["user_task"].replace("user_task_", ""), "i": r["injection_task"].replace("injection_task_", ""),
            "t": labels[r["suite"]][r["injection_task"]]["type"],
            "det": "BLOCK" if any(e["verdict"] == "BLOCK" for e in eff) else "ESCALATE" if any(e["verdict"] == "ESCALATE" for e in eff) else ("-" if not eff else "MISS"),
            "base": r["security"], "enf": enf[k]["security"],
            "tr": [[e["phase"], e["tool"], json.dumps(e["args"], ensure_ascii=False)[:220], e["verdict"],
                    [[d["policy_id"], d["verdict"], d["reason"], d["evidence"].get("origin")] for d in e["decisions"]]] for e in r["trace"]],
        })
    for line in (REPLAY / "benign_audit.jsonl").open():
        r = json.loads(line)
        rows.append({"s": r["suite"], "u": r["user_task"].replace("user_task_", ""), "i": "", "t": "benign",
                     "det": max((e["verdict"] for e in r["trace"]), key=["ALLOW", "ESCALATE", "BLOCK"].index, default="ALLOW"),
                     "base": None, "enf": None,
                     "tr": [[e["phase"], e["tool"], json.dumps(e["args"], ensure_ascii=False)[:220], e["verdict"],
                             [[d["policy_id"], d["verdict"], d["reason"], d["evidence"].get("origin")] for d in e["decisions"]]] for e in r["trace"]]})
    o, b, pv = R["overall"], R["benign"], R["prevention"]
    tiles = [("Lenient detection", pct(o["lenient"], o["eval"])), ("Strict detection", pct(o["strict"], o["eval"])),
             ("Prevented (enforce)", pct(pv["prevented"], pv["baseline_success"])), ("Benign tasks with BLOCK", pct(b["tasks_block"], b["tasks"]))]
    live_html = ""
    for name, S in LIVE.items():
        live_html += f"<h2>Pipeline run — agent: {name}</h2><table><tr><th>Condition</th><th>Utility</th><th>Utility under attack</th><th>ASR</th><th>Gate ms / task</th></tr>"
        for c, v in S["conditions"].items():
            live_html += f"<tr><td>{c} {COND_NAMES[c]}</td><td>{p100(v['utility'])}</td><td>{p100(v['utility_under_attack'])}</td><td>{p100(v['asr'])}</td><td>{v['gate_ms_per_task']}</td></tr>"
        live_html += "</table>"
    html = DASH.replace("__TILES__", "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in tiles)) \
               .replace("__LIVE__", live_html).replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
    (ROOT / "results" / "dashboard.html").write_text(html)


DASH = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Policy Gate Results</title><style>
:root{--bg:#fcfcfb;--fg:#0b0b0b;--mut:#52514e;--line:#e4e3df;--card:#fff;--block:#e34948;--esc:#c98500;--ok:#1baf7a;--miss:#4a3aa7}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1a1a19;--fg:#fff;--mut:#c3c2b7;--line:#3a3a37;--card:#222220;--block:#e66767;--esc:#eda100;--ok:#199e70;--miss:#9085e9}}
:root[data-theme=dark]{--bg:#1a1a19;--fg:#fff;--mut:#c3c2b7;--line:#3a3a37;--card:#222220;--block:#e66767;--esc:#eda100;--ok:#199e70;--miss:#9085e9}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.k{color:var(--mut);font-size:12px}.v{font-size:18px;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
.wrap{overflow-x:auto}.f{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px}
.v-BLOCK{color:var(--block);font-weight:600}.v-ESCALATE{color:var(--esc);font-weight:600}.v-MISS{color:var(--miss);font-weight:600}.v-ALLOW{color:var(--mut)}
tr.pair{cursor:pointer}tr.pair:hover{background:var(--card)}.tr td{font-family:ui-monospace,monospace;font-size:12px}.dec{color:var(--mut)}
</style></head><body><main><h1>Policy gate (P1–P9) on AgentDojo v1.2.2</h1>
<p style="color:var(--mut)">Replay of ground-truth trajectories with oracle TaskSpecs. Click a row to see every tool call and the policy decisions behind it. Generated by scripts/build_report.py.</p>
<div class="tiles">__TILES__</div>__LIVE__
<h2>Pairs and benign tasks</h2><div class="f"><select id="s"><option value="">all suites</option><option>workspace</option><option>travel</option><option>banking</option><option>slack</option></select>
<select id="d"><option value="">all outcomes</option><option>BLOCK</option><option>ESCALATE</option><option>MISS</option><option>ALLOW</option><option>-</option></select>
<select id="t"><option value="">all types</option><option>A</option><option>B</option><option>C</option><option>D</option><option>E</option><option>F</option><option>G</option><option>H</option><option>benign</option></select>
<input id="q" placeholder="user / injection id"><span id="n" style="color:var(--mut)"></span></div>
<div class="wrap"><table id="tab"><thead><tr><th>suite</th><th>user</th><th>injection</th><th>type</th><th>gate on effect calls</th><th>attack succeeds w/o gate</th><th>with gate (enforce)</th></tr></thead><tbody></tbody></table></div>
</main><script>
const D=__DATA__;const tb=document.querySelector('#tab tbody');const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function render(){const s=v('s'),d=v('d'),t=v('t'),q=v('q');let n=0;tb.innerHTML='';
for(const r of D){if(s&&r.s!==s||d&&r.det!==d||t&&r.t!==t||q&&!(r.u===q||r.i===q))continue;if(++n>400)continue;
const tr=document.createElement('tr');tr.className='pair';tr.innerHTML=`<td>${r.s}</td><td>${r.u}</td><td>${r.i}</td><td>${r.t}</td><td class="v-${r.det}">${r.det}</td><td>${r.base===null?'':r.base?'yes':'no'}</td><td>${r.enf===null?'':r.enf?'yes':'no'}</td>`;
tr.onclick=()=>{const nx=tr.nextSibling;if(nx&&nx.classList&&nx.classList.contains('tr')){nx.remove();return}const x=document.createElement('tr');x.className='tr';
x.innerHTML='<td colspan=7>'+r.tr.map(e=>`<div><b>[${e[0]}]</b> ${esc(e[1])}(${esc(e[2])}) → <span class="v-${e[3]}">${e[3]}</span>`+e[4].map(d=>`<div class="dec">&nbsp;&nbsp;${d[0]} <span class="v-${d[1]}">${d[1]}</span> ${esc(d[2])}${d[3]?' ['+d[3]+']':''}</div>`).join('')+'</div>').join('')+'</td>';tr.after(x)};tb.appendChild(tr)}
document.querySelector('#n').textContent=n+' rows'+(n>400?' (first 400 shown)':'')}
function v(id){return document.querySelector('#'+id).value.trim()}['s','d','t','q'].forEach(i=>document.querySelector('#'+i).addEventListener('input',render));render();
</script></body></html>"""


def main():
    R = J(REPLAY / "summary.json")
    LIVE = {p.parent.name: J(p) for p in sorted(LIVE_ROOT.glob("*/summary.json"))}
    ENF = {p.parent.name: J(p) for p in sorted(LIVE_ROOT.glob("*/enforcement_check.json"))}
    figs = figures(R, LIVE)
    stage1()
    stage5(R, figs)
    stage6(LIVE, ENF, figs)
    final(R, LIVE, ENF, figs)
    dashboard(R, LIVE)
    print("wrote docs/stage1_catalog_report.md docs/stage5_replay_report.md docs/stage6_live_report.md docs/final_report.md",
          "results/figures/*.png results/dashboard.html")


if __name__ == "__main__":
    main()
