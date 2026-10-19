"""판별기 입력 변환. 모델마다 학습한 입력 형태에 맞춘다(config/v2/detector.yaml의 input_format).

raw    에이전트가 받는 툴 응답 문자열 그대로(YAML). 툴·API 출력을 학습한 모델(horizon-base)
plain  레코드별 평문: 키·YAML 기호를 빼고 문자열 값만 줄 단위로, 레코드 사이는 빈 줄. 사용자 프롬프트 형태로
       학습한 모델(PIGuard)용. 값은 원문 그대로 남으므로 P10이 값의 위치를 찾을 수 있다.
"""

from __future__ import annotations

from typing import Any


def _values(obj: Any) -> list[str]:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return [v for item in obj.values() for v in _values(item)]
    if isinstance(obj, (list, tuple, set)):
        return [v for item in obj for v in _values(item)]
    if obj is None or isinstance(obj, bool):
        return []
    text = str(obj).strip()
    return [text] if text else []


def to_plain_text(raw_output: Any, output_text: str) -> str:
    if hasattr(raw_output, "model_dump"):
        raw_output = raw_output.model_dump(mode="json")
    if isinstance(raw_output, (list, tuple)):
        records = ["\n".join(_values(record)) for record in raw_output]
        return "\n\n".join(r for r in records if r)
    if isinstance(raw_output, dict):
        # dict of records keyed by id (e.g. files, channels): one record per value
        if raw_output and all(isinstance(v, (dict, list)) or hasattr(v, "model_dump") for v in raw_output.values()):
            return "\n\n".join("\n".join(_values(v)) for v in raw_output.values())
        return "\n".join(_values(raw_output))
    if isinstance(raw_output, str):
        return raw_output
    return "\n".join(_values(raw_output)) or output_text


def prepare_input(input_format: str, raw_output: Any, output_text: str) -> str:
    if input_format == "raw":
        return output_text
    if input_format == "plain":
        return to_plain_text(raw_output, output_text)
    raise ValueError(f"unknown detector input_format {input_format!r}")
