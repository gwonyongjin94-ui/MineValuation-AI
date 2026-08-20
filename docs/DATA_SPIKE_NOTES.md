# Phase 1.5 Data Spike Notes

`scripts/spike_companyfacts.py`로 GOOGL / MSFT / AAPL / JPM / HBB(10-K/A 사례)의 실제
`companyfacts`를 관찰한 결과. Phase 2 스키마 설계의 입력 자료다.

## 1. Revenue 태그 마이그레이션은 2018년 ASC 606 한 번으로 끝난 일이 아니다

MSFT, AAPL 모두 다음 순서로 태그가 바뀐 이력이 있다:

```
SalesRevenueNet (~2018 이전)
    → Revenues (2018 전환기, 1개 연도)
    → RevenueFromContractWithCustomerExcludingAssessedTax (ASC 606 정착 후)
```

그런데 **GOOGL은 FY2025 10-K(2026-02-05 제출, accn 0001652044-26-000018)에서
다시 `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues`로 되돌아갔다.**
즉 태그 변경은 "2018년 한 번의 일회성 이벤트"가 아니라 기업이 임의 시점에 또 바꿀 수 있는
현재진행형 리스크다. fallback chain은 계속 유지보수해야 하는 대상으로 설계해야 한다.

## 2. 같은 filing(accn) 안에서는 태그를 하나만 쓴다

관찰된 3개 정상기업(GOOGL/MSFT/AAPL) 전부, 한 번의 10-K(하나의 accn) 안에서는 비교연도
전체(보통 2~3개 연도)를 **동일한 태그 하나**로 일관되게 보고한다. 태그가 필요 시
바뀌는 시점은 "새 10-K accn"의 경계뿐이었다.

→ 설계 반영: fallback chain은 **filing(accn) 단위로 태그 하나를 확정**한 뒤 그 안의
모든 기간을 그 태그로 읽어야 한다. "필요한 연도마다 다른 태그를 섞어 찾는" 방식은
관찰된 패턴과 맞지 않는다.

## 3. 같은 경제적 기간이 여러 accn/fy에 재등장하고, 값이 실제로 바뀔 수 있다

10-K는 보통 최근 2~3개 연도를 비교표시하므로, 예를 들어 AAPL FY2024 매출은:
- fy=2024 (2024-11-01 제출 10-K, 당해년도로)
- fy=2025 (2025-10-31 제출 10-K, 전년도 비교값으로) 로 **두 번** 나타난다.

AAPL의 경우 두 값이 동일했지만, **HBB는 실제로 값이 달랐다:**

| 시점 | form | filed | FY2019 매출 |
|---|---|---|---|
| 원본 | 10-K | 2020-02-26 | $612,843,000 |
| 정정 | 10-K/A | 2020-07-24 | $611,786,000 |

→ "필요한 fy의 값"을 고를 때 단순히 최신 accn을 집는 것과 "원래 보고된 그대로"를 집는 것은
다른 선택이다. `accn` + `filed`를 반드시 스키마에 남기고, **"as reported"(최초 filed 기준)와
"as restated"(최신 filed 기준)를 구분해서 노출**해야 한다 — 이게 이전에 합의한
`filed_date ≤ as_of_date` 원칙이 실제로 필요해지는 지점이다.

## 4. 10-K/A는 연간 수치만 고치지 않는다 (열린 이슈)

HBB의 10-K/A(accn 0001709164-20-000032)는 연간 수치뿐 아니라 직전 여러 분기의
"Selected Quarterly Financial Data"까지 정정해서 같은 태그 아래 분기 단위 fact를
대량으로 함께 실었다. 초기 관찰 스크립트가 `fp` 필터 없이 이 필터링을 했더니
같은 `end` 날짜에 값이 다른 항목이 여러 개 나와서 "중복 키"처럼 보였는데, 실제로는
연간(FY)과 분기(Q1~Q4) 항목이 섞여 있었을 뿐이었다(`start`가 다름).

