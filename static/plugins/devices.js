(function () {
    'use strict';

    let dialogEl = null;
    let listData = null;
    let currentId = null;
    let selectedDetail = null;
    let focusedCell = null; // {i: hm0_index, j: period_index}

    // UI elements
    let sidebarEl, mainEl, listEl, errorEl, detailEl, loadingEl, footerEl, pickerState, resizeObserver;

    function initDialog() {
        if (dialogEl) return;

        dialogEl = EraExplorer.node('dialog', 'device-picker-dialog');
        const layout = EraExplorer.node('div', 'device-picker-layout');

        const header = EraExplorer.node('div', 'device-picker-header');
        const title = EraExplorer.node('h2', '', 'Choose a device power matrix');
        title.id = 'device-picker-title';
        const closeBtn = EraExplorer.node('button', 'device-picker-close', '×');
        closeBtn.setAttribute('aria-label', 'Close dialog');
        closeBtn.onclick = () => dialogEl.close();
        header.appendChild(title);
        header.appendChild(closeBtn);

        dialogEl.setAttribute('aria-labelledby', 'device-picker-title');

        sidebarEl = EraExplorer.node('div', 'device-picker-sidebar');
        mainEl = EraExplorer.node('div', 'device-picker-main');

        loadingEl = EraExplorer.node('p', 'hint', 'Loading devices…');
        errorEl = EraExplorer.node('div', 'form-error');
        errorEl.style.display = 'none';

        listEl = EraExplorer.node('ul', 'device-picker-list');
        listEl.setAttribute('role', 'listbox');
        listEl.tabIndex = 0;

        listEl.addEventListener('keydown', handleListKeydown);

        sidebarEl.appendChild(listEl);

        detailEl = EraExplorer.node('div', '');
        mainEl.appendChild(loadingEl);
        mainEl.appendChild(errorEl);
        mainEl.appendChild(detailEl);

        layout.appendChild(sidebarEl);
        layout.appendChild(mainEl);

        dialogEl.appendChild(header);
        dialogEl.appendChild(layout);

        footerEl = EraExplorer.node('div', 'device-picker-footer');
        dialogEl.appendChild(footerEl);

        dialogEl.addEventListener('close', () => {
            if (pickerState && pickerState.triggerBtn) {
                pickerState.triggerBtn.focus();
            }
        });

        document.body.appendChild(dialogEl);
    }

    function showError(msg) {
        errorEl.textContent = msg;
        errorEl.style.display = 'block';
        const retryBtn = EraExplorer.node('button', 'btn secondary', 'Retry');
        retryBtn.onclick = loadList;
        errorEl.appendChild(document.createElement('br'));
        errorEl.appendChild(retryBtn);
        loadingEl.style.display = 'none';
        detailEl.style.display = 'none';
    }

    function updateFooter(d, rInp, wInp, pSel) {
        footerEl.textContent = '';
        if (!d) return;

        const footerText = EraExplorer.node('div', 'device-picker-footer-text', `Selected: ${d.name}`);

        const cancelBtn = EraExplorer.node('button', 'btn', 'Cancel');
        cancelBtn.onclick = () => dialogEl.close();

        const pickBtn = EraExplorer.node('button', 'btn primary', 'Pick this device');
        pickBtn.onclick = () => {
            if (pickerState.onPick) {
                pickerState.onPick({
                    id: d.id,
                    name: d.name,
                    rated_kw: rInp ? rInp.value : '',
                    width_m: wInp ? wInp.value : '',
                    period_type: pSel ? pSel.value : 'unknown',
                    bin_convention: d.bin_convention
                });
            }
            dialogEl.close();
        };

        footerEl.appendChild(footerText);
        footerEl.appendChild(cancelBtn);
        footerEl.appendChild(pickBtn);
    }

    async function loadList() {
        errorEl.style.display = 'none';
        loadingEl.style.display = 'block';
        listEl.textContent = '';
        detailEl.textContent = '';
        footerEl.textContent = '';

        try {
            const data = await EraExplorer.api('/api/devices');
            listData = data;
            renderList();

            if (listData.devices.length > 0) {
                let toSelect = listData.devices[0].id;
                if (pickerState.initialId && listData.devices.find(d => d.id === pickerState.initialId)) {
                    toSelect = pickerState.initialId;
                }
                selectItem(toSelect);
            } else {
                detailEl.textContent = 'No devices found.';
            }
        } catch (e) {
            showError(e.message);
        }
    }

    function renderList() {
        loadingEl.style.display = 'none';
        listEl.textContent = '';
        detailEl.style.display = 'block';

        sidebarEl.querySelectorAll('.device-picker-note').forEach(el => el.remove());

        if (!listData.configured && listData.devices.length === 1 && listData.devices[0].synthetic) {
            const note = EraExplorer.node('p', 'hint device-picker-note', 'The catalogue folder is set with the DEVICES_DIR environment variable. Devices are read from devices.json and *.csv files in that folder.');
            sidebarEl.insertBefore(note, listEl);
        }

        if (listData.warnings && listData.warnings.length > 0) {
            const count = listData.warnings.length;
            const summaryText = count === 1 ? '1 file was skipped' : `${count} files were skipped`;
            const details = EraExplorer.node('details', 'device-picker-note');
            details.style.margin = '10px';
            const summary = EraExplorer.node('summary', '', summaryText);
            details.appendChild(summary);
            const ul = EraExplorer.node('ul');
            listData.warnings.forEach(w => ul.appendChild(EraExplorer.node('li', 'hint', w)));
            details.appendChild(ul);
            sidebarEl.insertBefore(details, listEl);
        }

        listData.devices.forEach(d => {
            const li = EraExplorer.node('li', 'device-picker-item');
            li.setAttribute('role', 'option');
            li.setAttribute('data-id', d.id);
            li.id = 'device-item-' + d.id;

            li.appendChild(EraExplorer.node('strong', '', d.name));

            let specStr = '';
            if (d.rated_kw !== null && d.rated_kw !== undefined) {
                specStr += `rated ${EraExplorer.fmtNum(d.rated_kw)} kW · `;
            }
            specStr += `${d.n_hm0} × ${d.n_period} cells`;
            li.appendChild(EraExplorer.node('p', 'hint', specStr));

            const badges = EraExplorer.node('div', 'device-picker-badges');

            let pTypeStr = 'period axis not stated';
            if (d.period_type === 'tp') pTypeStr = 'Tp';
            else if (d.period_type === 'te') pTypeStr = 'Te';
            const pBadge = EraExplorer.node('span', 'pill', pTypeStr);
            if (d.period_type === 'tp' || d.period_type === 'te') {
                pBadge.style.textTransform = 'none';
            }
            badges.appendChild(pBadge);

            if (d.synthetic) {
                badges.appendChild(EraExplorer.node('span', 'pill', 'synthetic'));
            } else {
                const provLower = (d.provenance || '').toLowerCase();
                if (provLower.includes('unverified') || (d.origin === 'csv' && d.provenance === 'No metadata entry')) {
                    badges.appendChild(EraExplorer.node('span', 'pill pill-failed', 'unverified'));
                }
            }

            li.appendChild(badges);

            li.onclick = () => selectItem(d.id);
            li.ondblclick = () => {
                selectItem(d.id);
                pickCurrent();
            };

            listEl.appendChild(li);
        });
    }

    function handleListKeydown(e) {
        if (!listData || listData.devices.length === 0) return;
        const ids = listData.devices.map(d => d.id);
        const currentIndex = ids.indexOf(currentId);

        let newIndex = currentIndex;
        if (e.key === 'ArrowDown') {
            newIndex = Math.min(ids.length - 1, currentIndex + 1);
            e.preventDefault();
        } else if (e.key === 'ArrowUp') {
            newIndex = Math.max(0, currentIndex - 1);
            e.preventDefault();
        } else if (e.key === 'Home') {
            newIndex = 0;
            e.preventDefault();
        } else if (e.key === 'End') {
            newIndex = ids.length - 1;
            e.preventDefault();
        } else if (e.key === 'Enter') {
            pickCurrent();
            e.preventDefault();
        }

        if (newIndex !== currentIndex && newIndex >= 0) {
            selectItem(ids[newIndex]);
            const li = document.getElementById('device-item-' + ids[newIndex]);
            if (li) li.scrollIntoView({ block: 'nearest' });
        }
    }

    async function selectItem(id) {
        currentId = id;

        const items = listEl.querySelectorAll('.device-picker-item');
        items.forEach(el => {
            if (el.getAttribute('data-id') === id) {
                el.setAttribute('aria-selected', 'true');
                listEl.setAttribute('aria-activedescendant', el.id);
            } else {
                el.setAttribute('aria-selected', 'false');
            }
        });

        try {
            const data = await EraExplorer.api(`/api/devices/${id}`);
            if (currentId !== id) return; // stale
            selectedDetail = data;
            renderDetail();
        } catch (e) {
            if (currentId === id) {
                detailEl.textContent = '';
                footerEl.textContent = '';
                const err = EraExplorer.node('div', 'form-error', e.message);
                detailEl.appendChild(err);
            }
        }
    }

    function renderDetail() {
        focusedCell = null;
        if (resizeObserver) {
            resizeObserver.disconnect();
            resizeObserver = null;
        }
        detailEl.textContent = '';
        if (!selectedDetail) return;

        const d = selectedDetail;

        // Heatmap SVG
        const hmContainer = EraExplorer.node('div', 'heatmap-container');

        const tooltip = EraExplorer.node('div', 'heatmap-tooltip');
        tooltip.style.display = 'none';

        const readout = EraExplorer.node('div', 'heatmap-readout');
        readout.setAttribute('aria-live', 'polite');

        const legend = EraExplorer.node('div', 'heatmap-legend');

        hmContainer.appendChild(legend);
        hmContainer.appendChild(readout);
        hmContainer.appendChild(tooltip);

        detailEl.appendChild(hmContainer);

        function drawHeatmap(containerWidth) {
            const oldSvg = hmContainer.querySelector('.heatmap-svg');
            if (oldSvg) oldSvg.remove();

            const svgNS = "http://www.w3.org/2000/svg";
            const svg = document.createElementNS(svgNS, "svg");
            svg.setAttribute('class', 'heatmap-svg');
            svg.setAttribute('tabindex', '0');
            svg.setAttribute('role', 'application');
            const maxStr = EraExplorer.fmtNum(d.power_max_kw);
            svg.setAttribute('aria-label', `${d.name} power matrix, ${d.n_hm0} by ${d.n_period} cells, maximum power ${maxStr} kW. Use arrow keys to explore cells.`);

            const hm0_edges = d.hm0_edges;
            const p_edges = d.period_edges;

            let p_min = p_edges[0];
            if (p_min < 0 && p_min >= -(p_edges[1] - p_edges[0])) p_min = 0;
            let p_max = p_edges[p_edges.length - 1];

            let h_min = hm0_edges[0];
            if (h_min < 0 && h_min >= -(hm0_edges[1] - hm0_edges[0])) h_min = 0;
            let h_max = hm0_edges[hm0_edges.length - 1];

            const p_span = p_max - p_min;
            const h_span = h_max - h_min;

            const marginLeft = 50;
            const marginBottom = 40;
            const marginTop = 10;
            const marginRight = 15;

            let plotWidth = containerWidth - marginLeft - marginRight;
            if (plotWidth < 100) plotWidth = 100;
            const plotHeight = Math.min(plotWidth * 0.8, 350);

            svg.setAttribute('viewBox', `0 0 ${plotWidth + marginLeft + marginRight} ${plotHeight + marginTop + marginBottom}`);
            svg.style.width = '100%';
            svg.style.height = 'auto';

            const defs = document.createElementNS(svgNS, 'defs');
            const pattern = document.createElementNS(svgNS, 'pattern');
            pattern.setAttribute('id', 'hatch-pattern');
            pattern.setAttribute('width', '8');
            pattern.setAttribute('height', '8');
            pattern.setAttribute('patternUnits', 'userSpaceOnUse');
            const path = document.createElementNS(svgNS, 'path');
            path.setAttribute('d', 'M-2,2 l4,-4 M0,8 l8,-8 M6,10 l4,-4');
            path.setAttribute('stroke', 'var(--muted)');
            path.setAttribute('stroke-width', '1');
            pattern.appendChild(path);
            defs.appendChild(pattern);
            svg.appendChild(defs);

            const plotG = document.createElementNS(svgNS, 'g');
            plotG.setAttribute('transform', `translate(${marginLeft}, ${marginTop})`);

            const mapX = p => (p - p_min) / p_span * plotWidth;
            const mapY = h => (1 - (h - h_min) / h_span) * plotHeight;

            let hasUndefined = false;
            const cells = [];
            const cellEls = [];

            for (let i = 0; i < hm0_edges.length - 1; i++) {
                cellEls[i] = [];
                for (let j = 0; j < p_edges.length - 1; j++) {
                    const p1 = Math.max(p_min, p_edges[j]);
                    const p2 = Math.max(p_min, p_edges[j+1]);
                    const h1 = Math.max(h_min, hm0_edges[i]);
                    const h2 = Math.max(h_min, hm0_edges[i+1]);

                    const val = d.power_kw[i][j];

                    const x = mapX(p1);
                    const w = mapX(p2) - x;
                    const y = mapY(h2);
                    const h = mapY(h1) - y;

                    if (w > 0 && h > 0) {
                        const rect = document.createElementNS(svgNS, 'rect');
                        rect.setAttribute('x', x);
                        rect.setAttribute('y', y);
                        rect.setAttribute('width', w);
                        rect.setAttribute('height', h);
                        rect.setAttribute('class', 'heatmap-cell');

                        if (val === null) {
                            hasUndefined = true;
                            rect.setAttribute('fill', 'url(#hatch-pattern)');
                        } else {
                            const pct = d.power_max_kw > 0 ? (val / d.power_max_kw) * 100 : 0;
                            rect.style.fill = `color-mix(in srgb, var(--chart-line) ${pct}%, var(--surface-2))`;
                        }

                        plotG.appendChild(rect);
                        cellEls[i][j] = rect;
                    }
                }
            }

            const outlineRect = document.createElementNS(svgNS, 'rect');
            outlineRect.setAttribute('fill', 'none');
            outlineRect.setAttribute('stroke', 'var(--ink)');
            outlineRect.setAttribute('stroke-width', '2');
            outlineRect.style.display = 'none';
            outlineRect.style.pointerEvents = 'none';
            plotG.appendChild(outlineRect);

            // Axes
            const xAxis = document.createElementNS(svgNS, 'g');
            xAxis.setAttribute('transform', `translate(0, ${plotHeight})`);
            xAxis.appendChild(createLine(0, 0, plotWidth, 0, 'heatmap-axis-line'));

            let pLabel = 'Period (s)';
            if (d.period_type === 'tp') pLabel = 'Tp (s)';
            else if (d.period_type === 'te') pLabel = 'Te (s)';

            const pTicks = getNiceTicks(p_min, p_max, plotWidth);
            for (const p of pTicks) {
                const x = mapX(p);
                if (x >= 0 && x <= plotWidth) {
                    xAxis.appendChild(createLine(x, 0, x, 5, 'heatmap-axis-line'));
                    const txt = createText(x, 18, plainNumber(p), 'heatmap-axis-text');
                    txt.setAttribute('text-anchor', 'middle');
                    xAxis.appendChild(txt);
                }
            }
            const pLabelEl = createText(plotWidth / 2, 35, pLabel, 'heatmap-axis-text');
            pLabelEl.setAttribute('text-anchor', 'middle');
            xAxis.appendChild(pLabelEl);
            plotG.appendChild(xAxis);

            const yAxis = document.createElementNS(svgNS, 'g');
            yAxis.appendChild(createLine(0, 0, 0, plotHeight, 'heatmap-axis-line'));

            const hTicks = getNiceTicks(h_min, h_max, plotHeight);
            for (const h of hTicks) {
                const y = mapY(h);
                if (y >= 0 && y <= plotHeight) {
                    yAxis.appendChild(createLine(0, y, -5, y, 'heatmap-axis-line'));
                    const txt = createText(-10, y + 4, plainNumber(h), 'heatmap-axis-text');
                    txt.setAttribute('text-anchor', 'end');
                    yAxis.appendChild(txt);
                }
            }
            const hLabelEl = createText(-40, plotHeight / 2, 'Hm0 (m)', 'heatmap-axis-text');
            hLabelEl.setAttribute('text-anchor', 'middle');
            hLabelEl.setAttribute('transform', `rotate(-90, -40, ${plotHeight/2})`);
            yAxis.appendChild(hLabelEl);
            plotG.appendChild(yAxis);

            svg.appendChild(plotG);

            function hoverCell(i, j) {
                focusedCell = {i, j};
                const val = d.power_kw[i][j];
                const p = (p_edges[j] + p_edges[j+1]) / 2;
                const h = (hm0_edges[i] + hm0_edges[i+1]) / 2;

                let valStr = "undefined (counts as 0 kW in the assessment)";
                if (val !== null && val !== undefined) {
                    valStr = `${EraExplorer.fmtNum(val)} kW`;
                    if (d.rated_kw) {
                        const pct = Math.round((val / d.rated_kw) * 100);
                        valStr += ` (${pct}% of rated)`;
                    }
                }

                const pName = d.period_type === 'tp' ? 'Tp' : d.period_type === 'te' ? 'Te' : 'Period';
                const text = `Hm0 ${plainNumber(h)} m · ${pName} ${plainNumber(p)} s → ${valStr}`;
                readout.textContent = text;
                tooltip.textContent = text;

                const rect = cellEls[i] && cellEls[i][j];
                if (rect) {
                    outlineRect.setAttribute('x', rect.getAttribute('x'));
                    outlineRect.setAttribute('y', rect.getAttribute('y'));
                    outlineRect.setAttribute('width', rect.getAttribute('width'));
                    outlineRect.setAttribute('height', rect.getAttribute('height'));
                    outlineRect.style.display = 'block';

                    const svgRect = svg.getBoundingClientRect();
                    const cellRect = rect.getBoundingClientRect();
                    tooltip.style.display = 'block';
                    tooltip.style.left = (cellRect.left - svgRect.left + cellRect.width / 2) + 'px';
                    tooltip.style.top = (cellRect.top - svgRect.top - 30) + 'px';
                }
            }

            svg.addEventListener('mouseleave', () => {
                tooltip.style.display = 'none';
            });

            svg.addEventListener('mousemove', (e) => {
                const svgRect = svg.getBoundingClientRect();

                const scaleX = (plotWidth + marginLeft + marginRight) / svgRect.width;
                const scaleY = (plotHeight + marginTop + marginBottom) / svgRect.height;

                const svgX = (e.clientX - svgRect.left) * scaleX - marginLeft;
                const svgY = (e.clientY - svgRect.top) * scaleY - marginTop;

                let found = false;

                if (svgX >= 0 && svgX <= plotWidth && svgY >= 0 && svgY <= plotHeight) {
                    const p = (svgX / plotWidth) * p_span + p_min;
                    const h = (1 - (svgY / plotHeight)) * h_span + h_min;

                    let j_idx = -1;
                    let i_idx = -1;

                    for (let j = 0; j < p_edges.length - 1; j++) {
                        if (p >= p_edges[j] && p < p_edges[j+1]) {
                            j_idx = j;
                            break;
                        }
                    }

                    for (let i = 0; i < hm0_edges.length - 1; i++) {
                        if (h >= hm0_edges[i] && h < hm0_edges[i+1]) {
                            i_idx = i;
                            break;
                        }
                    }

                    if (i_idx !== -1 && j_idx !== -1 && cellEls[i_idx] && cellEls[i_idx][j_idx]) {
                        if (!focusedCell || focusedCell.i !== i_idx || focusedCell.j !== j_idx) {
                            hoverCell(i_idx, j_idx);
                        }
                        found = true;
                    }
                }

                if (!found) {
                    tooltip.style.display = 'none';
                    outlineRect.style.display = 'none';
                    readout.textContent = '';
                    focusedCell = null;
                } else if (tooltip.style.display === 'block') {
                    tooltip.style.left = (e.clientX - svgRect.left + 15) + 'px';
                    tooltip.style.top = (e.clientY - svgRect.top + 15) + 'px';
                }
            });

            svg.addEventListener('keydown', (e) => {
                if (!focusedCell) {
                    if (['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
                        hoverCell(0, 0);
                        e.preventDefault();
                    }
                    return;
                }
                let {i, j} = focusedCell;

                if (e.key === 'ArrowRight') {
                    j = Math.min(p_edges.length - 2, j + 1);
                    e.preventDefault();
                } else if (e.key === 'ArrowLeft') {
                    j = Math.max(0, j - 1);
                    e.preventDefault();
                } else if (e.key === 'ArrowUp') {
                    i = Math.min(hm0_edges.length - 2, i + 1);
                    e.preventDefault();
                } else if (e.key === 'ArrowDown') {
                    i = Math.max(0, i - 1);
                    e.preventDefault();
                } else if (e.key === 'Home') {
                    j = 0;
                    e.preventDefault();
                } else if (e.key === 'End') {
                    j = p_edges.length - 2;
                    e.preventDefault();
                }

                if (i !== focusedCell.i || j !== focusedCell.j) {
                    hoverCell(i, j);
                }
            });

            legend.textContent = '';
            const gBar = EraExplorer.node('div', 'heatmap-gradient-bar');
            gBar.style.background = 'linear-gradient(to right, var(--surface-2), var(--chart-line))';
            legend.appendChild(EraExplorer.node('span', '', '0'));
            legend.appendChild(gBar);
            legend.appendChild(EraExplorer.node('span', '', EraExplorer.fmtNum(d.power_max_kw) + ' kW'));

            if (hasUndefined) {
                const undefWrap = EraExplorer.node('div');
                undefWrap.style.marginLeft = '16px';
                const swatch = EraExplorer.node('span', 'heatmap-swatch');
                swatch.style.background = 'url("data:image/svg+xml,%3Csvg xmlns=\\\'http://www.w3.org/2000/svg\\\' width=\\\'8\\\' height=\\\'8\\\'%3E%3Cpath d=\\\'M-2,2 l4,-4 M0,8 l8,-8 M6,10 l4,-4\\\' stroke=\\\'%235b6863\\\' stroke-width=\\\'1\\\'/%3E%3C/svg%3E")';
                undefWrap.appendChild(swatch);
                undefWrap.appendChild(document.createTextNode(' undefined in the source (counts as 0 kW in the assessment)'));
                legend.appendChild(undefWrap);
            }

            hmContainer.insertBefore(svg, legend);
        }

        resizeObserver = new ResizeObserver(entries => {
            for (let entry of entries) {
                if (entry.contentRect.width > 0) {
                    drawHeatmap(entry.contentRect.width);
                }
            }
        });
        resizeObserver.observe(hmContainer);
        detailEl.appendChild(hmContainer);

        const info = EraExplorer.node('div', 'device-picker-info');
        info.appendChild(EraExplorer.node('h4', '', d.name));

        if (d.source) info.appendChild(EraExplorer.node('p', '', d.source));
        if (d.provenance) info.appendChild(EraExplorer.node('p', '', d.provenance));

        if (d.notes && d.notes.length > 0) {
            info.appendChild(EraExplorer.node('strong', '', 'Read before using'));
            const ul = EraExplorer.node('ul');
            d.notes.forEach(n => ul.appendChild(EraExplorer.node('li', '', n)));
            info.appendChild(ul);
        }

        const kv = EraExplorer.node('table', 'kv');

        kv.appendChild(createKvRow('Matrix size', `${d.n_hm0} × ${d.n_period} cells`));
        kv.appendChild(createKvRow('Hm0 range', `${EraExplorer.fmtNum(d.hm0_range[0])} – ${EraExplorer.fmtNum(d.hm0_range[1])} m`));
        kv.appendChild(createKvRow('Period range', `${EraExplorer.fmtNum(d.period_range[0])} – ${EraExplorer.fmtNum(d.period_range[1])} s`));
        kv.appendChild(createKvRow('Maximum power', `${EraExplorer.fmtNum(d.power_max_kw)} kW`));
        kv.appendChild(createKvRow('Defined cells', `${d.defined_cells} of ${d.total_cells} (${EraExplorer.fmtNum(d.defined_pct)}%)`));

        const maxHint = 'a plateau: rated power, or a cap on the source\'s colour scale';
        kv.appendChild(createKvRow('Cells at 99% of the maximum or above', String(d.near_max_cells), maxHint));
        info.appendChild(kv);

        detailEl.appendChild(info);

        const settings = EraExplorer.node('div', 'device-settings-form');
        settings.appendChild(EraExplorer.node('h4', '', 'Settings used when picked'));

        const rDiv = EraExplorer.node('div', 'field');
        rDiv.appendChild(EraExplorer.node('label', '', 'Rated power (kW, optional)'));
        const rInp = EraExplorer.node('input');
        rInp.type = 'number'; rInp.step = 'any';
        if (d.rated_kw) rInp.value = d.rated_kw;
        rDiv.appendChild(rInp);

        const wDiv = EraExplorer.node('div', 'field');
        wDiv.appendChild(EraExplorer.node('label', '', 'Characteristic width (m, optional)'));
        const wInp = EraExplorer.node('input');
        wInp.type = 'number'; wInp.step = 'any';
        if (d.width_m) wInp.value = d.width_m;
        wDiv.appendChild(wInp);

        const pDiv = EraExplorer.node('div', 'field');
        pDiv.appendChild(EraExplorer.node('label', '', 'Period axis'));
        const pSel = EraExplorer.node('select');
        const oTe = EraExplorer.node('option', '', 'Te'); oTe.value = 'te';
        const oTp = EraExplorer.node('option', '', 'Tp'); oTp.value = 'tp';
        const oUn = EraExplorer.node('option', '', 'Unknown: evaluate both'); oUn.value = 'unknown';
        pSel.appendChild(oTe); pSel.appendChild(oTp); pSel.appendChild(oUn);
        pSel.value = d.period_type;
        if (pSel.selectedIndex === -1) pSel.value = 'unknown';
        pDiv.appendChild(pSel);

        if (d.period_type === 'tp') {
            const pHint = EraExplorer.node('p', 'hint', "The source labels this axis Tp. If the manufacturer's matrix really uses the energy period, choose Te or Unknown.");
            pHint.style.marginTop = '4px';
            pDiv.appendChild(pHint);
        }

        settings.appendChild(rDiv);
        settings.appendChild(wDiv);
        settings.appendChild(pDiv);

        detailEl.appendChild(settings);

        updateFooter(d, rInp, wInp, pSel);
    }

    function pickCurrent() {
        if (!selectedDetail) return;
        const btn = footerEl.querySelector('.btn.primary');
        if (btn) btn.click();
    }

    function createLine(x1, y1, x2, y2, cls) {
        const l = document.createElementNS("http://www.w3.org/2000/svg", 'line');
        l.setAttribute('x1', x1);
        l.setAttribute('y1', y1);
        l.setAttribute('x2', x2);
        l.setAttribute('y2', y2);
        l.setAttribute('class', cls);
        return l;
    }
    function createText(x, y, text, cls) {
        const t = document.createElementNS("http://www.w3.org/2000/svg", 'text');
        t.setAttribute('x', x);
        t.setAttribute('y', y);
        t.setAttribute('class', cls);
        t.textContent = text;
        return t;
    }
    function createKvRow(label, val, note) {
        const tr = EraExplorer.node('tr');
        tr.appendChild(EraExplorer.node('th', '', label));
        const td = EraExplorer.node('td', '', val);
        if (note) td.appendChild(EraExplorer.node('small', '', note));
        tr.appendChild(td);
        return tr;
    }

    // The fewest decimals that keep the value exact to six figures: 5, 7.5, 12.25 (not 5.00, 7.50).
    function plainNumber(value) {
        return String(parseFloat(value.toPrecision(6)));
    }

    function getNiceTicks(min, max, plotSize) {
        const maxTicks = Math.max(3, Math.min(8, Math.floor(plotSize / 40)));
        const range = max - min;
        if (range <= 0) return [min];

        const roughStep = range / (maxTicks - 1);
        if (roughStep === 0) return [min];

        const stepPower = Math.pow(10, Math.floor(Math.log10(roughStep)));
        const normalizedStep = roughStep / stepPower;

        let niceStep;
        if (normalizedStep < 1.5) niceStep = 1;
        else if (normalizedStep < 3) niceStep = 2;
        else if (normalizedStep < 7) niceStep = 5;
        else niceStep = 10;

        const step = niceStep * stepPower;
        let start = Math.ceil(min / step) * step;

        const ticks = [];
        for (let t = start; t <= max + 1e-9; t += step) {
            ticks.push(t);
        }
        return ticks;
    }

    EraExplorer.openDevicePicker = function(options) {
        initDialog();
        pickerState = options;
        pickerState.triggerBtn = document.activeElement;

        dialogEl.showModal();
        loadList();
    };
})();
