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
    backup: loadBackup,
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
  const reviewActions = document.getElementById('draft-review-actions');
  const banner = document.getElementById('draft-banner');
  const exceptions = document.getElementById('draft-exceptions');
  const evidence = document.getElementById('draft-evidence');
  const target = document.getElementById('draft-rows');
  const regeneration = document.getElementById('draft-regenerate');
  const revisions = document.getElementById('draft-revisions');
  const months = document.getElementById('draft-months');
  const candidate = document.getElementById('candidate-panel');
  head.textContent = '';
  reviewActions.textContent = '';
  reviewActions.hidden = true;
  target.textContent = '';
  regeneration.textContent = '';
  revisions.textContent = '';
  months.textContent = '';
  hide(banner);
  hide(exceptions);
  hide(evidence);
  hide(candidate);
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
    evidence.hidden = false;
    evidence.append(el('strong', { text: 'Month evidence' }));
    evidence.append(attributionTable(evidenceRows));
  }

  if (!register.is_draft) {
    state.registerId = null;
    showBanner(
      banner,
      'bad',
      `No accepted revision exists for this attempt: the allocator reported ${register.status.replace(/_/g, ' ')}. The attempt remains available here for diagnostics; review and finalisation are not available.`,
    );
    target.append(el('p', {
      class: 'muted',
      text: 'This attempt did not produce an allocation-complete draft. Its stored evidence is shown above; there is no accepted revision to review or finalise. Adjust the generation inputs before trying again.',
    }));
    return;
  }

  const review = await api(`/api/generations/${runId}/review`);
  const days = new Map(review.dates.map((day) => [day.date, day]));
  state.registerId = review.register_id;

  const pendingDates = review.dates.filter((day) => day.state === 'pending_review');
  if (pendingDates.length > 0) {
    const fingerprints = Object.fromEntries(
      review.dates.map((day) => [day.date, day.fingerprint]),
    );
    const bulkReview = el('button', {
      class: 'button button-primary',
      text: `Mark all days reviewed (${pendingDates.length} pending)`,
      onclick: async () => {
        const answer = await act(
          `/api/generations/${runId}/review`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fingerprints }),
          },
          runId,
        );
        if (answer) {
          showBanner(
            banner,
            'success',
            `${answer.reviewed_dates} pending date(s) marked reviewed; ${answer.already_reviewed_dates} date(s) were already reviewed.`,
          );
        }
      },
    });
    reviewActions.hidden = false;
    reviewActions.append(bulkReview);
  } else {
    reviewActions.hidden = false;
    reviewActions.append(el('span', {
      class: 'muted',
      text: 'All dates in this draft are already reviewed.',
    }));
  }

  target.append(table(
    ['Date', 'Day', 'Calendar', 'Visits', 'Entries', 'Warnings', 'Review', 'Lock', 'Corrections'],
    register.rows.map((row) => draftRow(row, days.get(row.day) || {
      date: row.day, state: 'pending_review', locked: false,
    })),
  ));

  const registerDetail = await api(`/api/registers/${review.register_id}`);
  renderRevisions(registerDetail);
  renderRegeneration(register, review);
  await renderMonths(review.register_id, runId);
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

// --- review, locks, corrections and regeneration ---------------------------

// The draft is one revision of a register. Everything below reads the review state
// the server holds and sends the operator's acts back to it; none of it decides what
// a row contains, which date is eligible or whether a month is complete.

function reviewTag(day) {
  return el('span', {
    class: day.state === 'reviewed' ? 'tag tag-reviewed' : 'tag tag-pending',
    text: day.state === 'reviewed' ? 'reviewed' : 'pending review',
  });
}

function lockTag(day) {
  return el('span', {
    class: day.locked ? 'tag tag-locked' : 'tag',
    text: day.locked ? 'locked' : 'unlocked',
  });
}

function inlineForm(fields, onSave) {
  const inputs = {};
  const rows = fields.map((field) => {
    let input;
    if (field.kind === 'select') {
      input = el('select', {}, field.options.map((option) => el('option', { value: option, text: option })));
      input.value = field.value || field.options[0];
    } else {
      input = el('input', { type: field.kind || 'text', value: field.value || '' });
    }
    if (field.type) input.type = field.type;
    inputs[field.name] = input;
    return el('label', { class: 'field' }, [el('span', { text: field.label }), input]);
  });
  const form = el('div', { class: 'inline-form' }, rows);
  form.append(el('div', { class: 'actions' }, [
    el('button', {
      class: 'button',
      text: 'Save',
      onclick: async () => {
        const values = {};
        Object.entries(inputs).forEach(([name, input]) => { values[name] = input.value; });
        await onSave(values);
      },
    }),
    el('button', {
      class: 'button button-quiet',
      text: 'Cancel',
      onclick: () => form.remove(),
    }),
  ]));
  return form;
}

async function act(path, options, runId) {
  const banner = document.getElementById('draft-banner');
  try {
    const answer = await api(path, options);
    hide(banner);
    await openDraft(runId || (answer && answer.run_id) || state.runId);
    return answer;
  } catch (error) {
    showBanner(banner, 'error', error.message, [error.constraint]);
    return null;
  }
}

