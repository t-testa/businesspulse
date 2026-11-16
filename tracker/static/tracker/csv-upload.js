/* Browser-only preview. The server is authoritative for mappings and every data row. */
(function () {
  'use strict';
  const normalize = value => value.trim().toLowerCase().replace(/[^a-z0-9]/g, '');

  function parsePreview(text, maxColumns = 100, maxHeaderLength = 200) {
    text = text.replace(/^\uFEFF/, '');
    if (text.includes('\0')) throw new Error('This does not look like a CSV file.');
    const records = [];
    let row = [], cell = '', quoted = false, closed = false, started = false;
    function endCell() {
      row.push(cell); cell = ''; closed = false;
      if (row.length > maxColumns) throw new Error(`Maximum ${maxColumns} columns.`);
    }
    function endRow() {
      if (!started && !row.length && !cell) return;
      endCell(); records.push(row); row = []; started = false;
    }
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (quoted) {
        if (char === '"') {
          if (text[i + 1] === '"') { cell += '"'; i++; }
          else { quoted = false; closed = true; }
        } else cell += char;
      } else if (char === ',') {
        endCell(); started = true;
      } else if (char === '\r' || char === '\n') {
        endRow();
        if (char === '\r' && text[i + 1] === '\n') i++;
        if (records.length === 11) break;
      } else {
        if (closed) throw new Error('Unexpected text after a closing quote. Check CSV quoting.');
        if (char === '"' && !cell) quoted = true;
        else cell += char;
        started = true;
      }
    }
    if (records.length < 11) {
      if (quoted) throw new Error('Unclosed quoted field in the preview.');
      endRow();
    }
    if (!records.length) throw new Error('CSV is empty or missing headers.');
    const headers = records[0];
    const keys = headers.map(header => normalize(header) || header.trim().toLowerCase());
    if (headers.some(header => !header.trim() || header.length > maxHeaderLength)) {
      throw new Error(`Headers must be nonempty and at most ${maxHeaderLength} characters.`);
    }
    if (new Set(keys).size !== headers.length) throw new Error('Duplicate or equivalent column names. Rename them in the source CSV.');
    const rows = records.slice(1);
    if (!rows.length) throw new Error('No data rows found in CSV.');
    if (rows.some(record => record.length !== headers.length)) throw new Error('A preview row has missing or extra cells. Check separators and quotes.');
    return {headers, rows};
  }

  function suggestMapping(headers, aliases) {
    return Object.fromEntries(Object.entries(aliases).map(([field, names]) => {
      const accepted = names.map(normalize);
      const matches = headers.filter(header => accepted.includes(normalize(header)));
      return [field, matches.length === 1 ? matches[0] : ''];
    }));
  }

  function init(doc) {
    const form = doc.getElementById('csv-upload-form');
    if (!form) return;
    const config = JSON.parse(doc.getElementById('preview-config').textContent);
    const saved = JSON.parse(doc.getElementById('saved-mappings').textContent);
    const fileInput = doc.getElementById('id_file');
    const preset = doc.getElementById('id_saved_mapping');
    const hidden = doc.getElementById('id_mapping');
    const status = doc.getElementById('preview-status');
    const button = doc.getElementById('import-button');
    const controls = doc.getElementById('mapping-controls');
    const preview = doc.getElementById('csv-preview');
    const selects = [...form.querySelectorAll('[data-mapping-field]')];
    let headers = [], generation = 0, previous = null, submitting = false;
    try { previous = JSON.parse(hidden.value); } catch (_) { /* Empty initial form. */ }
    button.disabled = true;

    function update() {
      const mapping = Object.fromEntries(selects.map(select => [select.dataset.mappingField, select.value]));
      const values = Object.values(mapping);
      const valid = headers.length && values.every(value => headers.includes(value)) && new Set(values).size === 5;
      hidden.value = JSON.stringify(mapping);
      button.disabled = !valid || submitting;
      status.textContent = valid ? 'Preview ready. Review the mappings, then import. All rows will be validated on the server.'
        : 'Choose a different source column for each of the five required fields. Missing or ambiguous suggestions need your review.';
    }
    function applyMapping(mapping) {
      selects.forEach(select => {
        const value = mapping && Object.hasOwn(mapping, select.dataset.mappingField) ? mapping[select.dataset.mappingField] : '';
        select.value = headers.includes(value) ? value : '';
      });
      update();
    }
    preset.addEventListener('change', () => {
      if (!headers.length) return;
      const profile = saved.find(item => String(item.id) === preset.value);
      applyMapping(profile ? profile.columns : suggestMapping(headers, config.aliases));
    });
    selects.forEach(select => select.addEventListener('change', update));
    fileInput.addEventListener('change', async () => {
      const current = ++generation;
      headers = []; hidden.value = ''; button.disabled = true;
      controls.hidden = true; preview.hidden = true;
      const file = fileInput.files[0];
      if (!file) { status.textContent = 'Choose a CSV file to preview.'; return; }
      status.textContent = 'Reading preview locally…';
      try {
        if (!file.name.toLowerCase().endsWith('.csv')) throw new Error('Please choose a .csv file.');
        if (file.size > config.maxBytes) throw new Error('Maximum file size is 2 MB.');
        const bytes = await file.arrayBuffer();
        if (current !== generation) return;
        let text;
        try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
        catch (_) { throw new Error('Save the file as UTF-8 CSV and try again.'); }
        const parsed = parsePreview(text, config.maxColumns, config.maxHeaderLength);
        headers = parsed.headers;
        selects.forEach(select => {
          select.replaceChildren();
          for (const name of ['', ...headers]) {
            const option = doc.createElement('option');
            option.value = name; option.textContent = name || 'Choose a column'; select.append(option);
          }
        });
        const table = doc.getElementById('preview-table');
        const head = table.querySelector('thead'), body = table.querySelector('tbody');
        head.replaceChildren(); body.replaceChildren();
        function appendRow(target, values, tag) {
          const row = doc.createElement('tr');
          values.forEach(value => {
            const cell = doc.createElement(tag);
            if (tag === 'th') cell.scope = 'col';
            cell.textContent = value.length > 200 ? value.slice(0, 200) + '…' : value;
            row.append(cell);
          });
          target.append(row);
        }
        appendRow(head, headers, 'th');
        parsed.rows.forEach(row => appendRow(body, row, 'td'));
        controls.hidden = false; preview.hidden = false;
        const profile = saved.find(item => String(item.id) === preset.value);
        applyMapping(previous || (profile ? profile.columns : suggestMapping(headers, config.aliases)));
        previous = null;
      } catch (error) {
        if (current === generation) status.textContent = error.message;
      }
    });
    form.addEventListener('submit', event => {
      update();
      if (button.disabled) { event.preventDefault(); return; }
      submitting = true; button.disabled = true; button.textContent = 'Validating and importing…';
    });
    doc.defaultView.addEventListener('pageshow', () => {
      // A browser back/forward-cache restore must not keep the submit lock.
      submitting = false; button.textContent = 'Validate and import';
      if (headers.length) update();
    });
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {parsePreview, suggestMapping, init};
  if (typeof document !== 'undefined') init(document);
}());
