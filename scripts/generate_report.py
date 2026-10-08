#!/usr/bin/env python3
"""
Генерирует интерактивный HTML-отчёт из 02_egrul_parsed.csv
Не требует внешних библиотек. Работает оффлайн.
"""
import csv
import json
from pathlib import Path
from datetime import datetime

def generate_html(csv_path: Path, output_path: Path):
    if not csv_path.exists():
        print(f"❌ Файл не найден: {csv_path}")
        return

    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                'inn': r.get('INN_CLEAN', '').strip(),
                'name': r.get('EGRUL_NAME', '').strip() or '—',
                'status': r.get('EGRUL_STATUS', '').strip(),
                'pdf_path': r.get('EGRUL_PDF_PATH', '').strip(),
                'log': r.get('EGRUL_LOG', '').strip(),
                'valid': r.get('IS_VALID', '').strip()
            })

    total = len(rows)
    success = sum(1 for r in rows if r['status'] == 'success')
    skipped = sum(1 for r in rows if r['status'] == 'skipped_existing')
    errors = total - success - skipped

    data_json = json.dumps(rows, ensure_ascii=False)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>INN_Reconciler | Отчёт ЕГРЮЛ</title>
<style>
:root {{ --bg: #f8f9fa; --card: #fff; --text: #212529; --muted: #6c757d; --success: #198754; --warn: #ffc107; --err: #dc3545; --info: #0dcaf0; --border: #dee2e6; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
body {{ background: var(--bg); color: var(--text); padding: 20px; line-height: 1.5; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; margin-bottom: 10px; font-size: 2rem; }}
.sub {{ text-align: center; color: var(--muted); margin-bottom: 20px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }}
.stat {{ background: var(--card); padding: 15px; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,.08); text-align: center; }}
.stat .val {{ font-size: 1.8rem; font-weight: 700; }}
.stat .lab {{ color: var(--muted); font-size: 0.9rem; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 15px; }}
input {{ padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; flex: 1; min-width: 200px; font-size: 1rem; }}
.btn {{ padding: 8px 14px; border: 1px solid var(--border); background: var(--card); border-radius: 8px; cursor: pointer; font-size: 0.9rem; transition: .2s; }}
.btn:hover {{ background: #e9ecef; }}
.btn.active {{ background: var(--text); color: #fff; border-color: var(--text); }}
.table-wrap {{ background: var(--card); border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,.06); overflow: hidden; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: #f1f3f5; position: sticky; top: 0; font-weight: 600; }}
tr:hover {{ background: #f8f9fa; }}
.badge {{ display: inline-block; padding: 4px 8px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; color: #fff; }}
.s-success {{ background: var(--success); }} .s-skip {{ background: var(--info); color: #000; }} .s-err {{ background: var(--err); }} .s-other {{ background: var(--muted); }}
.link {{ color: var(--success); text-decoration: none; font-weight: 500; }}
.link:hover {{ text-decoration: underline; }}
.no-data {{ text-align: center; padding: 30px; color: var(--muted); }}
@media (max-width: 768px) {{ th, td {{ padding: 8px; font-size: 0.9rem; }} .stat .val {{ font-size: 1.4rem; }} }}
</style>
</head>
<body>
<div class="container">
  <h1>📊 INN_Reconciler | Выписки ЕГРЮЛ</h1>
  <p class="sub">Сформировано: {timestamp} | Всего записей: {total}</p>
  
  <div class="stats">
    <div class="stat"><div class="val" style="color:var(--success)">{success}</div><div class="lab">✅ Успешно</div></div>
    <div class="stat"><div class="val" style="color:var(--info)">{skipped}</div><div class="lab">⏭️ Пропущено</div></div>
    <div class="stat"><div class="val" style="color:var(--err)">{errors}</div><div class="lab">❌ Ошибки</div></div>
    <div class="stat"><div class="val">{total}</div><div class="lab">📋 Всего</div></div>
  </div>

  <div class="controls">
    <input type="text" id="search" placeholder="🔍 Поиск по ИНН или названию...">
    <button class="btn active" data-f="all">Все</button>
    <button class="btn" data-f="success">Успешно</button>
    <button class="btn" data-f="skip">Пропущено</button>
    <button class="btn" data-f="err">Ошибки</button>
  </div>

  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>ИНН</th>
        <th>Наименование</th>
        <th>Статус</th>
        <th>PDF</th>
        <th>Лог</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
</div>

<script>
const DATA = {data_json};
const tbody = document.getElementById('tbody');
const search = document.getElementById('search');
let filter = 'all';

function render() {{
  const q = search.value.toLowerCase().trim();
  const filtered = DATA.filter(r => {{
    const matchStatus = filter === 'all' || 
      (filter === 'success' && r.status === 'success') || 
      (filter === 'skip' && r.status.includes('skip')) || 
      (filter === 'err' && r.status !== 'success' && !r.status.includes('skip'));
    const matchQ = !q || r.inn.includes(q) || r.name.toLowerCase().includes(q);
    return matchStatus && matchQ;
  }});

  tbody.innerHTML = filtered.length ? filtered.map(r => {{
    const badge = r.status === 'success' ? '<span class="badge s-success">success</span>' :
                  r.status.includes('skip') ? '<span class="badge s-skip">skipped</span>' :
                  r.status ? '<span class="badge s-err">' + r.status.replace('_',' ') + '</span>' :
                  '<span class="badge s-other">unknown</span>';
    const pdf = r.pdf_path ? `<a class="link" href="${{r.pdf_path}}" download>📥 Скачать</a>` : '—';
    return `<tr>
      <td><b>${{r.inn}}</b></td>
      <td>${{r.name}}</td>
      <td>${{badge}}</td>
      <td>${{pdf}}</td>
      <td style="font-size:0.85rem; color:#666">${{r.log || '—'}}</td>
    </tr>`;
  }}).join('') : '<tr><td colspan="5" class="no-data">Ничего не найдено</td></tr>';
}}

document.querySelectorAll('.btn').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.btn').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  filter = b.dataset.f;
  render();
}});

search.addEventListener('input', render);
render();
</script>
</body>
</html>"""

    output_path.write_text(html, encoding='utf-8')
    print(f"✅ Отчёт сохранён: {output_path}")
    print("💡 Откройте файл в браузере. Ссылки на PDF работают, если HTML лежит в `data/output/`")

if __name__ == '__main__':
    csv_file = Path('data/output/02_egrul_parsed.csv')
    html_file = Path('data/output/report_egrul.html')
    generate_html(csv_file, html_file)