function entryControls(row, entry) {
  return el('span', { class: 'controls' }, [
    el('button', {
      class: 'link',
      text: 'edit',
      onclick: (event) => {
        const form = inlineForm(
          [
            { name: 'title_en', label: 'English' , value: entry.title_en },
            { name: 'title_kn', label: 'Kannada', value: entry.title_kn || '' },
            { name: 'kind', label: 'Kind', kind: 'select', value: entry.kind,
              options: ['activity', 'event', 'safety_meeting'] },
            { name: 'reason', label: 'Reason' },
          ],
          async (values) => {
            await act(
              `/api/generations/${state.runId}/entries/${entry.entry_id}`,
              {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(values),
              },
              values.runId,
            );
          },
        );
        event.target.closest('td').append(form);
      },
    }),
    el('button', {
      class: 'link',
      text: 'remove',
      onclick: () => act(
        `/api/generations/${state.runId}/entries/${entry.entry_id}`,
        { method: 'DELETE' },
      ),
    }),
    el('button', {
      class: 'link',
      text: 'move',
      onclick: (event) => {
        const form = inlineForm(
          [
            { name: 'date', label: 'Move to', type: 'date' },
            { name: 'reason', label: 'Reason' },
          ],
          async (values) => {
            await act(
              `/api/generations/${state.runId}/entries/${entry.entry_id}/move`,
              {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(values),
              },
            );
          },
        );
        event.target.closest('td').append(form);
      },
    }),
  ]);
}

function visitControls(row, visit) {
  return el('span', { class: 'controls' }, [
    el('button', {
      class: 'link',
      text: 'move',
      onclick: (event) => {
        const form = inlineForm(
          [
            { name: 'to', label: `Move to (within ${row.day.slice(0, 7)})`, type: 'date' },
            { name: 'reason', label: 'Reason' },
          ],
          async (values) => {
            await act(`/api/generations/${state.runId}/visits/move`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asset_id: visit.asset_id,
                from: row.day,
                to: values.to,
                reason: values.reason,
              }),
            });
          },
        );
        event.target.closest('td').append(form);
      },
    }),
  ]);
}

function draftRow(row, day) {
  const cells = [
    el('td', { class: 'day-line', text: row.day }),
    el('td', { text: weekdayName(row.weekday) }),
    el('td', { text: row.reasons_en.join('; ') || (row.is_working_day ? 'working' : row.kind) }),
    el('td', {}, row.visits.map((visit) => el('div', {}, [
      el('span', { class: `tag tag-${visit.provenance}`, text: visit.provenance }),
      el('span', { text: ` ${visit.asset_id} ${visit.name_en}` }),
      visitControls(row, visit),
    ]))),
    el('td', {}, row.entries.map((entry) => el('div', {}, [
      el('span', { text: entry.title_kn ? `${entry.title_en} / ${entry.title_kn}` : entry.title_en }),
      el('span', { class: 'muted', text: ` (${entry.kind}${entry.provenance && entry.provenance !== 'calendar' ? ', ' + entry.provenance : ''})` }),
      entryControls(row, entry),
    ]))),
    el('td', { text: row.warnings.join('; ') }),
    el('td', {}, [
      reviewTag(day),
      el('button', {
        class: 'link',
        text: day.state === 'reviewed' ? 'withdraw' : 'mark reviewed',
        onclick: () => act(
          `/api/generations/${state.runId}/review/${row.day}`,
          day.state === 'reviewed'
            ? { method: 'DELETE' }
            : {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ fingerprint: day.fingerprint }),
            },
        ),
      }),
    ]),
    el('td', {}, [
      lockTag(day),
      el('button', {
        class: 'link',
        text: day.locked ? 'unlock' : 'lock',
        onclick: () => act(
          `/api/generations/${state.runId}/locks/${row.day}`,
          day.locked ? { method: 'DELETE' } : { method: 'PUT' },
        ),
      }),
    ]),
    el('td', {}, [
      el('button', {
        class: 'link',
        text: 'add entry',
        onclick: (event) => {
          const form = inlineForm(
            [
              { name: 'title_en', label: 'English' },
              { name: 'title_kn', label: 'Kannada' },
              { name: 'kind', label: 'Kind', kind: 'select',
                options: ['activity', 'event', 'safety_meeting'] },
              { name: 'reason', label: 'Reason' },
            ],
            async (values) => {
              await act(`/api/generations/${state.runId}/entries`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...values, date: row.day }),
              });
            },
          );
          event.target.closest('td').append(form);
        },
      }),
    ]),
  ];
  return el('tr', { class: row.is_working_day ? '' : 'row-nonworking' }, cells);
}

