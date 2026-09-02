"use strict";

const STORAGE_KEYS = {
  apiBaseUrl: "mva.apiBaseUrl",
  anthropicKey: "mva.anthropicKey",
};

const els = {
  configToggle: document.getElementById("config-toggle"),
  configBody: document.getElementById("config-body"),
  apiBaseUrl: document.getElementById("api-base-url"),
  qualToggle: document.getElementById("qual-toggle"),
  qualBody: document.getElementById("qual-body"),
  form: document.getElementById("analyze-form"),
  ticker: document.getElementById("ticker"),
  price: document.getElementById("price"),
  computeWacc: document.getElementById("compute-wacc"),
  computeComps: document.getElementById("compute-comps"),
  useWaccAsDiscountRate: document.getElementById("use-wacc-as-discount-rate"),
  analyze10k: document.getElementById("analyze-10k"),
  anthropicKey: document.getElementById("anthropic-key"),
  earningsCall: document.getElementById("earnings-call"),
  submitBtn: document.getElementById("submit-btn"),
  status: document.getElementById("status"),
  results: document.getElementById("results"),
};

function loadPersisted() {
  try {
    els.apiBaseUrl.value = localStorage.getItem(STORAGE_KEYS.apiBaseUrl) || "";
    els.anthropicKey.value = localStorage.getItem(STORAGE_KEYS.anthropicKey) || "";
  } catch {
    // Private-browsing / storage-blocked contexts - just start blank.
  }
}

function persist(key, value) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Ignore - nothing to persist to in this browser context.
  }
}

function setupToggle(button, body) {
  button.addEventListener("click", () => {
    const isHidden = body.hasAttribute("hidden");
    if (isHidden) body.removeAttribute("hidden");
    else body.setAttribute("hidden", "");
    button.setAttribute("aria-expanded", String(isHidden));
  });
}

function money(value) {
  if (value === null || value === undefined) return "n/a";
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pct(value) {
  if (value === null || value === undefined) return "n/a";
  return `${(value * 100).toFixed(1)}%`;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
  }
  for (const child of children || []) {
    if (child) node.appendChild(child);
  }
  return node;
}

// Proportional horizontal-bar "football field" chart - same idea as
// scripts/analyze.py's format_range_chart(), rendered as SVG instead of
// ASCII since this runs in a browser.
function renderRangeChart(ranges) {
  if (!ranges.length) return null;

  const width = 640;
  const rowHeight = 46;
  const labelWidth = 150;
  const chartWidth = width - labelWidth - 20;
  const height = ranges.length * rowHeight + 10;

  const overallLow = Math.min(...ranges.map((r) => r.low));
  const overallHigh = Math.max(...ranges.map((r) => r.high));
  const span = overallHigh - overallLow || 1;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "range-chart");

  ranges.forEach((r, i) => {
    const y = i * rowHeight + 10;
    const x1 = labelWidth + ((r.low - overallLow) / span) * chartWidth;
    const x2 = labelWidth + ((r.high - overallLow) / span) * chartWidth;
    const barWidth = Math.max(x2 - x1, 2);

    const label = document.createElementNS(svg.namespaceURI, "text");
    label.setAttribute("x", labelWidth - 10);
    label.setAttribute("y", y + 15);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("class", "method-label");
    label.textContent = r.method;
    svg.appendChild(label);

    const bar = document.createElementNS(svg.namespaceURI, "rect");
    bar.setAttribute("x", x1);
    bar.setAttribute("y", y + 4);
    bar.setAttribute("width", barWidth);
    bar.setAttribute("height", 18);
    bar.setAttribute("rx", 3);
    bar.setAttribute("class", r.method === "Overlap" ? "bar overlap" : "bar");
    svg.appendChild(bar);

    // Same problem scripts/analyze.py's ASCII chart hit (see
    // format_range_chart's "$2$264" bug) - a narrow bar (the Overlap
    // row especially) puts the low/high labels close enough to
    // collide. No live layout to measure against here, so this
    // estimates rendered width from character count (~7px/char at the
    // chart's 12px monospace) rather than doing a DOM-measure pass,
    // and merges into one "$low~$high" label when they'd overlap.
    const lowText = money(r.low);
    const highText = money(r.high);
    const estWidth = (s) => s.length * 7 + 2;
    const gap = x2 - x1;

    if (gap < estWidth(lowText) + estWidth(highText) + 8) {
      const mergedLabel = document.createElementNS(svg.namespaceURI, "text");
      const mergedText = `${lowText}~${highText}`;
      // Anchor at the bar's midpoint and center the text there, but
      // clamp so it never starts left of the label column or runs past
      // the chart's right edge - a merged label is usually wider than
      // the bar itself, unlike the two separate labels it replaces.
      const mid = (x1 + x2) / 2;
      const half = estWidth(mergedText) / 2;
      const clampedX = Math.min(Math.max(mid, labelWidth + half), width - half);
      mergedLabel.setAttribute("x", clampedX);
      mergedLabel.setAttribute("y", y + 36);
      mergedLabel.setAttribute("text-anchor", "middle");
      mergedLabel.textContent = mergedText;
      svg.appendChild(mergedLabel);
    } else {
      const lowLabel = document.createElementNS(svg.namespaceURI, "text");
      lowLabel.setAttribute("x", x1);
      lowLabel.setAttribute("y", y + 36);
      lowLabel.textContent = lowText;
      svg.appendChild(lowLabel);

      const highLabel = document.createElementNS(svg.namespaceURI, "text");
      highLabel.setAttribute("x", x2);
      highLabel.setAttribute("y", y + 36);
      highLabel.setAttribute("text-anchor", "end");
      highLabel.textContent = highText;
      svg.appendChild(highLabel);
    }
  });

  return svg;
}

