# SafetyMargin-AI

SEC EDGAR 공식 데이터를 수집해 재무 데이터를 표준화하고, FCFF/DCF 기반 내재가치와 안전마진을 계산해 API로 반환하는 시스템.

## V1 범위

SEC EDGAR(`submissions`, XBRL `companyfacts`)만 데이터 소스로 사용한다. LLM, RAG, 실시간 뉴스, 자동매매, UI는 V1 범위 밖이다.

## 상태: V1 완료

Phase 0~9 전부 완료. `POST /api/v1/analyze`에 ticker + market_price를 보내면
SEC 실데이터 기반 FCFF/DCF 내재가치와 안전마진(range)을 반환한다.

문서:
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - 레이어 구조, 데이터 흐름
- [DATA_MODEL.md](docs/DATA_MODEL.md) - XBRL 태그 매핑, fact 선택 규칙
- [VALUATION_METHOD.md](docs/VALUATION_METHOD.md) - FCFF/DCF/MOS 계산식
- [LIMITATIONS.md](docs/LIMITATIONS.md) - 알려진 한계
- [DATA_SPIKE_NOTES.md](docs/DATA_SPIKE_NOTES.md) - Phase 1.5 실데이터 관찰 기록

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # SEC_USER_AGENT를 실제 연락처로 수정
uvicorn app.main:app --reload
```

```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "market_price": 230}'
```

```bash
pytest                    # unit + integration(실제 SEC 호출 포함)
pytest -m "not integration"  # unit만, 네트워크 불필요
ruff check .
```
