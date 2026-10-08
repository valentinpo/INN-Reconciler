#!/usr/bin/env python3
"""
Генерирует точный HTML-отчёт, сверяя CSV с реальными PDF в папке.
"""
import pandas as pd
import json
from pathlib import Path
from datetime import datetime

def generate_accurate_report(csv_path: Path, pdf_dir: Path, output_path: Path):
    if not csv_path.exists():
        print(f"❌ CSV не найден: {csv_path}")
        return

    # 1. Загрузка и очистка данных
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df['EGRUL_STATUS'] = df['EGRUL_STATUS'].str.strip()
    df['EGRUL_PDF_PATH'] = df['EGRUL_PDF_PATH'].str.strip()
    df['INN_CLEAN'] = df['INN_CLEAN'].str.strip()
    df['EGRUL_NAME'] = df['EGRUL_NAME'].str.strip()
    df['EGRUL_LOG'] = df['EGRUL_LOG'].str.strip()

    # 2. Сверка с реальными файлами на диске
    pdf_dir.mkdir(parents=True, exist_ok=True)
    actual_pdfs = {p.stem.replace('egrul_', '') for p in pdf_dir.glob('egrul_*.pdf')}
    
    # Если файл есть на диске, но статус пустой/ошибочный → ставим success
    def fix_status(row):
        inn = row['INN_CLEAN']
        if inn in actual_pdfs:
            return 'success'
        return row['EGRUL_STATUS']

    df['EGRUL_STATUS'] = df.apply(fix_status, axis=1)
    df.loc[df['EGRUL_STATUS'] == 'success', 'EGRUL_PDF_PATH'] = df.loc[df['EGRUL_STATUS'] == 'success', 'INN_CLEAN'].apply(lambda i: f"pdfs/egrul_{i}.pdf")

    # 3. Точная статистика
    stats = {
        "total": len(df),
        "success": int((df['EGRUL_STATUS'] == 'success').sum()),
        "skipped": int((df['EGRUL_STATUS'] == 'skipped_existing').sum()),
        "errors": int(len(df) - df['EGRUL_STATUS'].isin(['success', 'skipped_existing']).sum()),
        "pdfs_on_disk": len(actual_pdfs)
    }

    # 4. Подготовка данных для таблицы
    records = df[['INN_CLEAN', 'EGRUL_STATUS', 'EGRUL_NAME', 'EGRUL_PDF_PATH', 'EGRUL_LOG']].to_dict(orient='records')
    for r in records:
        r['EGRUL_NAME'] = r['EGRUL_NAME'] or '—'
        r['EGRUL_LOG'] = r['EGRUL_LOG'] or ''
        r['EGRUL_PDF_PATH'] = r['EGRUL_PDF_PATH'] or '—'
        if r['EGRUL_PDF_PATH'] != '—' and 'pdfs/' in r['EGRUL_PDF_PATH']:
            r['EGRUL_PDF_PATH'] = r['EGRUL_PDF_PATH'].replace('\\', '/')

    data_json = json.dumps(records, ensure_ascii=False)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    # 5. HTML-шаблон
    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>INN_Reconciler | Отчёт ЕГРЮЛ</title>
