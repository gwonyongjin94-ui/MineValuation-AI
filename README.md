# MineValuation-AI

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

### 가장 빠른 사용법 (서버 없이, 터미널에서 바로)

서버를 안 띄우고 티커/현재가만 넣어서 바로 결과를 보고 싶으면:
```bash
python scripts/analyze.py AAPL 230
```
가정치는 항상 `DEFAULT_ASSUMPTIONS`(API 기본값과 동일: growth 5%, discount 9%,
terminal 2.5%, tax 21%)를 쓴다 - 매번 값을 넣을 필요 없음. 정성분석 옵션은
없지만 comps는 기본으로 켜져 있어서, 마지막에 DCF(FCFF)/DCF(Owner Earnings)/
Comps 세 방법의 범위를 터미널 바 차트로 같이 보여준다:
```
                      Valuation Range

DCF (FCFF)                  ├──────────────────────────────────────────┤
                            $261                                       $464

DCF (Owner Earnings)  ├─────────────────────────────────────┤
                      $232                                  $414

Comps                  ├─────┤
                       $236  $264

Overlap                     ├─┤
                            $261~$264
```
세 방법이 하나도 안 겹치면(예: NVDA - DCF는 $23~$52, Comps는 $139~$511)
`Overlap` 바 대신 "no overlap" 경고가 뜬다 - 억지로 하나로 합치지 않고 방법들이
의견이 갈린다는 걸 그대로 보여줌.

`--10k`/`--earnings-call`을 주면 정성 리스크 추출까지 이 스크립트 안에서
같이 돈다(서버 안 띄우고):
```bash
python scripts/analyze.py AAPL 230 --10k
python scripts/analyze.py AAPL 230 --earnings-call transcript.txt
```
`--earnings-call`은 어닝콜 텍스트를 붙여넣은 `.txt` 파일 경로를 받는다(터미널
인자로 긴 텍스트를 직접 넣긴 비현실적이라서). 둘 다 API와 똑같이
`ANTHROPIC_API_KEY`가 `.env`에 있어야 하고(`app.config.get_settings()`로
API와 같은 경로로 읽음), 호출당 비용이 실제로 발생한다 - 키가 없으면
LLM을 호출하기 전에 바로 에러로 알려준다. `cross_validate`/`include_sentiment`
같은 나머지 정성분석 옵션은 이 스크립트에 없음 - 그게 필요하면 서버를
띄워서 API를 직접 호출할 것(아래 "정성분석 포함" 참고).

### 기본 사용 (정량만, 무료) - HTTP API로

```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "market_price": 230}'
```

모든 응답에는 `fundamental_growth_estimate`도 항상 같이 들어있다 - 회사의
실제 재투자율(reinvestment rate) x ROIC로 계산한 "참고용" 성장률(Damodaran
방법론)로, 요청에서 넣은 `fcff_growth_rate` 가정치를 절대 대체하지 않고
나란히 보여주기만 한다. 별도 opt-in 없이 항상 계산됨 (SEC 데이터만 쓰고 LLM
비용도 없어서). 자세한 공식은 [VALUATION_METHOD.md](docs/VALUATION_METHOD.md)
참고.

`owner_earnings_estimate`(Buffett의 Owner Earnings 기반 DCF)도 마찬가지로
항상 계산된다 - 유지보수 capex를 정확히 나눌 방법이 없어서(SEC 태그에 없음)
"capex 전부를 유지보수로 본 보수적 값" ~ "D&A만 유지보수로 본 낙관적 값"
범위로 나온다. 그리고 `valuation_consensus`가 DCF(FCFF)/Owner Earnings DCF/
(요청 시) Comps의 범위를 전부 모아서 **교집합**을 계산한다 - `overlap_low`/
`overlap_high`가 그 값이고, 방법들이 안 겹치면 `null`+경고로 정직하게 표시.

### 정성분석 포함 (ANTHROPIC_API_KEY 필요, 호출당 비용 발생)
```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "AAPL",
    "market_price": 230,
    "analyze_10k": true,
    "include_sentiment": true,
    "earnings_call_text": "선택: 어닝콜 텍스트 직접 붙여넣기",
    "cross_validate": true
  }'
```

`cross_validate: true`는 정성 리스크 추출을 Claude 두 모델(haiku/sonnet)로
각각 돌려서 결과가 갈리면 `disagreement` 경고를 남긴다 - LLM 호출 비용이
두 배로 든다. 실데이터로는 `claude-sonnet-5`가 이 프로젝트가 실제로 다루는
6~7만 토큰대 10-K에서 재현 가능하게 실패해서, 현재로선 사실상 "Haiku 결과 +
Sonnet 실패 플래그"로 동작한다 - 자세한 내용은
[LIMITATIONS.md](docs/LIMITATIONS.md)와
[DATA_SPIKE_NOTES.md](docs/DATA_SPIKE_NOTES.md) 참고.

### 개인 Anthropic 키로 호출하기 (서버 키 대신)

