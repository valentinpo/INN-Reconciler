#!/usr/bin/env python3
"""
Этап 2 (Retry v2): Дозагрузка выписок ЕГРЮЛ/ЕГРИП
✅ Жёсткие таймауты на всех этапах
✅ Экспоненциальная задержка + джиттер
✅ Ротация сессий при ошибках
✅ Автосохранение + graceful exit (Ctrl+C)
"""
import pandas as pd
import requests
import time
import random
import logging
import sys
from pathlib import Path
from datetime import datetime

# ================= НАСТРОЙКИ =================
CONFIG = {
    "input_csv": "data/output/02_egrul_parsed.csv",
    "output_csv": "data/output/02_egrul_parsed.csv",
    "pdf_dir": "data/output/pdfs",
    "log_file": "data/output/02_egrul_retry_final.log",
    "delay_base": 6.0,          # Базовая задержка
    "delay_max": 25.0,          # Макс задержка при повторных ошибках
    "save_interval": 3,         # Сохранять каждые N записей
    "refresh_session_after": 10 # Обновлять сессию каждые N запросов
}
# =============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(CONFIG["log_file"], encoding="utf-8", mode="w")
    ]
)

def get_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'ru-RU,ru;q=0.9',
        'Referer': 'https://egrul.nalog.ru/index.html',
        'X-Requested-With': 'XMLHttpRequest'
    })
    return s

def fetch_egrul_safe(inn: str, pdf_dir: Path, session: requests.Session) -> dict:
    """Безопасный запрос с жёсткими таймаутами и защитой от зависаний"""
    result = {'EGRUL_STATUS': 'unknown', 'EGRUL_PDF_PATH': None, 'EGRUL_NAME': None, 'EGRUL_LOG': ''}
    pdf_path = pdf_dir / f"egrul_{inn}.pdf"
    
    # (connect_timeout, read_timeout)
    timeout = (12, 30) 

    try:
        # 1. Инициализация поиска
        resp = session.post('https://egrul.nalog.ru/', data={'query': inn, 'dl': '1'}, timeout=timeout)
        if resp.status_code in (400, 429, 503):
            return {**result, 'EGRUL_STATUS': 'rate_limited', 'EGRUL_LOG': f'HTTP {resp.status_code}'}
        resp.raise_for_status()
        data = resp.json()
        if data.get('ERRORS', {}).get('captchaSearch'):
            return {**result, 'EGRUL_STATUS': 'captcha_required', 'EGRUL_LOG': 'Нужна капча'}

        task_id = data.get('t')
        if not task_id: return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': 'Нет task_id'}

        # 2. Ожидание поиска (макс 45 сек)
        deadline = time.time() + 45
        while time.time() < deadline:
            s_resp = session.get(f'https://egrul.nalog.ru/search-result/{task_id}', timeout=timeout)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                if s_data.get('status') != 'wait': break
            time.sleep(2)
        else:
            return {**result, 'EGRUL_STATUS': 'timeout_search', 'EGRUL_LOG': 'Превышено 45с ожидания поиска'}

        rows = s_data.get('rows', [])
        if not rows: return {**result, 'EGRUL_STATUS': 'not_found', 'EGRUL_LOG': 'ИНН не найден'}

        row = rows[0]
        result['EGRUL_NAME'] = row.get('n', row.get('i', 'Unknown'))
        row_tid = row.get('t')
        if not row_tid: return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': 'Нет row_tid'}

        # 3. Запрос выписки
        vyp_req = session.get(f'https://egrul.nalog.ru/vyp-request/{row_tid}', timeout=timeout)
        if vyp_req.status_code in (400, 429): 
            return {**result, 'EGRUL_STATUS': 'rate_limited_vyp', 'EGRUL_LOG': f'HTTP {vyp_req.status_code}'}
        vyp_req.raise_for_status()
        vyp_tid = vyp_req.json().get('t')
        if not vyp_tid: return {**result, 'EGRUL_STATUS': 'error', 'EGRUL_LOG': 'Нет vyp_tid'}

        # 4. Ожидание PDF (макс 60 сек)
        deadline = time.time() + 60
        while time.time() < deadline:
            v_st = session.get(f'https://egrul.nalog.ru/vyp-status/{vyp_tid}', timeout=timeout)
            if v_st.status_code == 200 and v_st.json().get('status') == 'ready': break
            time.sleep(2)
        else:
            return {**result, 'EGRUL_STATUS': 'timeout_pdf', 'EGRUL_LOG': 'Превышено 60с ожидания PDF'}

        # 5. Скачивание
        dl = session.get(f'https://egrul.nalog.ru/vyp-download/{vyp_tid}', timeout=timeout)
        if dl.status_code == 200 and 'application/pdf' in dl.headers.get('Content-Type', ''):
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(dl.content)
            result['EGRUL_STATUS'] = 'success'
            result['EGRUL_PDF_PATH'] = str(pdf_path.relative_to(Path('data/output')))
            result['EGRUL_LOG'] = f'OK ({len(dl.content)//1024} KB)'
        else:
            result['EGRUL_STATUS'] = 'download_error'
            result['EGRUL_LOG'] = f'CT: {dl.headers.get("Content-Type")}, HTTP: {dl.status_code}'

    except requests.exceptions.Timeout:
        result['EGRUL_STATUS'] = 'timeout'
        result['EGRUL_LOG'] = 'Сервер не ответил в срок'
    except requests.exceptions.ConnectionError:
        result['EGRUL_STATUS'] = 'connection_error'
        result['EGRUL_LOG'] = 'Соединение сброшено/недоступно'
    except Exception as e:
        result['EGRUL_STATUS'] = 'exception'
        result['EGRUL_LOG'] = str(e)[:80]

    return result

