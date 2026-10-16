"""버전별 폴더의 코드 사본이 원본과 같은지 확인한다(원본을 고치면 sync_version_folders.py를 다시 실행)."""

import importlib.util

from agentgate.config import PROJECT_ROOT


def test_version_folders_match_sources():
    path = PROJECT_ROOT / "scripts/reporting/sync_version_folders.py"
    spec = importlib.util.spec_from_file_location("sync_version_folders", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.problems() == []
