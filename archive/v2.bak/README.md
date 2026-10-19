# v2 보관

2026-10-19 미팅 결정으로 v2(IPI detector + Trusted LLM) 실험을 중단하고 코드·설정·프롬프트·스크립트·테스트·결과를 이 디렉터리에 보관한다. 경로는 저장소 루트 기준 상대 경로를 그대로 따른다(`src/agentgate/v2/`, `config/v2/`, `prompts/v2/`, `scripts/v2/`, `tests/v2/`, `results/v2/`). P0 사용자 고정 설정(`src/agentgate/gate/user_policy.py`)은 v2만 쓰므로 함께 옮겼다. 이 코드는 현재 패키지와 연결되어 있지 않아 그대로는 실행되지 않는다. 결과는 `results/v2/RESULTS.md`에 있다.
