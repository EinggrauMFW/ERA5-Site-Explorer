(function () {
  'use strict';  // plugin scripts share app.js's global scope: keep every name local

const node = EraExplorer.node;

EraExplorer.registerAction({
  id: 'export-report',
  label: '↓ Report (.md)',
  order: 10,
  href(ctx) {
    if (!ctx.analysis) return null;
    const q = EraExplorer.nodeQuery();
    return `/api/jobs/${ctx.jobId}/export/report.md${q ? '?' + q : ''}`;
  }
});

EraExplorer.registerAction({
  id: 'export-bundle',
  label: '↓ Bundle (.zip)',
  order: 11,
  href(ctx) {
    if (!ctx.analysis) return null;
    const q = EraExplorer.nodeQuery();
    return `/api/jobs/${ctx.jobId}/export/bundle.zip${q ? '?' + q : ''}`;
  }
});

EraExplorer.registerTab({
  id: 'export',
  label: 'Export',
  order: 90,
  available(ctx) {
    return !!ctx.analysis;
  },
  mount(panel, ctx) {
    const q = EraExplorer.nodeQuery();
    const query = q ? '?' + q : '';
    const jobId = ctx.jobId;

    const wrap = node('div', 'section-block');
    wrap.append(node('h2', '', 'Export data and figures'));
    
    const table = node('table', 'kv');
    wrap.append(table);
    
    function addLink(url, label, desc) {
      const tr = node('tr');
      const th = node('th');
      const a = node('a');
      a.href = url;
      a.textContent = label;
      a.setAttribute('download', '');
      th.append(a);
      
      const td = node('td', '', desc);
      tr.append(th, td);
      table.append(tr);
    }
    
    EraExplorer.api(`/api/jobs/${jobId}/export`)
      .then(manifest => {
        addLink(`/api/jobs/${jobId}/export/${manifest.report}${query}`, manifest.report, 'Markdown report containing metadata and sections');
        addLink(`/api/jobs/${jobId}/export/${manifest.bundle}${query}`, manifest.bundle, 'Everything in one ZIP file (tables, figures, JSON, report)');
        
        for (const t of manifest.tables) {
          addLink(`/api/jobs/${jobId}/export/tables/${t.filename}${query}`, t.filename, t.description);
        }

        for (const f of manifest.figures) {
            const tr = node('tr');
            const th = node('th', '', f.title);
            const td = node('td');
            
            for (const v of f.variants) {
                const a = node('a');
                const urlArgs = [];
                if (v.theme !== 'light') urlArgs.push(`theme=${v.theme}`);
                if (v.quantity) urlArgs.push(`quantity=${v.quantity}`);
                const argsStr = urlArgs.length ? (query ? query + '&' : '?') + urlArgs.join('&') : query;
                a.href = `/api/jobs/${jobId}/export/figures/${f.name}.svg${argsStr}`;
                
                let text = v.theme === 'dark' ? 'Dark' : 'Light';
                if (v.quantity) {
                    text += ` (${v.quantity})`;
                }
                
                a.textContent = text;
                a.setAttribute('download', '');
                a.style.marginRight = '10px';
                td.append(a);
            }
            
            tr.append(th, td);
            table.append(tr);
        }
      })
      .catch(err => {
         const p = node('p', 'form-error');
         p.textContent = 'Failed to load export options: ' + err.message;
         wrap.append(p);
      });

    panel.append(wrap);
  }
});
})();
