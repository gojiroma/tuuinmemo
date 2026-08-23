// Shared date-bucketing + Chart.js rendering for the sleep/steps vitals
// chart, used by both the edit page and the doctor view page so the two
// stay in sync (period definitions, colors, highlight logic...).
(() => {
  // Chart.js defaults to a fixed gray for text/gridlines, which reads fine
  // on a light card but goes muddy on the dark card background — keep it in
  // step with the theme tokens in static/theme.css.
  function applyChartTheme() {
    if (typeof Chart === 'undefined') return;
    const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    Chart.defaults.color = dark ? '#9aa4b3' : '#667085';
    Chart.defaults.borderColor = dark ? '#2a2f39' : '#e2e6ec';
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

  const RECENT_DAYS = 50;

  function vitalsRangeForMode(mode) {
    const end = fmtDate(new Date());
    if (mode === 'recent') return { start: addDays(end, -(RECENT_DAYS - 1)), end };
    if (mode === 'week') return { start: addDays(end, -7 * 12 + 1), end };
    return { start: addDays(end, -365), end }; // month: ~12 months back
  }

  // Every bucket key in [start, end] for the given mode, so bar/line series stay aligned.
  function bucketKeysForRange(mode, start, end) {
    const keys = [];
    if (mode === 'recent') {
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

  function aggregateToKeys(rawRows, dateField, valueField, mode, keys, clinicDates, convertFn) {
    const groupFn = mode === 'week' ? weekStart : mode === 'month' ? monthStart : (d => d);
    const labelFn = mode === 'month' ? monthLabel : shortLabel;
    const buckets = {};
    keys.forEach(k => { buckets[k] = { sum: 0, count: 0, clinic: false }; });

    rawRows.forEach(r => {
      const key = groupFn(r[dateField]);
      if (buckets[key]) {
        buckets[key].sum += r[valueField];
        buckets[key].count += 1;
      }
    });
    clinicDates.forEach(cd => {
      const key = groupFn(cd);
      if (buckets[key]) buckets[key].clinic = true;
    });

    return {
      labels: keys.map(labelFn),
      values: keys.map(k => convertFn(buckets[k].count ? buckets[k].sum / buckets[k].count : 0)),
      colors: keys.map(k => buckets[k].clinic ? '#e0793a' : null),
    };
  }

  // entries: memo rows (every entry counts as a clinic-day marker).
  function renderVitalsChart(canvasEl, existingChart, entries, vitals, mode) {
    const { start, end } = vitalsRangeForMode(mode);
    const keys = bucketKeysForRange(mode, start, end);
    const clinicDates = new Set(
      entries.filter(m => m.date >= start && m.date <= end).map(m => m.date)
    );

    const sleepSeries = aggregateToKeys(vitals.sleep, 'date', 'duration', mode, keys, clinicDates, v => +(v / 60).toFixed(1));
    const stepsSeries = aggregateToKeys(vitals.steps, 'date', 'steps', mode, keys, clinicDates, v => Math.round(v));

    if (existingChart) existingChart.destroy();

    const modeTitle = mode === 'recent' ? `（直近${RECENT_DAYS}日）` : mode === 'week' ? '（週平均）' : '（月平均）';

    return new Chart(canvasEl, {
      data: {
        labels: stepsSeries.labels,
        datasets: [
          {
            type: 'bar',
            label: '歩数',
            data: stepsSeries.values,
            backgroundColor: stepsSeries.colors.map(c => c || '#4a9e6f'),
            yAxisID: 'ySteps',
          },
          {
            type: 'line',
            label: '睡眠(時間)',
            data: sleepSeries.values,
            borderColor: '#3a6ea5',
            backgroundColor: '#3a6ea5',
            pointBackgroundColor: sleepSeries.colors.map(c => c || '#3a6ea5'),
            pointRadius: sleepSeries.colors.map(c => c ? 6 : 3),
            tension: 0.25,
            yAxisID: 'ySleep',
          },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true },
          title: { display: true, text: `睡眠・歩数${modeTitle}` },
        },
        scales: {
          ySteps: { type: 'linear', position: 'left', beginAtZero: true, title: { display: true, text: '歩数' } },
          ySleep: { type: 'linear', position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: '睡眠(時間)' } },
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
