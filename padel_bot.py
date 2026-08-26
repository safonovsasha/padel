import asyncio
import logging
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import httpx

BOT_TOKEN = "8654397067:AAHQGslRg9urBjp2okusCtgCkFx9xbrgtY4"
CHAT_ID = "8654397067"
CHECK_INTERVAL = 300  # каждые 5 минут
URL = "https://outdoor.sport.mos.ru/#venues-events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

last_slots = set()


async def send_telegram(text: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        )


async def get_slots():
    slots = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Перехватываем API запросы
        api_responses = []
        
        async def handle_response(response):
            if "api.outdoor.sport.mos.ru" in response.url:
                try:
                    data = await response.json()
                    api_responses.append({"url": response.url, "data": data})
                except:
                    pass
        
        page.on("response", handle_response)
        
        await page.goto(URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        # Ищем кнопки/слоты на странице
        slot_elements = await page.query_selector_all("[class*='slot'], [class*='time'], [class*='booking'], button[class*='available']")
        
        for el in slot_elements:
            text = await el.inner_text()
            if text.strip():
                slots.append(text.strip())
        
        # Логируем найденные API запросы для отладки
        for r in api_responses:
            logging.info(f"API: {r['url']}")
        
        await browser.close()
    
    return slots, api_responses


async def check_and_notify():
    global last_slots
    logging.info("Проверяю слоты...")
    
    try:
        slots, api_data = await get_slots()
        
        current_slots = set(slots)
        new_slots = current_slots - last_slots
        
        if new_slots:
            msg = "🎾 <b>Новые слоты на падел!</b>\n\n"
            for s in sorted(new_slots):
                msg += f"• {s}\n"
            msg += f"\n<a href='{URL}'>Забронировать →</a>"
            await send_telegram(msg)
            logging.info(f"Отправил уведомление: {new_slots}")
        else:
            logging.info(f"Слотов нет. Всего найдено элементов: {len(slots)}")
        
        last_slots = current_slots
        
    except Exception as e:
        logging.error(f"Ошибка: {e}")


async def main():
    logging.info("Бот запущен")
    await send_telegram("🤖 Бот запущен! Слежу за слотами на <b>outdoor.sport.mos.ru</b>")
    
    while True:
        await check_and_notify()
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
