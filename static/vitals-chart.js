// Shared date-bucketing + Chart.js rendering for the sleep/steps vitals
// chart, used by both the edit page and the doctor view page so the two
// stay in sync (period definitions, colors, highlight logic...).
(() => {
  // Chart.js defaults to a fixed gray for text/gridlines, which reads fine
  // on a light card but goes muddy on the dark card background — keep it in
  // step with the theme tokens in static/theme.css.
  let cardBg = '#fffdfa'; // tracked for the point halo below; kept in sync with theme.css's --card-bg
  function applyChartTheme() {
    if (typeof Chart === 'undefined') return;
    const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    Chart.defaults.color = dark ? '#c7ab8e' : '#8a7862';
    Chart.defaults.borderColor = dark ? '#3e3024' : '#ecdfd0';
    cardBg = dark ? '#2b2119' : '#fffdfa';
    // Match the app-wide bump to bold + 1.2x size (Chart.js draws on canvas,
    // so it doesn't pick up the CSS font-weight/font-size overrides).
    Chart.defaults.font.weight = 'bold';
    Chart.defaults.font.size = Math.round(12 * 1.2);
  }
  applyChartTheme();
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyChartTheme);
  }

  function fmtDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function addDays(dateStr, n) {
    const d = new Date(dateStr + 'T00:00:00');
    d.setDate(d.getDate() + n);
    return fmtDate(d);
  }

  function weekStart(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    const offset = (d.getDay() + 6) % 7; // Monday = 0
    d.setDate(d.getDate() - offset);
    return fmtDate(d);
  }

  function monthStart(dateStr) {
    return dateStr.slice(0, 7) + '-01';
  }

  function nextMonthStart(dateStr) {
    const [y, m] = dateStr.split('-').map(Number);
    const ny = m === 12 ? y + 1 : y;
    const nm = m === 12 ? 1 : m + 1;
    return `${ny}-${String(nm).padStart(2, '0')}-01`;
  }

  function shortLabel(dateStr) {
    const [, m, d] = dateStr.split('-');
    return `${Number(m)}/${Number(d)}`;
  }

  function monthLabel(dateStr) {
    const [y, m] = dateStr.split('-');
    return `${y}/${Number(m)}`;
  }

  function hexToRgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  const STEPS_AXIS_MAX = 10000; // bars above this are visually clipped, which is fine — the point is the trend, not exact high counts
  const STEPS_DARK_THRESHOLD = 12000;
  const STEPS_COLOR = '#7c9a52';
  const STEPS_COLOR_DARK = '#5c7a3c';

  // Bars stay translucent so the sleep-score line reads clearly on top of
  // them; a 12,000+ step day gets a solid, darker bar so it still stands out.
  function stepBarColor(value, clinicColor) {
    if (clinicColor) return clinicColor;
    return value >= STEPS_DARK_THRESHOLD
      ? hexToRgba(STEPS_COLOR_DARK, 0.85)
      : hexToRgba(STEPS_COLOR, 0.55);
  }

  // Sleep score thresholds so the line reads at a glance instead of needing
  // the axis checked: 90+ green, 80+ blue, 60+ orange, below that red.
  function scoreColor(score) {
    if (score == null) return 'transparent'; // gap — no point/segment actually drawn here
    if (score >= 90) return '#6f8f45';
    if (score >= 80) return '#3f7cae';
    if (score >= 60) return '#e0793a';
    return '#b23b2c';
  }

  const RECENT_DAYS = 50; // default for the plain 'recent' mode (kept for backward compat)
  const FIXED_RANGE_START = '2025-12-01'; // week/month: fixed start covering all entries, not a rolling window

  // 'recent' == 50 days; 'recentNNN' (e.g. 'recent100') == NNN days.
  function recentModeDays(mode) {
    if (mode === 'recent') return RECENT_DAYS;
    const m = /^recent(\d+)$/.exec(mode);
    return m ? Number(m[1]) : null;
  }

  function vitalsRangeForMode(mode) {
    const end = fmtDate(new Date());
    const recentDays = recentModeDays(mode);
    if (recentDays) return { start: addDays(end, -(recentDays - 1)), end };
    return { start: FIXED_RANGE_START, end };
  }

  // Every bucket key in [start, end] for the given mode, so bar/line series stay aligned.
  function bucketKeysForRange(mode, start, end) {
    const keys = [];
    if (recentModeDays(mode)) {
      for (let d = start; d <= end; d = addDays(d, 1)) keys.push(d);
    } else if (mode === 'week') {
      const last = weekStart(end);
      for (let d = weekStart(start); d <= last; d = addDays(d, 7)) keys.push(d);
    } else {
      const last = monthStart(end);
      for (let d = monthStart(start); d <= last; d = nextMonthStart(d)) keys.push(d);
    }
    return keys;
  }

  // treatZeroAsMissing: a raw value of exactly 0 is a tracking error rather
  // than a real reading (used for sleep score — a device that failed to
  // record sleep reports 0, it didn't score a night at zero) so it's
  // excluded from the average, same as a day with no row at all.
  function aggregateToKeys(rawRows, dateField, valueField, mode, keys, clinicDates, convertFn, { treatZeroAsMissing = false } = {}) {
    const groupFn = mode === 'week' ? weekStart : mode === 'month' ? monthStart : (d => d);
    const labelFn = mode === 'month' ? monthLabel : shortLabel;
    const buckets = {};
    keys.forEach(k => { buckets[k] = { sum: 0, count: 0, clinic: false }; });

    rawRows.forEach(r => {
      const v = r[valueField];
      if (v == null) return; // e.g. legacy sleep rows with no score
      if (treatZeroAsMissing && v === 0) return;
      const key = groupFn(r[dateField]);
      if (buckets[key]) {
        buckets[key].sum += v;
        buckets[key].count += 1;
      }
    });
    clinicDates.forEach(cd => {
      const key = groupFn(cd);
      if (buckets[key]) buckets[key].clinic = true;
    });

    return {
      labels: keys.map(labelFn),
      // No valid reading in the bucket -> null (a real gap in the line/bar),
      // not 0 (which would misread as an actual measured low value).
      values: keys.map(k => buckets[k].count ? convertFn(buckets[k].sum / buckets[k].count) : null),
      colors: keys.map(k => buckets[k].clinic ? '#e0793a' : null),
    };
  }

  // entries: memo rows (every entry counts as a clinic-day marker).
  // onHoverEntry(memo), optional: called when the cursor lands on a bucket
  // that has a memo (daily-resolution modes only), so the caller can select
  // that entry into its own entry list / detail view.
  function renderVitalsChart(canvasEl, existingChart, entries, vitals, mode, onHoverEntry) {
    const { start, end } = vitalsRangeForMode(mode);
    const keys = bucketKeysForRange(mode, start, end);
    const clinicDates = new Set(
      entries.filter(m => m.date >= start && m.date <= end).map(m => m.date)
    );

    const sleepSeries = aggregateToKeys(vitals.sleep, 'date', 'score', mode, keys, clinicDates, v => +v.toFixed(1), { treatZeroAsMissing: true });
    const stepsSeries = aggregateToKeys(vitals.steps, 'date', 'steps', mode, keys, clinicDates, v => Math.round(v));

    if (existingChart) existingChart.destroy();

    const recentDays = recentModeDays(mode);
    const modeTitle = recentDays ? `（直近${recentDays}日）` : mode === 'week' ? '（週平均）' : '（月平均）';

    // Only daily-resolution modes map one bucket key to exactly one calendar
    // date, so the hover-to-select handoff is limited to those (week/month
    // buckets can span several entries and don't have a single memo to show).
    const entriesByDate = {};
    if (recentDays) entries.forEach(m => { entriesByDate[m.date] = m; });
    let lastHoverIndex = null;

    return new Chart(canvasEl, {
      data: {
        labels: stepsSeries.labels,
        datasets: [
          {
            type: 'bar',
            label: '歩数',
            data: stepsSeries.values,
            backgroundColor: stepsSeries.values.map((v, i) => stepBarColor(v, stepsSeries.colors[i])),
            yAxisID: 'ySteps',
          },
          {
            type: 'line',
            label: '睡眠スコア',
            data: sleepSeries.values,
            spanGaps: false, // null (no reading / a 0 tracking error) breaks the line instead of drawing a fake dip
            borderColor: '#a8763f',
            backgroundColor: '#a8763f',
            borderWidth: 3,
            pointBackgroundColor: sleepSeries.values.map((v, i) => sleepSeries.colors[i] || scoreColor(v)),
            // A light halo around every dot keeps it legible sitting on top of the step bars.
            pointBorderColor: cardBg,
            pointBorderWidth: 2,
            pointRadius: sleepSeries.colors.map(c => c ? 6 : 3),
            segment: {
              // Color each line segment by the score it's heading into, so the
              // trend itself carries the green/blue/orange/red read, not just the dots.
              borderColor: ctx => scoreColor(ctx.p1.parsed.y),
            },
            tension: 0.25,
            yAxisID: 'ySleep',
          },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            labels: {
              // Append a swatch-only entry explaining the orange highlight,
              // since it isn't a real dataset of its own.
              generateLabels(chart) {
                const items = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                items.push({
                  text: '通院日',
                  fillStyle: '#e0793a',
                  strokeStyle: '#e0793a',
                  lineWidth: 0,
                  hidden: false,
                });
                return items;
              },
            },
            onClick(e, legendItem, legend) {
              // The 通院日 swatch has no dataset behind it — ignore clicks on it
              // instead of letting Chart.js's default handler throw.
              if (legendItem.datasetIndex === undefined) return;
              Chart.defaults.plugins.legend.onClick.call(legend, e, legendItem, legend);
            },
          },
          title: { display: true, text: `睡眠・歩数${modeTitle}` },
          tooltip: {
            callbacks: {
              // Spell out units per series, and call out missing sleep-score
              // readings explicitly instead of silently omitting the row.
              label(ctx) {
                if (ctx.dataset.yAxisID === 'ySteps') {
                  return `歩数: ${ctx.parsed.y == null ? 'データなし' : ctx.parsed.y.toLocaleString() + '歩'}`;
                }
                return `睡眠スコア: ${ctx.parsed.y == null ? '欠損（記録なし）' : ctx.parsed.y}`;
              },
            },
          },
        },
        interaction: { mode: 'index', intersect: false },
        // Hovering a clinic day hands its memo off to the caller (to select
        // into the entry list / detail pane) rather than showing it in the
        // chart itself. Only fires on the index actually changing, so a
        // stray pass over the canvas doesn't spam re-selection.
        onHover(event, activeElements) {
          if (!recentDays || typeof onHoverEntry !== 'function') return;
          if (!activeElements.length) { lastHoverIndex = null; return; }
          const idx = activeElements[0].index;
          if (idx === lastHoverIndex) return;
          lastHoverIndex = idx;
          const memo = entriesByDate[keys[idx]];
          if (memo) onHoverEntry(memo);
        },
        scales: {
          ySteps: { type: 'linear', position: 'left', beginAtZero: true, max: STEPS_AXIS_MAX, title: { display: true, text: '歩数' } },
          ySleep: { type: 'linear', position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: '睡眠スコア' } },
        },
      },
    });
  }

  window.VitalsChart = {
    RECENT_DAYS,
    fmtDate,
    addDays,
    vitalsRangeForMode,
    renderVitalsChart,
  };
})();
