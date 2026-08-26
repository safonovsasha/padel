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

monitoring = set()
last_event_ids = {}
is_running = False
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
        await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)


def build_menu():
    buttons = []
    for vid, vname in VENUES.items():
        status = "✅" if vid in monitoring else "⬜️"
        buttons.append([{"text": f"{status} {vname}", "callback_data": f"toggle_{vid}"}])
    buttons.append([
        {"text": "▶️ Старт", "callback_data": "start_monitor"},
        {"text": "⏹ Стоп", "callback_data": "stop_monitor"},
    ])
    return {"inline_keyboard": buttons}


def menu_text():
    if monitoring:
        names = ", ".join(VENUES[v] for v in monitoring)
        status = "🟢 Мониторинг активен" if is_running else "⏸ Выбрано (не запущено)"
    else:
        names = "не выбрано"
        status = "😴 Не запущен"
    return f"🎾 <b>Падел-бот</b>\n{status}\nПлощадки: {names}\n\nВыбери площадки → нажми Старт:"


async def send_menu():
    await send_message(menu_text(), build_menu())


async def edit_menu(chat_id, message_id):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": menu_text(),
                "parse_mode": "HTML",
                "reply_markup": build_menu(),
            },
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
            return resp.json().get("bookable_event_ids", [])
    return None


# === Цикл 1: опрос Telegram ===
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
                        await send_menu()
                    elif text == "/stop":
                        is_running = False
                        monitoring.clear()
                        await send_message("⏹ Остановлен. /start — возобновить.")
                    elif text == "/status":
                        if is_running and monitoring:
                            names = ", ".join(VENUES[v] for v in monitoring)
                            await send_message(f"✅ Слежу за: {names}")
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
                        await edit_menu(chat_id, message_id)

                    elif data == "start_monitor":
                        if not monitoring:
                            await answer_callback(cb["id"])
                            await send_message("⚠️ Сначала выбери площадку!")
                        else:
                            is_running = True
                            await edit_menu(chat_id, message_id)
                            names = ", ".join(VENUES[v] for v in monitoring)
                            await send_message(f"✅ Запущен!\nСлежу за: {names}")

                    elif data == "stop_monitor":
                        is_running = False
                        monitoring.clear()
                        await edit_menu(chat_id, message_id)
                        await send_message("⏹ Остановлен. Нажми ▶️ Старт чтобы возобновить.")

        except Exception as e:
            logging.error(f"poll error: {e}")

        await asyncio.sleep(1)


# === Цикл 2: проверка слотов ===
async def check_slots():
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        if not is_running or not monitoring:
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
                    logging.info(f"{venue_name}: пусто ({datetime.now().strftime('%H:%M')})")

                last_event_ids[venue_id] = event_ids

            except Exception as e:
                logging.error(f"check error {venue_name}: {e}")


async def main():
    logging.info("Бот запущен")
    await send_message("🤖 Бот запущен! Напиши /start")
    await asyncio.gather(poll_updates(), check_slots())


if __name__ == "__main__":
    asyncio.run(main())
