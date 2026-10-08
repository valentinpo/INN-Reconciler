#!/usr/bin/env python3
"""
Тестовый скрипт для egrul.nalog.ru
Проверяет 3 валидных ИНН, скачивает выписки в PDF
"""
import requests
import json
import time
import logging
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('data/output/test_egrul.log', encoding='utf-8', mode='w')
    ]
)

# Сессия для сохранения cookies
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Referer': 'https://egrul.nalog.ru/index.html',
    'X-Requested-With': 'XMLHttpRequest'
})

BASE_URL = 'https://egrul.nalog.ru'

def fetch_egrul_pdf(inn: str, output_dir: Path, timeout_poll: int = 30) -> dict:
    """
    Скачивает выписку ЕГРЮЛ/ЕГРИП по ИНН
    Возвращает: {status, pdf_path, error}
    """
    result = {'status': 'unknown', 'pdf_path': None, 'error': None, 'name': None}
    
    try:
        # Шаг 1: Инициируем поиск
        logging.info(f"🔍 [{inn}] Запрос поиска...")
        search_resp = session.post(
            f'{BASE_URL}/',
            data={'query': inn, 'vyp3CaptchaToken': '', 'page': '', 'region': ''},
            timeout=20
        )
        
        if search_resp.status_code != 200:
            result['status'] = 'http_error'
            result['error'] = f'POST search: {search_resp.status_code}'
            return result
        
        search_data = search_resp.json()
        
        # Проверяем на капчу
        if search_data.get('ERRORS', {}).get('captchaSearch'):
            result['status'] = 'captcha_required'
            result['error'] = 'Требуется ввод CAPTCHA'
            return result
        
        task_id = search_data.get('t')
        if not task_id:
            result['status'] = 'no_task_id'
            result['error'] = 'Не получен task_id в ответе'
            return result
        
        # Шаг 2: Ожидаем результаты поиска (поллинг)
        logging.info(f"⏳ [{inn}] Ожидание результатов (задача: {task_id})...")
        for attempt in range(timeout_poll):
            status_resp = session.get(f'{BASE_URL}/search-result/{task_id}', timeout=15)
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                if status_data.get('status') != 'wait':
                    break
            time.sleep(1)
        else:
            result['status'] = 'search_timeout'
            result['error'] = f'Превышено время ожидания поиска ({timeout_poll}с)'
            return result
        
        # Извлекаем первую запись
        rows = status_data.get('rows', [])
        if not rows:
            result['status'] = 'not_found'
            result['error'] = 'Нет записей в ответе'
            return result
        
        item = rows[0]
        result['name'] = item.get('n', item.get('i', 'Unknown'))
        item_task_id = item.get('t')
        
        if not item_task_id:
            result['status'] = 'no_item_task'
            result['error'] = 'Нет task_id у найденной записи'
            return result
        
        # Шаг 3: Запрашиваем генерацию выписки
        logging.info(f"📄 [{inn}] Запрос на генерацию PDF...")
        vyp_req = session.get(f'{BASE_URL}/vyp-request/{item_task_id}', timeout=15)
        if vyp_req.status_code != 200:
            result['status'] = 'vyp_req_error'
            result['error'] = f'vyp-request: {vyp_req.status_code}'
            return result
        
        # Шаг 4: Ожидаем готовности PDF
        logging.info(f"⏳ [{inn}] Ожидание готовности PDF...")
        for attempt in range(timeout_poll):
            vyp_status = session.get(f'{BASE_URL}/vyp-status/{item_task_id}', timeout=15)
            if vyp_status.status_code == 200:
                status_json = vyp_status.json()
                if status_json.get('status') == 'ready':
                    break
            time.sleep(1)
        else:
            result['status'] = 'pdf_timeout'
            result['error'] = f'Превышено время ожидания PDF ({timeout_poll}с)'
            return result
        
        # Шаг 5: Скачиваем PDF
        pdf_path = output_dir / f"egrul_{inn}.pdf"
        logging.info(f"⬇️ [{inn}] Скачивание PDF...")
        download_resp = session.get(f'{BASE_URL}/vyp-download/{item_task_id}', timeout=60)
        
        if download_resp.status_code == 200 and download_resp.headers.get('Content-Type') == 'application/pdf':
            with open(pdf_path, 'wb') as f:
                f.write(download_resp.content)
            result['status'] = 'success'
            result['pdf_path'] = str(pdf_path)
            logging.info(f"✅ [{inn}] PDF сохранён: {pdf_path.name} ({len(download_resp.content) / 1024:.1f} КБ)")
        else:
            result['status'] = 'download_error'
            result['error'] = f'Content-Type: {download_resp.headers.get("Content-Type")}, Status: {download_resp.status_code}'
            
    except requests.exceptions.RequestException as e:
        result['status'] = 'request_error'
        result['error'] = str(e)[:100]
        logging.warning(f"⚠️ [{inn}] Ошибка запроса: {result['error']}")
    except json.JSONDecodeError as e:
        result['status'] = 'json_error'
        result['error'] = f'Неверный JSON: {str(e)[:50]}'
        logging.warning(f"⚠️ [{inn}] Ошибка парсинга JSON: {result['error']}")
    except Exception as e:
        result['status'] = 'unexpected_error'
        result['error'] = str(e)[:100]
        logging.error(f"❌ [{inn}] Неожиданная ошибка: {result['error']}")
    
    return result


def main():
    # Тестовые ИНН из твоего файла (валидные, 10-значные)
    test_inns = ['6601002420', '6601005004', '6601005011']
    
    output_dir = Path('data/output/pdfs')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"🚀 Запуск теста egrul.nalog.ru на {len(test_inns)} ИНН")
    logging.info(f"📁 PDF будут сохранены в: {output_dir}")
    
    results = []
    for i, inn in enumerate(test_inns, 1):
        logging.info(f"\n📋 [{i}/{len(test_inns)}] Обработка ИНН: {inn}")
        res = fetch_egrul_pdf(inn, output_dir)
        results.append({'inn': inn, **res})
        
        # Задержка между запросами, чтобы снизить риск капчи
        if i < len(test_inns):
            logging.info(f"⏱️ Пауза 5 секунд перед следующим запросом...")
            time.sleep(5)
    
    # Итоговый отчёт
    logging.info("\n" + "="*60)
    logging.info("📊 ИТОГИ ТЕСТА:")
    for r in results:
        status_icon = '✅' if r['status'] == 'success' else '❌'
        logging.info(f"{status_icon} {r['inn']} | {r['name'] or 'N/A'} | {r['status']} | {r['error'] or 'OK'}")
    logging.info("="*60)
    
    # Сохраняем результаты в JSON для анализа
    import json
    with open(output_dir / 'test_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logging.info(f"💾 Детали теста сохранены: {output_dir / 'test_results.json'}")


if __name__ == "__main__":
    main()