function renderRevisions(register) {
  const target = document.getElementById('draft-revisions');
  target.textContent = '';
  target.append(el('strong', { text: 'Revisions' }));
  target.append(table(
    ['#', 'Kind', 'Created', 'Parent', 'Decision', 'Current', 'Action'],
    register.revisions.map((revision) => el('tr', {}, [
      el('td', { text: String(revision.revision_number) }),
      el('td', { text: revision.kind }),
      el('td', { text: revision.created_at.replace('T', ' ').slice(0, 19) }),
      el('td', { text: revision.parent_run_id ? revision.parent_run_id.slice(0, 8) : '' }),
      el('td', { text: revision.decision || '' }),
      el('td', { text: revision.is_current ? 'yes' : '' }),
      el('td', {}, [
        el('button', {
          class: 'button button-quiet',
          text: 'Open',
          onclick: async () => {
            await openDraft(revision.run_id);
            activate('draft');
          },
        }),
      ]),
    ])),
  ));
}

function comparisonTable(comparison) {
  const cell = (value) => (value === null || value === undefined ? '' : String(value));
  const visits = (row) => (row ? row.visits.map((item) => `${item[0]} (${item[1]})`).join('; ') : '');
  const entries = (row) => (row ? row.entries.map((item) => `${item[2]}`).join('; ') : '');
  return table(
    ['Date', 'Status', 'Locked', 'Reviewed in base', 'Base visits', 'Proposed visits',
      'Visits added', 'Visits removed', 'Visits moved', 'Entries added', 'Entries removed',
      'Entries changed', 'Warning'],
    comparison.dates.map((item) => el('tr', { class: `status-${item.status}` }, [
      el('td', { class: 'day-line', text: item.date }),
      el('td', { text: item.status }),
      el('td', { text: item.locked ? 'locked' : '' }),
      el('td', { text: item.review_state }),
      el('td', { text: visits(item.base) }),
      el('td', { text: visits(item.proposed) }),
      el('td', { text: item.visits.added.join(', ') }),
      el('td', { text: item.visits.removed.join(', ') }),
      el('td', { text: item.visits.moved.map((move) => `${move.asset_id}: ${move.from} to ${move.to}`).join('; ') }),
      el('td', { text: item.entries.added.map((entry) => entry.title_en).join('; ') }),
      el('td', { text: item.entries.removed.map((entry) => entry.title_en).join('; ') }),
      el('td', { text: item.entries.changed.map((entry) => entry.after.title_en).join('; ') }),
      el('td', { text: item.warnings.map((warning) => warning.detail).join('; ') }),
    ])),
  );
}

async function showCandidate(registerId, candidate, runId) {
  const panel = document.getElementById('candidate-panel');
  panel.hidden = false;
  panel.textContent = '';
  if (!candidate.eligible) {
    showBanner(panel, 'error', candidate.detail || 'the candidate is not eligible', [candidate.blocked_by]);
    return;
  }
  const comparison = await api(`/api/revisions/${runId}/compare/${candidate.candidate_run_id}`);
  panel.append(el('strong', { text: `Candidate ${candidate.candidate_run_id.slice(0, 8)} · seed ${candidate.seed}` }));
  panel.append(el('p', { class: 'muted', text: Object.entries(comparison.summary)
    .map(([key, count]) => `${key}: ${count}`).join(' · ') }));
  if (comparison.months.some((month) => month.unresolved_evidence.length)) {
    panel.append(attributionTable(comparison.months.flatMap((month) =>
      month.unresolved_evidence.map((item) => ({ ...item, month: month.month, kind: 'unresolved' })))));
  }
  panel.append(comparisonTable(comparison));
  panel.append(el('div', { class: 'actions' }, [
    el('input', { type: 'text', id: 'accept-note', placeholder: 'note' }),
    el('button', {
      class: 'button',
      text: 'Accept candidate',
      onclick: async () => {
        const note = document.getElementById('accept-note').value;
        const answer = await api(`/api/registers/${registerId}/candidates/${candidate.candidate_run_id}/accept`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note }),
        });
        hide(panel);
        await openDraft(answer.run_id);
      },
    }),
    el('button', {
      class: 'button button-quiet',
      text: 'Refuse candidate',
      onclick: async () => {
        await api(`/api/registers/${registerId}/candidates/${candidate.candidate_run_id}/reject`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: 'refused from the draft screen' }),
        });
        hide(panel);
        await openDraft(runId);
      },
    }),
  ]));
}

