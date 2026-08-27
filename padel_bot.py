import asyncio
import logging
import httpx
from datetime import datetime, time
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

BOOKING_URL = "https://outdoor.sport.mos.ru/#venues-events"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

monitoring = set()
last_event_ids = {}
is_running = False
update_offset = 0

# Расписание по умолчанию: {weekday: (start_hour, end_hour)}
# 0=Пн, 1=Вт, 2=Ср, 3=Чт, 4=Пт, 5=Сб, 6=Вс
DEFAULT_SCHEDULE = {
    0: None,           # Пн — выходной
    1: (18, 20),       # Вт
    2: (18, 20),       # Ср
    3: (18, 20),       # Чт
    4: (18, 21),       # Пт
    5: (10, 21),       # Сб
    6: (10, 20),       # Вс
}
schedule = dict(DEFAULT_SCHEDULE)

DAYS_RU = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5.2 Safari/605.1.15",
    "Accept": "*/*",
    "Referer": "https://outdoor.sport.mos.ru/",
    "Accept-Language": "ru",
}

# Состояние редактирования расписания
editing_day = {}  # chat_id -> weekday


def is_active_time():
    now = datetime.now(MSK)
    return 7 <= now.hour < 23


def schedule_text():
    lines = []
    for wd in range(7):
        h = schedule.get(wd)
        if h:
            lines.append(f"{DAYS_RU[wd]}: {h[0]:02d}:00–{h[1]:02d}:00")
        else:
            lines.append(f"{DAYS_RU[wd]}: —")
    return "\n".join(lines)


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
    buttons.append([{"text": "🕐 Расписание", "callback_data": "show_schedule"}])
    return {"inline_keyboard": buttons}


def main_menu_text():
    active = "🟢 Активен" if is_running and is_active_time() else ("🟡 Запущен (вне времени)" if is_running else "😴 Остановлен")
    names = ", ".join(VENUES[v] for v in monitoring) if monitoring else "не выбрано"
    return (
        f"🎾 <b>Падел-бот</b> | {active}\n"
        f"Площадки: {names}\n"
        f"Активен: 07:00–23:00 каждый день\n\n"
        "Выбери площадки → нажми Старт:"
    )


def build_schedule_menu():
    buttons = []
    for wd in range(7):
        h = schedule.get(wd)
        label = f"{h[0]:02d}:00–{h[1]:02d}:00" if h else "выкл"
        buttons.append([{"text": f"{DAYS_RU[wd]}: {label}", "callback_data": f"edit_day_{wd}"}])
    buttons.append([{"text": "↩️ Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": buttons}


def build_day_edit_menu(wd):
    """Меню редактирования одного дня"""
    buttons = []
    # Варианты времени
    options = [
        None,
        (10, 20), (10, 21), (10, 22),
        (12, 20), (12, 21), (12, 22),
        (18, 20), (18, 21), (18, 22),
    ]
    current = schedule.get(wd)
    for opt in options:
        if opt is None:
            label = "⛔️ Выключить"
            val = "off"
        else:
            label = f"{opt[0]:02d}:00–{opt[1]:02d}:00"
            val = f"{opt[0]}_{opt[1]}"
        check = " ✅" if opt == current else ""
        buttons.append([{"text": label + check, "callback_data": f"settime_{wd}_{val}"}])
    buttons.append([{"text": "↩️ Назад", "callback_data": "show_schedule"}])
    return {"inline_keyboard": buttons}


async def edit_message(chat_id, message_id, text, markup):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id,
                  "text": text, "parse_mode": "HTML", "reply_markup": markup},
        )


async def answer_callback(callback_id: str, text: str = ""):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
        )


async def get_bookable(venue_id: int):
    url = f"https://outdoor.sport.mos.ru/_b/booking/bookable-summary?venue_id={venue_id}"
    async with httpx.AsyncClient(headers=HEADERS) as client:
        resp = await client.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("bookable_event_ids", [])
    return None


async def poll_updates():
    global update_offset, is_running, monitoring, schedule

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
                            active = "активен" if is_active_time() else "вне времени поиска"
                            await send_message(f"{'✅' if is_active_time() else '🟡'} Слежу за: {names}\nСтатус: {active}")
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
                            await send_message(f"✅ Запущен!\nСлежу за: {names}\n\n📅 Расписание:\n{schedule_text()}")

                    elif data == "stop_monitor":
                        is_running = False
                        monitoring.clear()
                        await edit_message(chat_id, message_id, main_menu_text(), build_main_menu())
                        await send_message("⏹ Остановлен.")

                    elif data == "show_schedule":
                        await edit_message(chat_id, message_id,
                            f"🕐 <b>Расписание поиска</b>\nНажми день чтобы изменить:\n\n{schedule_text()}",
                            build_schedule_menu())

                    elif data == "back_main":
                        await edit_message(chat_id, message_id, main_menu_text(), build_main_menu())

                    elif data.startswith("edit_day_"):
                        wd = int(data.split("_")[2])
                        await edit_message(chat_id, message_id,
                            f"🕐 <b>{DAYS_RU[wd]}</b> — выбери время поиска:",
                            build_day_edit_menu(wd))

                    elif data.startswith("settime_"):
                        parts = data.split("_")
                        wd = int(parts[1])
                        val = "_".join(parts[2:])
                        if val == "off":
                            schedule[wd] = None
                        else:
                            h_start, h_end = int(parts[2]), int(parts[3])
                            schedule[wd] = (h_start, h_end)
                        await edit_message(chat_id, message_id,
                            f"🕐 <b>{DAYS_RU[wd]}</b> — выбери время поиска:",
                            build_day_edit_menu(wd))

        except Exception as e:
            logging.error(f"poll error: {e}")

        await asyncio.sleep(1)


async def check_slots():
    while True:
        await asyncio.sleep(CHECK_INTERVAL)

        if not is_running or not monitoring:
            continue

        if not is_active_time():
            now = datetime.now(MSK)
            logging.info(f"Вне расписания ({now.strftime('%a %H:%M')}), пропускаю")
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
    await send_message(
        "🤖 Бот запущен!\n\n"
        f"📅 Расписание по умолчанию:\n{schedule_text()}\n\n"
        "Напиши /start чтобы выбрать площадки."
    )
    await asyncio.gather(poll_updates(), check_slots())


if __name__ == "__main__":
    asyncio.run(main())
