import asyncio
import logging
import httpx
from datetime import datetime

BOT_TOKEN = "8654397067:AAHQGslRg9urBjp2okusCtgCkFx9xbrgtY4"
CHAT_ID = "8654397067"
CHECK_INTERVAL = 60  # каждую минуту

# venue_id для каждой площадки
VENUES = {
    19: "ОК «Лужники»",
    # добавим остальные если нужно
}

BOOKING_URL = "https://outdoor.sport.mos.ru/#venues-events"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

last_event_ids = {}


async def send_telegram(text: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        )
        return resp.status_code


async def get_bookable(venue_id: int):
    url = f"https://outdoor.sport.mos.ru/_b/booking/bookable-summary?venue_id={venue_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        data = resp.json()
        return data.get("bookable_event_ids", [])


async def check_and_notify():
    global last_event_ids

    for venue_id, venue_name in VENUES.items():
        try:
            event_ids = await get_bookable(venue_id)
            prev_ids = set(last_event_ids.get(venue_id, []))
            curr_ids = set(event_ids)
            new_ids = curr_ids - prev_ids

            if new_ids:
                msg = (
                    f"🎾 <b>Слоты появились!</b>\n"
                    f"📍 {venue_name}\n"
                    f"🆔 Event ID: {', '.join(str(i) for i in new_ids)}\n\n"
                    f"<a href='{BOOKING_URL}'>👉 Бронировать</a>"
                )
                await send_telegram(msg)
                logging.info(f"Новые слоты на {venue_name}: {new_ids}")
            else:
                logging.info(f"{venue_name}: слотов нет ({datetime.now().strftime('%H:%M')})")

            last_event_ids[venue_id] = event_ids

        except Exception as e:
            logging.error(f"Ошибка {venue_name}: {e}")


async def main():
    logging.info("Бот запущен")
    await send_telegram(
        "🤖 <b>Падел-бот запущен!</b>\n"
        "Слежу за слотами каждую минуту.\n"
        f"<a href='{BOOKING_URL}'>outdoor.sport.mos.ru</a>"
    )

    while True:
        await check_and_notify()
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
