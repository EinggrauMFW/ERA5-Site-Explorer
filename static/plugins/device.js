/* global EraExplorer */

EraExplorer.registerTab({
    id: "device",
    label: "Device",
    order: 20,
    available(ctx) { return true; },
    mount(panel, ctx) {
        let savedDevices = [];
        let currentResult = null;
        let showEnergy = false;
        
        function render() {
            panel.textContent = '';
            
            const formContainer = EraExplorer.node('div', 'device-form');
            
            const errorDiv = EraExplorer.node('div', 'form-error');
            errorDiv.style.display = 'none';
            formContainer.appendChild(errorDiv);
            
            const nameDiv = EraExplorer.node('div', 'field');
            nameDiv.appendChild(EraExplorer.node('label', '', 'Device name'));
            const nameInput = EraExplorer.node('input', 'input');
            nameInput.type = 'text';
            nameInput.id = 'device_name';
            nameInput.maxLength = 60;
            nameInput.required = true;
            nameDiv.appendChild(nameInput);
            
            const csvDiv = EraExplorer.node('div', 'field');
            const csvLabel = EraExplorer.node('label', '', 'Power Matrix CSV (');
            const useExample = EraExplorer.node('a', '', 'use the synthetic example');
            useExample.href = '#';
            useExample.id = 'use_example_csv';
            csvLabel.appendChild(useExample);
            csvLabel.appendChild(document.createTextNode(')'));
            csvDiv.appendChild(csvLabel);
            const csvInput = EraExplorer.node('input', '');
            csvInput.type = 'file';
            csvInput.id = 'device_csv';
            csvInput.accept = '.csv,text/csv';
            csvDiv.appendChild(csvInput);
            
            const ratedDiv = EraExplorer.node('div', 'field');
            ratedDiv.appendChild(EraExplorer.node('label', '', 'Rated power (kW, optional)'));
            const ratedInput = EraExplorer.node('input', 'input');
            ratedInput.type = 'number';
            ratedInput.id = 'device_rated';
            ratedInput.min = '0';
            ratedInput.step = 'any';
            ratedDiv.appendChild(ratedInput);
            
            const widthDiv = EraExplorer.node('div', 'field');
            widthDiv.appendChild(EraExplorer.node('label', '', 'Characteristic width (m, optional)'));
            const widthInput = EraExplorer.node('input', 'input');
            widthInput.type = 'number';
            widthInput.id = 'device_width';
            widthInput.min = '0';
            widthInput.step = 'any';
            widthDiv.appendChild(widthInput);
            
            const row1 = EraExplorer.node('div', 'device-form-row');
            row1.appendChild(nameDiv);
            row1.appendChild(csvDiv);
            formContainer.appendChild(row1);
            
            const row2 = EraExplorer.node('div', 'device-form-row');
            row2.appendChild(ratedDiv);
            row2.appendChild(widthDiv);
            formContainer.appendChild(row2);
            
            const periodDiv = EraExplorer.node('div', 'field');
            periodDiv.appendChild(EraExplorer.node('label', '', 'Period axis'));
            const periodSelect = EraExplorer.node('select', 'input');
            periodSelect.id = 'device_period';
            const optTe = EraExplorer.node('option', '', 'Te (energy period)'); optTe.value = 'te';
            const optTp = EraExplorer.node('option', '', 'Tp (peak period)'); optTp.value = 'tp';
            const optUnknown = EraExplorer.node('option', '', 'Unknown: evaluate both'); optUnknown.value = 'unknown'; optUnknown.selected = true;
            periodSelect.appendChild(optTe); periodSelect.appendChild(optTp); periodSelect.appendChild(optUnknown);
            periodDiv.appendChild(periodSelect);
            
            const binDiv = EraExplorer.node('div', 'field');
            binDiv.appendChild(EraExplorer.node('label', '', 'Bin convention'));
            const binSelect = EraExplorer.node('select', 'input');
            binSelect.id = 'device_bin';
            const optCentres = EraExplorer.node('option', '', 'Centres'); optCentres.value = 'centres'; optCentres.selected = true;
            const optLower = EraExplorer.node('option', '', 'Lower edges'); optLower.value = 'lower_edges';
            binSelect.appendChild(optCentres); binSelect.appendChild(optLower);
            binDiv.appendChild(binSelect);
            
            const row3 = EraExplorer.node('div', 'device-form-row');
            row3.appendChild(periodDiv);
            row3.appendChild(binDiv);
            formContainer.appendChild(row3);
            
            const btnDiv = EraExplorer.node('div', 'field');
            const assessBtn = EraExplorer.node('button', 'btn primary', 'Assess');
            btnDiv.appendChild(assessBtn);
            formContainer.appendChild(btnDiv);
            
            panel.appendChild(formContainer);
            
            useExample.addEventListener('click', async (e) => {
                e.preventDefault();
                try {
                    const res = await fetch(`/api/device/example.csv`);
                    if (!res.ok) throw new Error("Failed to load example CSV");
                    const text = await res.text();
                    nameInput.value = "Synthetic Example";
                    panel.dataset.csvText = text;
                    csvInput.value = "";
                } catch(err) {
                    showError(err.message);
                }
            });
            
            csvInput.addEventListener('change', (e) => {
                const file = e.target.files[0];
                if (file) {
                    const reader = new FileReader();
                    reader.onload = (ev) => {
                        panel.dataset.csvText = ev.target.result;
                    };
                    reader.readAsText(file);
                }
            });
            
            function showError(msg) {
                errorDiv.textContent = msg;
                errorDiv.style.display = 'block';
            }
            
            assessBtn.addEventListener('click', async () => {
                errorDiv.style.display = 'none';
                
                const name = nameInput.value;
                const csv_text = panel.dataset.csvText;
                const rated_kw = ratedInput.value;
                const width_m = widthInput.value;
                const period_type = periodSelect.value;
                const bin_convention = binSelect.value;
                
                if (!name || !csv_text) {
                    showError("Name and CSV are required.");
                    return;
                }
                
                try {
                    const payload = {
                        name, csv_text, rated_kw, width_m, period_type, bin_convention
                    };
                    const res = await EraExplorer.api(`/api/jobs/${ctx.jobId}/device`, {
                        method: 'POST',
                        body: JSON.stringify(payload)
                    });
                    currentResult = res;
                    loadSaved();
                    renderResult();
                } catch (e) {
                    showError(e.message);
                }
            });
            
            const resultContainer = EraExplorer.node('div', 'device-result');
            panel.appendChild(resultContainer);
            
            const savedContainer = EraExplorer.node('div', 'device-saved-list section-block');
            savedContainer.appendChild(EraExplorer.node('h3', '', 'Saved Devices'));
            const listContent = EraExplorer.node('div', 'list-content');
            savedContainer.appendChild(listContent);
            panel.appendChild(savedContainer);
            
            function buildMetric(label, valueText, hintText = null) {
                const div = EraExplorer.node('div', 'metric');
                div.appendChild(EraExplorer.node('label', '', label));
                div.appendChild(EraExplorer.node('div', 'value', valueText));
                if (hintText) {
                    div.appendChild(EraExplorer.node('div', 'hint', hintText));
                }
                return div;
            }

            function buildTr(cells, isTh = false) {
                const tr = EraExplorer.node('tr');
                cells.forEach(c => {
                    const cell = EraExplorer.node(isTh ? 'th' : 'td', c.className || '');
                    cell.textContent = c.text;
                    tr.appendChild(cell);
                });
                return tr;
            }
            
            function renderResult() {
                resultContainer.textContent = '';
                if (!currentResult) return;
                
                if (currentResult.ambiguity) {
                    const amb = currentResult.ambiguity;
                    const banner = EraExplorer.node('div', 'device-ambiguity-banner', `AEP lies between ${amb.aep_mwh_min.toFixed(1)} and ${amb.aep_mwh_max.toFixed(1)} MWh/yr depending on the period axis; confirm the device's period definition with its source.`);
                    resultContainer.appendChild(banner);
                }
                
                const cardsContainer = EraExplorer.node('div', 'device-cards-container');
                
                for (const [key, evalData] of Object.entries(currentResult.indexings)) {
                    const card = EraExplorer.node('div', 'device-card');
                    card.appendChild(EraExplorer.node('h3', '', currentResult.period_labels[key]));
                    
                    const binData = evalData.bin;
                    
                    const metrics = EraExplorer.node('div', 'metrics');
                    const aepVal = binData.aep_mwh !== null ? `${EraExplorer.fmtNum(binData.aep_mwh)} MWh/yr` : '-';
                    metrics.appendChild(buildMetric('AEP', aepVal));
                    
                    if (binData.capacity_factor_pct !== null) {
                        metrics.appendChild(buildMetric('Capacity Factor', `${EraExplorer.fmtNum(binData.capacity_factor_pct)}%`));
                    }
                    
                    const mpVal = binData.mean_power_kw !== null ? `${EraExplorer.fmtNum(binData.mean_power_kw)} kW` : '-';
                    metrics.appendChild(buildMetric('Mean Power', mpVal));
                    
                    const cwVal = binData.capture_width_energy_weighted_m !== null ? `${EraExplorer.fmtNum(binData.capture_width_energy_weighted_m)} m` : '-';
                    let cwHint = null;
                    if (binData.capture_width_energy_weighted_ratio !== null && binData.capture_width_energy_weighted_ratio !== undefined) {
                        cwHint = `Ratio: ${EraExplorer.fmtNum(binData.capture_width_energy_weighted_ratio)}`;
                    }
                    metrics.appendChild(buildMetric('Capture Width (Energy Weighted)', cwVal, cwHint));
                    
                    const soVal = binData.share_outside_pct !== null ? `${EraExplorer.fmtNum(binData.share_outside_pct)}%` : '-';
                    metrics.appendChild(buildMetric('Records Outside', soVal));
                    
                    const sfoVal = binData.flux_outside_support_pct !== null ? `${EraExplorer.fmtNum(binData.flux_outside_support_pct)}%` : '-';
                    metrics.appendChild(buildMetric('Flux Outside', sfoVal));
                    
                    card.appendChild(metrics);
                    
                    const compDiv = EraExplorer.node('div', '');
                    compDiv.style.marginTop = '1rem';
                    const compTable = EraExplorer.node('table', 'grid-table');
                    compTable.style.width = '100%';
                    const compThead = EraExplorer.node('thead');
                    compThead.appendChild(buildTr([{text: 'Estimator'}, {text: 'AEP (MWh/yr)'}, {text: 'Mean Power (kW)'}], true));
                    compTable.appendChild(compThead);
                    
                    const compTbody = EraExplorer.node('tbody');
                    compTbody.appendChild(buildTr([
                        {text: 'Bin'},
                        {text: evalData.bin.aep_mwh !== null ? EraExplorer.fmtNum(evalData.bin.aep_mwh) : '-', className: 'num'},
                        {text: evalData.bin.mean_power_kw !== null ? EraExplorer.fmtNum(evalData.bin.mean_power_kw) : '-', className: 'num'}
                    ]));
                    compTbody.appendChild(buildTr([
                        {text: 'Bilinear'},
                        {text: evalData.bilinear.aep_mwh !== null ? EraExplorer.fmtNum(evalData.bilinear.aep_mwh) : '-', className: 'num'},
                        {text: evalData.bilinear.mean_power_kw !== null ? EraExplorer.fmtNum(evalData.bilinear.mean_power_kw) : '-', className: 'num'}
                    ]));
                    compTable.appendChild(compTbody);
                    compDiv.appendChild(compTable);
                    card.appendChild(compDiv);
                    
                    const monthlyDiv = EraExplorer.node('div', '');
                    monthlyDiv.style.marginTop = '1rem';
                    monthlyDiv.appendChild(EraExplorer.node('h4', '', 'Monthly'));
                    const mTable = EraExplorer.node('table', 'grid-table');
                    mTable.style.width = '100%';
                    const mThead = EraExplorer.node('thead');
                    mThead.appendChild(buildTr([{text: 'Month'}, {text: 'Records'}, {text: 'Mean Power (kW)'}, {text: 'Share of Annual Energy (%)'}], true));
                    mTable.appendChild(mThead);
                    
                    const mTbody = EraExplorer.node('tbody');
                    for (const m of evalData.monthly) {
                        mTbody.appendChild(buildTr([
                            {text: m.month.toString()},
                            {text: m.records.toString(), className: 'num'},
                            {text: m.mean_power_kw !== null ? EraExplorer.fmtNum(m.mean_power_kw) : '-', className: 'num'},
                            {text: m.energy_share_pct !== null ? EraExplorer.fmtNum(m.energy_share_pct) : '-', className: 'num'}
                        ]));
                    }
                    mTable.appendChild(mTbody);
                    monthlyDiv.appendChild(mTable);
                    card.appendChild(monthlyDiv);
                    
                    const occWrapper = EraExplorer.node('div', 'occupancy-wrapper');
                    const occToggle = EraExplorer.node('button', 'btn ghost', showEnergy ? 'Showing: Share of Energy' : 'Showing: Share of Records');
                    occToggle.onclick = () => {
                        showEnergy = !showEnergy;
                        renderResult();
                    };
                    occWrapper.appendChild(occToggle);
                    
                    const occTable = EraExplorer.node('table', 'occupancy-table');
                    const hm0_edges = evalData.hm0_edges;
                    const p_edges = evalData.period_edges;
                    const matrix_data = showEnergy ? evalData.energy_pct : evalData.occupancy_pct;
                    
                    const oThead = EraExplorer.node('thead');
                    const oTr = EraExplorer.node('tr');
                    const oTh0 = EraExplorer.node('th', '', 'Hm0 \\ Period');
                    oTr.appendChild(oTh0);
                    for(let j=0; j<p_edges.length-1; j++) {
                        oTr.appendChild(EraExplorer.node('th', '', `[${p_edges[j].toFixed(1)}, ${p_edges[j+1].toFixed(1)})`));
                    }
                    oThead.appendChild(oTr);
                    occTable.appendChild(oThead);
                    
                    const oTbody = EraExplorer.node('tbody');
                    for (let i = hm0_edges.length - 2; i >= 0; i--) {
                        const tr = EraExplorer.node('tr');
                        tr.appendChild(EraExplorer.node('th', '', `[${hm0_edges[i].toFixed(1)}, ${hm0_edges[i+1].toFixed(1)})`));
                        for (let j = 0; j < p_edges.length - 1; j++) {
                            const val = matrix_data[i][j];
                            const td = EraExplorer.node('td', 'num');
                            if (val > 0) {
                                td.textContent = val.toFixed(2);
                                td.style.background = `color-mix(in srgb, var(--chart-line) ${val}%, transparent)`;
                                if (val > 50) {
                                    td.style.color = 'var(--surface)';
                                }
                            } else {
                                td.textContent = '-';
                            }
                            tr.appendChild(td);
                        }
                        oTbody.appendChild(tr);
                    }
                    occTable.appendChild(oTbody);
                    occWrapper.appendChild(occTable);
                    card.appendChild(occWrapper);
                    
                    cardsContainer.appendChild(card);
                }
                
                resultContainer.appendChild(cardsContainer);
                
                if (currentResult.warnings && currentResult.warnings.length > 0) {
                    const warnDiv = EraExplorer.node('div', 'warnings');
                    warnDiv.appendChild(EraExplorer.node('strong', '', 'Warnings:'));
                    const ul = EraExplorer.node('ul');
                    currentResult.warnings.forEach(w => ul.appendChild(EraExplorer.node('li', '', w)));
                    warnDiv.appendChild(ul);
                    resultContainer.appendChild(warnDiv);
                }
                
                if (currentResult.notes && currentResult.notes.length > 0) {
                    const notesDiv = EraExplorer.node('div', 'hint');
                    notesDiv.appendChild(EraExplorer.node('strong', '', 'Notes:'));
                    const ul = EraExplorer.node('ul');
                    currentResult.notes.forEach(n => ul.appendChild(EraExplorer.node('li', '', n)));
                    notesDiv.appendChild(ul);
                    resultContainer.appendChild(notesDiv);
                }
            }
            
            async function loadSaved() {
                try {
                    const res = await EraExplorer.api(`/api/jobs/${ctx.jobId}/device`);
                    savedDevices = res.devices;
                    renderSaved();
                } catch(e) {
                    console.error("Failed to load saved devices", e);
                }
            }
            
            function renderSaved() {
                listContent.textContent = '';
                if (savedDevices.length === 0) {
                    listContent.appendChild(EraExplorer.node('p', 'hint', 'No saved devices yet.'));
                    return;
                }
                
                savedDevices.forEach(d => {
                    const item = EraExplorer.node('div', 'device-list-item');
                    const infoDiv = EraExplorer.node('div');
                    const title = EraExplorer.node('strong', '', d.name);
                    infoDiv.appendChild(title);
                    if (d.node) {
                        infoDiv.appendChild(document.createTextNode(' '));
                        infoDiv.appendChild(EraExplorer.node('span', 'hint', `(node ${d.node[0].toFixed(3)}, ${d.node[1].toFixed(3)})`));
                    }
                    
                    let aepText = '';
                    if (d.aep_mwh_range[0] === null || d.aep_mwh_range[1] === null) {
                        aepText = '- MWh/yr';
                    } else if (d.aep_mwh_range[0] === d.aep_mwh_range[1]) {
                        aepText = `${EraExplorer.fmtNum(d.aep_mwh_range[0])} MWh/yr`;
                    } else {
                        aepText = `${EraExplorer.fmtNum(d.aep_mwh_range[0])} - ${EraExplorer.fmtNum(d.aep_mwh_range[1])} MWh/yr`;
                    }
                    
                    infoDiv.appendChild(EraExplorer.node('div', 'hint', aepText));
                    item.appendChild(infoDiv);
                    
                    const actions = EraExplorer.node('div', 'device-list-item-actions');
                    const openBtn = EraExplorer.node('button', 'btn secondary', 'Open');
                    openBtn.onclick = async () => {
                        try {
                            currentResult = await EraExplorer.api(`/api/jobs/${ctx.jobId}/device/${d.slug}`);
                            renderResult();
                        } catch(e) {
                            showError(e.message);
                        }
                    };
                    
                    const delBtn = EraExplorer.node('button', 'btn ghost', 'Delete');
                    delBtn.onclick = async () => {
                        try {
                            await EraExplorer.api(`/api/jobs/${ctx.jobId}/device/${d.slug}`, { method: 'DELETE' });
                            if (currentResult && currentResult.name === d.name) {
                                currentResult = null;
                                renderResult();
                            }
                            loadSaved();
                        } catch(e) {
                            showError(e.message);
                        }
                    };
                    
                    actions.appendChild(openBtn);
                    actions.appendChild(delBtn);
                    item.appendChild(actions);
                    listContent.appendChild(item);
                });
            }
            
            loadSaved();
        }
        
        render();
    }
});
