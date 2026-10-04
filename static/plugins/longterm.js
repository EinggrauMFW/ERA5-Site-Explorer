/* Long-term statistics tab: climatology, variability indices, interannual variation, extremes. */
(function () {
  'use strict';

  const { api, node, fmtNum, fmt, chartTheme, trackChart } = EraExplorer;

  /* ---- helpers ---------------------------------------------------------- */
  function kvRow(table, label, value, note) {
    const tr = node('tr');
    tr.append(node('th', '', label));
    const td = node('td', '', value);
    if (note) td.append(node('small', '', note));
    tr.append(td);
    table.append(tr);
  }

  function gridTable(columns, rows) {
    const table = node('table', 'grid-table');
    const head = node('tr');
    columns.forEach((col, i) => head.append(node('th', i ? 'num' : '', col)));
    table.append(head);
    rows.forEach(values => {
      const tr = node('tr');
      values.forEach((v, i) => tr.append(node('td', i ? 'num' : '', v)));
      table.append(tr);
    });
    return table;
  }

  function card(title) {
    const el = node('article', 'section-block lt-card');
    el.append(node('h3', '', title));
    return el;
  }

  function fmtV(v, unit) {
    if (v === null || v === undefined) return '—';
    return fmt(v, unit);
  }

  /* ---- build the tab ---------------------------------------------------- */
  function mount(panel, ctx) {
    panel.classList.add('lt-panel');

    /* parameter row */
    const paramRow = node('div', 'lt-params');
    const mkField = (label, id, val, attrs) => {
      const wrap = node('label', 'field');
      wrap.append(node('span', '', label));
      const input = node('input', '');
      input.id = id; input.value = val;
      Object.entries(attrs || {}).forEach(([k, v]) => input.setAttribute(k, v));
      wrap.append(input);
      return wrap;
    };
    paramRow.append(mkField('Threshold %', 'lt-thresh', '95', { type: 'number', min: '50', max: '99.9', step: '0.5' }));
    paramRow.append(mkField('Decluster h', 'lt-declust', '48', { type: 'number', min: '1', max: '720', step: '1' }));
    paramRow.append(mkField('Return years', 'lt-returns', '1,10,50,100', { type: 'text' }));
    paramRow.append(mkField('Bootstrap', 'lt-boot', '300', { type: 'number', min: '0', max: '1000', step: '50' }));
    const computeBtn = node('button', 'btn primary', 'Compute');
    paramRow.append(computeBtn);
    panel.append(paramRow);

    const status = node('p', 'lt-status', 'Loading…');
    panel.append(status);

    const content = node('div', 'lt-content');
    panel.append(content);

    /* load data */
    async function load() {
      status.className = 'lt-status';
      status.textContent = 'Computing long-term statistics…';
      content.replaceChildren();
      const thresh = document.getElementById('lt-thresh').value;
      const declust = document.getElementById('lt-declust').value;
      const returns = document.getElementById('lt-returns').value;
      const boot = document.getElementById('lt-boot').value;
      const path = `/api/jobs/${ctx.jobId}/longterm?threshold_pct=${encodeURIComponent(thresh)}&decluster_hours=${encodeURIComponent(declust)}&return_years=${encodeURIComponent(returns)}&bootstrap=${encodeURIComponent(boot)}`;
      let data;
      try {
        data = await api(path);
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
        status.className = 'lt-status form-error';
        return;
      }
      status.textContent = '';
      render(content, data);
    }
    computeBtn.addEventListener('click', load);
    load();
  }

  /* ---- render result ---------------------------------------------------- */
  function render(container, data) {
    container.replaceChildren();

    /* header with record length */
    const rec = data.record;
    const header = node('p', '', `Record: ${rec.start} to ${rec.end} · ${fmtV(rec.years, 'years')} · ${rec.records} records · ${rec.full_years.length} full years`);
    container.append(header);

    /* warnings box */
    if (data.warnings && data.warnings.length) {
      const box = node('div', 'warnings');
      data.warnings.forEach(w => box.append(node('p', '', w)));
      container.append(box);
    }

    /* monthly climatology chart */
    renderMonthlyChart(container, data.climatology.monthly);

    /* seasonal shares table */
    renderSeasonal(container, data.climatology.seasonal);

    /* indices table */
    renderIndices(container, data.indices);

    /* interannual */
    renderInterannual(container, data.interannual);

    /* extremes */
    if (data.extremes.status === 'ok') {
      renderExtremes(container, data.extremes);
    } else {
      const c = card('Extreme Hm0');
      c.append(node('p', 'hint', data.extremes.reason || 'Extremes not computed.'));
      container.append(c);
    }

    /* notes and unverified */
    if (data.notes && data.notes.length) {
      const c = card('Notes');
      data.notes.forEach(n => c.append(node('p', 'hint', n)));
      container.append(c);
    }
    if (data.unverified && data.unverified.length) {
      const c = card('Unverified');
      data.unverified.forEach(u => c.append(node('p', 'hint', u)));
      container.append(c);
    }
  }

  /* monthly climatology: bar chart + P10-P90 band */
  function renderMonthlyChart(container, monthly) {
    const c = card('Monthly climatology — mean flux (kW/m)');
    const holder = node('div', 'chart');
    c.append(holder);
    container.append(c);

    const months = monthly.map(m => m.month);
    const means = monthly.map(m => m.flux_mean_kw_m);
    const p10 = monthly.map(m => m.flux_p10_kw_m);
    const p90 = monthly.map(m => m.flux_p90_kw_m);
    const hasRange = p10.some(v => v !== null);

    const { line, band, accent, axis } = chartTheme();
    const series = [{ label: 'Month' }, { label: 'Mean flux (kW/m)', stroke: line, width: 2, fill: band }];
    const plotData = [months, means];
    if (hasRange) {
      series.push({ label: 'P10 (kW/m)', stroke: accent, width: 1, dash: [4, 4] });
      series.push({ label: 'P90 (kW/m)', stroke: accent, width: 1, dash: [4, 4] });
      plotData.push(p10, p90);
    }

    requestAnimationFrame(() => {
      trackChart(new uPlot({
        width: Math.max(280, holder.clientWidth || 600), height: 240,
        series, scales: { x: { time: false } },
        axes: [{ ...axis, label: 'Month', values: (_, ticks) => ticks.map(t => t >= 1 && t <= 12 ? EraExplorer.MONTHS[Math.round(t) - 1] : '') },
               { ...axis, label: 'kW/m', size: 56 }],
        legend: { live: true }
      }, plotData, holder));
    });
  }

  function renderSeasonal(container, seasonal) {
    const c = card('Seasonal shares');
    const rows = seasonal.map(s => [s.season, fmtV(s.flux_mean_kw_m, 'kW/m'), fmtV(s.share_of_annual_pct, '%')]);
    c.append(gridTable(['Season', 'Mean flux (kW/m)', 'Share of annual (%)'], rows));
    container.append(c);
  }

  function renderIndices(container, idx) {
    const c = card('Variability indices');
    const table = node('table', 'kv');
    const items = [
      ['COV', idx.cov],
      ['Annual mean flux', idx.annual_mean_flux_kw_m],
      ['MVI (monthly)', idx.mvi],
      ['SVI (seasonal)', idx.svi],
    ];
    items.forEach(([label, entry]) => {
      const val = entry.value !== null ? fmtNum(entry.value) : `— (${entry.reason})`;
      kvRow(table, label, val, entry.formula);
    });
    c.append(table);
    container.append(c);
  }

  function renderInterannual(container, ia) {
    const c = card('Interannual variation');
    if (!ia || ia.data === null) {
      c.append(node('p', 'hint', ia ? ia.reason : 'Not available'));
      container.append(c);
      return;
    }

    /* summary */
    const table = node('table', 'kv');
    kvRow(table, 'COV of annual means', fmtNum(ia.cov_of_annual_means));
    kvRow(table, 'Max year', String(ia.max_year));
    kvRow(table, 'Min year', String(ia.min_year));
    kvRow(table, 'Range ratio (max/min)', fmtNum(ia.range_ratio));
    c.append(table);

    /* table of annual means */
    const rows = ia.data.map(d => [String(d.year), fmtV(d.flux_mean_kw_m, 'kW/m'),
                                    fmtV(d.hm0_mean_m, 'm'), fmtV(d.anomaly_pct, '%')]);
    c.append(gridTable(['Year', 'Flux mean (kW/m)', 'Hm0 mean (m)', 'Anomaly (%)'], rows));

    /* chart */
    const chartHolder = node('div', 'chart');
    c.append(chartHolder);
    const years = ia.data.map(d => d.year);
    const fluxes = ia.data.map(d => d.flux_mean_kw_m);
    const { line, axis } = chartTheme();
    requestAnimationFrame(() => {
      trackChart(new uPlot({
        width: Math.max(280, chartHolder.clientWidth || 600), height: 200,
        series: [{ label: 'Year' }, { label: 'Annual mean flux (kW/m)', stroke: line, width: 2, points: { show: true, size: 5, fill: line } }],
        scales: { x: { time: false } },
        axes: [{ ...axis, label: 'Year', values: (_, ticks) => ticks.map(t => String(Math.round(t))) },
               { ...axis, label: 'kW/m', size: 56 }],
        legend: { live: true }
      }, [years, fluxes], chartHolder));
    });

    container.append(c);
  }

  function renderExtremes(container, ext) {
    /* summary */
    const c = card('Extreme Hm0 — peaks over threshold');
    const table = node('table', 'kv');
    kvRow(table, 'Threshold', fmtV(ext.threshold_m, 'm') + ` (P${ext.threshold_pct})`);
    kvRow(table, 'Peaks', String(ext.n_peaks));
    kvRow(table, 'Rate', fmtV(ext.rate_per_yr, '/yr'));
    kvRow(table, 'GPD shape ξ', fmtNum(ext.xi));
    kvRow(table, 'GPD scale σ', fmtV(ext.sigma, 'm'));
    c.append(table);

    /* return-level table */
    const rows = ext.levels.map(lv => [
      fmtNum(lv.return_period_yr) + ' yr',
      fmtV(lv.level_m, 'm'),
      lv.ci_low_m !== null ? fmtV(lv.ci_low_m, 'm') : '—',
      lv.ci_high_m !== null ? fmtV(lv.ci_high_m, 'm') : '—',
    ]);
    c.append(gridTable(['Return period', 'Level (m)', '90% CI low (m)', '90% CI high (m)'], rows));

    /* return-level plot */
    const chartHolder = node('div', 'chart');
    c.append(chartHolder);
    renderReturnLevelPlot(chartHolder, ext);

    container.append(c);

    /* threshold sensitivity */
    if (ext.sensitivity && ext.sensitivity.length) {
      const sc = card('Threshold sensitivity');
      const sRows = ext.sensitivity.map(s => [
        fmtNum(s.percentile) + '%',
        fmtV(s.threshold_m, 'm'),
        String(s.n_peaks),
        s.xi !== null ? fmtNum(s.xi) : '—',
        s.sigma !== null ? fmtV(s.sigma, 'm') : '—',
        s.level_m !== null ? fmtV(s.level_m, 'm') : '—',
      ]);
      sc.append(gridTable(['Percentile', 'Threshold (m)', 'Peaks', 'ξ', 'σ (m)', 'Return level (m)'], sRows));
      container.append(sc);
    }

    /* method */
    if (ext.method) {
      const mc = node('p', 'hint', ext.method);
      container.append(mc);
    }
  }

  function renderReturnLevelPlot(holder, ext) {
    const empirical = ext.empirical || [];
    const curve = ext.curve || [];
    if (!empirical.length && !curve.length) return;

    // uPlot needs one x array in ascending order: merge the fitted curve and the empirical peaks into
    // rows, drop non-positive return periods (log axis), sort by return period.
    const rows = [
      ...curve.map(c => [c.return_period_yr, c.level_m, null]),
      ...empirical.map(e => [e.return_period_yr, null, e.hm0_m]),
    ].filter(r => Number.isFinite(r[0]) && r[0] > 0).sort((a, b) => a[0] - b[0]);
    const data = [rows.map(r => r[0]), rows.map(r => r[1]), rows.map(r => r[2])];

    const { line, accent, axis } = chartTheme();
    requestAnimationFrame(() => {
      trackChart(new uPlot({
        width: Math.max(280, holder.clientWidth || 600), height: 260,
        series: [
          { label: 'Return period (yr)' },
          { label: 'Fitted (m)', stroke: line, width: 2, spanGaps: true },
          { label: 'Empirical (m)', stroke: accent, width: 0, points: { show: true, size: 5, fill: accent, stroke: accent } },
        ],
        scales: { x: { time: false, distr: 3 } },   // distr 3 = log10 axis, the usual return-level plot
        axes: [
          { ...axis, label: 'Return period (yr, log scale)', size: 50 },
          { ...axis, label: 'Hm0 (m)', size: 56 },
        ],
        legend: { live: true }
      }, data, holder));
    });
  }

  /* ---- register --------------------------------------------------------- */
  EraExplorer.registerTab({
    id: 'longterm',
    label: 'Long-term',
    order: 30,
    available() { return true; },
    mount
  });
})();
