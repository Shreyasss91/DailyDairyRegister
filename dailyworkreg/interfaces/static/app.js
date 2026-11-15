'use strict';

// The client renders what the server decided and sends what the operator entered.
// It holds no date rules, no capacity rules and no scheduling of its own: a rule
// copied into the browser is a rule that will disagree with the server later.

const state = {
  view: 'home',
  runId: null,
  monthKey: null,
};

// --- small helpers ---------------------------------------------------------

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload && payload.error ? payload.error.detail : response.statusText;
    const constraint = payload && payload.error ? payload.error.constraint : 'request_refused';
    const error = new Error(detail || constraint);
    error.constraint = constraint;
    throw error;
  }
  return payload;
}

function el(tag, attributes, children) {
  const node = document.createElement(tag);
  Object.entries(attributes || {}).forEach(([key, value]) => {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  });
  (children || []).forEach((child) => {
    if (child === null || child === undefined) return;
    node.append(typeof child === 'string' ? document.createTextNode(child) : child);
  });
  return node;
}

function table(headers, rows, className) {
  const head = el('thead', {}, [el('tr', {}, headers.map((text) => el('th', { text })))]);
  const body = el('tbody', {}, rows);
  return el('table', { class: className || 'table' }, [head, body]);
}

function showBanner(node, kind, message, items) {
  node.hidden = false;
  node.className = `banner banner-${kind}`;
  node.textContent = '';
  node.append(el('div', { text: message }));
  if (items && items.length) {
    node.append(el('ul', {}, items.map((item) => el('li', { text: item }))));
  }
}

function hide(node) {
  node.hidden = true;
  node.textContent = '';
}

function isoDay(value) {
  return value && value.length >= 10 ? value.slice(0, 10) : value;
}

function weekdayName(index) {
  return ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][index] || '';
}

// --- navigation ------------------------------------------------------------

const views = () => Array.from(document.querySelectorAll('.view'));

function activate(view) {
  state.view = view;
  views().forEach((node) => { node.hidden = node.id !== `view-${view}`; });
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.view === view);
  });
  const loaders = {
    home: loadOverview,
    imports: loadImports,
    masters: loadMasters,
    calendar: loadCalendar,
    generate: loadGenerateDefaults,
    history: loadHistory,
  };
  if (loaders[view]) loaders[view]();
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => activate(tab.dataset.view));
});

// --- overview --------------------------------------------------------------

async function loadOverview() {
  const panel = document.getElementById('readiness-table').querySelector('tbody');
  const missing = document.getElementById('missing');
  panel.textContent = '';
  try {
    const readiness = await api('/api/readiness');
    document.getElementById('readiness-summary').textContent =
      readiness.can_generate
        ? (readiness.is_operational_diary
          ? 'All masters are present.'
          : 'A draft can be generated, but the result is not yet a complete operational diary.')
        : 'A draft cannot be generated until the blocking items are stored.';
    panel.append(...readiness.items.map((item) => el('tr', {}, [
      el('td', { text: item.label }),
      el('td', {
        class: item.satisfied ? 'state-ok' : 'state-missing',
        text: item.satisfied ? 'present' : (item.blocks_draft ? 'missing (blocks)' : 'missing'),
      }),
      el('td', { text: item.detail }),
    ])));
    if (readiness.missing_content.length) {
      showBanner(missing, 'warn', 'Missing content:', readiness.missing_content);
    } else {
      hide(missing);
    }
  } catch (error) {
    showBanner(missing, 'bad', error.message);
  }

  const summary = document.getElementById('summary-table').querySelector('tbody');
  summary.textContent = '';
  try {
    const counts = await api('/api/masters/summary');
    const labels = {
      dtc: 'DTCs', area: 'Areas', feeder: 'Feeders', activity: 'Activities',
      holiday: 'General Holidays', leave_record: 'Leave records', event: 'Dated events',
    };
    Object.entries(labels).forEach(([key, label]) => {
      summary.append(el('tr', {}, [
        el('td', { text: label }),
        el('td', { text: String(counts[key] ?? 0) }),
      ]));
    });
    document.getElementById('db-note').textContent = `master version ${counts.version}`;
  } catch (error) {
    summary.append(el('tr', {}, [el('td', { text: error.message })]));
  }
}

