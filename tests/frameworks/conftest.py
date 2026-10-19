"""Harness tests run LLM-free: the policy gate's ToolSpec extractor is replaced by an empty one."""

import pytest

from agentgate.harness.conformance import offline_toolspec


@pytest.fixture(autouse=True)
def _offline_toolspec():
    with offline_toolspec():
        yield