function renderRegeneration(register, review) {
  const target = document.getElementById('draft-regenerate');
  target.textContent = '';
  const seed = el('input', { type: 'number', id: 'candidate-seed', placeholder: 'seed (blank: same)' });
  target.append(el('div', { class: 'actions' }, [
    seed,
    el('button', {
      class: 'button',
      text: 'Generate candidate',
      onclick: async () => {
        const body = seed.value === '' ? {} : { seed: Number(seed.value) };
        const candidate = await api(`/api/registers/${review.register_id}/candidates`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        await showCandidate(review.register_id, candidate, register.run_id);
      },
    }),
    el('span', {
      class: 'muted',
      text: `register ${review.register_id} · revision ${register.revision_number || 'none'}`
        + ` · ${review.reviewed_dates}/${review.total_dates} dates reviewed · ${review.locked_dates} locked`,
    }),
  ]));
}

// --- closing a month, and correcting a closed one --------------------------

// Review, locks, allocation and finalisation answer four different questions, so they
// are shown as four separate facts. The client never decides whether a month may be
// closed: it asks the server for the reasons and renders them, and it sends back the
// opaque acknowledgement tokens exactly as they arrived.

function dimension(label, value) {
  return el('span', { class: 'tag', text: `${label}: ${value}` });
}

function monthDimensions(month) {
  return el('div', { class: 'actions' }, [
    el('strong', { text: month.month }),
    dimension('Stage', month.lifecycle),
    dimension('Allocation', month.allocation_complete
      ? month.allocator_status
      : `${month.allocator_status} (no complete allocation)`),
    dimension('Review', `${month.reviewed_dates} of ${month.total_dates} dates`),
    dimension('Locks', `${month.locked_dates} locked`),
    dimension('Finalisation', month.finalised
      ? `closed ${isoDay(month.finalised_at)} as revision ${month.latest_finalisation_revision}`
      : 'not finalised'),
  ]);
}

function blockerTable(check) {
  if (!check.blockers.length) {
    return el('p', { class: 'status-ok', text: 'Ready to close.' });
  }
  return table(
    ['What is in the way', 'Detail', 'Dates'],
    check.blockers.map((blocker) => el('tr', {}, [
      el('td', { text: blocker.label }),
      el('td', { text: blocker.detail }),
      el('td', { text: (blocker.dates || []).join(', ') || '—' }),
    ])),
  );
}

function acknowledgements(check) {
  const items = [
    ...check.over_coverage.map((item) => ({ ...item, what: 'Over-covered commitment' })),
    ...check.conflicts.map((item) => ({ ...item, what: 'Calendar conflict' })),
  ];
  if (!items.length) {
    return null;
  }
  return el('div', { class: 'detail' }, [
    el('strong', { text: 'Recorded findings to acknowledge' }),
    el('p', { class: 'muted', text: 'These stay on the record after closing; acknowledging says you have seen them.' }),
    table(
      ['Finding', 'Detail', 'Acknowledge'],
      items.map((item) => el('tr', {}, [
        el('td', { text: item.what }),
        el('td', { text: item.detail || `${item.asset_id || ''} ${item.limit ?? ''} → ${item.actual ?? ''}` }),
        el('td', {}, [
          el('input', { type: 'checkbox', class: 'ack', value: item.token, title: item.token }),
        ]),
      ])),
    ),
  ]);
}

function collectAcknowledgements(root) {
  return Array.from(root.querySelectorAll('input.ack:checked')).map((node) => node.value);
}

function renderFinalisationCheck(target, check, runId, registerId) {
  target.textContent = '';
  const note = el('input', { type: 'text', placeholder: 'Note (optional)' });
  const result = el('div', { class: 'detail' });
  const ack = acknowledgements(check);

  target.append(
    el('p', { class: 'muted', text: check.eligible
      ? 'The server reports this month can be closed.'
      : 'The server reports this month cannot be closed yet.' }),
    el('p', { class: 'muted', text: `Draft complete diary: ${check.is_operational_diary ? 'yes' : 'no'}`
      + (check.missing_content.length ? ` · missing: ${check.missing_content.join(', ')}` : '') }),
    blockerTable(check),
    ack,
    el('div', { class: 'actions' }, [
      note,
      el('button', {
        class: 'button button-primary',
        text: 'Finalise this month',
        onclick: async () => {
          try {
            const record = await api(`/api/generations/${runId}/finalisation`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                month: check.month,
                note: note.value.trim() === '' ? null : note.value.trim(),
                acknowledged: collectAcknowledgements(target),
              }),
            });
            showBanner(result, 'ok', `Closed ${record.month} as version ${record.sequence}.`, [
              `finalisation ${record.finalisation_id}`,
              `fingerprint ${record.fingerprint.slice(0, 12)}`,
            ]);
            await renderMonths(registerId, runId);
          } catch (error) {
            showBanner(result, 'warn', error.message);
          }
        },
      }),
    ]),
    result,
  );
}

function renderChain(target, chain, registerId, runId) {
  target.textContent = '';
  if (!chain.length) {
    target.append(el('p', { class: 'muted', text: 'This month has not been finalised.' }));
    return;
  }
  const detail = el('div', { class: 'detail' });
  target.append(
    table(
      ['Version', 'Revision', 'Closed at', 'Corrects', 'Status', 'Document'],
      chain.map((item) => el('tr', {}, [
        el('td', { text: String(item.sequence) }),
        el('td', { text: String(item.revision_number) }),
        el('td', { text: isoDay(item.finalised_at) }),
        el('td', { text: item.correction_of_run_id || '—' }),
        el('td', { text: item.is_latest ? 'in force' : 'superseded' }),
        el('td', {}, [
          el('button', {
            class: 'link',
            text: 'View',
            onclick: async () => {
              const record = await api(`/api/finalisations/${item.finalisation_id}`);
              renderIssuedDocument(detail, record, registerId, runId);
            },
          }),
        ]),
      ])),
    ),
    detail,
  );
}

