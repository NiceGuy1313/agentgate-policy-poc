"""버전별 폴더(v2_detector_trusted_llm/, v3_trusted_llm_policy_doc/)의 코드 사본을 원본에서 다시 만든다.

폴더의 코드는 읽기용 사본이다. 실행·테스트는 저장소 루트의 원본으로 한다.
  - copy: 그 버전에만 쓰는 파일을 같은 상대 경로로 복사
  - excerpts: 두 버전이 함께 쓰는 파일(rules.py 등)에서 그 버전의 정의만 원문 그대로 발췌
README.md와 RESULTS.md는 건드리지 않는다(RESULTS.md는 build_report.py가 만든다).

사용법: python scripts/reporting/sync_version_folders.py [--check]
  --check: 파일을 쓰지 않고 사본이 원본과 다르면 종료 코드 1 (tests/test_version_folders.py가 사용)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANAGED_SUFFIXES = {".py", ".yaml", ".md"}
UNMANAGED = {"README.md", "RESULTS.md"}

FOLDERS = {
    "v2_detector_trusted_llm": {
        "copy": [
            "src/agentgate/detector/__init__.py", "src/agentgate/detector/base.py",
            "src/agentgate/detector/chunking.py", "src/agentgate/detector/hf.py",
            "src/agentgate/llm/trusted_judge.py",
            "config/detector.yaml", "config/user_policy.yaml",
            "prompts/trusted_judge.md", "prompts/trusted_judge_system.md",
            "scripts/analysis/detector_format_probe.py",
            "tests/policy/test_p0.py", "tests/policy/test_p10.py", "tests/test_trusted_judge.py",
        ],
        "excerpts": {
            "src/agentgate/policy/rules_v2_excerpt.py": (
                "src/agentgate/policy/rules.py", ["UserFixedPolicy", "DetectorLabelPolicy", "_detector_candidates"]),
        },
    },
    "v3_trusted_llm_policy_doc": {
        "copy": [
            "config/policy_rules_v3.yaml", "config/policy_rules_v31.yaml", "config/user_policy.yaml",
            "src/agentgate/llm/trusted_judge_v3.py", "src/agentgate/llm/toolspec_extractor.py",
            "src/agentgate/llm/taskspec_extractor.py",
            "prompts/extract_toolspec.md", "prompts/extract_taskspec.md",
            "scripts/analysis/analyze_atr_corpora.py", "scripts/evaluation/toolspec_evaluation.py",
            "tests/policy/test_p11_p12.py", "tests/policy/test_v31.py",
        ],
        "excerpts": {
            "src/agentgate/policy/rules_v3_excerpt.py": (
                "src/agentgate/policy/rules.py",
                ["_v31", "_delegated_request_match", "_unrequested", "_requested_egress_to_known_destination",
                 "InstructedActionPolicy", "RequestedActionPolicy", "UserFixedPolicy"]),
            "src/agentgate/provenance_v3_excerpt.py": (
                "src/agentgate/provenance.py", ["is_delegated_source_response", "ValueIndex.instructed_requests"]),
        },
    },
}


def _definition_source(text: str, tree: ast.Module, name: str) -> str:
    """Source of a top-level def/class, or of 'Class.method' (dedented)."""
    owner, _, member = name.partition(".")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == owner:
            if member:
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == member:
                        node = child
                        break
                else:
                    raise KeyError(name)
            start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
            lines = text.splitlines()[start:node.end_lineno]
            indent = len(lines[0]) - len(lines[0].lstrip())
            return "\n".join(line[indent:] for line in lines)
    raise KeyError(name)


def render_excerpt(source: str, names: list[str]) -> str:
    text = (PROJECT_ROOT / source).read_text()
    tree = ast.parse(text)
    header = (f'"""발췌: {source} — 이 버전의 정의만 원문 그대로 옮긴 읽기용 사본이다.\n'
              f'import와 공유 helper는 원본에 있다. 생성: scripts/reporting/sync_version_folders.py\n"""\n')
    return header + "\n\n\n".join(f"# {source} :: {name}\n" + _definition_source(text, tree, name)
                                  for name in names) + "\n"


def expected_files() -> dict[Path, str]:
    files = {}
    for folder, spec in FOLDERS.items():
        for rel in spec["copy"]:
            files[PROJECT_ROOT / folder / rel] = (PROJECT_ROOT / rel).read_text()
        for rel, (source, names) in spec["excerpts"].items():
            files[PROJECT_ROOT / folder / rel] = render_excerpt(source, names)
    return files


def managed_existing() -> set[Path]:
    return {p for folder in FOLDERS for p in (PROJECT_ROOT / folder).rglob("*")
            if p.is_file() and p.suffix in MANAGED_SUFFIXES and p.name not in UNMANAGED
            and "__pycache__" not in p.parts}


def problems() -> list[str]:
    expected = expected_files()
    out = [f"stale: {p.relative_to(PROJECT_ROOT)}" for p in sorted(managed_existing() - set(expected))]
    out += [f"out of sync: {p.relative_to(PROJECT_ROOT)}" for p, body in expected.items()
            if not p.exists() or p.read_text() != body]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        issues = problems()
        print("\n".join(issues) or "version folders in sync")
        sys.exit(1 if issues else 0)
    expected = expected_files()
    for path in managed_existing() - set(expected):
        path.unlink()
    for path, body in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    for folder in FOLDERS:   # drop directories left empty by removed copies
        for d in sorted((PROJECT_ROOT / folder).rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    print(f"wrote {len(expected)} files")


if __name__ == "__main__":
    main()