function renderRiskCard(risk) {
  return el("div", { class: `risk-card severity-${risk.severity}` }, [
    el("p", { class: "risk-meta", text: `${risk.severity.toUpperCase()} · ${risk.status}` }),
    el("p", { class: "risk-label", text: risk.label }),
    el("p", { class: "risk-desc", text: risk.description }),
    el("blockquote", { text: `"${risk.supporting_quote}" (${risk.grounding})` }),
  ]);
}

function renderResult(data, marketPrice) {
  els.results.innerHTML = "";
  els.results.removeAttribute("hidden");

  const header = el("div", { class: "result-header" }, [
    el("h2", { text: `${data.company.ticker} · ${data.company.name}` }),
    el("span", { class: "category-badge", text: data.company.valuation_category }),
  ]);
  els.results.appendChild(header);

  const mos = data.margin_of_safety;
  if (mos) {
    const mosClass = mos.margin_of_safety >= 0 ? "mos-positive" : "mos-negative";
    els.results.appendChild(
      el("div", { class: "card" }, [
        el("h3", { text: "Margin of Safety" }),
        el("div", { class: "stat-row" }, [
          el("span", { text: "시장가" }),
          el("span", { class: "value", text: money(marketPrice) }),
        ]),
        el("div", { class: "stat-row" }, [
          el("span", { text: "내재가치" }),
          el(
            "span",
            {
              class: "value",
              text: `${money(mos.intrinsic_value_per_share)}  (${money(mos.intrinsic_value_low)} ~ ${money(mos.intrinsic_value_high)})`,
            },
            []
          ),
        ]),
        el("div", { class: "stat-row" }, [
          el("span", { text: "안전마진" }),
          el(
            "span",
            {
              class: `value ${mosClass}`,
              text: `${pct(mos.margin_of_safety)}  (${pct(mos.margin_of_safety_low)} ~ ${pct(mos.margin_of_safety_high)})`,
            },
            []
          ),
        ]),
      ])
    );
  } else {
    els.results.appendChild(
      el("div", { class: "card" }, [
        el("h3", { text: "Margin of Safety" }),
        el("p", { text: `계산 불가: ${data.unsupported_reason || "알 수 없음"}` }),
      ])
    );
  }

  const consensus = data.valuation_consensus;
  const chartRanges = [...consensus.ranges];
  if (consensus.overlap_low !== null && consensus.overlap_low !== undefined) {
    chartRanges.push({ method: "Overlap", low: consensus.overlap_low, high: consensus.overlap_high });
  }
  if (chartRanges.length) {
    const chartCard = el("div", { class: "card" }, [el("h3", { text: "Valuation Range" })]);
    const svg = renderRangeChart(chartRanges);
    if (svg) chartCard.appendChild(svg);
    if (consensus.overlap_low === null || consensus.overlap_low === undefined) {
      for (const w of consensus.warnings) {
        chartCard.appendChild(el("p", { class: "no-overlap-warning", text: `⚠ ${w}` }));
      }
    }
    els.results.appendChild(chartCard);
  }

  if (data.wacc_estimate) {
    const w = data.wacc_estimate;
    els.results.appendChild(
      el("div", { class: "card" }, [
        el("h3", { text: "WACC 참고치" }),
        el("div", { class: "stat-row" }, [
          el("span", { text: "업종" }),
          el("span", { class: "value", text: w.industry }),
        ]),
        el("div", { class: "stat-row" }, [
          el("span", { text: "WACC" }),
          el("span", { class: "value", text: pct(w.wacc) }),
        ]),
      ])
    );
  }

  if (data.comps_estimate && data.comps_estimate.peers && data.comps_estimate.peers.length) {
    const comps = data.comps_estimate;
    const table = el("table", { class: "comps-table" }, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Peer" }),
          el("th", { text: "EV/EBITDA" }),
          el("th", { text: "EV/Rev" }),
          el("th", { text: "P/E" }),
        ]),
      ]),
    ]);
    const tbody = el("tbody", {}, []);
    for (const p of comps.peers) {
      tbody.appendChild(
        el("tr", {}, [
          el("td", { text: p.ticker }),
          el("td", { text: p.ev_to_ebitda !== null ? p.ev_to_ebitda.toFixed(1) : "n/a" }),
          el("td", { text: p.ev_to_revenue !== null ? p.ev_to_revenue.toFixed(1) : "n/a" }),
          el("td", { text: p.price_to_earnings !== null ? p.price_to_earnings.toFixed(1) : "n/a" }),
        ])
      );
    }
    table.appendChild(tbody);
    els.results.appendChild(el("div", { class: "card" }, [el("h3", { text: "Comps Peers" }), table]));
  }

  const growth = data.fundamental_growth_estimate;
  els.results.appendChild(
    el("div", { class: "card" }, [
      el("h3", { text: "참고 성장률 (Fundamental Growth Estimate)" }),
      el("p", { text: pct(growth.suggested_growth_rate) }),
    ])
  );

  if (data.qualitative_analyses && data.qualitative_analyses.length) {
    for (const qa of data.qualitative_analyses) {
      const card = el("div", { class: "card" }, [
        el("h3", { text: `정성분석: ${qa.source_label}` }),
        el("p", { text: qa.summary }),
      ]);
      for (const risk of qa.risks) card.appendChild(renderRiskCard(risk));
      els.results.appendChild(card);
    }
  }

  if (data.warnings && data.warnings.length) {
    const details = el("details", { class: "warnings" }, [
      el("summary", { text: `경고 ${data.warnings.length}건` }),
    ]);
    const ul = el("ul", {}, []);
    for (const w of data.warnings) ul.appendChild(el("li", { text: w }));
    details.appendChild(ul);
    els.results.appendChild(details);
  }

  els.results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function handleSubmit(event) {
  event.preventDefault();

  const apiBaseUrl = els.apiBaseUrl.value.trim().replace(/\/$/, "");
  if (!apiBaseUrl) {
    els.status.textContent = "먼저 설정에서 백엔드 API 주소를 입력하세요.";
    els.status.classList.add("error");
    els.configBody.removeAttribute("hidden");
    els.configToggle.setAttribute("aria-expanded", "true");
    return;
  }

  const ticker = els.ticker.value.trim().toUpperCase();
  const price = parseFloat(els.price.value);
  const anthropicKey = els.anthropicKey.value.trim();
  const earningsCallText = els.earningsCall.value.trim();
  const analyze10k = els.analyze10k.checked;

  persist(STORAGE_KEYS.apiBaseUrl, apiBaseUrl);
  persist(STORAGE_KEYS.anthropicKey, anthropicKey);

  const payload = {
    ticker,
    market_price: price,
    compute_wacc: els.computeWacc.checked,
    compute_comps: els.computeComps.checked,
    use_wacc_as_discount_rate: els.useWaccAsDiscountRate.checked,
  };
  if (analyze10k) payload.analyze_10k = true;
  if (earningsCallText) payload.earnings_call_text = earningsCallText;
  if ((analyze10k || earningsCallText) && anthropicKey) payload.anthropic_api_key = anthropicKey;

  els.submitBtn.disabled = true;
  els.status.classList.remove("error");
  els.status.textContent = "분석 중... (SEC/Yahoo 조회" + (analyze10k || earningsCallText ? ", LLM 호출" : "") + ")";
  els.results.setAttribute("hidden", "");

  try {
    const response = await fetch(`${apiBaseUrl}/api/v1/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || `서버 오류 (HTTP ${response.status})`);
    }
    els.status.textContent = "";
    renderResult(data, price);
  } catch (err) {
    els.status.classList.add("error");
    els.status.textContent =
      err instanceof TypeError
        ? "서버에 연결할 수 없습니다 - API 주소와 CORS 설정(ALLOWED_ORIGINS)을 확인하세요."
        : err.message;
  } finally {
    els.submitBtn.disabled = false;
  }
}

setupToggle(els.configToggle, els.configBody);
setupToggle(els.qualToggle, els.qualBody);
els.form.addEventListener("submit", handleSubmit);
loadPersisted();
