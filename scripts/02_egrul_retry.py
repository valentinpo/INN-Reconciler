#!/usr/bin/env python3
"""
Этап 2 (Retry): Дозагрузка выписок ЕГРЮЛ/ЕГРИП для проваленных запросов
Обрабатывает только строки с network_error, timeout, download_error или пустым статусом.
"""
import pandas as pd
import requests
import time
import random
import logging
from pathlib import Path

# ================= НАСТРОЙКИ =================
CONFIG = {
    "input_csv": "data/output/02_egrul_parsed.csv",
    "output_csv": "data/output/02_egrul_parsed.csv",  # перезаписываем тот же файл
    "pdf_dir": "data/output/pdfs",
    "log_file": "data/output/02_egrul_retry.log",
    "delay_base": 10.0,       # базовая задержка между ИНН (сек)
    "delay_jitter": 6.0,      # случайная добавка
    "max_retries": 3,         # повторы при 400/500
    "save_interval": 5,       # сохранять каждые N обработанных строк
    "refresh_session_every": 18  # обновлять сессию каждые N запросов
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

BASE_URL = 'https://egrul.nalog.ru'

def get_fresh_session():
    """Создаёт новую сессию с реалистичными заголовками"""
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://egrul.nalog.ru/index.html',
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
    })
    return s

