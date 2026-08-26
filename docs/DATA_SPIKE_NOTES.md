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

## V5 — Comps(비교기업 배수) 추가: SEC 자체 업종검색 API가 못 쓸 수준이었다

JPM/BlackRock/버핏이 회사를 어떻게 밸류에이션하는지 설명하다가, "우리는
DCF 하나만 하고 상대가치(comps)는 아예 없다"는 게 나와서 `app/valuation/comps.py`를
추가했다. Peer 기업을 어떻게 찾을지가 핵심 문제였다.

**SEC의 `browse-edgar` SIC 검색 API를 먼저 시도했다가 버렸다.**
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=7372&...&output=atom`로
실제 호출해보니:
- 응답 XML의 회사명 필드가 **`title="ARRAY(0x55c3e678d318)"`처럼 아예 깨져서
  나온다** — SEC 레거시 CGI 백엔드의 배열 직렬화 버그로 보임. CIK는 정상 추출
  가능하지만 이름은 못 씀.
- 결과가 **크기/관련도순이 아니라 그냥 알파벳순**이라, count=40으로 받아도
  대형주 몇 개 옆에 껍데기 회사들이 무작위로 섞여 나온다. "진짜 비교 가능한
  peer"를 걸러내려면 후보 전부의 시가총액을 먼저 조회해야 하는데, 그러면
  요청 하나에 SEC 호출 수십~백 번 — 응답시간이 감당 안 됨.

→ **결정**: `wacc.py`의 업종베타 테이블과 똑같은 방식으로, SIC 접두사별
대형 상장사 peer 목록을 직접 큐레이션해서 상수로 박아뒀다(`INDUSTRY_PEERS`).
실시간 검색이 아니라 문서화된 참고자료 취급 — 이미 베타 테이블에서 받아들인
트레이드오프를 그대로 재사용한 것.

**Peer가 1개뿐인 업종에서 median이 통째로 비어버리는 버그를 실데이터로 잡았다.**
HD(Retail Building Supply) peer 목록이 LOW 하나뿐인데, 처음 코드는
"최소 2개 peer 있어야 median 계산"으로 짜서 HD를 돌리면 `implied_value_per_share`가
전부 `null`로 나왔다 — 버그처럼 보이는 결과. 실제로는 데이터가 없는 게
아니라 peer가 1개뿐이라 통계적으로 약할 뿐이라, "무조건 계산은 하되
peer 수가 적으면 경고"로 바꿨다(`select_base_fcff()`의 "부족해도 평균 내고
경고" 패턴과 동일).

**실데이터 검증(NVDA, peer 5개: AVGO/QCOM/TXN/AMD/INTC)**: EV/EBITDA 배수
기준 내재가치 $338.5, EV/Revenue 기준 $139.1, P/E 기준 $510.7 — 세 방법이
서로 크게 갈린다(반도체 업종 자체가 워낙 다양해서). 이것도 "하나의 숫자"로
합치지 않고 세 배수 결과를 전부 그대로 노출한다.

## CI에서 발견된 문제 — FRED가 GitHub Actions IP를 막고 있었다

Comps 커밋(f7e8c03)을 푸시한 뒤 CI가 `test_analyze_compute_wacc_real_data`에서
실패했다: `wacc_estimate`가 `None`으로 나옴. 로컬에서는 FRED가 정상 응답해서
처음엔 일시적 네트워크 문제인가 싶었는데, **CI를 그대로 재실행해도 똑같이
실패**(2/2) — 우연이 아니라 재현되는 문제였다.

같은 CI 실행에서 **Yahoo Finance(comps의 peer 가격 소스)는 정상 동작**했다는
게 결정적 단서였다 — 네트워크 자체가 막힌 게 아니라 **FRED만** GitHub Actions의
호스팅 러너 IP 대역을 막고 있는 것으로 보인다(스크래핑 방지용 IP 평판 차단으로
추정, FRED 쪽 정책이라 우리가 확인/제어할 방법은 없음). 이건 CI만의 문제가
아니라 **AWS/GCP/Azure 등 클라우드에 이 앱을 배포하면 똑같이 겪을 수 있는
실서비스 신뢰성 문제**라고 판단했다.

**결정**: FRED fetch가 실패하면 `wacc_estimate` 전체를 포기하는 대신,
`wacc.py`에 실측 기반 fallback 상수(`FALLBACK_RISK_FREE_RATE`, 이 기능
개발 중 실제로 관찰한 4.7%)를 문서화해서 넣고, 실패 시 그 값으로 대체하되
경고를 남기도록 고쳤다(`analysis_service.py`). CRCL/HD/BA 때와 같은
원칙 — 죽이지 않고 degrade, 근데 왜 degrade됐는지는 숨기지 않는다.

## V6 — Owner Earnings + 범위/교집합 + 터미널 차트

**Owner Earnings 유지보수 capex 범위가 실데이터에서 정확히 예상대로 갈렸다.**
AAPL(성숙 기업, capex ≈ D&A)은 두 가정(전체capex 유지보수 vs D&A만 유지보수)의
차이가 최근 연도 기준 0.2% 이내로 거의 안 갈린다. 반대로 AMZN(AI/데이터센터
capex 급증)은 FY2023 스프레드 $40.7억 → FY2024 $302억 → FY2025 $660.6억로
capex가 D&A를 앞지르는 만큼 정확히 넓어졌다 — 범위 폭 자체가 "이 회사가 지금
얼마나 재량적 투자를 하고 있는지"를 보여주는 실제 신호였다.

**범위 교집합도 실데이터로 두 방향 다 확인**: HD는 DCF(FCFF) $261~$464,
Owner Earnings DCF $232~$414, Comps $236~$264 세 방법이 겹쳐서 최종
교집합 $261~$264로 좁게 수렴. NVDA는 DCF 두 방식이 $23~$52 근처에
몰려있는데 Comps는 $139~$511이라 **아예 안 겹침** — `overlap_low/high`가
`null`로 나오고 "no overlap" 경고가 붙는 걸 확인. 억지로 하나의 숫자로
합치지 않고 "방법들이 진짜 의견이 갈린다"는 걸 그대로 노출하는 설계가
의도대로 동작함.

**터미널 바 차트 만들면서 라벨 배치 버그 2개를 실행해보고 바로 잡았다.**
1) 막대 오른쪽 끝 근처에 숫자 라벨을 놓으면 고정 길이 리스트 경계를 넘어가서
   글자가 잘림(`$464`가 `$`로 잘려서 출력됨).
2) 겹치는 구간(Overlap) 막대처럼 폭이 아주 좁으면 low/high 라벨이 서로
   겹쳐서 `$2$264`처럼 깨진 텍스트가 나옴.
둘 다 실제로 `python scripts/analyze.py HD 344.3`을 돌려보고서야 발견함 —
단위 테스트만으로는 못 잡았을 렌더링 버그. 라벨 리스트를 필요시 늘어나게
고치고, 막대가 라벨 두 개를 담기엔 너무 좁으면 `"$low~$high"` 하나로
합쳐 표시하도록 수정해서 재검증했다.

## V7 — operating_income 역산: 태그가 없는 게 아니라 애초에 존재하지 않았다

NKE/MRK/CVX가 FCFF 계산에서 전부 `operating_income` 누락으로 막혀 있길래
처음엔 "이 회사들도 태그가 마이그레이션됐나" 싶어서 회사별 companyfacts
JSON을 뒤졌는데, `OperatingIncomeLoss`는 물론이고 다른 어떤 이름의
영업이익 태그도 아예 존재하지 않았다. 실제 10-K 원문(`fetch_filing_document`로
직접 가져온 손익계산서 텍스트)까지 확인해보니 — 이 회사들의 손익계산서
자체에 "Operating Income"이라는 소계 줄이 없다. US GAAP은 영업이익
소계를 의무화하지 않아서, 회사가 "매출 → 각종 비용 나열 → 바로 세전이익"
형태로 표시하면 그 회사의 XBRL에는 애초에 태그할 숫자 자체가 없는 게
정상이다. 데이터 누락이 아니라 정당한 표시방식 선택이었다.

**태그가 없어도 세전이익에서 역산은 가능했다** — NKE와 MRK는 손으로 계산한
실제 값과 정확히 일치했다(NKE $3,797,000,000, MRK $21,218,000,000).
공식은 `pretax_income - InterestIncomeExpenseNonoperatingNet -
OtherNonoperatingIncomeExpense`. 다만 이 공식이 보편적이지 않다는 것도
실데이터로 먼저 확인했다 — 원래 태그가 있는 HD/MSFT에 같은 공식을
적용해봤더니 HD는 애초에 필요한 하위 태그가 없어서 계산 자체가 안 됐고,
MSFT는 실제 값과 약 $60억 차이가 났다. 즉 이 공식은 "태그가 원래
있으면 검증/대체용으로 쓸 만한 것"이 아니라 "태그가 아예 없을 때만 쓰는
최후의 근사치"로만 설계해야 한다는 뜻 — 그래서 `_derive_operating_income()`은
`operating_income` 태그가 하나도 안 잡혔을 때만 호출되고, 실제 태그가
있으면 절대 덮어쓰지 않는다.

**CVX는 근사치가 불완전하다는 것도 실데이터로 확인**: 세전이익 태그는
있지만 `InterestIncomeExpenseNonoperatingNet`/`OtherNonoperatingIncomeExpense`
둘 다 없어서 역산값이 세전이익과 정확히 같은 $19,743,000,000으로 나온다.
정유 메이저 특유의 지분법 관계사 이익(equity affiliates, 약 $30억)이라는
세 번째 조정 항목이 이 공식에 없어서 실제 영업이익보다 낮게 잡힐 걸로
보임 — 경고 메시지에 "완전하지 않을 수 있다"를 명시하고, 이 세 번째
항목 추가는 사용자가 요청하지 않는 한 진행하지 않기로 함.

**JPM(은행)에서 회귀 버그를 자체 검증 중에 잡았다**: 처음엔 category
게이트 없이 역산 함수를 추가했는데, JPM도 우연히 세전이익류 태그가
매칭돼서 `operating_income`이 (의미 없는) $72,595,000,000으로 채워지는
걸 전체 테스트 스위트를 돌려보고서야 발견함. 은행은 이자수익/비용이
"비영업" 조정 항목이 아니라 핵심 사업 그 자체라서 이 공식이 통계적으로
말이 안 됨 — `company.valuation_category == ValuationCategory.STANDARD`
게이트를 추가해서 금융회사는 역산을 아예 시도하지 않도록 고쳤다
(관련 근거는 [6번 항목](#6-jpm은행은-이론이-아니라-데이터로-fcff-부적합이-확인됨) 참고).

**역산으로 FCFF는 풀렸지만 주당가치는 별개 문제로 여전히 막혀 있다.**
NKE/MRK/CVX 모두 이제 `base_fcff`가 실제 값으로 계산되지만,
`value_per_share`는 여전히 `None`이다 — 이 세 회사는 `shares_outstanding`
태그도 따로 빠져 있는데(KO/JNJ/MCD/PG/WMT/V와 같은, 이미 알려진 별개의
이슈), 그건 이번 요청 범위 밖이라 손대지 않았다.

## V8 — shares_outstanding 역산: 9개 종목이 막혀 있던 진짜 이유가 3가지로 갈렸다

엑셀을 다시 뽑아서 보여줬더니 NKE/MRK/CVX 칸이 여전히 비어 있다는 피드백을
받고, "operating_income은 고쳤다면서 왜 안 바뀌었냐"는 의심을 받았다 —
직접 `python scripts/analyze.py NKE 40.76`을 돌려서 경고가
`operating_income: no standard tag found`가 아니라 `shares_outstanding
not found`로 바뀌어 있는 걸 먼저 보여줘서, 앞선 수정은 제대로 작동했고
막힌 지점이 완전히 다른 태그로 넘어갔을 뿐이라는 걸 증명한 뒤에 이 문제로
들어갔다.

**KO/JNJ/MCD/PG/WMT/V까지 포함해서 9개 종목 전부 실데이터로 원인을
분리해서 확인**했다:
1. NKE/MRK/CVX/KO/JNJ/PG (6개) — `CommonStockSharesOutstanding` 태그가
   애초에 하나도 없음. 대신 EPS 계산에 쓰는
   `WeightedAverageNumberOfDilutedSharesOutstanding`(또는 `...Basic`)은
   전부 정상적으로, 실제 알려진 발행주식수와 맞아떨어지는 원단위 값으로
   존재함(예: NKE 1,481,000,000 — 공개적으로 알려진 실제 발행주식수와
   일치).
2. WMT (1개) — `CommonStockSharesOutstanding`을 아예 안 쓴 게 아니라
   **FY2011까지만 쓰고 FY2012부터 완전히 끊음** — 이번 세션 내내 반복된
   "태그 마이그레이션" 패턴과 또 다른 변종("태그를 그냥 그만 쓴 회사").
   WMT도 `WeightedAverageNumberOfDilutedSharesOutstanding`은 최신
   10-K까지 정상 존재.
3. V/Visa (1개) — `dei`/`us-gaap`/`srt`/`invest`/`ffd` 전체 네임스페이스를
   뒤져봐도 발행주식수·가중평균주식수 그 어떤 형태의 태그도 존재하지
   않음. 이건 "태그 이름이 다른" 문제가 아니라 **진짜로 이 회사가 그
   개념 자체를 XBRL로 전혀 리포트하지 않는** 경우 — 역산으로 못 고치는
   진짜 데이터 공백. `shares_outstanding: no standard tag found` 경고를
   그대로 유지.

**6+1(WMT) = 7개 종목은 가중평균 희석주식수로 폴백해서 고쳤다.** 이
필드가 "특정 시점(대차대조표 기준일)의 발행주식수"가 아니라 "해당
회계연도의 가중평균"이라는 개념 차이는 실존하지만(자사주 매입/신주
발행이 연중에 크게 있었던 해라면 오차가 생길 수 있음), 실무에서 주당
DCF 가치를 계산할 때 흔히 쓰이는 표준적인 대체 지표라 경고와 함께
채택함 — 태그가 아예 없어서 계산 자체가 막히는 것보다 이 편이 훨씬
낫다는 판단.

**그런데 MCD에서 세 번째 문제가 하나 더 나왔다 — 단위 자체가 다르게
찍혀 있었다.** MCD의 `WeightedAverageNumberOfDilutedSharesOutstanding`
값이 `716.4`처럼 나오는데, 실제 발행주식수는 약 7억 1,640만 주다. 처음엔
"필터링 회사의 XBRL 태깅 실수인가" 의심했지만, 실제 10-K 원문
(`fetch_filing_document`로 직접 가져온 손익계산서)을 확인해보니
McDonald's는 아예 **손익계산서 전체를 "In millions" 단위로 표시**하고,
그 표 안의 "Weighted-average shares outstanding-diluted" 줄도 예외 없이
`716.4`로 인쇄되어 있었다 — 실수가 아니라 회사가 실제로 그렇게
공시한 것. SEC의 companyfacts/companyconcept API는 원본 XBRL의
`decimals` 속성(스케일 정보)을 노출하지 않아서, 구조적으로 이걸 구분할
방법이 없었다. 그래서 "이 프로젝트가 다루는 규모(DJIA 급 대형주)의
회사라면 발행주식수가 1,000만 주 밑으로 떨어질 리 없다"는 크기 기반
휴리스틱을 도입해서, 그 밑이면 백만 단위로 보정(×1,000,000)하고 경고를
남기기로 했다. **검증**: 보정된 값(716,400,000)으로 순이익을 나누면
$11.9528/주 — MCD의 실제 공시 희석 EPS $11.95와 정확히 일치, 확실한
근거로 확인됨.

**결과: NKE/MRK/CVX/KO/JNJ/MCD/PG/WMT 8개 종목 모두 실제 주당가치가
나오기 시작했다. V(Visa)만 진짜 데이터 공백으로 여전히 `None`.**
이걸로 세션 초반부터 반복됐던 "빈칸이 왜 이렇게 많냐"는 질문에 대한
근본 원인이 사실상 다 규명됨 — operating_income 역산(V7) + 이번
shares_outstanding 역산(V8) 두 건이 DJIA 30종목 표에서 관찰됐던 공백의
대부분을 설명한다.

## V9 — 20-F(IFRS) 외국계 발행사 지원: NVO(Novo Nordisk)로 실데이터 검증

사용자가 NVO(덴마크 회사, 뉴욕증시 ADR)를 넣었더니 "no financial
statements filed"로 완전히 막혔다. 원인을 실데이터로 바로 확인: NVO는
연차보고서를 `10-K`가 아니라 **`20-F`**로 제출한다(외국계 민간발행사,
foreign private issuer). 이 프로젝트의 `ANNUAL_FORMS = ("10-K",
"10-K/A")` 필터가 20-F를 아예 인식하지 못해서 회계연도 자체가 하나도
발견되지 않았던 것.

**단순히 폼 타입만 바꾸면 되는 문제가 아니었다.** NVO의 실제
companyfacts를 열어보니 `us-gaap` 네임스페이스 태그가 **0개**였다 —
전부 `ifrs-full`(IFRS) 네임스페이스(253개 태그)였다. 즉 CONCEPT_CANDIDATES
전체가 US-GAAP 태그 이름 기준이라 20-F 필터만 고쳐도 아무 값도 안
잡혔을 것. 그래서 완전히 별도의 `IFRS_CONCEPT_CANDIDATES` 매핑 테이블을
만들고, `_detect_taxonomy()`로 `facts_ns`에 `us-gaap`이 있으면 GAAP
경로, `ifrs-full`만 있으면 IFRS 경로로 분기하도록 정규화 로직을 확장했다.

**IFRS 태그는 실제 NVO 2025 회계연도 숫자와 정확히 대조해서 확정**:
Revenue(매출) 309,064백만 DKK, ProfitLossFromOperatingActivities(영업이익)
127,658백만 DKK, ProfitLoss(순이익) 102,434백만 DKK 등 전부 실제 20-F에
보고된 수치와 일치 확인. capex는 us-gaap과 동일하게 PP&E 매입분만
사용(`PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`)
- NVO는 별도로 `PurchaseOfIntangibleAssets`(2025년 DKK 300억, 전년
대비 7배 급증)라는 큰 항목도 있지만, 이게 경상적 재투자인지 일회성
라이선싱/M&A인지 검증이 안 돼서 의도적으로 합산에서 뺐다 - 이번
세션 내내 지켜온 "검증 안 된 건 추측해서 합치지 않는다" 원칙 그대로
적용.

**US-GAAP 전용 3개 특수 로직(short_term_debt 합산, operating_income
역산, shares_outstanding 가중평균 폴백)은 IFRS 경로에 그대로 옮기지
않았다** - 전부 NVO 하나로만 검증된 것들이라, 다른 IFRS 발행사에서
같은 패턴이 재현되는지 확인 안 된 채로 일반화하면 이전에 봤던
JPM 회귀 버그(V7)와 같은 실수를 반복하게 된다. 대신 NVO는 이 세
필드를 전부 직접 태그로 갖고 있어서(`ShorttermBorrowings`,
`ProfitLossFromOperatingActivities`, `NumberOfSharesOutstanding`)
폴백 자체가 필요 없었다.

**통화 문제 - DKK 그대로 두면 결과가 완전히 틀어진다.** NVO의 모든
금액은 DKK로 보고되는데, 사용자가 입력하는 시장가는 USD다. 그대로
나누면 주당가치가 실제보다 약 6.4배 부풀려진다. 실시간 환율이
필요했는데, 별도 HTTP 로직을 새로 만들지 않고 이미 있던
`fetch_current_price()`(Yahoo Finance chart API)를 그대로 재사용했다
- Yahoo는 통화쌍도 일반 티커처럼 취급해서 `"DKKUSD=X"`를 그대로
받아준다는 걸 실제로 호출해서 확인(0.156 반환, 실제 DKK/USD 환율과
합리적으로 일치). `app/financials/normalizer.py`는 원래 네트워크
호출이 전혀 없는 순수 함수라서(offline 설계), 환율 변환은 `normalize()`
안이 아니라 `analyze()`에서 `normalize()` 직후, DCF/WACC/Comps/Owner
Earnings 등 어떤 계산도 시작하기 전에 한 번만 적용한다 - 이러면
아래 모든 소비자가 전혀 통화를 신경 쓸 필요 없이 이미 USD로 변환된
값을 읽게 된다(wacc.py/comps.py/owner_earnings.py/dcf.py 전부 무수정).

**변환 검증**: MCD 사례(V8)와 똑같은 방식으로 확인 - 변환된 순이익을
발행주식수로 나눈 값이 실제 공개된 수치와 맞아떨어지는지 체크. NVO는
정확한 EPS 대신 최종 `intrinsic_value_per_share = $40.37`(범위
$31~58)이 시장가 $48.69와 합리적인 스케일로 나옴(6~7배씩 어긋나지
않음)으로 검증. Comps($72~93, 미국 제약 동종업계 배수 적용)까지 세
방법 전부 정상 작동하지만 서로 안 겹쳐서(overlap 없음) "no overlap"
경고가 뜨는 것도 확인 - 이 프로젝트의 기존 설계(방법들이 진짜 의견이
갈리면 억지로 합치지 않고 그대로 노출)가 외국계 발행사에도 그대로
적용됨.

**시장데이터 클라이언트 필수화**: 통화 변환이 필요한데
`market_data_client`가 없으면(직접 `analyze()`를 호출하는 테스트/스크립트
등에서) 잘못된 통화로 조용히 계산을 진행하는 대신 `MarketDataError`를
바로 던지도록 했다 - WACC/Comps 옵트인 시 이미 쓰던 것과 같은
"명시적으로 실패, 조용히 틀린 값 내지 않기" 패턴. 실제로는 CLI
(`scripts/analyze.py`)와 API(`app/api/analysis.py`) 둘 다 이미 항상
`market_data_client`를 만들어서 넘기고 있어서, 이 경로가 실제로 막히는
건 그 클라이언트를 안 넘기는 직접 프로그래매틱 호출/테스트뿐이다.