→ 교훈: `(tag, unit, end, fy, form)`만으로 fact를 선택하면 안 되고, 반드시
**`fp == "FY"` 그리고 `start`~`end` 기간이 ~365일인지**까지 같이 확인해야 한다.
AAPL 케이스(`revenue_tag_history`, fp 필터 적용)는 깨끗했던 반면 HBB 케이스는
필터를 안 걸었더니 바로 지저분해졌다 — Phase 2 정규화 로직에서 이 필터를 생략하면
안 된다는 게 직접 재현됐다.

## 5. capex/D&A는 revenue 못지않게 태그가 갈린다

관찰된 후보 태그 전부 실사용 중이었다:
- D&A: `DepreciationDepletionAndAmortization`, `DepreciationAmortizationAndAccretionNet`,
  `DepreciationAndAmortization`, `Depreciation` — AAPL은 이 넷 중 세 개를 연도별로 다르게 씀
- CapEx: `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets`

→ revenue와 동일한 fallback chain 설계를 그대로 적용하면 된다. 별도 로직 불필요.

## 6. JPM(은행)은 이론이 아니라 데이터로 FCFF 부적합이 확인됨

JPM `companyfacts`에서 우리 관심 concept 중:
- `OperatingIncomeLoss`: **없음**
- `AssetsCurrent` / `LiabilitiesCurrent`: **없음** (은행은 대차대조표를 유동/비유동으로 분류하지 않음)
- capex 태그 후보 3개: **전부 없음**
- `Revenues`(총괄 tag)와 `NetIncomeLoss`는 존재
- `NetCashProvidedByUsedInOperatingActivities`는 존재하지만 **-$42B, -$147B로 대규모 음수**
  (예금/트레이딩 흐름이 지배적이라 일반기업의 "영업활동현금흐름"과 의미가 다름)

→ `financial_company` 판정을 SIC 코드(`sicDescription: "National Commercial Banks"`)로
게이트하는 설계가 이론적 우려가 아니라 실제 정규화 단계에서 막힐 수밖에 없다는 게 확인됐다.
Phase 2에서 SIC 기반 `valuation_category` 분류를 정규화보다 먼저 태워야 한다
(정규화 시도 후 실패하는 게 아니라, 애초에 정규화 대상 concept 자체가 없다).

## 7. NWC 관련 태그는 표준기업에서는 안정적으로 존재

GOOGL/MSFT/AAPL/HBB 전부 `AssetsCurrent`, `LiabilitiesCurrent`,
`CashAndCashEquivalentsAtCarryingValue`가 존재. 다만 short-term debt 태그는
`ShortTermBorrowings` / `DebtCurrent` / `LongTermDebtCurrent`로 기업마다 다르다 —
이전에 합의한 "operating NWC = (유동자산-현금) - (유동부채-단기부채)" 계산을 위해
이 셋도 fallback chain에 포함해야 한다.

## 8. Provenance 필드는 전부 raw JSON에 이미 존재

`accn`, `fy`, `fp`, `form`, `filed`, `start`, `end`가 각 fact entry에 기본으로 포함되어
있음을 5개 기업 전부에서 확인. 스키마에 담는 데 추가 비용 없음.

## Phase 2로 넘기는 결정 사항

1. Fallback chain은 **filing(accn) 단위**로 태그를 확정한다.
2. Fact 선택은 `fp == "FY"` + `start~end ≈ 365일` 두 조건을 모두 건다.
3. 같은 기간이 여러 번 나오면 **"as reported"(가장 이른 filed)** 를 기본으로 쓰고,
   이후 restated 버전이 있으면 별도로 노출한다(자동으로 최신값에 덮어쓰지 않는다).
4. `valuation_category` 분류(SIC 기반)는 정규화 파이프라인의 맨 앞단에서 실행한다.
5. D&A/CapEx/short-term debt 모두 revenue와 동일한 fallback chain 패턴을 쓴다.

---

## V2 — 10-K/10-Q 본문 HTML 구조 spike

V2에서 Risk Factors/MD&A를 자동으로 섹션 단위로 잘라낼 수 있는지 확인하기 위해
AAPL과 MSFT의 최신 10-K 원문 HTML(inline XBRL)을 직접 받아 비교했다.

