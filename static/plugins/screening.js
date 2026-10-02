/* Site screening tab: map colouring, ranking, seasonal view and node comparison. */
(function() {
  const { registerTab, api, colourNodes, selectNode, nodeKey, chartTheme, trackChart, palette, fmtNum, fmt, node } = EraExplorer;

  let screeningData = null;
  let comparedNodes = [];
  let mapStat = 'flux_mean_kw_m';
  
  const stats = [
    { id: 'flux_mean_kw_m', label: 'Mean flux (kW/m)' },
    { id: 'hm0_mean_m', label: 'Mean Hm0 (m)' },
    { id: 'te_mean_s', label: 'Mean Te (s)' },
    { id: 'flux_p95_kw_m', label: '95th percentile flux (kW/m)' },
    { id: 'flux_cov', label: 'Flux variability (COV)' },
    { id: 'seasonality_ratio', label: 'Seasonality ratio (max/min season)' }
  ];
  
  const seasons = ['DJF', 'MAM', 'JJA', 'SON'];
  seasons.forEach(s => stats.push({ id: `season_${s}`, label: `Mean flux ${s} (kW/m)` }));
  
  const months = EraExplorer.MONTHS;
  months.forEach((m, i) => stats.push({ id: `month_${i}`, label: `Mean flux ${m} (kW/m)` }));
  
  function getStat(n, id) {
    if (id.startsWith('month_')) {
      const idx = parseInt(id.replace('month_', ''), 10);
      return n.monthly_flux_kw_m[idx];
    }
    if (id.startsWith('season_')) {
      return n.season_flux_kw_m[id.replace('season_', '')];
    }
    return n[id];
  }

  function updateMap() {
    if (!mapStat) {
      colourNodes(null);
      return;
    }
    const statDef = stats.find(s => s.id === mapStat);
    const values = {};
    screeningData.nodes.filter(n => n.valid).forEach(n => {
      values[nodeKey(n.lat, n.lon)] = getStat(n, mapStat);
    });
    colourNodes({ label: statDef.label, values });
  }

  function renderTable(container) {
    const table = node('table', 'screening-table grid-table');
    const thead = node('thead', '');
    const headerRow = node('tr', '');
    ['Lat', 'Lon', 'Depth (m)', 'Dist (km)', 'Hm0 (m)', 'Te (s)', 'Flux (kW/m)', 'P95 (kW/m)', 'COV', 'Seasonality'].forEach(text => {
      headerRow.append(node('th', 'num', text));
    });
    headerRow.append(node('th', '', 'Actions'));
    thead.append(headerRow);
    table.append(thead);

    const tbody = node('tbody', '');
    const validNodes = screeningData.nodes.filter(n => n.valid);
    
    // sort by selected mapStat
    validNodes.sort((a, b) => (getStat(b, mapStat) || 0) - (getStat(a, mapStat) || 0));

    validNodes.forEach(n => {
      const row = node('tr', '');
      let latText = EraExplorer.fmtCoord(n.lat);
      if (screeningData.default_node && n.lat === screeningData.default_node.lat && n.lon === screeningData.default_node.lon) {
        latText += ' (nearest)';
      }
      row.append(node('td', 'num', latText));
      row.append(node('td', 'num', EraExplorer.fmtCoord(n.lon)));
      row.append(node('td', 'num', n.depth_m != null ? fmtNum(n.depth_m) : '—'));
      row.append(node('td', 'num', fmtNum(n.distance_km)));
      row.append(node('td', 'num', n.hm0_mean_m != null ? fmtNum(n.hm0_mean_m) : '—'));
      row.append(node('td', 'num', n.te_mean_s != null ? fmtNum(n.te_mean_s) : '—'));
      row.append(node('td', 'num', n.flux_mean_kw_m != null ? fmtNum(n.flux_mean_kw_m) : '—'));
      row.append(node('td', 'num', n.flux_p95_kw_m != null ? fmtNum(n.flux_p95_kw_m) : '—'));
      row.append(node('td', 'num', n.flux_cov != null ? fmtNum(n.flux_cov) : '—'));
      row.append(node('td', 'num', n.seasonality_ratio != null ? fmtNum(n.seasonality_ratio) : '—'));

      const actions = node('td', '');
      const btnCompare = node('button', 'btn ghost', 'Compare');
      btnCompare.addEventListener('click', () => {
        if (comparedNodes.length < 3 && !comparedNodes.find(c => c.lat === n.lat && c.lon === n.lon)) {
          comparedNodes.push(n);
          renderComparison();
        }
      });
      const btnAnalyse = node('button', 'btn ghost', 'Analyse');
      btnAnalyse.addEventListener('click', () => {
        selectNode(n.lat, n.lon);
      });
      actions.append(btnCompare, btnAnalyse);
      row.append(actions);
      tbody.append(row);
    });
    table.append(tbody);
    
    container.replaceChildren(table);
  }

  function renderSeasonal(container) {
    const validNodes = screeningData.nodes.filter(n => n.valid);
    validNodes.sort((a, b) => (getStat(b, mapStat) || 0) - (getStat(a, mapStat) || 0));
    const top10 = validNodes.slice(0, 10);

    const table = node('table', 'heat-table');
    const thead = node('thead', '');
    const headerRow = node('tr', '');
    headerRow.append(node('th', '', 'Node'));
    months.forEach(m => headerRow.append(node('th', 'num', m)));
    thead.append(headerRow);
    table.append(thead);

    const tbody = node('tbody', '');
    
    // Find max for color scaling
    let maxVal = 0;
    top10.forEach(n => {
      n.monthly_flux_kw_m.forEach(v => {
        if (v && v > maxVal) maxVal = v;
      });
    });

    top10.forEach(n => {
      const row1 = node('tr', '');
      row1.append(node('td', '', `${EraExplorer.fmtCoord(n.lat)}, ${EraExplorer.fmtCoord(n.lon)} (Months)`));
      n.monthly_flux_kw_m.forEach(v => {
        const td = node('td', 'heat-cell');
        if (v != null) {
          td.textContent = fmtNum(v);
          const pct = maxVal > 0 ? (v / maxVal) * 100 : 0;
          td.style.background = `color-mix(in srgb, var(--chart-line) ${pct}%, transparent)`;
        } else {
          td.textContent = '—';
        }
        row1.append(td);
      });
      tbody.append(row1);

      const row2 = node('tr', '');
      row2.append(node('td', '', 'Seasons'));
      // span 3 for each season
      seasons.forEach((s) => {
        const td = node('td', 'heat-cell');
        td.colSpan = 3;
        const v = n.season_flux_kw_m[s];
        if (v != null) {
          td.textContent = `${s}: ${fmtNum(v)}`;
          const pct = maxVal > 0 ? (v / maxVal) * 100 : 0;
          td.style.background = `color-mix(in srgb, var(--chart-line) ${pct}%, transparent)`;
        } else {
          td.textContent = `${s}: —`;
        }
        row2.append(td);
      });
      tbody.append(row2);
    });

    table.append(tbody);
    container.replaceChildren(table);
  }

  let compareContainerEl = null;

  function renderComparison() {
    if (!compareContainerEl) return;
    compareContainerEl.replaceChildren();

    if (comparedNodes.length === 0) {
      compareContainerEl.append(node('p', 'hint', 'Select 2 or 3 nodes to compare.'));
      return;
    }

    const chips = node('div', 'compare-chips');
    comparedNodes.forEach((n, idx) => {
      const chip = node('div', 'compare-chip');
      chip.append(node('span', '', `${EraExplorer.fmtCoord(n.lat)}, ${EraExplorer.fmtCoord(n.lon)}`));
      const rm = node('button', '', '×');
      rm.addEventListener('click', () => {
        comparedNodes.splice(idx, 1);
        renderComparison();
      });
      chip.append(rm);
      chips.append(chip);
    });
    compareContainerEl.append(chips);

    if (comparedNodes.length >= 2) {
      const table = node('table', 'screening-table grid-table');
      const thead = node('thead', '');
      const hRow = node('tr', '');
      hRow.append(node('th', '', 'Metric'));
      comparedNodes.forEach(n => hRow.append(node('th', 'num', `${EraExplorer.fmtCoord(n.lat)}, ${EraExplorer.fmtCoord(n.lon)}`)));
      thead.append(hRow);
      table.append(thead);

      const tbody = node('tbody', '');
      const metrics = [
        { label: 'Depth (m)', key: 'depth_m' },
        { label: 'Distance (km)', key: 'distance_km' },
        { label: 'Mean flux (kW/m)', key: 'flux_mean_kw_m' },
        { label: 'Delta vs first (%)', fn: (n) => {
            const base = comparedNodes[0].flux_mean_kw_m;
            if (!base || base === 0 || !n.flux_mean_kw_m) return '—';
            return fmtNum((n.flux_mean_kw_m - base) / base * 100);
        }},
        { label: 'P95 flux (kW/m)', key: 'flux_p95_kw_m' },
        { label: 'COV', key: 'flux_cov' },
        { label: 'Mean Hm0 (m)', key: 'hm0_mean_m' },
        { label: 'Mean Te (s)', key: 'te_mean_s' },
        { label: 'Seasonality ratio', key: 'seasonality_ratio' }
      ];
      
      seasons.forEach(s => {
        metrics.push({ label: `Season ${s} (kW/m)`, fn: (n) => n.season_flux_kw_m[s] });
      });

      metrics.forEach(m => {
        const tr = node('tr', '');
        tr.append(node('td', '', m.label));
        comparedNodes.forEach(n => {
          let val;
          if (m.fn) val = m.fn(n);
          else val = n[m.key];
          tr.append(node('td', 'num', val != null ? (typeof val === 'number' ? fmtNum(val) : val) : '—'));
        });
        tbody.append(tr);
      });
      table.append(tbody);
      compareContainerEl.append(table);

      // uPlot Chart
      const chartContainer = node('div', 'compare-chart-container');
      compareContainerEl.append(chartContainer);

      const data = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
      ];
      const series = [
        { label: 'Month' }
      ];

      const colors = palette();
      
      comparedNodes.forEach((n, idx) => {
        data.push(n.monthly_flux_kw_m.map(v => v != null ? v : null));
        series.push({
          label: `${EraExplorer.fmtCoord(n.lat)}, ${EraExplorer.fmtCoord(n.lon)}`,
          stroke: colors[idx % colors.length],
          width: 2
        });
      });

      const opts = {
        width: 800,
        height: 300,
        ...chartTheme(),
        series,
        axes: [
          {
            scale: 'x',
            values: (u, vals) => vals.map(v => months[v - 1] || v)
          },
          {
            scale: 'y',
            label: 'Mean flux (kW/m)'
          }
        ]
      };

      const chart = new uPlot(opts, data, chartContainer);
      trackChart(chart);
    }
  }

  EraExplorer.registerTab({
    id: 'screening',
    label: 'Screening',
    order: 10,
    available(ctx) {
      return ctx.nodeData && ctx.nodeData.nodes && ctx.nodeData.nodes.filter(n => n.valid).length >= 2;
    },
    async mount(panel, ctx) {
      panel.append(node('p', 'hint', 'Loading site screening statistics...'));
      try {
        screeningData = await api(`/api/jobs/${ctx.jobId}/screening`, { noNode: true });
        
        panel.replaceChildren();

        if (screeningData.warnings && screeningData.warnings.length > 0) {
          const wDiv = node('div', 'warnings');
          screeningData.warnings.forEach(w => wDiv.append(node('p', '', w)));
          panel.append(wDiv);
        }
        if (screeningData.sampling_note) {
          panel.append(node('p', 'hint', screeningData.sampling_note));
        }
        const rec = screeningData.record;
        const rc = screeningData.requested_coordinate;
        panel.append(node('p', 'hint', `Statistics are over the downloaded record (${rec.start.substring(0, 10)} to ${rec.end.substring(0, 10)}) for site (${rc.latitude}, ${rc.longitude}) on route ${screeningData.route}. A short record is not a resource estimate.`));

        const controls = node('div', 'screening-controls');
        
        const select = node('select', '');
        stats.forEach(s => {
          const opt = node('option', '', s.label);
          opt.value = s.id;
          if (s.id === mapStat) opt.selected = true;
          select.append(opt);
        });
        
        select.addEventListener('change', () => {
          mapStat = select.value;
          updateMap();
          renderTable(tableCard);
          renderSeasonal(seasonalCard);
        });

        const resetMapBtn = node('button', 'btn ghost', 'Reset map colours');
        resetMapBtn.addEventListener('click', () => {
          mapStat = '';
          select.value = '';
          updateMap();
        });

        const downloadLink = node('a', 'btn secondary', 'Download table (CSV)');
        downloadLink.href = `/api/jobs/${ctx.jobId}/screening.csv`;
        
        controls.append(node('span', '', 'Colour the map by:'), select, resetMapBtn, downloadLink);
        panel.append(controls);

        const tableCard = node('div', 'card screening-table-card');
        panel.append(tableCard);

        const seasonalCard = node('div', 'card');
        panel.append(node('h3', '', 'Seasonal View (Top 10 Nodes)'));
        panel.append(seasonalCard);

        compareContainerEl = node('div', 'card compare-container');
        panel.append(node('h3', '', 'Comparison'));
        panel.append(compareContainerEl);

        updateMap();
        renderTable(tableCard);
        renderSeasonal(seasonalCard);
        renderComparison();
        
      } catch (err) {
        panel.replaceChildren(node('p', 'form-error', err.message));
      }
    }
  });

})();
