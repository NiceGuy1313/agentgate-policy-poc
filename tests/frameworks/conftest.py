"""프레임워크 테스트 공통 설정: 정책 Gate의 ToolSpec 추출기를 빈 것으로 바꿔 LLM 없이 돌린다."""

import pytest

from agentgate.harness.conformance import offline_toolspec


@pytest.fixture(autouse=True)
def _offline_toolspec():
    with offline_toolspec():
        yield