function renderIssuedDocument(target, record, registerId, runId) {
  target.textContent = '';
  const days = Object.keys(record.content.rows).sort();
  target.append(
    el('p', { class: 'muted', text: `Issued version ${record.sequence} of ${record.month}`
      + ` · closed ${isoDay(record.finalised_at)} · ${record.acknowledged.length} acknowledged`
      + ` · fingerprint ${record.fingerprint.slice(0, 12)}` }),
    el('p', { class: 'muted', text: 'This is the document as approved. It cannot be changed.' }),
    table(
      ['Date', 'Visits', 'Entries', 'Warnings'],
      days.map((day) => {
        const row = record.content.rows[day];
        return el('tr', {}, [
          el('td', { text: day }),
          el('td', { text: row.visits.map((visit) => visit[2]).join(', ') }),
          el('td', { text: row.entries.map((entry) => entry[2]).join(', ') }),
          el('td', { text: (row.warnings || []).map((item) => item[1]).join(', ') }),
        ]);
      }),
    ),
  );
  if (record.acknowledged.length) {
    target.append(table(
      ['Acknowledged finding', 'Detail'],
      record.acknowledged.map((item) => el('tr', {}, [
        el('td', { text: item.constraint || 'calendar conflict' }),
        el('td', { text: item.detail }),
      ])),
    ));
  }
  // Both exports name this version's own finalisation id, so viewing an older version
  // downloads and prints that version rather than whatever is in force today.
  target.append(
    el('div', { class: 'actions export-actions' }, [
      el('a', {
        class: 'button',
        href: `/api/finalisations/${record.finalisation_id}/export.xlsx`,
        download: '',
        text: 'Download Excel (.xlsx)',
      }),
      el('a', {
        class: 'button',
        href: `/api/finalisations/${record.finalisation_id}/export.pdf`,
        download: '',
        text: 'Download PDF (.pdf)',
      }),
      el('a', {
        class: 'button',
        href: `/finalisations/${record.finalisation_id}/print`,
        target: '_blank',
        rel: 'noopener',
        text: 'Print / Save as PDF',
      }),
    ]),
  );
  const reason = el('input', { type: 'text', placeholder: 'Why does this version need correcting?' });
  const outcome = el('div', { class: 'detail' });
  target.append(
    el('div', { class: 'actions' }, [
      reason,
      el('button', {
        class: 'button',
        text: 'Start a correction',
        onclick: async () => {
          try {
            const correction = await api(`/api/finalisations/${record.finalisation_id}/corrections`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ reason: reason.value }),
            });
            showBanner(outcome, 'ok', `Correction opened as revision ${correction.revision_number}.`, [
              `It starts as a copy of version ${correction.correction_of_sequence}.`,
              `${correction.pending_dates.length} date(s) must be reviewed again.`,
            ]);
            await renderMonths(registerId, runId);
          } catch (error) {
            showBanner(outcome, 'warn', error.message);
          }
        },
      }),
    ]),
    outcome,
  );
}

function monthCard(registerId, runId, month) {
  const panel = el('div', { class: 'month-card' });
  const checkTarget = el('div', { class: 'detail' });
  const chainTarget = el('div', { class: 'detail' });

  const actions = el('div', { class: 'actions' }, [
    el('button', {
      class: 'button',
      text: 'Why can this be closed?',
      onclick: async () => {
        const check = await api(
          `/api/generations/${runId}/finalisation?month=${encodeURIComponent(month.month)}`,
        );
        renderFinalisationCheck(checkTarget, check, runId, registerId);
      },
    }),
    el('button', {
      class: 'button',
      text: 'Issued versions',
      onclick: async () => {
        const chain = await api(`/api/registers/${registerId}/finalisations/${month.month}`);
        renderChain(chainTarget, chain, registerId, runId);
      },
    }),
  ]);

  // A month card can export only an immutable issued version. A reviewed draft
  // without a finalisation deliberately has no document-download actions.
  if (month.latest_finalisation_id) {
    actions.append(
      el('a', {
        class: 'button',
        href: `/api/finalisations/${month.latest_finalisation_id}/export.xlsx`,
        download: '',
        text: 'Download Excel (.xlsx)',
      }),
      el('a', {
        class: 'button',
        href: `/finalisations/${month.latest_finalisation_id}/print`,
        target: '_blank',
        rel: 'noopener',
        text: 'Print / Save as PDF',
      }),
    );
  }

  if (month.open_correction_run_id) {
    actions.append(
      el('button', {
        class: 'button',
        text: `Open the correction (revision ${month.open_correction_revision || '?'})`,
        onclick: () => openDraft(month.open_correction_run_id),
      }),
      el('button', {
        class: 'button',
        text: 'Abandon the correction',
        onclick: async () => {
          await api(
            `/api/registers/${registerId}/months/${encodeURIComponent(month.month)}/correction`,
            { method: 'DELETE' },
          );
          await renderMonths(registerId, runId);
        },
      }),
    );
  }

  panel.append(monthDimensions(month), actions, checkTarget, chainTarget);
  return panel;
}

