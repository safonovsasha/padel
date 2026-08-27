import asyncio
import logging
import httpx
from datetime import datetime
import pytz

BOT_TOKEN = "8654397067:AAHQGslRg9urBjp2okusCtgCkFx9xbrgtY4"
CHAT_ID = "412895587"
CHECK_INTERVAL = 60
MSK = pytz.timezone("Europe/Moscow")

VENUES = {
    19: "🎾 Лужники",
    12: "🎾 Баррикадная",
    14: "🎾 Третьяковская",
    15: "🎾 Римская",
}

EXCLUDED_KEYWORDS = ["сайкл", "велосипед", "cycle"]

BOOKING_URL = "https://outdoor.sport.mos.ru/#venues-events"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

monitoring = set()
last_event_ids = {}
is_running = False
update_offset = 0
excluded_event_ids = set()  # event_id сайкла и других исключений

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5.2 Safari/605.1.15",
    "Accept": "*/*",
    "Referer": "https://outdoor.sport.mos.ru/",
    "Accept-Language": "ru",
}


def is_active_time():
    now = datetime.now(MSK)
    return 7 <= now.hour < 23


async def load_excluded_events():
    """Загружает event_id которые нужно исключить (сайкл и др.)"""
    global excluded_event_ids
    for venue_id in VENUES:
        try:
            url = f"https://outdoor.sport.mos.ru/_b/booking/event-cards?venue_id={venue_id}"
            async with httpx.AsyncClient(headers=HEADERS) as client:
                resp = await client.get(url, timeout=10)
                if resp.status_code != 200:
                    continue
                cards = resp.json().get("data", [])
                for card in cards:
                    title = card.get("title", "").lower()
                    if any(kw in title for kw in EXCLUDED_KEYWORDS):
                        ids = card.get("events", [])
                        excluded_event_ids.update(ids)
                        logging.info(f"Исключаю '{card['title'].strip()}' ({len(ids)} событий) из venue {venue_id}")
        except Exception as e:
            logging.error(f"Ошибка загрузки event-cards для venue {venue_id}: {e}")
    logging.info(f"Всего исключено event_id: {len(excluded_event_ids)}")


async def send_message(text: str, reply_markup=None):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)


def build_main_menu():
    buttons = []
    for vid, vname in VENUES.items():
        status = "✅" if vid in monitoring else "⬜️"
        buttons.append([{"text": f"{status} {vname}", "callback_data": f"toggle_{vid}"}])
    buttons.append([
        {"text": "▶️ Старт", "callback_data": "start_monitor"},
        {"text": "⏹ Стоп", "callback_data": "stop_monitor"},
    ])
    return {"inline_keyboard": buttons}


def main_menu_text():
    active = "🟢 Активен" if is_running and is_active_time() else ("🟡 Ждёт 07:00–23:00" if is_running else "😴 Остановлен")
    names = ", ".join(VENUES[v] for v in monitoring) if monitoring else "не выбрано"
    return (
        f"🎾 <b>Падел-бот</b> | {active}\n"
        f"Площадки: {names}\n"
        f"Работает: 07:00–23:00 каждый день\n\n"
        "Выбери площадки → нажми Старт:"
    )


async def edit_message(chat_id, message_id, text, markup):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id,
                  "text": text, "parse_mode": "HTML", "reply_markup": markup},
        )


async def answer_callback(callback_id: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id},
        )


async def get_bookable(venue_id: int):
    url = f"https://outdoor.sport.mos.ru/_b/booking/bookable-summary?venue_id={venue_id}"
    async with httpx.AsyncClient(headers=HEADERS) as client:
        resp = await client.get(url, timeout=10)
        if resp.status_code == 200:
            all_ids = resp.json().get("bookable_event_ids", [])
            # Фильтруем исключённые события
            filtered = [eid for eid in all_ids if eid not in excluded_event_ids]
            return filtered
    return None


async def poll_updates():
    global update_offset, is_running, monitoring

    while True:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                    params={"offset": update_offset, "timeout": 2},
                    timeout=10,
                )
                updates = resp.json().get("result", []) if resp.status_code == 200 else []

            for upd in updates:
                update_offset = upd["update_id"] + 1

                if "message" in upd:
                    text = upd["message"].get("text", "")
                    if text in ("/start", "/menu"):
                        await send_message(main_menu_text(), build_main_menu())
                    elif text == "/stop":
                        is_running = False
                        monitoring.clear()
                        await send_message("⏹ Остановлен. /start — меню.")
                    elif text == "/status":
                        if is_running:
                            names = ", ".join(VENUES[v] for v in monitoring)
                            await send_message(f"{'✅' if is_active_time() else '🟡'} Слежу за: {names}")
                        else:
                            await send_message("😴 Не запущен. /start — меню.")

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
                        await edit_message(chat_id, message_id, main_menu_text(), build_main_menu())

                    elif data == "start_monitor":
                        if not monitoring:
                            await send_message("⚠️ Сначала выбери площадку!")
                        else:
                            is_running = True
                            await edit_message(chat_id, message_id, main_menu_text(), build_main_menu())
                            names = ", ".join(VENUES[v] for v in monitoring)
                            await send_message(f"✅ Запущен! Слежу за: {names}")

                    elif data == "stop_monitor":
                        is_running = False
                        monitoring.clear()
                        await edit_message(chat_id, message_id, main_menu_text(), build_main_menu())
                        await send_message("⏹ Остановлен.")

        except Exception as e:
            logging.error(f"poll error: {e}")

        await asyncio.sleep(1)


async def check_slots():
    while True:
        await asyncio.sleep(CHECK_INTERVAL)

        if not is_running or not monitoring:
            continue

        if not is_active_time():
            logging.info(f"Сплю до 07:00 ({datetime.now(MSK).strftime('%H:%M')})")
            continue

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
                    await send_message(
                        f"🔔 <b>Слоты появились!</b>\n"
                        f"📍 {venue_name}\n\n"
                        f"<a href='{BOOKING_URL}'>👉 Бронировать!</a>"
                    )
                    logging.info(f"Новые слоты: {venue_name} {new_ids}")
                else:
                    logging.info(f"{venue_name}: пусто ({datetime.now(MSK).strftime('%H:%M')})")

                last_event_ids[venue_id] = event_ids

            except Exception as e:
                logging.error(f"check error {venue_name}: {e}")


async def main():
    logging.info("Бот запущен")
    await load_excluded_events()
    await send_message("🤖 Бот запущен! Напиши /start чтобы выбрать площадки.")
    await asyncio.gather(poll_updates(), check_slots())


if __name__ == "__main__":
    asyncio.run(main())
