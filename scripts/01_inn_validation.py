#!/usr/bin/env python3
"""
Этап 2: Массовый парсинг выписок ЕГРЮЛ/ЕГРИП с egrul.nalog.ru
ИСПРАВЛЕНИЕ: корректная обработка булевых столбцов после read_csv
"""
import pandas as pd
import requests
import time
import logging
from pathlib import Path
from datetime import datetime

# ================= НАСТРОЙКИ =================
CONFIG = {
    "input_csv": "data/output/01_validated_inn.csv",
    "output_csv": "data/output/02_egrul_parsed.csv",
    "pdf_dir": "data/output/pdfs",
    "log_file": "data/output/02_egrul_parsing.log",
    "skip_existing_pdfs": True,
    "delay_between_requests": 4.5,
    "poll_timeout": 40,
    "save_interval": 50,
    "max_retries": 3
}
# =============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(CONFIG["log_file"], encoding="utf-8", mode="w")
    ]
)

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Referer': 'https://egrul.nalog.ru/index.html',
    'X-Requested-With': 'XMLHttpRequest'
})

BASE_URL = 'https://egrul.nalog.ru'

def fetch_egrul_pdf(inn: str, pdf_dir: Path) -> dict:
    """Скачивает выписку ЕГРЮЛ/ЕГРИП. Возвращает статус и метаданные."""
    result = {'EGRUL_STATUS': 'unknown', 'EGRUL_PDF_PATH': None, 'EGRUL_NAME': None, 'EGRUL_LOG': ''}
    pdf_path = pdf_dir / f"egrul_{inn}.pdf"

    try:
        # 1. Запуск поиска
        resp = session.post(BASE_URL, data={'query': inn}, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if data.get('ERRORS', {}).get('captchaSearch'):
            return {'EGRUL_STATUS': 'captcha_required', **result, 'EGRUL_LOG': 'Требуется капча'}

        task_id = data.get('t')
        if not task_id:
            return {'EGRUL_STATUS': 'error', **result, 'EGRUL_LOG': 'Нет task_id'}

        # 2. Ожидание результатов поиска
        for _ in range(CONFIG["poll_timeout"]):
            status_resp = session.get(f'{BASE_URL}/search-result/{task_id}', timeout=15)
            if status_resp.status_code == 200:
                s_data = status_resp.json()
                if s_data.get('status') != 'wait':
                    break
            time.sleep(1)
        else:
            return {'EGRUL_STATUS': 'timeout_search', **result, 'EGRUL_LOG': 'Превышено время поиска'}

        rows = s_data.get('rows', [])
        if not rows:
            return {'EGRUL_STATUS': 'not_found', **result, 'EGRUL_LOG': 'ИНН не найден в ЕГРЮЛ'}

        row = rows[0]
        result['EGRUL_NAME'] = row.get('n', row.get('i', 'Unknown'))
        row_task_id = row.get('t')
        if not row_task_id:
            return {'EGRUL_STATUS': 'error', **result, 'EGRUL_LOG': 'Нет row_task_id'}

        # 3. Запрос на генерацию выписки
        vyp_req = session.get(f'{BASE_URL}/vyp-request/{row_task_id}', timeout=15)
        if vyp_req.status_code != 200:
            return {'EGRUL_STATUS': 'error', **result, 'EGRUL_LOG': f'vyp-request HTTP {vyp_req.status_code}'}

        vyp_task_id = vyp_req.json().get('t')
        if not vyp_task_id:
            return {'EGRUL_STATUS': 'error', **result, 'EGRUL_LOG': 'Нет vyp_task_id'}

        # 4. Ожидание готовности PDF
        for _ in range(CONFIG["poll_timeout"]):
            v_status = session.get(f'{BASE_URL}/vyp-status/{vyp_task_id}', timeout=15)
            if v_status.status_code == 200 and v_status.json().get('status') == 'ready':
                break
            time.sleep(1)
        else:
            return {'EGRUL_STATUS': 'timeout_pdf', **result, 'EGRUL_LOG': 'Превышено время генерации PDF'}

        # 5. Скачивание
        dl = session.get(f'{BASE_URL}/vyp-download/{vyp_task_id}', timeout=60)
        if dl.status_code == 200 and 'application/pdf' in dl.headers.get('Content-Type', ''):
            pdf_dir.mkdir(parents=True, exist_ok=True)
            with open(pdf_path, 'wb') as f:
                f.write(dl.content)
            result['EGRUL_STATUS'] = 'success'
            result['EGRUL_PDF_PATH'] = str(pdf_path.relative_to(Path('data/output')))
            result['EGRUL_LOG'] = f'OK ({len(dl.content)//1024} KB)'
        else:
            result['EGRUL_STATUS'] = 'download_error'
            result['EGRUL_LOG'] = f'Content-Type: {dl.headers.get("Content-Type")}, HTTP: {dl.status_code}'

    except requests.exceptions.RequestException as e:
        result['EGRUL_STATUS'] = 'network_error'
        result['EGRUL_LOG'] = str(e)[:80]
    except Exception as e:
        result['EGRUL_STATUS'] = 'unexpected_error'
        result['EGRUL_LOG'] = str(e)[:80]

    return result

def main():
    input_path = Path(CONFIG["input_csv"])
    output_path = Path(CONFIG["output_csv"])
    pdf_dir = Path(CONFIG["pdf_dir"])
    pdf_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        logging.error("❌ Файл не найден. Запустите сначала 01_inn_validation.py")
        return

    # 🔧 ИСПРАВЛЕНИЕ: читаем без dtype=str, чтобы сохранить типы
    df = pd.read_csv(input_path)
    
    # Восстанавливаем строковый тип для ИНН (чтобы не потерять ведущие нули)
    if 'INN_CLEAN' in df.columns:
        df['INN_CLEAN'] = df['INN_CLEAN'].astype(str)
        
    # Безопасно приводим IS_VALID к boolean
    if 'IS_VALID' in df.columns and df['IS_VALID'].dtype == 'object':
        df['IS_VALID'] = df['IS_VALID'].str.lower().map({'true': True, 'false': False})
        
    valid_mask = df['IS_VALID'] == True
    total = valid_mask.sum()
    
    logging.info(f"📥 Загружено {len(df)} строк. Валидных ИНН: {total}")
    if total == 0:
        logging.warning("⚠️ Столбец IS_VALID пуст или не найден. Проверьте 01_validated_inn.csv")
        logging.info(f"🔍 Доступные столбцы: {list(df.columns)}")
        return

    # Инициализация столбцов, если их нет
    for col in ['EGRUL_STATUS', 'EGRUL_PDF_PATH', 'EGRUL_NAME', 'EGRUL_LOG']:
        if col not in df.columns:
            df[col] = None

    valid_df = df[valid_mask].copy()
    processed = 0
    skipped = 0
    errors = 0

    logging.info("🔄 Запуск массового парсинга...")
    for idx, row in valid_df.iterrows():
        inn = row['INN_CLEAN']
        pdf_path = pdf_dir / f"egrul_{inn}.pdf"

        # Пропуск уже скачанных
        if CONFIG["skip_existing_pdfs"] and pdf_path.exists():
            df.at[idx, 'EGRUL_STATUS'] = 'skipped_existing'
            df.at[idx, 'EGRUL_PDF_PATH'] = str(pdf_path.relative_to(Path('data/output')))
            df.at[idx, 'EGRUL_LOG'] = 'Файл уже был скачан ранее'
            skipped += 1
            continue

        logging.info(f"[{idx+1}/{total}] 📄 {inn} | Запрос выписки...")
        res = fetch_egrul_pdf(inn, pdf_dir)
        for k, v in res.items():
            df.at[idx, k] = v

        if res['EGRUL_STATUS'] == 'success':
            processed += 1
        else:
            errors += 1
            logging.warning(f"⚠️ {inn} | {res['EGRUL_STATUS']}: {res['EGRUL_LOG']}")

        # Инкрементальное сохранение
        if (processed + skipped + errors) % CONFIG["save_interval"] == 0:
            df.to_csv(output_path, index=False, encoding="utf-8-sig")
            logging.info(f"💾 Промежуточное сохранение: {output_path}")

        time.sleep(CONFIG["delay_between_requests"])

    # Финальное сохранение
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    logging.info("\n" + "="*60)
    logging.info("📊 ИТОГИ:")
    logging.info(f"   ✅ Успешно: {processed}")
    logging.info(f"   ⏭️ Пропущено (уже есть): {skipped}")
    logging.info(f"   ❌ Ошибки/Капча/Не найдено: {errors}")
    logging.info(f"   💾 Результат: {output_path}")
    logging.info("="*60)

if __name__ == "__main__":
    main()