**AAPL (Workiva 생성 문서)**: 목차가 `<a href="#i719388...52">Item 1A.</a>` 형태로
실제 섹션의 `id="i719388...52"` 앵커에 하이퍼링크돼 있다. Item 1A와 Item 1B
앵커 사이를 그대로 슬라이싱하면 Risk Factors 섹션만 정확히 떨어져 나온다.

**MSFT**: 같은 방식이 전혀 안 통한다. 목차에 하이퍼링크 자체가 없고
"Item 1A"라는 텍스트가 (인용/각주 등으로) 문서 전체에 21번 등장하는데
그중 무엇이 실제 섹션 시작인지 구분할 앵커가 없다.

즉 **filer(문서 생성 대행사)마다 10-K HTML 구조가 완전히 다르다** — SEC가
Item 단위로 파싱해서 제공하는 공식 API도 없다. 이건 V1에서 기업마다 XBRL
태그가 다르게 나온 것과 같은 종류의 문제이지만, 여기서는 fallback chain으로
해결할 수 있는 수준이 아니라(회사마다 완전히 다른 HTML 구조) 범용 파서를
만들려면 훨씬 큰 투자가 필요하다.

**결정**: 섹션 단위로 자르지 않고, `clean_filing_html()`로 스크립트/스타일/
숨겨진 inline-XBRL 메타데이터 블록(`display:none`)만 제거한 뒤 **문서 전체를
LLM에 넘긴다.** 섹션을 찾는 부담을 파서가 아니라 LLM의 독해력에 맡기는 선택.

정리 후 텍스트 크기(참고용, AAPL/MSFT 최신 10-K 기준):

| 기업 | 원본 HTML | 정리 후 텍스트 | 대략 토큰 수 |
|---|---|---|---|
| AAPL | 1.5MB | 208,370자 | ~52,000 |
| MSFT | 8.5MB | 336,285자 | ~84,000 |

두 경우 다 Claude 컨텍스트 윈도우 안에 여유 있게 들어간다.

## V2 — FinBERT 스파이크

FinBERT(`ProsusAI/finbert`)는 BERT 계열이라 컨텍스트가 ~512 토큰밖에 안 돼서
10-K 전체를 그냥 넣을 수 없다. 문장 단위로 쪼개서 돌려야 한다.

**문장 분리**: 금융 문서는 "U.S.", "Mr.", "Dept." 같은 약어가 많아서 단순
정규식(". "로 split)은 문장을 잘못 자른다. `pysbd`(규칙 기반, 모델 다운로드
없음)로 바꾸니 약어를 정확히 처리했다.

**실제 AAPL 10-K로 전체 파이프라인 실행**:
- 문장 분리: 990개 중 4단어 미만(페이지 번호, 표 파편 등 노이즈) 제외하고 925개
- 분리 시간: ~12초, FinBERT 분류 시간: ~11초(CPU, batch_size=16, 모델 로드 이후)
- 분포: neutral 640 / negative 216 / positive 69 — Risk Factors 위주 문서라 부정/중립에 치우침
- 가장 부정적인 문장 상위권: 세그먼트별 매출 감소, 관세로 인한 매출총이익률 하락,
  재고 평가손실 관련 문구 등 — 실제로 의미 있는 신호였음(일부는 페이지 헤더가
  섞여 들어간 노이즈도 있었음)

**결정**: 자체 호스팅(`transformers`+`torch`, `ProsusAI/finbert`)으로 간다.
호스팅 추론 API 대신 자체 호스팅을 택한 이유는 이미 Anthropic API 키 하나를
추가로 받은 상황에서 또 다른 외부 서비스 계정(HuggingFace 등)을 늘리고 싶지
않았고, FinBERT가 원래 "LLM 앞단의 빠른 무료 1차 필터" 역할이라 호출당 과금이
없는 쪽이 그 취지에 맞기 때문이다.

**의존성 분리**: `torch`+`transformers`는 무거워서(~1GB) 기본 설치에는 안 넣고
`pip install -e ".[sentiment]"` optional extra로 뺐다. CI는 이 extra 없이
도는데, `app/qualitative/sentiment.py`가 `transformers` import를
`_get_classifier()` 함수 안에서 지연 로딩하기 때문에 모듈 자체는 문제없이
import된다 — 실제로 별도 venv에 extra 없이 설치해서 앱 임포트와 테스트 스위트가
정상 도는지 직접 확인했다.

