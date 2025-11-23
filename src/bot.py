import os
import logging
import re
import asyncio
import random
from datetime import datetime, time, timezone, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from icebreakers import ICEBREAKER_QUESTIONS
from dotenv import load_dotenv
from db import (
    add_or_update_user,
    get_active_coffee_shops,
    create_coffee_request,
    get_shop_working_hours,
    get_pending_requests,
    pair_user_for_request,
    get_request_details,
    get_user_details,
    get_user_requests,
    cancel_request,
    get_meetings_for_reminder,
    mark_reminder_as_sent,
    expire_pending_requests,
    unmatch_request,
    cancel_request_by_creator,
    get_shop_details,
    mark_feedback_as_requested,
    get_meetings_for_feedback,
    save_meeting_outcome,
    update_user_profile,
    get_meetings_for_icebreaker,
    get_all_active_users,
    save_feedback_text,
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MOSCOW_TIMEZONE = timezone(timedelta(hours=3), name="Europe/Moscow")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

(
    CHOOSING_ACTION,
    CHOOSING_SHOP,
    VIEWING_SHOP_DETAILS,
    CHOOSING_DATE,
    CHOOSING_TIME,
    CHOOSING_REQUEST,
    MANAGING_REQUESTS,
    REGISTER_SCHOOL,
    REGISTER_YEAR,
) = range(9)

SCHOOLS_LIST = [
    ["ФРКТ", "ВШПИ", "ЛФИ"],
    ["ФАКТ", "ФЭФМ", "ФПМИ"],
    ["ФБМФ", "КНТ", "ШИР"],
    ["Никакая из них"],
]
YEARS_LIST = [["1", "2", "3", "4"], ["5", "6", "7", "8"], ["Вернуться назад"]]

STATUS_CONFIG = {
    "pending": {
        "icon": "⏳",
        "details_template": "*Ожидание* в «{shop_name}»",
        "show_cancel_button": True,
    },
    "matched": {
        "icon": "🤝",
        "details_template": "Кофе-мит с {partner_mention} в «{shop_name}»",
        "show_cancel_button": False,
    },
    "cancelled": {
        "icon": "❌",
        "details_template": "*Отменено* в «{shop_name}»",
        "show_cancel_button": False,
    },
    "expired": {
        "icon": "📭",
        "details_template": "*Кофе-мит истёк* в «{shop_name}»",
        "show_cancel_button": False,
    },
}


def build_inline_keyboard(buttons_data: list[tuple]) -> InlineKeyboardMarkup:
    keyboard = []
    for text, callback_data in buttons_data:
        keyboard.append(
            [InlineKeyboardButton(text=text, callback_data=str(callback_data))]
        )
    return InlineKeyboardMarkup(keyboard)


async def show_main_menu_keyboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
):
    keyboard = [["☕️ Найти компанию"], ["📂 Мои заявки"], ["ℹ️ Гайд"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=False
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup
    )


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await show_main_menu_keyboard(update, context, text="Главное меню:")

    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"User: {user.username} (ID: {user.id}) started bot.")
    add_or_update_user(
        user_id=user.id, first_name=user.first_name, username=user.username
    )
    user_details = get_user_details(user.id)
    if user_details and user_details.get("phystech_school"):
        welcome_text = (
            "Привет! 👋 Я бот для случайных кофе-митов.\n\n"
            "Скорее жми «☕️ Найти компанию»"
        )
        await show_main_menu_keyboard(update, context, text=welcome_text)
        return ConversationHandler.END

    await update.message.reply_text(
        "Привет! 👋 Перед тем как начать, давай познакомимся.\n\n"
        "Из какой ты Физтех-школы?",
        reply_markup=ReplyKeyboardMarkup(
            SCHOOLS_LIST, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return REGISTER_SCHOOL


async def register_school(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    school = update.message.text

    flat_schools = [item for sublist in SCHOOLS_LIST for item in sublist]
    if school not in flat_schools:
        await update.message.reply_text("Пожалуйста, выбери вариант, используя кнопки.")
        return REGISTER_SCHOOL

    context.user_data["reg_school"] = school

    if school == "Никакая из них":
        user_id = update.effective_user.id
        update_user_profile(user_id, school="External", year=None)

        await show_main_menu_keyboard(
            update,
            context,
            text="Добро пожаловать!\nТеперь ты можешь искать компанию.",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"Отлично, {school}! А на каком ты курсе?",
        reply_markup=ReplyKeyboardMarkup(
            YEARS_LIST, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return REGISTER_YEAR


async def register_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    year_str = update.message.text

    if year_str == "Вернуться назад":
        await update.message.reply_text(
            "Хорошо, давай выберем школу заново.",
            reply_markup=ReplyKeyboardMarkup(
                SCHOOLS_LIST, one_time_keyboard=True, resize_keyboard=True
            ),
        )
        return REGISTER_SCHOOL

    if not year_str.isdigit() or not (1 <= int(year_str) <= 8):
        await update.message.reply_text("Пожалуйста, выбери курс кнопкой (1-8).")
        return REGISTER_YEAR

    school = context.user_data.get("reg_school")
    year = int(year_str)
    user_id = update.effective_user.id

    update_user_profile(user_id, school=school, year=year)

    await show_main_menu_keyboard(
        update, context, text="Профиль заполнен! 🎉\nТеперь ты готов к кофе-митам."
    )
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Вот что я умею:\n\n"
        "«☕️ *Найти компанию*» — здесь можно посмотреть, кто уже ищет компанию, или создать свою заявку на кофе-мит.\n"
        "«В заявке ты выбираешь место встречи, дату и время.\n\n"
        "«📂 *Мои заявки*» — тут хранятся все твои кофейные планы. Можно отменить заявку, если планы поменялись.\n\n"
        "Всё просто! Если что-то пошло не так, команда /cancel всегда прервет любое действие."
    )
    await update.message.reply_text(
        help_text, parse_mode="Markdown", disable_web_page_preview=True
    )


async def post_init(app):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "🚀 Перезапустить бота"),
            BotCommand("find", "☕️ Найти компанию"),
            BotCommand("my_coffee_requests", "📂 Посмотреть мои заявки"),
            BotCommand("cancel", "⛔️ Отменить текущее действие"),
            BotCommand("help", "ℹ️ Гайд по боту"),
        ]
    )

    app.job_queue.run_repeating(send_icebreakers, interval=60, first=20)
    app.job_queue.run_repeating(send_reminders, interval=60, first=10)
    app.job_queue.run_repeating(expire_requests, interval=60, first=15)
    app.job_queue.run_repeating(request_feedback, interval=1800, first=60)


async def find_company_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [
            InlineKeyboardButton(
                "👀 Посмотреть доступные заявки",
                callback_data="view_available_requests",
            )
        ],
        [
            InlineKeyboardButton(
                "✍️ Создать свою заявку", callback_data="create_new_request"
            )
        ],
        [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "Выбери действие:", reply_markup=reply_markup
        )
    else:
        await update.message.reply_text("Выбери действие:", reply_markup=reply_markup)

    return CHOOSING_ACTION


async def show_shop_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    shop_id = int(query.data.split("_")[1])
    shop_details = get_shop_details(shop_id=shop_id)

    if not shop_details:
        await query.edit_message_text(
            "Ошибка: не удалось найти информацию о кофейне. Попробуйте снова."
        )
        return await create_request_step1_shop(update, context)

    shop_name = shop_details["name"]
    shop_desc = shop_details["description"]

    text = f"📍 *{shop_name}*\n\n{shop_desc}\n\nВыбираем это место?"

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Да, выбрать это место", callback_data=f"confirm_shop_{shop_id}"
            )
        ],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="back_to_shop_list")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=text, reply_markup=reply_markup, parse_mode="Markdown"
    )

    return VIEWING_SHOP_DETAILS