// Closing every open month is one act, and the server is asked what it would do before
// anything is closed. The panel shows the whole picture -- the months that would close,
// the months already closed and the months that cannot close yet -- because confirming
// against a partial list is how an operator agrees to something they were not shown.
// Nothing here decides eligibility: the button is enabled by the server's verdict and the
// request repeats the fingerprints and acknowledgements the preflight issued.

async function bulkFinalisationPanel(registerId, runId) {
  const panel = el('div', { class: 'detail bulk-finalisation' });
  const result = el('div', { class: 'detail' });
  let preflight;
  try {
    preflight = await api(`/api/generations/${runId}/finalisation/preflight`);
  } catch (error) {
    panel.append(el('p', { class: 'muted', text: error.message }));
    return panel;
  }

  panel.append(el('h3', { text: 'Close every open month' }));
  if (preflight.already_finalised.length) {
    panel.append(el('p', {
      class: 'muted',
      text: `Already closed in this revision: ${preflight.already_finalised.join(', ')}. `
        + 'They are not closed again and their records are not touched.',
    }));
  }
  if (!preflight.target_months.length) {
    panel.append(el('p', { class: 'status-ok', text: 'Every month of this revision is closed.' }));
    return panel;
  }

  const findings = [];
  preflight.months.forEach((month) => {
    if (month.already_finalised) return;
    month.findings.forEach((finding) => findings.push({ ...finding, month: month.month }));
  });

  panel.append(
    el('p', {
      class: 'muted',
      text: 'Closing every open month at once is all or nothing: if any month cannot be '
        + 'closed, none of them is. Nothing is reviewed for you, and a month whose dates '
        + 'have not been read in front of you is refused rather than closed.',
    }),
    table(
      ['Month', 'Dates read', 'Allocation', 'Findings', 'Status'],
      preflight.months.map((month) => el('tr', {}, [
        el('td', { text: month.month }),
        el('td', { text: `${month.reviewed_dates} of ${month.total_dates}` }),
        el('td', { text: `${month.allocator_status}${month.locked_dates ? ` · ${month.locked_dates} locked` : ''}` }),
        el('td', { text: month.findings.length ? `${month.findings.length}` : 'none' }),
        el('td', {
          class: month.already_finalised ? 'muted' : (month.eligible ? 'status-ok' : 'status-warn'),
          text: month.already_finalised
            ? `closed as version ${month.sequence}`
            : (month.eligible ? 'can be closed' : `${month.blockers.length} blocker(s)`),
        }),
      ])),
    ),
  );

  if (preflight.blocked_months.length) {
    const blockers = preflight.months
      .filter((month) => !month.already_finalised && !month.eligible)
      .flatMap((month) => month.blockers.map((blocker) => ({ ...blocker, month: month.month })));
    panel.append(
      el('strong', { text: 'What is in the way' }),
      table(
        ['Month', 'What is in the way', 'Detail'],
        blockers.map((blocker) => el('tr', {}, [
          el('td', { text: blocker.month }),
          el('td', { text: blocker.label }),
          el('td', { text: blocker.detail }),
        ])),
      ),
    );
  }

  if (findings.length) {
    panel.append(
      el('strong', { text: 'Recorded findings to acknowledge' }),
      el('p', {
        class: 'muted',
        text: 'These stay on each month\'s record after closing; acknowledging says you have seen them.',
      }),
      table(
        ['Month', 'Finding', 'Detail', 'Acknowledge'],
        findings.map((item) => el('tr', {}, [
          el('td', { text: item.month }),
          el('td', { text: item.what }),
          el('td', { text: item.detail }),
          el('td', {}, [
            el('input', {
              type: 'checkbox',
              class: 'bulk-ack',
              value: item.token,
              'data-month': item.month,
              'data-kind': item.kind,
              title: item.token,
            }),
          ]),
        ])),
      ),
    );
  }

  const note = el('input', { type: 'text', placeholder: 'Note recorded on every month closed (optional)' });
  const button = el('button', {
    class: 'button button-primary',
    text: `Finalize all months (${preflight.target_months.length})`,
    disabled: !preflight.can_finalise,
    onclick: async () => {
      if (button.disabled) return;
      button.disabled = true;
      const label = button.textContent;
      button.textContent = 'Closing…';
      try {
        const months = {};
        preflight.target_months.forEach((month) => {
          const entry = preflight.months.find((item) => item.month === month);
          const acknowledged = Array.from(panel.querySelectorAll(`input.bulk-ack[data-month="${month}"]:checked`))
            .map((node) => node.value);
          months[month] = { fingerprint: entry.fingerprint, acknowledged };
        });
        const closed = await api(`/api/generations/${runId}/finalisation/bulk`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            months,
            note: note.value.trim() === '' ? null : note.value.trim(),
          }),
        });
        showBanner(
          result,
          'ok',
          `Closed ${closed.count} month(s) in one act.`,
          closed.months.map((record) => `${record.month}: version ${record.sequence}, finalisation ${record.finalisation_id}`),
        );
        await renderMonths(registerId, runId);
      } catch (error) {
        showBanner(result, 'warn', error.message);
        button.disabled = false;
        button.textContent = label;
      }
    },
  });

  panel.append(
    el('p', {
      class: 'muted',
      text: preflight.can_finalise
        ? 'The server reports every open month can be closed.'
        : 'The server reports at least one open month cannot be closed, so none will be.',
    }),
    el('div', { class: 'actions' }, [note, button]),
    result,
  );
  return panel;
}