---

## 코드 리뷰로 발견된 문제 — restatement가 as_of_date에 실제로 반영 안 되고 있었음

Spike/테스트가 아니라 외부 코드 리뷰로 잡힌 문제. `docs/DATA_SPIKE_NOTES.md`
결정사항 #3(normalizer는 as-reported를 기본값으로 쓰고 restated는 별도
노출)은 정확히 구현돼 있었지만, **그 restated_facts를 실제로 소비하는
쪽(margin_of_safety.py)이 만들어지지 않은 채로 방치**돼 있었다.

`compute_margin_of_safety`의 look-ahead 필터는 as-reported fact의
`filed_date`만 봤다. 그래서:

```
HBB FY2019 revenue
  원본 10-K   filed 2020-02-26   $612,843,000
  10-K/A      filed 2020-07-24   $611,786,000

as_of_date = 2020-12-31 로 분석하면?
  (수정 전) 여전히 $612,843,000 사용 — 그 시점에 시장은 이미
  $611,786,000을 알고 있는데도
```

즉 look-ahead는 막지만(미래 데이터 사용 금지), 그 반대 방향인 "그 시점에
이미 공개된 정정값을 반영"은 안 하고 있었다 — 결정사항 #3은 normalizer
계층에서는 맞게 구현됐지만, valuation 계층에서 이어받지 못한 반쪽짜리
구현이었다.

**수정**: `_resolve_fact_as_of()`가 as-reported + restated_facts 전체 중
`filed_date <= as_of_date`인 것들 가운데 **가장 늦게 filed된 것**(=그
시점에 알려진 최신값)을 고르도록 변경. 실제 HBB 데이터로 검증:

```
as_of_date=2020-06-01 (정정 전) → $612,843,000 (원본)
as_of_date=2020-08-01 (정정 후) → $611,786,000 (정정값)
```

의도한 대로 동작함을 확인. 회귀 방지 테스트는
`tests/unit/test_margin_of_safety.py`에 HBB 실수치 기반으로 추가.

---

## 두 번째 코드 리뷰로 발견된 문제 — "같은 accn, 다른 tag" 전수조사에서 진짜 버그가 나옴

리뷰에서 "HBB dual-tag는 우연히 값이 같아서 문제없었을 뿐, 값이 다른 일반적인
케이스까지 해결된 건 아니다"라는 지적이 나와서, 실제로 5개 spike 기업 전체에
대해 "같은 (period_end, accn)에 여러 candidate tag가 매칭되고 값도 다른" 케이스를
전수조사했다. 결과: **82건 발견.** 이전 판단("실제로는 별 문제 없을 것")이 너무
낙관적이었다.

분류해보면:

1. **AAPL `depreciation_amortization`**: `DepreciationDepletionAndAmortization`
   (현금흐름표 add-back, 더 큰 값) vs `Depreciation`(PP&E 각주, 더 작은 값)이
   매 연도 동시에 존재. 진짜로 다른 개념(전자가 무형자산 상각 포함, 후자는
   유형자산 감가상각만)이라 fallback 우선순위가 우연히 FCFF에 맞는 걸(전자) 고르고
   있음 — 다만 검증된 건 아니고 "그럴듯함" 수준.
2. **AAPL `cash`**: `CashAndCashEquivalentsAtCarryingValue`(제한현금 제외) vs
   `...RestrictedCashAndRestrictedCashEquivalents`(제한현금 포함)도 동시 존재.
   현재는 좁은 쪽(제외)을 우선순위로 고르는데, `AssetsCurrent`가 제한현금을
   포함하는지 확인 안 된 상태라 이것도 미해결 — LIMITATIONS.md에 열린 문제로 남김.
3. **MSFT `short_term_debt`**: `ShortTermBorrowings`(상업어음)와
   `LongTermDebtCurrent`(장기부채의 유동성 부분)가 **서로 다른, 상호보완적인
   금액**으로 동시에 존재 (예: FY2015 $4,985M + $2,499M). 이건 대체 태그가 아니라
   **더해야 하는 두 항목**이었다. 기존 fallback 방식은 하나만 고르고 나머지를
   버려서 **FY2013 short_term_debt를 $0으로(실제 $2,999M) 계산하는 실제 버그**였다.

