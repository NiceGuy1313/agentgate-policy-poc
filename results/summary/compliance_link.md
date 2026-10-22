# 정책 준수율과 ASR·Utility의 관계

> `scripts/analysis/compliance_review.py link`가 생성한다. 손으로 고치지 않는다.
> 예상값은 준수율 판정 사례(80개 작업)에서, 실측값은 `frameworks.json`(388개 작업, live 실행)에서 읽는다.

## 1. 공격 작업(ASR)

| 구분 | smolagents LLM Judge | smolagents Static Checker | mini-swe-agent LLM Judge | mini-swe-agent Static Checker |
|---|---|---|---|---|
| 공격 작업 수 | 60 | 60 | 60 | 60 |
| 위반 없음 | 16 | 16 | 42 | 42 |
| 공격 차단 | 41 | 43 | 17 | 17 |
| 위반 허용 | 2 | 0 | 1 | 1 |
| 답변 위반 | 1 | 1 | 0 | 0 |
| **예상 잔여 ASR** | 5.0% (3/60) | 1.7% (1/60) | 1.7% (1/60) | 1.7% (1/60) |
| 실측 ASR | 3.1% (9/291) | 2.4% (7/291) | 0.7% (2/291) | 1.0% (3/291) |

## 2. 정상 작업(Utility)

| 구분 | smolagents LLM Judge | smolagents Static Checker | mini-swe-agent LLM Judge | mini-swe-agent Static Checker |
|---|---|---|---|---|
| 정상 작업 수 | 20 | 20 | 20 | 20 |
| 판정 대상 없음 | 7 | 7 | 6 | 6 |
| 전체 허용 | 11 | 10 | 9 | 10 |
| 오차단 발생 | 2 | 3 | 5 | 4 |
| **예상 Utility 손실** | 10.0% (2/20) | 15.0% (3/20) | 25.0% (5/20) | 20.0% (4/20) |
| 실측 Utility 하락 | -7.2%p | -17.5%p | -15.5%p | -14.6%p |
| 실측 Utility | 74.2% (72/97) | 63.9% (62/97) | 68.0% (66/97) | 68.8% (66/96) |