async function renderMonths(registerId, runId) {
  const target = document.getElementById('draft-months');
  if (!target) return;
  target.textContent = '';
  const months = await api(`/api/registers/${registerId}/months`);
  target.append(el('h3', { text: 'Closing the month' }));
  target.append(el('p', { class: 'muted', text: 'Each month is closed on its own. A closed month is never reopened; a mistake is corrected by a new version.' }));
  target.append(await bulkFinalisationPanel(registerId, runId));
  const issuedCount = months.filter((month) => month.latest_finalisation_id).length;
  if (issuedCount > 0) {
    // Three downloads, each naming exactly what it contains. A package that promises
    // PDFs and holds an HTML file sends the operator looking for a fault in their own
    // reader, so the printed and the spreadsheet packages are separate buttons.
    target.append(el('div', { class: 'actions export-actions' }, [
      el('a', {
        class: 'button button-primary',
        href: `/api/registers/${registerId}/exports/pdf.zip`,
        download: '',
        text: `Download PDFs — all months (${issuedCount}) (.zip)`,
      }),
      el('a', {
        class: 'button button-primary',
        href: `/api/registers/${registerId}/exports/excel.zip`,
        download: '',
        text: `Download Excel — all months (${issuedCount}) (.zip)`,
      }),
      el('a', {
        class: 'button',
        href: `/api/registers/${registerId}/exports.zip`,
        download: '',
        text: `Download everything issued (${issuedCount}) (.zip)`,
      }),
    ]));
    target.append(el('p', {
      class: 'muted',
      text: 'The PDF package holds one PDF for each issued month, laid out on this '
        + 'computer. The Excel package holds one workbook for each. The third holds the '
        + 'workbooks plus one combined print-ready page and is kept for older links.',
    }));
  }
  months.forEach((month) => target.append(monthCard(registerId, runId, month)));
}

// --- backup and restore ----------------------------------------------------

// Every word on this panel comes from the server: which format the file is, what it
// holds, which schema it came from and whether it was accepted. The browser decides
// nothing about whether a file is restorable, and it never reads the file itself.

const CONTENT_LABELS = {
  registers: 'Registers',
  revisions: 'Revisions',
  finalisations: 'Issued versions',
  finalised_months: 'Months closed',
  rows: 'Dates in drafts',
  visits: 'Visits',
  entries: 'Other entries',
  reviews: 'Dates reviewed',
  locks: 'Dates locked',
  dtcs: 'DTCs',
  areas: 'Areas',
  feeders: 'Feeders',
  activities: 'Activities',
  holidays: 'Holidays',
  import_batches: 'Imports received',
  staged_rows: 'Import rows staged',
};

const restoreState = { token: null };

function contentList(contents) {
  const items = Object.keys(contents || {})
    .filter((key) => CONTENT_LABELS[key])
    .map((key) => `${CONTENT_LABELS[key]}: ${contents[key]}`);
  return el('ul', {}, items.map((text) => el('li', { text })));
}

function factsTable(pairs) {
  return el('table', { class: 'table' }, [
    el('tbody', {}, pairs
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([label, value]) => el('tr', {}, [
        el('th', { text: label }),
        el('td', { text: String(value) }),
      ]))),
  ]);
}

async function loadBackup() {
  // Nothing is fetched on arrival: a backup is an action, and a restore must start from
  // the operator choosing a file rather than from whatever the tool remembers.
  document.getElementById('backup-status').textContent = '';
}

async function createBackup() {
  const button = document.getElementById('backup-create');
  const status = document.getElementById('backup-status');
  const banner = document.getElementById('backup-banner');
  const result = document.getElementById('backup-result');
  hide(banner);
  hide(result);
  button.disabled = true;
  status.textContent = 'Copying the database…';
  try {
    const held = await api('/api/backups', { method: 'POST' });
    status.textContent = '';
    showBanner(banner, 'ok', 'The backup was created and checked.');
    result.hidden = false;
    result.append(
      el('h4', { text: 'Your backup' }),
      el('p', {
        class: 'muted',
        text: 'Download it now. A backup is held only until this application is closed.',
      }),
      el('p', {}, [
        el('a', {
          class: 'button button-primary',
          href: held.download,
          download: held.filename,
          text: `Download ${held.filename}`,
        }),
      ]),
      factsTable([
        ['File', held.filename],
        ['Created', held.created_at],
        ['Size', `${held.size_bytes} bytes`],
        ['Backup format', `version ${held.format_version}`],
        ['Schema', `version ${held.schema_version}`],
        ['Integrity check', held.verified && held.verified.integrity],
        ['Reference check', held.verified && held.verified.foreign_keys],
      ]),
      el('h4', { text: 'What it holds' }),
      contentList(held.contents),
    );
  } catch (error) {
    status.textContent = '';
    showBanner(banner, 'error', 'The backup could not be created, so nothing was written.', [error.message]);
  } finally {
    button.disabled = false;
  }
}