**수정한 것은 3번뿐이다.** `_select_short_term_debt()`를 새로 만들어
`ShortTermBorrowings + LongTermDebtCurrent`를 합산하고, 둘 다 없을 때만
`DebtCurrent`로 fallback하도록 바꿨다(`DebtCurrent`가 다른 두 태그와 동시에
존재하는 사례는 5개 기업 전체에서 없음을 먼저 확인). 실제 MSFT 데이터로
FY2013 $0 → $2,999,000,000, FY2015 $4,985,000,000 → $7,484,000,000로
정정된 것을 확인.

**1, 2번(D&A/cash)은 고치지 않고 열린 문제로 남겼다** — 지금 우선순위가 우연히
맞을 가능성이 높지만 "왜 맞는지"를 `AssetsCurrent`/현금흐름표 구조까지 파고들어
검증하지 않은 상태에서 바꾸는 건 오히려 근거 없는 수정이 될 수 있다고 판단했다.

---

## V2 — 논문(Caridi et al., Electronics 2026) 기반 개선: self-check grounding, cross-model validation

"AI-Assisted Value Investing" 논문(HITL gate, prompt self-check, cross-model
검증)을 참고해서 `app/qualitative/risk_extraction.py`에 두 가지를 추가했다.

**1. Grounding 필드 추가 후 real API로 재검증하다가 버그 두 개를 실제로 잡았다.**

`supporting_quote`/`grounding` 필드를 스키마에 추가하고 실제 AAPL 10-K로
돌렸더니:

- `claude-sonnet-5`가 `max_tokens=2000`에서 중간에 잘림(`stop_reason=max_tokens`,
  `tool_use.input == {}`) → `MAX_TOKENS`를 4096으로 올리고, truncation을
  명시적으로 체크해서 `KeyError` 대신 명확한 `QualitativeAnalysisError`를 내도록 수정.
- `MAX_TOKENS`를 올린 뒤에도 `claude-sonnet-5`가 **`risks` 배열에 6,456개
  항목을 채우고 `summary`는 끝내 못 쓰는 완전히 망가진 응답**을 냄
  (`stop_reason`은 `tool_use`로 정상 종료 — truncation이 아니라 순수
  degenerate generation). JSON Schema의 `maxItems: 8`은 모델에게 힌트일 뿐
  서버가 강제하는 제약이 아니라는 걸 실제로 확인.

**2. Cross-model 검증을 실제로 돌려보니 Sonnet이 큰 문서에서 재현 가능하게 실패한다.**

AAPL(~70K 토큰)과 HBB(~61K 토큰) 두 기업, 총 3회 실행에서 **`claude-sonnet-5`가
매번** 위 degenerate 패턴으로 실패했고 `claude-haiku-4-5`는 매번 정상 완료했다.
즉 이건 특정 문서의 우연이 아니라 **"6~7만 토큰대 문서 + 이 tool schema +
Sonnet" 조합에서 재현되는 한계**로 보인다 — 우리가 실제로 다루는 문서 크기가
전부 이 구간이라, 지금 상태로는 cross-model 검증에서 Sonnet 쪽이 사실상
거의 항상 실패한다고 봐야 한다.

**결정**: 원인을 더 파고들어 프롬프트를 고치기보다, `run_cross_model_extraction()`이
한쪽 모델이 실패해도 죽지 않고 나머지 결과 + `failed_models` + `disagreement=True`로
degrade하도록 만드는 쪽을 택했다. 논문 7.3절이 말하는 "verifier를 무오류로 취급하지
않는다"는 원칙 그대로다 — cross-model 검증 인프라 자체는 갖췄고 실제로 한쪽이
죽었을 때 전체가 죽지 않는다는 것까지 실증했지만, **지금 시점에는 사실상 Haiku
단독 실행 + "Sonnet 검증 시도했으나 실패" 플래그**로 동작한다는 걸 정직하게
남겨야 한다.

## V3 — 가정치(assumptions) 근거: fundamental growth rate 추가하며 실데이터로 관찰한 것