document.getElementById('refresh-readiness').addEventListener('click', loadOverview);

// --- imports ---------------------------------------------------------------

async function loadImports() {
  const body = document.getElementById('imports-table').querySelector('tbody');
  body.textContent = '';
  try {
    const batches = await api('/api/imports');
    if (!batches.length) {
      body.append(el('tr', {}, [el('td', { colspan: '7', class: 'muted', text: 'No file has been read yet.' })]));
      return;
    }
    batches.forEach((batch) => {
      body.append(el('tr', {}, [
        el('td', { text: batch.filename }),
        el('td', { text: batch.dataset }),
        el('td', { text: String(batch.row_count) }),
        el('td', { text: String(batch.error_count) }),
        el('td', { text: String(batch.warning_count) }),
        el('td', { text: batch.status }),
        el('td', {}, [
          el('button', { class: 'button', text: 'Open', onclick: () => openBatch(batch.batch_id) }),
        ]),
      ]));
    });
  } catch (error) {
    body.append(el('tr', {}, [el('td', { colspan: '7', text: error.message })]));
  }
}

document.getElementById('import-upload').addEventListener('click', async () => {
  const result = document.getElementById('import-result');
  const fileInput = document.getElementById('import-file');
  const dataset = document.getElementById('import-dataset').value;
  if (!fileInput.files.length) {
    showBanner(result, 'warn', 'Choose a file first.');
    return;
  }
  const form = new FormData();
  form.append('file', fileInput.files[0]);
  try {
    const batch = await api(`/api/imports?dataset=${encodeURIComponent(dataset)}`, {
      method: 'POST',
      body: form,
    });
    showBanner(
      result,
      batch.error_count ? 'bad' : 'warn',
      `${batch.row_count} row(s) read: ${batch.error_count} cannot be used, ${batch.warning_count} need attention.`,
    );
    await loadImports();
    await openBatch(batch.batch_id);
  } catch (error) {
    showBanner(result, 'bad', error.message);
  }
});

