# SafetyMargin-AI

SEC EDGAR 공식 데이터를 수집해 재무 데이터를 표준화하고, FCFF/DCF 기반 내재가치와
안전마진을 계산해 API로 반환하는 시스템. V2에서는 10-K 본문/사용자가 입력한
어닝콜 텍스트에서 LLM 기반 정성 리스크와 FinBERT 감성분석을 뽑아 정량 결과와
나란히 제공한다(숫자 하나로 합치지 않음 - 이유는 `docs/VALUATION_METHOD.md` 참고).

## 상태: V1 완료 + V2(정성분석) 진행 중

Phase 0~9(V1) 전부 완료. V2는 10-K/10-Q 텍스트 수집, LLM 정성 리스크 추출,
FinBERT 감성분석까지 `/api/v1/analyze`에 연결되어 있다.

문서:
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - 레이어 구조, 데이터 흐름
- [DATA_MODEL.md](docs/DATA_MODEL.md) - XBRL 태그 매핑, fact 선택 규칙, filing 텍스트 처리
- [VALUATION_METHOD.md](docs/VALUATION_METHOD.md) - FCFF/DCF/MOS 계산식, 정성분석과의 관계
- [LIMITATIONS.md](docs/LIMITATIONS.md) - 알려진 한계
- [DATA_SPIKE_NOTES.md](docs/DATA_SPIKE_NOTES.md) - 실데이터 관찰 기록(V1 XBRL + V2 filing HTML/FinBERT)

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # SEC_USER_AGENT를 실제 연락처로 수정
uvicorn app.main:app --reload
```

정성분석(V2)까지 쓰려면:
```bash
# .env에 ANTHROPIC_API_KEY 추가 (analyze_10k / earnings_call_text용)
pip install -e ".[dev,sentiment]"  # include_sentiment(FinBERT)용, ~1GB
```

### 기본 사용 (정량만, 무료)
```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "market_price": 230}'
```

### 정성분석 포함 (ANTHROPIC_API_KEY 필요, 호출당 비용 발생)
```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "AAPL",
    "market_price": 230,
    "analyze_10k": true,
    "include_sentiment": true,
    "earnings_call_text": "선택: 어닝콜 텍스트 직접 붙여넣기"
  }'
```

## 테스트

```bash
pytest                              # 전부 (실제 SEC/네트워크 필요)
pytest -m "not llm"                 # 유료 LLM 호출 제외
pytest -m "not llm and not model"   # LLM + FinBERT(무거운 로컬 모델) 둘 다 제외 - CI가 이 조합으로 돈다
ruff check .
```