`fcff_growth_rate`를 사용자가 직접 넣는 대신 데이터로 참고치를 계산해주는
`app/valuation/growth.py`(Damodaran의 reinvestment rate × ROIC 공식)를 추가하고
AAPL 실데이터로 돌려봤다.

**1. `suggested_growth_rate`는 말이 되는 범위로 나온다.** AAPL 최근 3개 회계연도
평균 fundamental growth rate가 약 **-3.4%**로 나옴 — 애플이 최근 몇 년간 순 CapEx
대비 감가상각이 더 크고(사실상 net capex가 음수), 자사주 매입이 커서 재투자율
자체가 마이너스인 걸 반영한 결과. 비정상값이 아니라 실제로 애플이 "성장에
재투자"하는 단계를 지났다는 걸 숫자로 보여주는 사례 — 논문이나 이론이 아니라
실제로 돌려봐야 이런 걸 확인할 수 있다.

**2. 같은 `fiscal_year`가 여러 번 나오는 경우를 발견했다 (기존 normalizer 이슈,
이번 작업 범위 밖).** AAPL의 가장 오래된 10-K(2009년 제출, accn
0001193125-09-214859)에서 `fiscal_year=2009`로 태깅된 statement가 **3개**
나온다 — period_end가 각각 2007-09-29, 2008-09-27, 2009-09-26으로 서로
다른데도 전부 `fy=2009`. 같은 필링 안에 비교연도 데이터가 같이 들어있는데
normalizer가 SEC XBRL의 `fy` 필드(그 사실이 태깅된 "필링 기준 회계연도")를
그대로 `fiscal_year`로 쓰기 때문으로 보인다.
`estimate_fundamental_growth_rate()`는 `period_end`로 정렬/매칭하기 때문에 계산
자체는 영향받지 않지만(최근 3개년 평균도 정상), `financials`/`metrics` 응답에
"fiscal_year: 2009"가 3번 찍혀서 API 응답만 보는 사람 입장에서는 혼란스러울 수
있다. AAPL의 초기 XBRL 태깅(2009년 전후, XBRL 의무화 초기)에서만 관찰됨 — 2010년
이후 연도는 전부 정상. 이번 작업(fundamental growth rate) 범위 밖이라 고치지
않고 관찰만 기록한다.

**3. 후속 조사 후 수정 완료.** SEC `fy`는 fact별이 아니라 **필링 하나에 한 번
찍히는 값**이라, 비교연도로 같이 보고된 이전 기간도 전부 같은 fy를 물려받는다
(AAPL 2009년 필링만의 예외가 아니라 5개 spike 기업 전체·전체 필링 이력에서
보편적으로 확인됨). `_build_statement()`가 `fact.fiscal_year` 대신
`period_end.year`를 쓰도록 고쳤고(`app/financials/normalizer.py`, 상세는
DATA_MODEL.md), 5개 기업 전체 실데이터로 재검증해 `fiscal_year` 중복·
`period_end.year`와의 불일치 0건을 확인했다. 회계연도를 "끝나는 해"로 부르는
관례를 가정하는데 5곳 모두 이 관례를 따르는 것만 확인됐다 — 반대 관례 기업은
LIMITATIONS.md에 열린 문제로 남김.

**4. 다우존스 30개 종목 전수조사에서 ROIC 폭주 버그를 발견 → 시장가 기준으로 수정.**
`fundamental_growth_estimate`를 이번 프로젝트 밖 작업(다우지수 구성종목 엑셀
정리)에서 30개 종목 전부에 실제로 돌려봤더니, HD(홈디포)와 BA(보잉)에서
`roic`이 각각 144%, 113%라는 말이 안 되는 값이 나왔다. 원인: 둘 다 자사주매입
(HD)이나 누적손실+레버리지(BA)로 **장부상 자기자본이 거의 0에 가까워져** 있어서,
ROIC 분모(투하자본)가 작아지면서 비율이 폭주함 — CRCL의 "마이너스×마이너스"
부호 함정과는 다른, "분모가 0에 가까우면 폭주한다"는 별개의 함정이었다.

