# SafetyMargin-AI

SEC EDGAR 공식 데이터를 수집해 재무 데이터를 표준화하고, FCFF/DCF 기반 내재가치와 안전마진을 계산해 API로 반환하는 시스템.

## V1 범위

SEC EDGAR(`submissions`, XBRL `companyfacts`)만 데이터 소스로 사용한다. LLM, RAG, 실시간 뉴스, 자동매매, UI는 V1 범위 밖이다.

## 개발 순서

Phase 0(현재) → SEC Client → Data Spike → Financial Schema → Normalizer → Metrics → FCFF/DCF → Margin of Safety → REST API → Tests/CI → Docs.

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

```bash
pytest
ruff check .
```