async function openBatch(batchId) {
  const target = document.getElementById('import-detail');
  target.hidden = false;
  target.textContent = '';
  const batch = await api(`/api/imports/${batchId}`);
  target.append(
    el('div', { class: 'row' }, [
      el('strong', { text: `${batch.dataset} from ${batch.filename}` }),
      el('span', { class: 'tag', text: batch.status }),
      el('span', { class: 'muted', text: `digest ${batch.digest.slice(0, 12)}` }),
    ]),
    table(
      ['Row', 'State', 'Detail'],
      batch.rows.map((row) => el('tr', {}, [
        el('td', { text: String(row.row_number) }),
        el('td', { text: row.decision }),
        el('td', { text: row.messages.join('; ') || (row.raw ? JSON.stringify(row.raw).slice(0, 120) : '') }),
      ])),
    ),
  );
  if (batch.can_accept) {
    target.append(el('div', { class: 'row' }, [
      el('button', {
        class: 'button button-primary',
        text: 'Accept into master data',
        onclick: async () => {
          try {
            const done = await api(`/api/imports/${batchId}/accept`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({}),
            });
            showBanner(
              document.getElementById('import-result'),
              'warn',
              `Accepted: ${done.inserted} added, ${done.updated} updated, ${done.excluded} excluded, ${done.feeder_overrides_applied} approved feeder assignment(s) applied.`,
            );
            await loadImports();
            await openBatch(batchId);
          } catch (error) {
            showBanner(document.getElementById('import-result'), 'bad', error.message);
          }
        },
      }),
      el('button', {
        class: 'button',
        text: 'Reject',
        onclick: async () => {
          await api(`/api/imports/${batchId}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          });
          await loadImports();
          await openBatch(batchId);
        },
      }),
    ]));
  }
}

// --- masters ---------------------------------------------------------------

const masterFields = {
  dtc: [
    ['dtc_id', 'DTC id'], ['name', 'Name'], ['dtc_class', 'Class (DEPT or SELF_DTC)'],
    ['canonical_feeder_id', 'Canonical feeder'], ['kva', 'kVA'], ['station', 'Station'],
    ['monthly_max', 'Monthly maximum'], ['revisit_gap_days', 'Revisit gap (days)'],
  ],
  area: [
    ['area_id', 'Area id'], ['name_kn', 'Name (Kannada)'], ['name_en', 'Name (English)'],
    ['weight', 'Weight'], ['monthly_max', 'Monthly maximum'], ['revisit_gap_days', 'Revisit gap (days)'],
  ],
  feeder: [
    ['feeder_id', 'Feeder id'], ['name', 'Name'], ['manual_multiplier', 'Manual multiplier'],
    ['monthly_max', 'Monthly maximum'], ['revisit_gap_days', 'Revisit gap (days)'],
  ],
  activity: [
    ['activity_id', 'Activity id'], ['category', 'Category'], ['description_en', 'Description'],
    ['description_kn', 'Description (Kannada)'], ['monthly_max', 'Monthly maximum'],
    ['repeat_gap_days', 'Repeat gap (days)'],
  ],
};

async function loadMasters() {
  const kind = document.getElementById('master-kind').value;
  const form = document.getElementById('master-form');
  form.textContent = '';
  masterFields[kind].forEach(([key, label]) => {
    form.append(el('label', {}, [label, el('input', { id: `m-${key}`, type: key.includes('gap') || key.includes('max') ? 'number' : 'text' })]));
  });
  form.append(el('button', {
    class: 'button button-primary',
    text: 'Save record',
    onclick: async () => {
      const payload = {};
      masterFields[kind].forEach(([key]) => {
        const value = document.getElementById(`m-${key}`).value;
        if (value !== '') payload[key] = value;
      });
      ['kva', 'weight', 'manual_multiplier', 'monthly_max', 'revisit_gap_days', 'repeat_gap_days'].forEach((key) => {
        if (payload[key] !== undefined) payload[key] = Number(payload[key]);
      });
      try {
        await api(`/api/masters/${kind}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        await loadMasters();
      } catch (error) {
        window.alert(error.message);
      }
    },
  }));

  const target = document.getElementById('master-table');
  target.textContent = '';
  const rows = await api(`/api/masters/${kind}`);
  if (!rows.length) {
    target.append(el('p', { class: 'muted', text: 'Nothing is stored for this master.' }));
    return;
  }
  const columns = Object.keys(rows[0]);
  target.append(table(columns, rows.map((row) => el('tr', {}, columns.map((column) => {
    const value = row[column];
    return el('td', { text: Array.isArray(value) ? value.join(', ') : String(value ?? '') });
  })))));
}

document.getElementById('master-refresh').addEventListener('click', loadMasters);
document.getElementById('master-kind').addEventListener('change', loadMasters);

// --- calendar --------------------------------------------------------------

async function loadCalendar() {
  const today = new Date();
  const first = new Date(today.getFullYear(), today.getMonth(), 1);
  const last = new Date(today.getFullYear(), today.getMonth() + 1, 0);
  const startInput = document.getElementById('cal-start');
  const endInput = document.getElementById('cal-end');
  if (!startInput.value) startInput.value = first.toISOString().slice(0, 10);
  if (!endInput.value) endInput.value = last.toISOString().slice(0, 10);

  try {
    const rules = await api('/api/settings/calendar');
    document.getElementById('rule-sunday').checked = rules.sundays;
    document.getElementById('rule-second-sat').checked = rules.second_saturday;
    document.getElementById('rule-fourth-sat').checked = rules.fourth_saturday;
  } catch (error) {
    /* the rules are not fatal to the rest of the view */
  }
  await previewCalendar();
  await loadHolidays();
  await loadEvents();
}

async function previewCalendar() {
  const start = document.getElementById('cal-start').value;
  const end = document.getElementById('cal-end').value;
  const target = document.getElementById('cal-days');
  const conflicts = document.getElementById('cal-conflicts');
  if (!start || !end) return;
  target.textContent = '';
  try {
    const preview = await api(`/api/calendar/preview?start=${start}&end=${end}`);
    if (preview.conflicts.length) showBanner(conflicts, 'warn', 'Dates needing attention:', preview.conflicts);
    else hide(conflicts);
    const entryList = preview.entries;
    target.append(table(
      ['Date', 'Day', 'Classification', 'Reasons', 'Entries'],
      preview.days.map((day) => {
        const dayEntries = entryList.filter((item) => item.day === day.day);
        const rowClass = day.kind === 'general_holiday'
          ? 'row-holiday'
          : (day.is_working_day ? '' : 'row-nonworking');
        return el('tr', { class: rowClass }, [
          el('td', { class: 'day-line', text: day.day }),
          el('td', { text: weekdayName((new Date(day.day).getDay() + 6) % 7) }),
          el('td', { text: day.is_working_day ? 'working' : day.kind }),
          el('td', { text: day.reasons_en.join('; ') }),
          el('td', { text: dayEntries.map((item) => item.title_en).join('; ') }),
        ]);
      }),
      'table'),
    );
  } catch (error) {
    showBanner(conflicts, 'bad', error.message);
  }
}

document.getElementById('cal-preview').addEventListener('click', previewCalendar);

document.getElementById('rules-save').addEventListener('click', async () => {
  await api('/api/settings/calendar', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sundays: document.getElementById('rule-sunday').checked,
      second_saturday: document.getElementById('rule-second-sat').checked,
      fourth_saturday: document.getElementById('rule-fourth-sat').checked,
    }),
  });
  await previewCalendar();
});