**수정**: `estimate_fundamental_growth_rate()`에 `market_price` 파라미터를
추가해서, **가장 최근 회계연도에 한해서만** 장부 자기자본 대신 시장가 기준
자기자본(`market_price × shares_outstanding`)을 쓰도록 바꿨다 — `analyze()`가
이미 받고 있는 시장가를 재사용하는 거라 새 외부 데이터는 아니다. 실데이터로
재검증: HD ROIC 144% → **4.8%**, BA ROIC 113% → **1.5%**로 정상화됨.
단, 과거 연도(3년 평균에 들어가는 나머지 2개년)는 그 시점 주가 데이터가 없어서
여전히 장부가를 쓴다 — 그래서 `suggested_growth_rate`(3년 평균)는 최근 연도만큼
완전히 고쳐지진 않는다. 이건 LIMITATIONS.md에 열린 한계로 남겼다.

## V4 — WACC 참고치 추가: interest_expense 태그도 마이그레이션 중이었다

`app/valuation/wacc.py`(업종평균 베타 + 실시간 무위험금리 + 이자보상배율 기반
부채비용)를 만들면서 `interest_expense`를 새 정규화 필드로 추가했다.
`InterestExpense` 태그 하나만 믿고 갔으면 안 됐다 — 실데이터로 확인:

- **HD/AAPL/JPM 전부 `InterestExpense` 태그가 최근 연도(2024년 전후)에서
  갑자기 안 잡힌다.** Revenue 태그 마이그레이션(finding #1)이랑 완전히 같은
  패턴이 이자비용에서도 일어나고 있었다.
- **HD**: `InterestExpenseNonoperating`로 개명함. FY2025 10-K에서 같은 기간
  (FY2024)을 새 태그로 다시 보고하는데 값이 동일 — 깨끗한 개명, 다른 개념이
  섞인 게 아님을 확인(accn 여러 개에 걸쳐 값 대조).
- **BA(보잉)**: `InterestExpense` 태그가 애초에 존재한 적이 없다. 비슷한 이름의
  `FinancingInterestExpense`는 $28-32M로 보잉 실제 이자비용(수십억 달러대)치고
  터무니없이 작아서 함정 태그였음 — 진짜 총이자비용은 `InterestAndDebtExpense`
  ($27.7억, 알려진 부채규모와 맞음).
- **AAPL**: FY2024/2025 10-K 어디에도 "이자비용" 개념의 명시적 태그가 없다.
  4개 후보 태그(`InterestExpense`, `InterestExpenseNonoperating`,
  `InterestAndDebtExpense`, `InterestExpenseDebt`) 다 확인했지만 전부 없음 —
  진짜 결측으로 보고 `null` + 경고로 처리, 억지로 추정하지 않음.

→ 폴백 체인에 4개 태그를 순서대로 넣었고(`interest_expense` in
`normalizer.py`), AAPL처럼 정말 없는 경우는 그대로 결측 처리한다.

베타/ERP/신용등급 스프레드 테이블은 Damodaran이 공개한 자료(실시간 API 아님)를
`wacc.py`에 출처·날짜와 함께 상수로 박아뒀다. 실제로 살아있는 API로 매번
가져오는 건 무위험금리(FRED DGS10) 하나뿐 — 이게 이 프로젝트에서 SEC EDGAR가
아닌 외부 데이터를 쓰는 유일한 지점이다.

실데이터 검증(HD/BA/MSFT/JPM/NVDA, `market_price` 실시간값 사용):

| 종목 | 업종 | WACC | 근거 |
|---|---|---|---|
| HD | Retail (Building Supply) | 10.25% | Aaa/AAA 등급 (이자보상배율 높음) |
| MSFT | Software | 9.89% | Aaa/AAA 등급 |
| NVDA | Semiconductor | 11.01% | 업종 베타 1.49로 가장 높음 |
| BA | Aerospace/Defense | 8.33% | B2/B 등급(정크본드급)인데도 업종베타(0.85)가 낮아서 WACC 자체는 낮게 나옴 — 보잉 고유 리스크(737 MAX 사태)는 업종평균 베타로는 못 잡는다는 한계를 그대로 보여주는 사례 |
| JPM | Banks (Regional) | 계산 안 됨 | 은행이라 operating_income/interest_expense 자체가 우리 태그로 안 잡힘(finding #6과 동일 이유) |
