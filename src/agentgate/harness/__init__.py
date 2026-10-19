"""프레임워크 독립 harness: session마다 AgentDojo 시나리오 하나와 Gate를 두고 그 위에서 임의의 프레임워크를 돌린다.

- `session.Session`        AgentDojo 환경 + 인젝션 + PolicyGateRuntime + TaskSpec + 채점
- `bridge.GateToolBridge`  프레임워크가 툴에 닿는 유일한 문: tool_schemas() / invoke() / decide()
- `events`                 어댑터·대시보드·Wrapper client가 함께 쓰는 정규화 이벤트 스키마
- `models.ModelSpec`       제공자 독립 모델 선택, 프레임워크별로 변환
- `registry`               프레임워크 어댑터(내장, config/frameworks.yaml, entry point)
- `service`                HTTP 상의 Wrapper 규약 v1(/v1/sessions/...)
"""

CONTRACT_VERSION = "v1"