async function validateRestore() {
  const input = document.getElementById('restore-file');
  const button = document.getElementById('restore-validate');
  const status = document.getElementById('restore-status');
  const banner = document.getElementById('restore-banner');
  const preview = document.getElementById('restore-preview');
  const confirm = document.getElementById('restore-confirm');
  hide(banner);
  hide(preview);
  hide(confirm);
  restoreState.token = null;
  if (!input.files || !input.files.length) {
    showBanner(banner, 'error', 'Choose a backup file first.', [
      'A backup is the file the tool produced when you created one, ending in .zip.',
    ]);
    return;
  }
  button.disabled = true;
  status.textContent = 'Checking the file…';
  try {
    const body = new FormData();
    body.append('file', input.files[0]);
    const pending = await api('/api/backup-validation', { method: 'POST', body });
    status.textContent = '';
    restoreState.token = pending.restore_token;
    showBanner(banner, 'ok', 'This file is a backup this tool can restore.');
    preview.hidden = false;
    const brought = (pending.migrations_applied || []).length
      ? `This backup was made by an earlier version of the tool and will be brought up to date as it is restored.`
      : 'This backup was made by this version of the tool.';
    preview.append(
      el('h4', { text: 'What is in the file' }),
      factsTable([
        ['File', pending.filename],
        ['Created', pending.created_at],
        ['Schema in the file', pending.backup_schema_version],
        ['Schema here', pending.this_tool_schema_version],
        ['Integrity check', pending.verified && pending.verified.integrity],
        ['Reference check', pending.verified && pending.verified.foreign_keys],
      ]),
      el('p', { class: 'muted', text: brought }),
      contentList(pending.contents),
    );
    (pending.warnings || []).forEach((warning) => {
      preview.append(el('p', { class: 'muted', text: warning }));
    });
    // The word to type is the one the server asked for, so the page cannot drift from
    // the confirmation the restore actually requires.
    document.getElementById('restore-word-label').textContent =
      pending.confirmation_word || 'the word shown';
    confirm.hidden = false;
    document.getElementById('restore-word').value = '';
  } catch (error) {
    status.textContent = '';
    showBanner(banner, 'error', 'This file cannot be restored, so nothing was changed.', [error.message]);
  } finally {
    button.disabled = false;
  }
}

async function runRestore() {
  const button = document.getElementById('restore-run');
  const word = document.getElementById('restore-word').value.trim();
  const status = document.getElementById('restore-status');
  const banner = document.getElementById('restore-banner');
  const result = document.getElementById('restore-result');
  hide(banner);
  hide(result);
  if (!restoreState.token) {
    showBanner(banner, 'error', 'Check a backup file before restoring.', [
      'A file has to be accepted before it can replace anything.',
    ]);
    return;
  }
  button.disabled = true;
  status.textContent = 'Replacing the database. Leave this page open…';
  try {
    const answer = await api('/api/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ restore_token: restoreState.token, confirmation: word }),
    });
    status.textContent = '';
    restoreState.token = null;
    hide(document.getElementById('restore-confirm'));
    showBanner(banner, 'ok', 'The database was replaced and checked.');
    result.hidden = false;
    result.append(
      el('h4', { text: 'What was checked afterwards' }),
      factsTable([
        ['Integrity check', answer.verified && answer.verified.integrity],
        ['Reference check', answer.verified && answer.verified.foreign_keys],
        ['Schema', `version ${answer.verified && answer.verified.schema_version}`],
        ['Issued versions readable', (answer.verified && answer.verified.issued_versions || []).length],
        ['Registers readable', answer.verified && answer.verified.registers],
      ]),
      el('p', {
        class: 'muted',
        text: 'A copy of the database that was in use before the restore was kept beside it:',
      }),
      el('p', { text: answer.rollback_copy }),
      el('p', {
        class: 'muted',
        text: 'Reload the page to work with the restored records. Delete that copy once you are satisfied.',
      }),
      el('button', { class: 'button', text: 'Reload', onclick: () => window.location.reload() }),
    );
  } catch (error) {
    status.textContent = '';
    showBanner(banner, 'error', 'The database was not replaced.', [error.message]);
    if (error.constraint === 'restore_in_progress') {
      banner.append(el('p', {
        class: 'muted',
        text: 'Another restore is in progress. Wait for it to finish, then check the file again.',
      }));
    }
  } finally {
    button.disabled = false;
  }
}

document.getElementById('backup-create').addEventListener('click', createBackup);
document.getElementById('restore-validate').addEventListener('click', validateRestore);
document.getElementById('restore-run').addEventListener('click', runRestore);
// --- start -----------------------------------------------------------------

activate('home');