def fetch_egrul_pdf_retry(inn: str, pdf_dir: Path, session: requests.Session) -> dict:
    """Скачивает выписку с поддержкой ретраев и обработки 400"""
    result = {'EGRUL_STATUS': 'unknown', 'EGRUL_PDF_PATH': None, 'EGRUL_NAME': None, 'EGRUL_LOG': ''}
    pdf_path = pdf_dir / f"egrul_{inn}.pdf"

    for attempt in range(CONFIG["max_retries"]):
        try:
            # 1. Инициализация поиска
            resp = session.post(BASE_URL, data={'query': inn, 'dl': '1'}, timeout=20)
            if resp.status_code in (400, 429, 502, 503):
                wait = CONFIG["delay_base"] + (attempt * 5) + random.uniform(2, CONFIG["delay_jitter"])
                logging.warning(f"[{inn}] Попытка {attempt+1}: HTTP {resp.status_code}. Ждём {wait:.1f}с...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()

            if data.get('ERRORS', {}).get('captchaSearch'):
                return {**result, 'EGRUL_STATUS': 'captcha_required', 'EGRUL_LOG': 'Требуется капча'}

            task_id = data.get('t')
            if not task_id:
                return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': 'Нет task_id'}

            # 2. Ожидание результатов поиска
            for _ in range(40):
                s_resp = session.get(f'{BASE_URL}/search-result/{task_id}', timeout=15)
                if s_resp.status_code == 200:
                    s_data = s_resp.json()
                    if s_data.get('status') != 'wait': break
                time.sleep(1)
            else:
                return {**result, 'EGRUL_STATUS': 'timeout_search', 'EGRUL_LOG': 'Превышено время поиска'}

            rows = s_data.get('rows', [])
            if not rows:
                return {**result, 'EGRUL_STATUS': 'not_found', 'EGRUL_LOG': 'ИНН не найден'}

            row = rows[0]
            result['EGRUL_NAME'] = row.get('n', row.get('i', 'Unknown'))
            row_task_id = row.get('t')
            if not row_task_id:
                return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': 'Нет row_task_id'}

            # 3. Запрос выписки
            vyp_req = session.get(f'{BASE_URL}/vyp-request/{row_task_id}', timeout=15)
            if vyp_req.status_code in (400, 429) and attempt < CONFIG["max_retries"] - 1:
                time.sleep(CONFIG["delay_base"] + 3)
                continue
            if vyp_req.status_code != 200:
                return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': f'vyp-request HTTP {vyp_req.status_code}'}

            vyp_task_id = vyp_req.json().get('t')
            if not vyp_task_id:
                return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': 'Нет vyp_task_id'}

            # 4. Ожидание готовности PDF
            for _ in range(40):
                v_st = session.get(f'{BASE_URL}/vyp-status/{vyp_task_id}', timeout=15)
                if v_st.status_code == 200 and v_st.json().get('status') == 'ready': break
                time.sleep(1)
            else:
                return {**result, 'EGRUL_STATUS': 'timeout_pdf', 'EGRUL_LOG': 'Превышено время генерации'}

            # 5. Скачивание
            dl = session.get(f'{BASE_URL}/vyp-download/{vyp_task_id}', timeout=60)
            if dl.status_code == 200 and 'application/pdf' in dl.headers.get('Content-Type', ''):
                pdf_dir.mkdir(parents=True, exist_ok=True)
                with open(pdf_path, 'wb') as f: f.write(dl.content)
                result['EGRUL_STATUS'] = 'success'
                result['EGRUL_PDF_PATH'] = str(pdf_path.relative_to(Path('data/output')))
                result['EGRUL_LOG'] = f'OK ({len(dl.content)//1024} KB)'
            else:
                result['EGRUL_STATUS'] = 'download_error'
                result['EGRUL_LOG'] = f'Content-Type: {dl.headers.get("Content-Type")}, HTTP: {dl.status_code}'
            break  # успех

        except requests.exceptions.RequestException as e:
            if attempt < CONFIG["max_retries"] - 1:
                time.sleep(CONFIG["delay_base"] + attempt * 2)
                continue
            result['EGRUL_STATUS'] = 'network_error'
            result['EGRUL_LOG'] = str(e)[:80]
        except Exception as e:
            result['EGRUL_STATUS'] = 'exception'
            result['EGRUL_LOG'] = str(e)[:80]
            break

    return result


def main():
    input_path = Path(CONFIG["input_csv"])
    pdf_dir = Path(CONFIG["pdf_dir"])
    pdf_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        logging.error("❌ Файл 02_egrul_parsed.csv не найден.")
        return

    df = pd.read_csv(input_path, dtype=str)
    logging.info(f"📥 Загружено {len(df)} строк для анализа.")

    # Фильтр: берём только строки, которые требуют дозагрузки
    error_statuses = {None, '', 'network_error', 'timeout_search', 'timeout_pdf', 'download_error', 'error', 'exception', 'captcha_required'}
    mask = df['EGRUL_STATUS'].isin(error_statuses) | df['EGRUL_STATUS'].isna()
    to_process = df[mask].copy()
    total = len(to_process)
    
    if total == 0:
        logging.info("✅ Все выписки уже скачаны или имеют финальный статус. Дозагрузка не требуется.")
        return

    logging.info(f"🔄 Найдено {total} строк для повторной обработки.")
    
    processed, success, skipped = 0, 0, 0
    session = get_fresh_session()

    for idx, row in to_process.iterrows():
        inn = row['INN_CLEAN']
        if not inn or len(inn) < 10: continue

        pdf_path = pdf_dir / f"egrul_{inn}.pdf"
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            df.at[idx, 'EGRUL_STATUS'] = 'skipped_existing'
            df.at[idx, 'EGRUL_PDF_PATH'] = str(pdf_path.relative_to(Path('data/output')))
            skipped += 1
            continue

        logging.info(f"[{processed+skipped+1}/{total}] 📄 {inn} | Попытка дозагрузки...")
        res = fetch_egrul_pdf_retry(inn, pdf_dir, session)
        for k, v in res.items(): df.at[idx, k] = v

        if res['EGRUL_STATUS'] == 'success': success += 1
        processed += 1

        # Ротация сессии
        if processed % CONFIG["refresh_session_every"] == 0:
            logging.info("🔄 Обновление HTTP-сессии (анти-бан)...")
            session = get_fresh_session()
            time.sleep(3)

        # Случайная задержка
        delay = CONFIG["delay_base"] + random.uniform(0, CONFIG["delay_jitter"])
        time.sleep(delay)

        # Инкрементальное сохранение
        if processed % CONFIG["save_interval"] == 0:
            df.to_csv(CONFIG["output_csv"], index=False, encoding="utf-8-sig")
            logging.info(f"💾 Промежуточное сохранение: {success} новых выписок")

    df.to_csv(CONFIG["output_csv"], index=False, encoding="utf-8-sig")
    logging.info("\n" + "="*60)
    logging.info("📊 ИТОГИ ДОЗАГРУЗКИ:")
    logging.info(f"   ✅ Успешно скачано: {success}")
    logging.info(f"   ⏭️ Пропущено (уже есть): {skipped}")
    logging.info(f"   ❌ Осталось с ошибками: {total - success - skipped}")
    logging.info(f"   💾 Файл обновлён: {CONFIG['output_csv']}")
    logging.info("="*60)

if __name__ == "__main__":
    main()