<style>
:root{{--bg:#f8f9fa;--card:#fff;--text:#212529;--muted:#6c757d;--success:#198754;--warn:#ffc107;--err:#dc3545;--info:#0dcaf0;--border:#dee2e6}}
*{{box-sizing:border-box;margin:0;padding:0;font-family:system-ui,-apple-system,sans-serif}}
body{{background:var(--bg);color:var(--text);padding:20px;line-height:1.6}}
.container{{max-width:1200px;margin:0 auto}}
h1{{text-align:center;margin-bottom:10px;font-size:2rem}}
.sub{{text-align:center;color:var(--muted);margin-bottom:20px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:15px;margin-bottom:20px}}
.stat{{background:var(--card);padding:15px;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,.08);text-align:center}}
.stat .val{{font-size:1.8rem;font-weight:700}}.stat .lab{{color:var(--muted);font-size:.85rem}}
.controls{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:15px}}
input{{padding:10px 12px;border:1px solid var(--border);border-radius:8px;flex:1;min-width:200px}}
.btn{{padding:8px 14px;border:1px solid var(--border);background:var(--card);border-radius:8px;cursor:pointer;transition:.2s}}
.btn:hover{{background:#e9ecef}}.btn.active{{background:var(--text);color:#fff;border-color:var(--text)}}
.table-wrap{{background:var(--card);border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow-x:auto}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;text-align:left;border-bottom:1px solid var(--border)}}
th{{background:#f1f3f5;position:sticky;top:0;font-weight:600}}tr:hover{{background:#f8f9fa}}
.badge{{display:inline-block;padding:4px 8px;border-radius:6px;font-size:.85rem;font-weight:600;color:#fff}}
.s-success{{background:var(--success)}}.s-skip{{background:var(--info);color:#000}}.s-err{{background:var(--err)}}.s-other{{background:var(--muted)}}
.link{{color:var(--success);text-decoration:none;font-weight:500}}.link:hover{{text-decoration:underline}}
.no-data{{text-align:center;padding:30px;color:var(--muted)}}
@media(max-width:768px){{th,td{{padding:8px;font-size:.9rem}}}}
</style></head><body>
<div class="container">
  <h1>📊 INN_Reconciler | Выписки ЕГРЮЛ</h1>
  <p class="sub">Сформировано: {timestamp} | Всего записей: {stats['total']}</p>
  
  <div class="stats">
    <div class="stat"><div class="val" style="color:var(--success)">{stats['success']}</div><div class="lab">✅ Успешно</div></div>
    <div class="stat"><div class="val" style="color:var(--info)">{stats['skipped']}</div><div class="lab">⏭️ Пропущено</div></div>
    <div class="stat"><div class="val" style="color:var(--err)">{stats['errors']}</div><div class="lab">❌ Ошибки/Пусто</div></div>
    <div class="stat"><div class="val">{stats['pdfs_on_disk']}</div><div class="lab">📁 PDF на диске</div></div>
  </div>

  <div class="controls">
    <input type="text" id="search" placeholder="🔍 Поиск по ИНН или названию...">
    <button class="btn active" data-f="all">Все</button>
    <button class="btn" data-f="success">Успешно</button>
    <button class="btn" data-f="skip">Пропущено</button>
    <button class="btn" data-f="err">Ошибки</button>
  </div>

  <div class="table-wrap">
    <table><thead><tr><th>ИНН</th><th>Наименование</th><th>Статус</th><th>PDF</th><th>Лог</th></tr></thead>
    <tbody id="tbody"></tbody></table>
  </div>
</div>
<script>
const DATA = {data_json};
const tbody = document.getElementById('tbody');
const search = document.getElementById('search');
let filter = 'all';
function render(){{
  const q = search.value.toLowerCase().trim();
  const filtered = DATA.filter(r => {{
    const s = r.EGRUL_STATUS.toLowerCase();
    const matchStatus = filter === 'all' || (filter==='success' && (s==='success')) || (filter==='skip' && s.includes('skip')) || (filter==='err' && s!=='success' && !s.includes('skip'));
    const matchQ = !q || r.INN_CLEAN.includes(q) || r.EGRUL_NAME.toLowerCase().includes(q);
    return matchStatus && matchQ;
  }});
  tbody.innerHTML = filtered.length ? filtered.map(r => {{
    const badge = r.EGRUL_STATUS === 'success' ? '<span class="badge s-success">success</span>' :
                  r.EGRUL_STATUS.includes('skip') ? '<span class="badge s-skip">skipped</span>' :
                  r.EGRUL_STATUS ? `<span class="badge s-err">${{r.EGRUL_STATUS}}</span>` :
                  '<span class="badge s-other">pending</span>';
    const pdf = (r.EGRUL_PDF_PATH && r.EGRUL_PDF_PATH !== '—') ? `<a class="link" href="${{r.EGRUL_PDF_PATH}}" download>📥 Скачать</a>` : '—';
    return `<tr><td><b>${{r.INN_CLEAN}}</b></td><td>${{r.EGRUL_NAME}}</td><td>${{badge}}</td><td>${{pdf}}</td><td style="font-size:.85rem;color:#666">${{r.EGRUL_LOG || '—'}}</td></tr>`;
  }}).join('') : '<tr><td colspan="5" class="no-data">Ничего не найдено</td></tr>';
}}
document.querySelectorAll('.btn').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.btn').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); filter = b.dataset.f; render();
}});
search.addEventListener('input', render);
render();
</script></body></html>"""
    
    output_path.write_text(html, encoding='utf-8')
    print(f"✅ Отчёт сохранён: {output_path}")
    print(f"📊 Статистика: {stats['success']} успех, {stats['skipped']} пропущено, {stats['errors']} ошибки, {stats['pdfs_on_disk']} файлов на диске.")

if __name__ == '__main__':
    generate_accurate_report(
        csv_path=Path('data/output/02_egrul_parsed.csv'),
        pdf_dir=Path('data/output/pdfs'),
        output_path=Path('data/output/report_egrul.html')
    )