async function loadHolidays() {
  const holidays = await api('/api/calendar/holidays');
  const target = document.getElementById('holiday-table');
  target.textContent = '';
  if (!holidays.length) {
    target.append(el('p', {
      class: 'muted',
      text: 'No General Holiday is stored. Until the official list is imported the calendar shows only weekly offs and leave.',
    }));
    return;
  }
  target.append(table(
    ['Date', 'Name', 'Printed wording', 'Source', ''],
    holidays.map((holiday) => el('tr', {}, [
      el('td', { class: 'day-line', text: holiday.holiday_date }),
      el('td', { text: holiday.name }),
      el('td', { text: holiday.description || '' }),
      el('td', { text: holiday.source }),
      el('td', {}, [el('button', {
        class: 'button',
        text: 'Delete',
        onclick: async () => {
          await api(`/api/calendar/holidays/${holiday.holiday_id}`, { method: 'DELETE' });
          await loadHolidays();
          await previewCalendar();
        },
      })]),
    ])),
  ));
}

document.getElementById('holiday-save').addEventListener('click', async () => {
  const day = document.getElementById('holiday-date').value;
  const name = document.getElementById('holiday-name').value;
  if (!day || !name) {
    window.alert('A date and a name are both needed.');
    return;
  }
  await api('/api/calendar/holidays', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      holiday_date: day,
      name,
      description: document.getElementById('holiday-description').value || null,
      source: 'operator',
    }),
  });
  await loadHolidays();
  await previewCalendar();
});

async function loadEvents() {
  const events = await api('/api/calendar/events');
  const target = document.getElementById('event-table');
  target.textContent = '';
  if (!events.length) {
    target.append(el('p', { class: 'muted', text: 'No dated event is stored. Safety meetings are entered automatically on every Monday.' }));
    return;
  }
  target.append(table(
    ['Date', 'Kind', 'Title'],
    events.map((event) => el('tr', {}, [
      el('td', { class: 'day-line', text: event.event_date }),
      el('td', { text: event.event_kind }),
      el('td', { text: event.title_en }),
    ])),
  ));
}