async def create_request_step1_shop(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    shops = get_active_coffee_shops()
    if not shops:
        await query.edit_message_text(
            text="К сожалению сейчас не нашлись активные кофейни, попробуй позже. 😉"
        )
        return ConversationHandler.END

    buttons = [(f"📍 {name}", f"shop_{shop_id}") for shop_id, name in shops]
    buttons.append(("⬅️ Назад в главное меню", "main_menu"))

    reply_markup = build_inline_keyboard(buttons_data=buttons)

    await query.edit_message_text(
        text="Отлично, поехали! Для начала выбери кофейню, где тебе было бы уютно. 📍",
        reply_markup=reply_markup,
    )
    return CHOOSING_SHOP


async def create_request_step2_date(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    # Parse shop_id from callback data like "confirm_shop_67"
    chosen_shop_id = int(query.data.split("_")[2])
    context.user_data["chosen_shop_id"] = chosen_shop_id

    back_button_keyboard = build_inline_keyboard(
        [("⬅️ Назад в главное меню", "main_menu")]
    )

    logger.info(
        f"User {update.effective_user.id} chose coffee shop with ID: {chosen_shop_id}"
    )

    await query.edit_message_text(
        text="Принято! ✅\n\nТеперь давай определимся с датой. Напиши в формате *ДД.ММ*, в какой день тебе удобно встретиться (например, *25.12*).",
        reply_markup=back_button_keyboard,
    )
    return CHOOSING_DATE


async def create_request_step3_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    user_date_str = update.message.text

    if not re.match(r"^(\d{1,2})\.(\d{1,2})$", user_date_str):
        await update.message.reply_text(
            "Формат даты неверный 😥. Пожалуйста, введи дату как *ДД.ММ*, например: *25.12* или *1.9*"
        )
        return CHOOSING_DATE

    try:
        now_moscow = datetime.now(MOSCOW_TIMEZONE)
        day, month = map(int, user_date_str.split("."))

        proposed_date_naive = datetime(now_moscow.year, month, day)

        if proposed_date_naive.date() < now_moscow.date():
            await update.message.reply_text(
                "Эта дата уже в прошлом 🤓! Пожалуйста, выбери сегодня или дату из будущего."
            )
            return CHOOSING_DATE

        if (proposed_date_naive.date() - now_moscow.date()).days > 14:
            await update.message.reply_text(
                "Давай не будем планировать так далеко 🤓! Выбери дату в пределах следующих 14 дней."
            )
            return CHOOSING_DATE
    except ValueError:
        await update.message.reply_text(
            "Такой даты не существует (например, *31.02*). Пожалуйста, введи корректную дату."
        )
        return CHOOSING_DATE

    context.user_data["chosen_date"] = proposed_date_naive
    proposed_date_str = proposed_date_naive.strftime("%d.%m.%Y")

    back_button_keyboard = build_inline_keyboard(
        [("⬅️ Назад в главное меню", "main_menu")]
    )

    logger.info(f"User {update.effective_user.id} chose date {proposed_date_str}.")

    await update.message.reply_text(
        "Отлично! ✅\n\n Теперь давай определимся со временем. Напиши, во сколько тебе удобно встретиться (например, *14:30*).",
        reply_markup=back_button_keyboard,
    )
    return CHOOSING_TIME


def is_shop_open_at_time(working_hours: dict, meet_time: datetime) -> bool:
    days_of_week = [
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    ]
    day_name = days_of_week[meet_time.weekday()]

    if day_name not in working_hours:
        logger.info(f"Shop is closed on {day_name}.")
        return False

    try:
        open_time, close_time = working_hours[day_name].split("-")
        open_time = time.fromisoformat(open_time)
        close_time = time.fromisoformat(close_time)
        proposed_time = meet_time.time()
    except ValueError:
        logger.error(f"time string parsing error: {working_hours[day_name]}")
        return False
    return open_time <= proposed_time <= close_time


async def create_request_step4_validate(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    user = update.effective_user
    user_time_str = update.message.text

    match = re.match(r"^(\d{1,2}):(\d{1,2})$", user_time_str)
    if not match:
        await update.message.reply_text(
            "Хм, что-то я не разобрал время. 🤔\n\n Попробуй, пожалуйста, в формате *ЧЧ:ММ*, например: *15:00* или *9:45*"
        )
        return CHOOSING_TIME

    hour_str, minute_str = match.groups()
    hour, minute = int(hour_str), int(minute_str)

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text(
            "Такого времени не бывает 🤔. Часы должны быть от 0 до 23, а минуты — от 0 до 59."
        )
        return CHOOSING_TIME

    chosen_date = context.user_data["chosen_date"]
    if not chosen_date:
        await update.message.reply_text(
            "Что-то пошло не так, я забыл дату. 😶‍🌫️]\n\n Начнем заново."
        )
        return ConversationHandler.END

    try:
        naive_meet_time = chosen_date.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        meet_time = naive_meet_time.replace(tzinfo=MOSCOW_TIMEZONE)
    except ValueError:
        await update.message.reply_text("Произошла ошибка. Попробуй ещё раз.")
        return CHOOSING_TIME

    if meet_time < datetime.now(MOSCOW_TIMEZONE):
        await update.message.reply_text(
            "Это время уже прошло! 😅 Пожалуйста, выбери время в будущем."
        )
        return CHOOSING_TIME

    shop_id = context.user_data["chosen_shop_id"]
    if not shop_id:
        await update.message.reply_text(
            "Что-то пошло не так, я забыл, какую кофейню ты выбрал. 😶‍🌫️\n\n Начнем заново."
        )
        return ConversationHandler.END

    working_hours = get_shop_working_hours(shop_id=shop_id)
    if is_shop_open_at_time(working_hours, meet_time):
        create_coffee_request(
            creator_user_id=user.id, shop_id=shop_id, meet_time=meet_time
        )
        success_text = "Готово! ✨\n\n Твоя заявка в игре. Как только кто-то откликнется, я пришлю уведомление. 🔔"
        await show_main_menu_keyboard(update, context, text=success_text)
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Ой, кажется, эта кофейня в это время уже спит 😴. Давай попробуем другое время?"
        )
        return CHOOSING_TIME


async def view_available_requests(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    requests = get_pending_requests(user_id)

    if not requests:
        reply_markup = build_inline_keyboard(
            buttons_data=[("Создать свою заявку", "create_new_request")]
        )

        await query.edit_message_text(
            text="Упс, сейчас свободных заявок нет. Похоже, все уже нашли себе компанию. 😢\n\nМожет, создашь свою и станешь первым? ",
            reply_markup=reply_markup,
        )
        return CHOOSING_ACTION

    buttons = []
    for request_id, shop_name, meet_time in requests:
        meet_time_moscow = meet_time.astimezone(MOSCOW_TIMEZONE)

        date_str = meet_time_moscow.strftime("%d.%m")
        time_str = meet_time_moscow.strftime("%H:%M")

        button_text = f"📍 {shop_name} - {date_str} @ {time_str}"
        buttons.append((button_text, f"accept_{request_id}"))

    reply_markup = build_inline_keyboard(buttons_data=buttons)

    await query.edit_message_text(
        text="Список доступных заявок. Выбери одну из них или создай свою заявку 😉",
        reply_markup=reply_markup,
    )

    return CHOOSING_REQUEST


def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return text.replace("\\", "\\\\").translate(
        str.maketrans({c: f"\\{c}" for c in escape_chars})
    )


async def my_requests_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    requests = get_user_requests(user_id=user_id)

    keyboard_rows = []

    if not requests:
        message_text = (
            "У тебя пока нет запланированных или завершенных кофе-митов. "
            "Время найти компанию! ☕️"
        )
    else:
        message_parts = ["*Твои кофе-миты ☕️:*\n"]
        now_moscow = datetime.now(MOSCOW_TIMEZONE)

        for req in requests:
            status = req["status"]
            if status not in STATUS_CONFIG:
                continue

            config = STATUS_CONFIG[status]
            meet_time_moscow = req["meet_time"].astimezone(MOSCOW_TIMEZONE)

            partner_mention = ""
            if status == "matched":
                time_until_meet = meet_time_moscow - now_moscow

                if time_until_meet > timedelta(minutes=20):
                    partner_mention = "🕵️ *Секретный партнер*"
                else:
                    is_creator = user_id == req["creator_user_id"]
                    username_to_mention = (
                        req["partner_username"]
                        if is_creator
                        else req["creator_username"]
                    )
                    if username_to_mention:
                        safe_username = escape_markdown(username_to_mention)
                        partner_mention = f"@{safe_username}"
                    else:
                        partner_mention = "партнером"

            icon = config["icon"]
            if status == "matched" and meet_time_moscow < now_moscow:
                icon = "✅"

            date_str = meet_time_moscow.strftime("%d.%m.%Y")
            time_str = meet_time_moscow.strftime("%H:%M")

            details_str = config["details_template"].format(
                shop_name=escape_markdown(req["shop_name"]),
                partner_mention=partner_mention,
            )

            message_parts.append(f"{icon} *{date_str}* в *{time_str}*\n{details_str}")

            # Логика кнопок (только для будущих встреч)
            button_to_add = None
            if meet_time_moscow > now_moscow:
                if status == "pending" and user_id == req["creator_user_id"]:
                    button_to_add = InlineKeyboardButton(
                        f"❌ Отменить заявку в «{req['shop_name']}»",
                        callback_data=f"cancel_{req['request_id']}",
                    )
                elif status == "matched":
                    if user_id == req["partner_user_id"]:
                        button_to_add = InlineKeyboardButton(
                            f"❌ Отказаться от встречи в «{req['shop_name']}»",
                            callback_data=f"unmatch_{req['request_id']}",
                        )
                    elif user_id == req["creator_user_id"]:
                        button_to_add = InlineKeyboardButton(
                            f"❌ Отменить встречу в «{req['shop_name']}»",
                            callback_data=f"cancel_matched_{req['request_id']}",
                        )

            if button_to_add:
                keyboard_rows.append([button_to_add])

        message_text = "\n\n".join(message_parts)

    keyboard_rows.append(
        [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="main_menu")]
    )

    reply_markup = InlineKeyboardMarkup(keyboard_rows)
    await update.message.reply_text(
        message_text, parse_mode="Markdown", reply_markup=reply_markup
    )

    return MANAGING_REQUESTS


async def handle_accept_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    # because it came in like accept_420
    request_id = int(query.data.split("_")[1])
    partner_user_id = update.effective_user.id

    logger.info(f"User {partner_user_id} is attempting to accept request {request_id}")

    success = pair_user_for_request(
        request_id=request_id, partner_user_id=partner_user_id
    )

    if success:
        logger.info(f"SUCCESS: User {partner_user_id} accepted request {request_id}")
        await query.edit_message_text(text="✅ Отлично! Вы приняли заявку.")
        await notify_users_about_pairing(request_id=request_id, context=context)
        await show_main_menu_keyboard(
            update, context, text="Я уведомил создателя заявки. Главное меню:"
        )
    else:
        await query.edit_message_text(text="❌ Увы, эту заявку уже кто-то принял.")
        await show_main_menu_keyboard(
            update, context, text="Попробуйте обновить список! Главное меню:"
        )
        logger.warning(
            f"FAILURE: User {partner_user_id} failed to accept request {request_id}"
        )

    return ConversationHandler.END


async def handle_cancel_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    # because data is like: request_123
    request_id = int(query.data.split("_")[1])
    user_id = update.effective_user.id

    logger.info(f"User {user_id} is attempting to cancel request {request_id}.")

    success = cancel_request(request_id=request_id, user_id=user_id)

    if success:
        await query.edit_message_text(text="✅ Заявка успешно отменена.")
        logger.info(f"SUCCESS: User {user_id} cancelled request {request_id}")
    else:
        await query.edit_message_text(
            text="❌ Не удалось отменить заявку. Возможно, она уже была принята или отменена."
        )
        logger.warning(f"FAILURE: User {user_id} failed to cancel request {request_id}")

    await show_main_menu_keyboard(update, context, text="Главное меню:")
    return ConversationHandler.END


async def handle_cancel_request_as_creator(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    # split("_")[2] because of "cancel_matched_123"
    request_id = int(query.data.split("_")[2])
    creator_id = update.effective_user.id

    request_details = get_request_details(request_id=request_id)
    partner_id = cancel_request_by_creator(
        request_id=request_id, creator_user_id=creator_id
    )

    if partner_id and request_details:
        logger.info(
            f"SUCCESS: Creator {creator_id} cancelled matched request {request_id}."
        )
        await query.edit_message_text(text="✅ Вы успешно отменили встречу.")

        try:
            shop_name = escape_markdown(request_details["shop_name"])
            meet_time_moscow = request_details["meet_time"].astimezone(MOSCOW_TIMEZONE)
            date_str = meet_time_moscow.strftime("%d.%m.%Y")
            time_str = meet_time_moscow.strftime("%H:%M")

            partner_message = (
                f"К сожалению, создатель заявки отменил вашу встречу в «*{shop_name}*» "
                f"({date_str} в {time_str}). 😔"
            )
            await context.bot.send_message(
                chat_id=partner_id, text=partner_message, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(
                f"Failed to send cancellation notification to partner {partner_id}: {e}"
            )
    else:
        logger.warning(
            f"FAILURE: Creator {creator_id} failed to cancel matched request {request_id}."
        )
        await query.edit_message_text(text="❌ Не удалось отменить встречу.")

    await show_main_menu_keyboard(update, context, text="Главное меню:")
    return ConversationHandler.END


async def handle_unmatch_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    request_id = int(query.data.split("_")[1])
    partner_id = update.effective_user.id

    logger.info(
        f"User {partner_id} is attempting to unmatch from request {request_id}."
    )

    request_details = get_request_details(request_id=request_id)

    creator_id = unmatch_request(request_id=request_id, partner_user_id=partner_id)

    if creator_id and request_details:
        logger.info(f"SUCCESS: User {partner_id} unmatched from request {request_id}.")
        await query.edit_message_text(
            text="✅ Отменил участие в кофе-мите! Заявка снова стала доступна для других.\n\n Может, создашь новую для встречи в другое время?)"
        )

        try:
            shop_name = escape_markdown(request_details["shop_name"])
            meet_time_moscow = request_details["meet_time"].astimezone(MOSCOW_TIMEZONE)
            date_str = meet_time_moscow.strftime("%d.%m.%Y")
            time_str = meet_time_moscow.strftime("%H:%M")

            creator_message = (
                f"К сожалению, ваш партнер по кофе отменил встречу в «*{shop_name}*» "
                f"({date_str} в {time_str}). 😔\n\n"
                "Но не переживайте, ваша заявка снова активна и видна другим пользователям!"
            )
            await context.bot.send_message(
                chat_id=creator_id, text=creator_message, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(
                f"Failed to send unmatch notification to creator {creator_id}: {e}"
            )

    else:
        logger.warning(
            f"FAILURE: User {partner_id} failed to unmatch from request {request_id}."
        )
        await query.edit_message_text(
            text="❌ Не удалось отменить участие. Возможно, создатель уже отменил эту встречу."
        )

    await show_main_menu_keyboard(update, context, text="Главное меню:")
    return ConversationHandler.END


async def notify_users_about_pairing(
    request_id: int, context: ContextTypes.DEFAULT_TYPE
):
    logger.info(f"Sending notifications for request_id: {request_id}.")

    details = get_request_details(request_id=request_id)
    if not details:
        logger.error(f"ERROR details not found for {request_id}")
        return

    creator_id = details["creator_user_id"]
    partner_id = details["partner_user_id"]
    shop_name = details["shop_name"]

    meet_time_moscow = details["meet_time"].astimezone(MOSCOW_TIMEZONE)
    meet_time_str = meet_time_moscow.strftime("%H:%M")

    common_text = (
        f"Кофе-мит в «{shop_name}» в {meet_time_str}.\n\n"
        f"ℹ️ *Контакт собеседника будет скрыт до момента напоминания "
        f"менее предвзяты и открыты новому! 🕵️‍♂️"
    )

    message_to_creator = f"Ура, на твою заявку откликнулись! 🎉\n\n{common_text}"

    message_to_partner = f"Есть мэтч! 🎉\n\nТы присоединился к заявке. {common_text}"

    try:
        await context.bot.send_message(
            chat_id=creator_id, text=message_to_creator, parse_mode="Markdown"
        )
        await context.bot.send_message(
            chat_id=partner_id, text=message_to_partner, parse_mode="Markdown"
        )
        logger.info(
            f"SUCCESS in sending notifications to {creator_id} and {partner_id}."
        )
    except Exception as e:
        logger.error(f"ERROR in sending notifications for request {request_id}: {e}")


async def send_icebreakers(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: sending icebreakers...")
    meetings = get_meetings_for_icebreaker()

    if not meetings:
        return

    for meeting in meetings:
        creator_id = meeting["creator_user_id"]
        partner_id = meeting["partner_user_id"]
        request_id = meeting["request_id"]

        question = random.choice(ICEBREAKER_QUESTIONS)

        text = (
            f"💡 *Тема для разогрева*\n\n"
            f"Встреча уже совсем скоро! Если не знаете, с чего начать разговор, попробуйте обсудить это:\n\n"
            f"«_{question}_»"
        )

        try:
            await context.bot.send_message(
                chat_id=creator_id, text=text, parse_mode="Markdown"
            )
            await context.bot.send_message(
                chat_id=partner_id, text=text, parse_mode="Markdown"
            )
            logger.info(f"Successfully sent icebreaker for request_id: {request_id}")
        except Exception as e:
            logger.error(f"Failed to send icebreaker for request_id {request_id}: {e}")


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: sending reminders...")
    meetings = get_meetings_for_reminder()

    for meeting in meetings:
        creator_id = meeting["creator_user_id"]
        partner_id = meeting["partner_user_id"]

        creator_mention = (
            f"@{meeting['creator_username']}"
            if meeting["creator_username"]
            else meeting["creator_first_name"]
        )
        partner_mention = (
            f"@{meeting['partner_username']}"
            if meeting["partner_username"]
            else meeting["partner_first_name"]
        )

        shop_name = meeting["shop_name"]
        meet_time_moscow = meeting["meet_time"].astimezone(MOSCOW_TIMEZONE)
        meet_time_str = meet_time_moscow.strftime("%H:%M")
        request_id = meeting["request_id"]

        reminder_text = f"""Хей! Просто дружеское напоминание 🔔

        В {meet_time_str} у тебя кофе-мит в {shop_name}!

        Время зарядиться кофе и общением! ☕️"""

        message_to_creator = (
            f"{reminder_text}\n\nВаша компания — {partner_mention}. Не опаздывайте! 😉"
        )
        message_to_partner = (
            f"{reminder_text}\n\nВаша компания — {creator_mention}. Не опаздывайте! ☕️"
        )

        try:
            await context.bot.send_message(chat_id=creator_id, text=message_to_creator)
            await context.bot.send_message(chat_id=partner_id, text=message_to_partner)
        except Exception as e:
            logger.error(f"Failed to send reminder for request_id {request_id}: {e}")


async def expire_requests(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: checking for expired requests...")
    exp_requests = expire_pending_requests()

    if not exp_requests:
        logger.info("No requests to expire")
        return

    for request in exp_requests:
        creator_id = request["creator_user_id"]
        request_id = request["request_id"]

        shop_name = request["shop_name"]
        meet_time_moscow = request["meet_time"].astimezone(MOSCOW_TIMEZONE)
        meet_time_str = meet_time_moscow.strftime("%H:%M")

        failure_message = f"""Эх, в этот раз не сложилось: кофе-мит в {shop_name} в {meet_time_str} был отменён. \n\n   
        Похоже, сегодня вселенная кофе была чем-то занята, и на твою заявку никто не откликнулся. 😥\n\nНо это не повод грустить! Попробуй создать новую заявку на другое время или в другом месте. Следующий мэтч может быть всего в паре кликов от тебя! ✨"""

        try:
            await context.bot.send_message(chat_id=creator_id, text=failure_message)
            logger.info(
                f"Successfully sent failure notification for expired request_id: {request_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to send failure notification for request_id {request_id}: {e}"
            )


async def request_feedback(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: checking for meetings to request feedback on...")
    meetings_for_feedback = get_meetings_for_feedback()

    for meeting in meetings_for_feedback:
        request_id = meeting["request_id"]
        creator_id = meeting["creator_user_id"]
        partner_id = meeting["partner_user_id"]

        feedback_text = (
            f"Привет! Как прошел ваш кофе-мит в «{meeting['shop_name']}» "
            f"в {meeting['meet_time'].astimezone(MOSCOW_TIMEZONE).strftime('%H:%M')}? "
            "Это поможет нам улучшить бота. 🙏"
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Все отлично, встреча состоялась!",
                    callback_data=f"feedback_attended_{request_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 Партнер не пришел",
                    callback_data=f"feedback_partner_no_show_{request_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "😔 Я не смог(ла) прийти",
                    callback_data=f"feedback_creator_no_show_{request_id}",
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=creator_id, text=feedback_text, reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(
                f"Failed to send feedback req to creator {creator_id} (req {request_id}): {e}"
            )

        try:
            await context.bot.send_message(
                chat_id=partner_id, text=feedback_text, reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(
                f"Failed to send feedback req to partner {partner_id} (req {request_id}): {e}"
            )

        success = mark_feedback_as_requested(request_id)
        if success:
            logger.info(f"Marked feedback as requested for request_id: {request_id}")
        else:
            logger.error(
                f"CRITICAL: Failed to mark feedback in DB for request_id: {request_id}"
            )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        callback_prefix, request_id_str = query.data.rsplit("_", 1)
        request_id = int(request_id_str)
    except (ValueError, IndexError):
        logger.error(f"Could not parse feedback callback_data: {query.data}")
        await query.edit_message_text(
            text="Произошла ошибка при обработке вашего ответа."
        )
        return

    outcome_str = callback_prefix[len("feedback_") :]

    user_id = update.effective_user.id

    details = get_request_details(request_id)
    is_creator = user_id == details.get("creator_user_id")

    final_outcome = None
    if outcome_str == "attended":
        final_outcome = "attended"
    elif outcome_str == "partner_no_show":
        final_outcome = "partner_no_show" if is_creator else "creator_no_show"
    elif outcome_str == "creator_no_show":
        final_outcome = "creator_no_show" if is_creator else "partner_no_show"

    if final_outcome:
        save_meeting_outcome(request_id, final_outcome)
        if final_outcome == "attended":
            context.user_data["awaiting_feedback_id"] = request_id

            keyboard = [
                [
                    InlineKeyboardButton(
                        "Пропустить этот шаг ⏩", callback_data="skip_feedback"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                text="Супер! Рад, что встреча состоялась. 🎉\n\n"
                "Напиши пару слов о том, как все прошло? Это поможет мне стать лучше, "
                "а лучшие истории попадут в еженедельный дайджест (анонимно).",
                reply_markup=reply_markup,
            )
        else:
            await query.edit_message_text(
                text="Спасибо за ответ! Надеюсь, в следующий раз повезет больше. 🙌"
            )
    else:
        logger.warning(
            f"Unknown feedback outcome_str: {outcome_str} from data: {query.data}"
        )
        await query.edit_message_text(text="Произошла ошибка. Спасибо за попытку!")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cancel_text = "Без проблем, всё отменил. Если надумаешь вернуться — ты знаешь, где меня искать! 👍"
    await show_main_menu_keyboard(update, context, text=cancel_text)
    return ConversationHandler.END


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = int(os.getenv("ADMIN_ID", "0"))
    if update.effective_user.id != admin_id:
        return

    message_to_send = update.message.text.partition(" ")[2]

    if not message_to_send:
        await update.message.reply_text(
            "⚠️ Ошибка: Пустое сообщение.\n"
            "Использование: `/broadcast Текст вашей рассылки`",
            parse_mode="Markdown",
        )
        return

    users = get_all_active_users()
    if not users:
        await update.message.reply_text("Нет активных пользователей для рассылки.")
        return

    await update.message.reply_text(
        f"📢 Начинаю рассылку на {len(users)} пользователей..."
    )

    success_count = 0
    block_count = 0

    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message_to_send,
                parse_mode="Markdown",
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Broadcast failed for {user_id}: {e}")
            block_count += 1

    await update.message.reply_text(
        f"✅ Рассылка завершена!\n\n"
        f"📨 Отправлено: {success_count}\n"
        f"🚫 Не доставлено (бан): {block_count}"
    )


async def skip_feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("awaiting_feedback_id", None)

    await query.edit_message_text(
        text="Окей, без проблем! Спасибо, что пользуешься ботом. ☕️"
    )


async def process_feedback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request_id = context.user_data.get("awaiting_feedback_id")
    if not request_id:
        return

    user_text = update.message.text
    if user_text in ["☕️ Найти компанию", "📂 Мои заявки", "ℹ️ Гайд"]:
        context.user_data.pop("awaiting_feedback_id", None)
        return

    save_feedback_text(request_id, user_text)
    context.user_data.pop("awaiting_feedback_id", None)

    await update.message.reply_text("Спасибо! Твой отзыв записан. ❤️")


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    find_handler = MessageHandler(
        filters.Regex("^☕️ Найти компанию$"), find_company_start
    )
    my_requests_handler = MessageHandler(
        filters.Regex("^📂 Мои заявки$"), my_requests_start
    )

    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REGISTER_SCHOOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_school)
            ],
            REGISTER_YEAR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_year)
            ],
        },
        fallbacks=[CommandHandler("cancel", start)],
        allow_reentry=True,
    )

    conv_handler = ConversationHandler(
        entry_points=[
            find_handler,
            CommandHandler("find", find_company_start),
            my_requests_handler,
            CommandHandler("my_coffee_requests", my_requests_start),
        ],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(
                    create_request_step1_shop, pattern="^create_new_request$"
                ),
                CallbackQueryHandler(
                    view_available_requests, pattern="^view_available_requests$"
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            CHOOSING_SHOP: [
                CallbackQueryHandler(show_shop_details, pattern="^shop_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            CHOOSING_DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, create_request_step3_time
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            VIEWING_SHOP_DETAILS: [
                CallbackQueryHandler(
                    create_request_step2_date, pattern="^confirm_shop_"
                ),
                CallbackQueryHandler(
                    create_request_step1_shop, pattern="^back_to_shop_list$"
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            CHOOSING_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, create_request_step4_validate
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            CHOOSING_REQUEST: [
                CallbackQueryHandler(handle_accept_request, pattern="^accept_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            MANAGING_REQUESTS: [
                CallbackQueryHandler(handle_cancel_request, pattern="^cancel_[0-9]+$"),
                CallbackQueryHandler(handle_unmatch_request, pattern="^unmatch_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
                CallbackQueryHandler(
                    handle_cancel_request_as_creator, pattern="^cancel_matched_"
                ),
            ],
        },
        fallbacks=[
            find_handler,
            my_requests_handler,
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(registration_conv)
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(
        CallbackQueryHandler(skip_feedback_handler, pattern="^skip_feedback$")
    )
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Гайд$"), help_command))

    app.add_handler(CallbackQueryHandler(handle_feedback, pattern="^feedback_"))
    feedback_filter = (
        filters.TEXT
        & ~filters.COMMAND
        & ~filters.Regex("^(☕️ Найти компанию|📂 Мои заявки|ℹ️ Гайд)$")
    )
    app.add_handler(MessageHandler(feedback_filter, process_feedback_text))

    logger.info(
        "Starting the bot. Reference to bot: https://t.me/random_coffee_mipt_bot"
    )
    app.run_polling()


if __name__ == "__main__":
    main()