서버의 `ANTHROPIC_API_KEY`를 쓰지 않고 요청마다 직접 키를 넣을 수도 있다
(멀티테넌트 배포에서 호출자가 자기 LLM 비용을 직접 부담하는 경우 등):
```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "AAPL",
    "market_price": 230,
    "analyze_10k": true,
    "anthropic_api_key": "sk-ant-..."
  }'
```
이 키는 그 요청 하나를 처리하는 데만 쓰이고 응답에도 절대 다시 나타나지
않는다 - `anthropic_api_key` 필드는 pydantic `SecretStr`라 실수로
로그/에러메시지에 찍히는 걸 막아주고, 애초에 이 프로젝트는 DB도 없고
요청을 파일이나 로그에 남기는 미들웨어도 없어서 요청이 끝나면 이 키도,
`earnings_call_text`에 붙여넣은 텍스트(개인정보가 섞여 있을 수 있음)도
메모리에서 같이 사라진다. 자세한 내용은
[ARCHITECTURE.md](docs/ARCHITECTURE.md)의 "요청 데이터 보관 정책" 참고.

### WACC(할인율) 참고 추정치 - 실제 시장데이터 사용

```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "market_price": 230, "compute_wacc": true}'
```
`compute_wacc: true`면 응답에 `wacc_estimate`가 추가된다 - 업종평균 베타
(SIC 코드 기반) + 실시간으로 가져온 10년 국채금리(FRED) + 이자보상배율 기반
신용등급으로 계산한 회사별 WACC 참고치. `fundamental_growth_estimate`와
같은 원칙으로, `assumptions.discount_rate`(모든 종목에 동일 적용되는 값)를
절대 대체하지 않고 옆에만 표시된다. 베타/ERP 테이블은 Damodaran이 공개한
데이터를 문서화된 상수로 박아둔 거라 실시간은 아니고, 무위험금리(FRED)만
실제로 매번 가져옴. 한계는 [LIMITATIONS.md](docs/LIMITATIONS.md), 계산식은
[VALUATION_METHOD.md](docs/VALUATION_METHOD.md) 참고.

### Comps(비교기업 배수) 참고 추정치

```bash
curl -X POST localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "market_price": 230, "compute_comps": true}'
```
`compute_comps: true`면 응답에 `comps_estimate`가 추가된다 - JPM이 실제로 쓰는
방법론 중 DCF 말고 다른 한 축(상대가치). 같은 업종(SIC 기반, 큐레이션된
peer 리스트) 대형주들의 EV/EBITDA·EV/Revenue·P/E 중앙값을 이 회사 자체
지표에 적용해서 "비슷한 회사들 배수로 치면 얼마"를 계산한다. peer 가격은
Yahoo Finance에서 실시간으로 가져옴 - 비공식 API라 SEC/FRED보다 안정성
보장이 약하다는 점은 감안. 역시 `margin_of_safety`를 절대 대체하지 않고
옆에만 표시됨. peer가 하나뿐인 업종도 있어서(예: HD의 유일한 peer는
Lowe's) 그런 경우 "low-confidence sample" 경고가 붙는다 - 계산 자체는
숨기지 않고 항상 보여줌. 한계는 [LIMITATIONS.md](docs/LIMITATIONS.md)의
"Comps layer", 계산식은 [VALUATION_METHOD.md](docs/VALUATION_METHOD.md)의
"5. Comps" 참고.

### 20-F(IFRS) 외국계 발행사 지원 - NVO(Novo Nordisk) 등

```bash
python scripts/analyze.py NVO 48.69
```
미국 국내 상장사(10-K)뿐 아니라 20-F를 제출하는 외국계 발행사(예:
덴마크 회사 Novo Nordisk)도 별도 옵션 없이 자동으로 지원된다. IFRS
태그 매핑(`ifrs-full` 네임스페이스)으로 재무제표를 읽고, 회사가 보고하는
현지 통화(NVO는 DKK)를 실시간 환율(Yahoo Finance)로 USD 변환한 뒤
DCF/Owner Earnings/Comps 전부 그대로 계산한다 - `market_price`가 항상
USD로 취급되기 때문에 변환 없이는 결과가 6~7배씩 어긋난다. 현재
NVO 하나로만 실데이터 검증됐고, 다른 20-F 발행사에서 같은 IFRS 태그가
그대로 쓰이는지는 확인 전이라 태그가 안 맞으면 해당 항목만 `None`+경고로
빠진다(추측해서 채우지 않음). 한계는
[LIMITATIONS.md](docs/LIMITATIONS.md), 계산식은
[VALUATION_METHOD.md](docs/VALUATION_METHOD.md)의 "8. IFRS / non-USD
filers" 참고.

## 테스트

```bash
pytest                              # 전부 (실제 SEC/네트워크 필요)
pytest -m "not llm"                 # 유료 LLM 호출 제외
pytest -m "not llm and not model"   # LLM + FinBERT(무거운 로컬 모델) 둘 다 제외 - CI가 이 조합으로 돈다
ruff check .
```