def main():
    input_path = Path(CONFIG["input_csv"])
    pdf_dir = Path(CONFIG["pdf_dir"])
    pdf_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        logging.error("❌ Файл не найден. Запустите сначала 01_inn_validation.py")
        return

    # Читаем аккуратно, чтобы не потерять типы
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    
    # Фильтр: берём только те, где статус пустой или указывает на ошибку/таймаут
    error_statuses = {'', 'network_error', 'timeout_search', 'timeout_pdf', 'download_error', 'error', 'exception', 'rate_limited', 'rate_limited_vyp', 'timeout'}
    mask = df['EGRUL_STATUS'].isin(error_statuses) | (df['EGRUL_STATUS'].str.strip() == '')
    to_process = df[mask].copy()
    total = len(to_process)
    
    if total == 0:
        logging.info("✅ Все строки уже имеют финальный статус. Дозагрузка не требуется.")
        return

    logging.info(f"🔄 Найдено {total} строк для обработки. Запуск...")
    
    processed, success, skipped = 0, 0, 0
    session = get_session()
    consecutive_errors = 0

    try:
        for idx, row in to_process.iterrows():
            inn = row['INN_CLEAN'].strip()
            if len(inn) < 10: continue

            pdf_path = pdf_dir / f"egrul_{inn}.pdf"
            if pdf_path.exists() and pdf_path.stat().st_size > 1000:
                df.at[idx, 'EGRUL_STATUS'] = 'skipped_existing'
                df.at[idx, 'EGRUL_PDF_PATH'] = str(pdf_path.relative_to(Path('data/output')))
                skipped += 1
                consecutive_errors = 0
                continue

            logging.info(f"[{processed+skipped+1}/{total}] 📄 {inn} | Запрос...")
            res = fetch_egrul_safe(inn, pdf_dir, session)
            for k, v in res.items(): df.at[idx, k] = v

            if res['EGRUL_STATUS'] == 'success': 
                success += 1
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                logging.warning(f"⚠️ {inn} | {res['EGRUL_STATUS']}: {res['EGRUL_LOG']}")

            processed += 1

            # Ротация сессии или пауза при ошибках
            if processed % CONFIG["refresh_session_after"] == 0:
                session = get_session()
                logging.info("🔄 Сессия обновлена")
            
            if consecutive_errors >= 3:
                wait = random.uniform(15, 30)
                logging.warning(f"🛑 3 ошибки подряд. Пауза {wait:.0f}с (защита от бана)")
                time.sleep(wait)
                session = get_session()
                consecutive_errors = 0

            # Экспоненциальная задержка
            delay = CONFIG["delay_base"] + (consecutive_errors * 3) + random.uniform(-1, 2)
            time.sleep(max(3, delay))

            # Автосохранение
            if processed % CONFIG["save_interval"] == 0:
                df.to_csv(CONFIG["output_csv"], index=False, encoding="utf-8-sig")
                logging.info(f"💾 Прогресс сохранён: {success} новых")

    except KeyboardInterrupt:
        logging.warning("\n⏹️ Прервано пользователем. Сохраняю прогресс...")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка: {e}")

    # Финальное сохранение
    df.to_csv(CONFIG["output_csv"], index=False, encoding="utf-8-sig")
    logging.info("\n" + "="*60)
    logging.info("📊 ИТОГИ:")
    logging.info(f"   ✅ Успешно: {success}")
    logging.info(f"   ⏭️ Пропущено (файл есть): {skipped}")
    logging.info(f"   📁 Файл: {CONFIG['output_csv']}")
    logging.info("="*60)

if __name__ == "__main__":
    main()