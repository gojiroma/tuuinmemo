// manual_entries.js - シンプルな行追加 / 一括送信ロジック

function createCellInput(type, name, placeholder='', value='') {
  const td = document.createElement('td');
  let input;
  if (type === 'date') {
    input = document.createElement('input');
    input.type = 'date';
  } else {
    input = document.createElement('input');
    input.type = type || 'text';
    input.placeholder = placeholder;
  }
  input.name = name;
  input.value = value;
  td.appendChild(input);
  return td;
}

function createActionCell() {
  const td = document.createElement('td');
  const del = document.createElement('button');
  del.type = 'button';
  del.textContent = '削除';
  del.addEventListener('click', () => td.parentElement.remove());
  td.appendChild(del);
  return td;
}

function addStepRow(date='', steps='', memo='') {
  const tbody = document.querySelector('#steps-table tbody');
  const tr = document.createElement('tr');
  tr.appendChild(createCellInput('date', 'date', '', date));
  tr.appendChild(createCellInput('number', 'steps', '歩数', steps));
  tr.appendChild(createCellInput('text', 'memo', 'メモ', memo));
  tr.appendChild(createActionCell());
  tbody.appendChild(tr);
}

function addSleepRow(date='', duration='', score='') {
  const tbody = document.querySelector('#sleep-table tbody');
  const tr = document.createElement('tr');
  tr.appendChild(createCellInput('date', 'date', '', date));
  tr.appendChild(createCellInput('number', 'duration', '分', duration));
  tr.appendChild(createCellInput('number', 'score', '0-100', score));
  tr.appendChild(createActionCell());
  tbody.appendChild(tr);
}

function collectTableData(tableId, fieldNames) {
  const rows = Array.from(document.querySelectorAll(`#${tableId} tbody tr`));
  return rows.map(tr => {
    const inputs = tr.querySelectorAll('input');
    const obj = {};
    inputs.forEach((input, i) => {
      obj[fieldNames[i]] = input.value ? input.value.trim() : '';
    });
    return obj;
  }).filter(r => Object.values(r).some(v => v !== ''));
}

async function postJson(url, data) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
    credentials: 'same-origin'
  });
  return res.json();
}

function setStatus(msg) {
  document.getElementById('status').textContent = msg;
}

async function loadRecent(type) {
  // 共通の vitals API を使って最近50日分を取得
  const end = new Date().toISOString().slice(0,10);
  const startDate = new Date();
  startDate.setDate(startDate.getDate() - 49);
  const start = startDate.toISOString().slice(0,10);
  const res = await fetch(`/api/vitals?start=${start}&end=${end}`, {headers: {'X-Access-Token': ''}});
  // Note: X-Access-Token はブラウザ側からは使えない場合があります（ADMIN_TOKEN を直接渡すのは危険）。
  // 管理者トークン認証はブラウザから token=... クエリで行う運用にするか、あなたの運用に合わせて調整してください.
  if (!res.ok) {
    setStatus('読み込みに失敗しました（認可が必要）');
    return;
  }
  const j = await res.json();
  if (type === 'steps') {
    document.querySelector('#steps-table tbody').innerHTML = '';
    j.steps.forEach(r => addStepRow(r.date, r.steps || '', ''));
    setStatus('歩数データを読み込みました');
  } else {
    document.querySelector('#sleep-table tbody').innerHTML = '';
    j.sleep.forEach(r => addSleepRow(r.date, r.duration || '', r.score || ''));
    setStatus('睡眠データを読み込みました');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('add-step-row').addEventListener('click', () => addStepRow());
  document.getElementById('add-sleep-row').addEventListener('click', () => addSleepRow());
  document.getElementById('save-steps').addEventListener('click', async () => {
    const data = collectTableData('steps-table', ['date','steps','memo']);
    if (!data.length) { setStatus('保存する行がありません'); return; }
    setStatus('送信中...');
    const result = await postJson('/api/manual/steps', {entries: data});
    setStatus(result.message || JSON.stringify(result));
  });
  document.getElementById('save-sleep').addEventListener('click', async () => {
    const data = collectTableData('sleep-table', ['date','duration','score']);
    if (!data.length) { setStatus('保存する行がありません'); return; }
    setStatus('送信中...');
    const result = await postJson('/api/manual/sleep', {entries: data});
    setStatus(result.message || JSON.stringify(result));
  });
  document.getElementById('load-steps').addEventListener('click', () => loadRecent('steps'));
  document.getElementById('load-sleep').addEventListener('click', () => loadRecent('sleep'));

  // 最低1行入れておく
  if (!document.querySelector('#steps-table tbody tr')) addStepRow();
  if (!document.querySelector('#sleep-table tbody tr')) addSleepRow();
});
