import asyncio
import logging
import httpx
from datetime import datetime

BOT_TOKEN = "8654397067:AAHQGslRg9urBjp2okusCtgCkFx9xbrgtY4"
CHAT_ID = "412895587"
CHECK_INTERVAL = 60

VENUES = {
    19: "🎾 Лужники",
    12: "🚇 Баррикадная",
    14: "🚇 Третьяковская",
    15: "🚇 Римская",
}

BOOKING_URL = "https://outdoor.sport.mos.ru/#venues-events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# Состояние
monitoring = set()  # venue_id которые мониторим
last_event_ids = {}
is_running = True
update_offset = 0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5.2 Safari/605.1.15",
    "Accept": "*/*",
    "Referer": "https://outdoor.sport.mos.ru/",
    "Accept-Language": "ru",
}


async def send_message(text: str, reply_markup=None):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
        )
        return resp.status_code


async def get_updates():
    global update_offset
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": update_offset, "timeout": 1},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    return []


async def send_start_menu():
    # Кнопки выбора площадок
    buttons = []
    for vid, vname in VENUES.items():
        status = "✅" if vid in monitoring else "⬜"
        buttons.append([{"text": f"{status} {vname}", "callback_data": f"toggle_{vid}"}])
    
    buttons.append([{"text": "▶️ Начать мониторинг", "callback_data": "start_monitor"}])
    buttons.append([{"text": "⏹ Остановить", "callback_data": "stop_monitor"}])

    markup = {"inline_keyboard": buttons}
    
    selected = [VENUES[v] for v in monitoring] if monitoring else ["не выбрано"]
    text = (
        "🎾 <b>Падел-бот</b>\n\n"
        f"Слежу за: {', '.join(selected)}\n\n"
        "Выбери площадки и нажми <b>Начать мониторинг</b>:"
    )
    await send_message(text, markup)


async def answer_callback(callback_id: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id},
        )


async def edit_menu(chat_id, message_id):
    buttons = []
    for vid, vname in VENUES.items():
        status = "✅" if vid in monitoring else "⬜"
        buttons.append([{"text": f"{status} {vname}", "callback_data": f"toggle_{vid}"}])
    buttons.append([{"text": "▶️ Начать мониторинг", "callback_data": "start_monitor"}])
    buttons.append([{"text": "⏹ Остановить", "callback_data": "stop_monitor"}])
    markup = {"inline_keyboard": buttons}

    selected = [VENUES[v] for v in monitoring] if monitoring else ["не выбрано"]
    text = (
        "🎾 <b>Падел-бот</b>\n\n"
        f"Слежу за: {', '.join(selected)}\n\n"
        "Выбери площадки и нажми <b>Начать мониторинг</b>:"
    )
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text,
                  "parse_mode": "HTML", "reply_markup": markup},
        )


async def get_bookable(venue_id: int):
    url = f"https://outdoor.sport.mos.ru/_b/booking/bookable-summary?venue_id={venue_id}"
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        resp = await client.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("bookable_event_ids", [])
    return None


async def check_slots():
    if not monitoring or not is_running:
        return

    for venue_id in list(monitoring):
        venue_name = VENUES[venue_id]
        try:
            event_ids = await get_bookable(venue_id)
            if event_ids is None:
                continue

            prev_ids = set(last_event_ids.get(venue_id, []))
            curr_ids = set(event_ids)
            new_ids = curr_ids - prev_ids

            if new_ids:
                msg = (
                    f"🔔 <b>Появились слоты!</b>\n"
                    f"📍 {venue_name}\n\n"
                    f"<a href='{BOOKING_URL}'>👉 Бронировать!</a>"
                )
                await send_message(msg)
                logging.info(f"Новые слоты на {venue_name}: {new_ids}")
            else:
                logging.info(f"{venue_name}: пусто ({datetime.now().strftime('%H:%M')})")

            last_event_ids[venue_id] = event_ids

        except Exception as e:
            logging.error(f"Ошибка {venue_name}: {e}")


async def handle_updates():
    global update_offset, is_running, monitoring

    updates = await get_updates()
    for upd in updates:
        update_offset = upd["update_id"] + 1

        # Текстовые команды
        if "message" in upd:
            text = upd["message"].get("text", "")
            if text == "/start" or text == "/menu":
                await send_start_menu()
            elif text == "/stop":
                is_running = False
                monitoring = set()
                await send_message("⏹ Мониторинг остановлен. Напиши /start чтобы возобновить.")
            elif text == "/status":
                if monitoring and is_running:
                    names = ", ".join(VENUES[v] for v in monitoring)
                    await send_message(f"✅ Слежу за: {names}")
                else:
                    await send_message("😴 Мониторинг не запущен. Напиши /start")

        # Кнопки
        if "callback_query" in upd:
            cb = upd["callback_query"]
            data = cb["data"]
            chat_id = cb["message"]["chat"]["id"]
            message_id = cb["message"]["message_id"]
            await answer_callback(cb["id"])

            if data.startswith("toggle_"):
                vid = int(data.split("_")[1])
                if vid in monitoring:
                    monitoring.discard(vid)
                else:
                    monitoring.add(vid)
                await edit_menu(chat_id, message_id)

            elif data == "start_monitor":
                if not monitoring:
                    await send_message("⚠️ Выбери хотя бы одну площадку!")
                else:
                    is_running = True
                    names = ", ".join(VENUES[v] for v in monitoring)
                    await send_message(f"✅ Мониторинг запущен!\nСлежу за: {names}\nПроверяю каждую минуту.")

            elif data == "stop_monitor":
                is_running = False
                monitoring = set()
                await send_message("⏹ Мониторинг остановлен. Напиши /start чтобы возобновить.")


async def main():
    logging.info("Бот запущен")
    await send_message("🤖 <b>Падел-бот запущен!</b>\nНапиши /start чтобы выбрать площадки.")

    while True:
        await handle_updates()
        if is_running and monitoring:
            await check_slots()
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