document.getElementById('event-save').addEventListener('click', async () => {
  const day = document.getElementById('event-date').value;
  const title = document.getElementById('event-title').value;
  if (!day || !title) {
    window.alert('A date and a title are both needed.');
    return;
  }
  await api('/api/calendar/events', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      event_date: day,
      title_en: title,
      event_kind: document.getElementById('event-kind').value,
    }),
  });
  await loadEvents();
  await previewCalendar();
});

// --- generation ------------------------------------------------------------

function loadGenerateDefaults() {
  const today = new Date();
  const start = document.getElementById('gen-start');
  const end = document.getElementById('gen-end');
  const cycle = document.getElementById('gen-cycle-start');
  if (!start.value) start.value = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().slice(0, 10);
  if (!end.value) end.value = new Date(today.getFullYear(), today.getMonth() + 1, 0).toISOString().slice(0, 10);
  if (!cycle.value) cycle.value = start.value.slice(0, 7);
}

document.getElementById('gen-start').addEventListener('change', () => {
  const start = document.getElementById('gen-start').value;
  if (start) document.getElementById('gen-cycle-start').value = start.slice(0, 7);
});

document.getElementById('gen-run').addEventListener('click', async () => {
  const result = document.getElementById('gen-result');
  const mode = document.getElementById('gen-mode').value;
  const payload = {
    start: document.getElementById('gen-start').value,
    end: document.getElementById('gen-end').value,
    mode,
    seed: Number(document.getElementById('gen-seed').value || 0),
  };
  if (mode === 'coverage_cycle') {
    payload.cycle_start_month = document.getElementById('gen-cycle-start').value;
    payload.cycle_duration_months = Number(document.getElementById('gen-months-input')?.value || document.getElementById('gen-cycle-months').value || 1);
    payload.cycle_visits_per_asset = Number(document.getElementById('gen-visits').value || 1);
  } else {
    const target = document.getElementById('gen-dtc-target').value;
    if (target !== '') payload.dtc_target = Number(target);
  }
  try {
    const done = await api('/api/generations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const kind = done.is_draft ? 'warn' : 'bad';
    showBanner(
      result,
      kind,
      done.is_draft
        ? `A draft was generated: ${done.months} month(s), ${done.rows} date row(s), ${done.visits} visit(s).`
        : `No draft was produced: the allocator reported ${done.status.replace('_', ' ')} for at least one month. The attempt is stored with its evidence.`,
      done.missing_content,
    );
    state.runId = done.run_id;
    await openDraft(done.run_id);
    activate('draft');
  } catch (error) {
    showBanner(result, 'bad', error.message);
  }
});

// --- draft -----------------------------------------------------------------

/**
 * Render validator attribution exactly as the server sent it.
 *
 * A single-asset finding shows its own id and type; a multi-asset finding shows
 * the contributing set. Nothing here reads `detail` to recover an asset name, and
 * nothing here decides which assets are relevant: an empty set is displayed as an
 * empty set, because that is what the domain reported.
 */
function attributionTable(items) {
  const ids = (item) => (item.contributing_assets || []).map((asset) => `${asset.asset_id} (${asset.asset_type})`).join(', ');
  const dates = (item) => [item.visit_date, ...(item.offending_dates || [])].filter(Boolean).join(', ');
  return table(
    ['Month', 'Kind', 'Constraint', 'Asset', 'Type', 'Limit', 'Actual', 'Dates', 'Contributing assets', 'Detail'],
    items.map((item) => el('tr', {}, [
      el('td', { text: item.month || '' }),
      el('td', { text: item.kind || '' }),
      el('td', { text: item.constraint }),
      el('td', { text: item.asset_id || '' }),
      el('td', { text: item.asset_type || '' }),
      el('td', { text: item.limit === null || item.limit === undefined ? '' : String(item.limit) }),
      el('td', { text: item.actual === null || item.actual === undefined ? '' : String(item.actual) }),
      el('td', { text: dates(item) }),
      el('td', { text: ids(item) }),
      el('td', { text: item.detail }),
    ])),
  );
}

async function openDraft(runId) {
  state.runId = runId;
  const head = document.getElementById('draft-head');
  const exceptions = document.getElementById('draft-exceptions');
  const evidence = document.getElementById('draft-evidence');
  const target = document.getElementById('draft-rows');
  head.textContent = '';
  target.textContent = '';
  hide(exceptions);
  hide(evidence);
  exceptions.textContent = '';
  evidence.textContent = '';

  const register = await api(`/api/generations/${runId}`);
  head.append(
    el('p', {}, [
      el('strong', { text: register.is_draft ? 'Allocator-backed draft' : 'No draft — attempt recorded' }),
      el('span', { text: ` · ${register.start} to ${register.end} · seed ${register.seed} · generator ${register.generator_version} · allocator ${register.allocator_version}` }),
    ]),
    el('p', { class: 'muted', text: register.is_operational_diary
      ? 'Every required master is present.'
      : `Not yet a complete operational diary: ${register.missing_content.join(', ') || 'nothing missing'}.` }),
    el('p', { class: 'muted', text: register.months.map((month) => `${month.month}: ${month.allocator_status} (${month.visits} visit(s))`).join(' · ') }),
  );

  if (register.exceptions.length) {
    exceptions.hidden = false;
    exceptions.append(el('strong', { text: 'Exceptions' }));
    exceptions.append(attributionTable(register.exceptions));
  }

  const evidenceRows = register.months.flatMap((month) =>
    month.evidence.map((item) => ({ ...item, month: month.month, kind: 'evidence' })));
  if (evidenceRows.length) {
    const block = document.getElementById('draft-evidence');
    block.hidden = false;
    block.textContent = '';
    block.append(el('strong', { text: 'Month evidence' }));
    block.append(attributionTable(evidenceRows));
  }

  target.append(table(
    ['Date', 'Day', 'Calendar', 'Visits', 'Entries', 'Warnings'],
    register.rows.map((row) => el('tr', { class: row.is_working_day ? '' : 'row-nonworking' }, [
      el('td', { class: 'day-line', text: row.day }),
      el('td', { text: weekdayName(row.weekday) }),
      el('td', { text: row.reasons_en.join('; ') || (row.is_working_day ? 'working' : row.kind) }),
      el('td', {}, row.visits.map((visit) => el('div', {}, [
        el('span', { class: `tag tag-${visit.provenance}`, text: visit.provenance }),
        el('span', { text: ` ${visit.asset_id} ${visit.name_en}` }),
      ]))),
      el('td', { text: row.entries.map((entry) => entry.title_en).join('; ') }),
      el('td', { text: row.warnings.join('; ') }),
    ])),
  ));
}

// --- history ---------------------------------------------------------------

async function loadHistory() {
  const target = document.getElementById('history-groups');
  target.textContent = '';
  const grouped = await api('/api/generations');
  const titles = {
    allocation_complete: 'Draft registers',
    infeasible: 'Attempts that could not cover the month',
    unresolved: 'Attempts the search could not settle',
  };
  Object.entries(titles).forEach(([key, title]) => {
    const runs = grouped[key] || [];
    target.append(el('div', { class: 'group-title', text: `${title} (${runs.length})` }));
    if (!runs.length) {
      target.append(el('p', { class: 'muted', text: 'None.' }));
      return;
    }
    target.append(table(
      ['Created', 'Period', 'Mode', 'Revision', 'Seed', 'Action'],
      runs.map((run) => el('tr', {}, [
        el('td', { text: run.created_at.replace('T', ' ').slice(0, 19) }),
        el('td', { text: `${run.start} to ${run.end}` }),
        el('td', { text: run.mode }),
        el('td', { text: run.revision_number === null ? 'none' : String(run.revision_number) }),
        el('td', { text: String(run.seed) }),
        el('td', {}, [
          el('button', {
            class: 'button',
            text: 'Open',
            onclick: async () => {
              await openDraft(run.run_id);
              activate('draft');
            },
          }),
        ]),
      ])),
    ));
  });
}

// --- start -----------------------------------------------------------------

activate('home');
