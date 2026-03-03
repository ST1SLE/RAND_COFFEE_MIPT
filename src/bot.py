import os
import logging
import re
import asyncio
import random
import html
import argparse
import json
from datetime import datetime, time, timezone, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from icebreakers import ICEBREAKER_QUESTIONS, VALENTINE_ICEBREAKERS
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
    expire_pending_requests,
    unmatch_request,
    cancel_request_by_creator,
    get_shop_details,
    mark_feedback_as_requested,
    get_meetings_for_feedback,
    save_meeting_outcome,
    update_user_profile,
    update_user_bio,
    get_meetings_for_icebreaker,
    get_all_active_users,
    save_feedback_text,
    get_meetings_to_confirm,
    confirm_meeting_participation,
    increment_no_show_counter,
    cancel_unconfirmed_matches,
    ban_user,
    is_user_active,
    increment_streaks,
    reset_user_streak,
    init_db_pool,
    save_verification_code,
    get_new_matches_for_notification,
    # Мэтчинг по интересам
    set_interest_search,
    is_user_searching_interest,
    get_interest_search_count,
    has_user_bio,
    get_pending_interest_match,
    get_interest_match_by_id,
    get_new_interest_matches_for_notification,
    propose_meeting,
    accept_meeting_proposal,
    decline_interest_match,
    expire_interest_matches,
    get_stale_interest_proposals,
    mark_proposal_reminder_sent,
    # Пол
    get_user_gender,
    set_user_gender,
)

load_dotenv()
BOT_CONFIG = {}

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
    REGISTER_BIO,
    EDITING_PROFILE,
    EDITING_BIO,
    INTEREST_MATCH_MENU,
    INTEREST_PROPOSE_SHOP,
    INTEREST_PROPOSE_DATE,
    INTEREST_PROPOSE_TIME,
    GENDER_GATE,
    REGISTER_GENDER,
) = range(18)

MAX_NEGOTIATION_ROUNDS = 5


def display_similarity(raw_score: float) -> int:
    """Remap cosine similarity [0.15, 1.0] -> [55%, 95%] для отображения."""
    clamped = max(0.15, min(raw_score, 1.0))
    return round(55 + (clamped - 0.15) * (95 - 55) / (1.0 - 0.15))


VALENTINE_START = datetime(2026, 2, 13, tzinfo=MOSCOW_TIMEZONE)
VALENTINE_END = datetime(2026, 2, 16, tzinfo=MOSCOW_TIMEZONE)


def is_valentine_period() -> bool:
    """Проверяет, активен ли Valentine's Day режим (13-15 февраля 2026)."""
    now = datetime.now(MOSCOW_TIMEZONE)
    return VALENTINE_START <= now <= VALENTINE_END


async def _check_gender_gate(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Показывает выбор пола, если не указан. True = гейт сработал."""
    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]
    gender = get_user_gender(user_id, uni_id)
    if gender is not None:
        return False

    keyboard = [
        [
            InlineKeyboardButton("👨 Парень", callback_data="set_gender_M"),
            InlineKeyboardButton("👩 Девушка", callback_data="set_gender_F"),
        ],
    ]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Один момент! Укажи, пожалуйста, свой пол:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return True


async def handle_gender_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор пола через inline-кнопку."""
    query = update.callback_query
    await query.answer()
    gender = query.data.replace("set_gender_", "")  # M, F, or skip
    success = set_user_gender(
        update.effective_user.id, gender, BOT_CONFIG["university_id"]
    )
    if success:
        await query.edit_message_text("Готово!")
        await show_main_menu_keyboard(update, context, "Теперь продолжай 👇")
    else:
        await query.edit_message_text(
            "Произошла ошибка при сохранении. Попробуй ещё раз позже."
        )
    return ConversationHandler.END


def _get_next_matching_time_str() -> str:
    """Вычисляет, когда будет следующий мэтчинг (12:00 МСК)."""
    now_moscow = datetime.now(MOSCOW_TIMEZONE)
    matching_today = now_moscow.replace(hour=12, minute=0, second=0, microsecond=0)
    if now_moscow < matching_today:
        return "Следующий мэтчинг: сегодня в 12:00"
    return "Следующий мэтчинг: завтра в 12:00"


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
    keyboard = [
        ["☕️ Найти компанию", "📂 Мои заявки"],
        ["🔍 Мэтчинг по интересам"],
        ["👤 Мой профиль", "ℹ️ Гайд"],
    ]
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
    uni_id = BOT_CONFIG["university_id"]

    add_or_update_user(  # Сначала обновляем/создаем запись в БД
        user_id=user.id,
        first_name=user.first_name,
        username=user.username,
        uni_id=uni_id,
    )

    user_details = get_user_details(user.id, uni_id=uni_id)

    # Проверяем, зарегистрирован ли пользователь (есть ли факультет)
    if user_details and user_details.get("phystech_school"):
        welcome_text = "С возвращением! 👋\n\n" "Скорее жми «☕️ Найти компанию»"
        await show_main_menu_keyboard(update, context, text=welcome_text)
        return ConversationHandler.END

    # Если факультета нет, запускаем полную регистрацию
    schools = BOT_CONFIG["schools"]
    await update.message.reply_text(
        "Привет! 👋 Перед тем как начать, давай познакомимся.\n\n"
        "С какого ты факультета?",
        reply_markup=ReplyKeyboardMarkup(
            schools, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return REGISTER_SCHOOL


async def register_school(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    school = update.message.text
    uni_id = BOT_CONFIG["university_id"]  # Получаем ID вуза

    schools_list = BOT_CONFIG["schools"]
    flat_schools = [item for sublist in schools_list for item in sublist]
    if school not in flat_schools:
        await update.message.reply_text("Пожалуйста, выбери вариант, используя кнопки.")
        return REGISTER_SCHOOL

    context.user_data["reg_school"] = school

    if school == "Никакой из них":
        context.user_data["reg_year"] = None
        gender_keyboard = [["👨 Парень", "👩 Девушка"]]
        await update.message.reply_text(
            "Понял! Мы рады гостям и сотрудникам. 😊\n\n"
            "Укажи, пожалуйста, свой пол:",
            reply_markup=ReplyKeyboardMarkup(
                gender_keyboard, one_time_keyboard=True, resize_keyboard=True
            ),
        )
        return REGISTER_GENDER

    years_list = BOT_CONFIG["years"]
    await update.message.reply_text(
        f"Отлично, {school}! А на каком ты курсе?",
        reply_markup=ReplyKeyboardMarkup(
            years_list, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return REGISTER_YEAR


async def register_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    year_str = update.message.text
    # uni_id здесь не нужен, так как запись в БД переносится в следующий шаг

    if year_str == "Вернуться назад":
        schools_list = BOT_CONFIG["schools"]
        await update.message.reply_text(
            "Хорошо, давай выберем факультет заново.",
            reply_markup=ReplyKeyboardMarkup(
                schools_list, one_time_keyboard=True, resize_keyboard=True
            ),
        )
        return REGISTER_SCHOOL

    if not year_str.isdigit() or not (1 <= int(year_str) <= 8):
        await update.message.reply_text("Пожалуйста, выбери курс кнопкой (1-8).")
        return REGISTER_YEAR

    # Сохраняем во временное хранилище
    context.user_data["reg_year"] = int(year_str)

    gender_keyboard = [["👨 Парень", "👩 Девушка"]]
    await update.message.reply_text(
        "Супер! Укажи, пожалуйста, свой пол:",
        reply_markup=ReplyKeyboardMarkup(
            gender_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return REGISTER_GENDER


async def register_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг регистрации: выбор пола."""
    choice = update.message.text
    gender_map = {"👨 Парень": "M", "👩 Девушка": "F"}

    if choice not in gender_map:
        await update.message.reply_text("Пожалуйста, выбери вариант, используя кнопки.")
        return REGISTER_GENDER

    context.user_data["reg_gender"] = gender_map[choice]

    await update.message.reply_text(
        "Последний шаг: расскажи немного о себе. 📝\n\n"
        "Напиши пару предложений: чем увлекаешься, о чем любишь говорить, "
        "какой кофе пьешь. Это поможет алгоритму подбирать тебе интересную компанию.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REGISTER_BIO


async def register_bio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bio_text = update.message.text
    uni_id = BOT_CONFIG["university_id"]

    if len(bio_text) < 10:
        await update.message.reply_text(
            "Слишком коротко! Напиши хотя бы пару слов о себе."
        )
        return REGISTER_BIO

    if len(bio_text) > 400:
        await update.message.reply_text(
            "Попробуй уложиться в 400 символов, пожалуйста."
        )
        return REGISTER_BIO

    school = context.user_data.get("reg_school")
    year = context.user_data.get("reg_year")
    user_id = update.effective_user.id

    # Сохраняем все данные
    update_user_profile(user_id, school=school, year=year, bio=bio_text, uni_id=uni_id)

    # Сохраняем пол (если был указан при регистрации)
    reg_gender = context.user_data.get("reg_gender")
    if reg_gender:
        set_user_gender(user_id, reg_gender, uni_id)

    await show_main_menu_keyboard(
        update, context, text="Профиль заполнен! 🎉\nТеперь всё готово для кофе-митов."
    )
    return ConversationHandler.END


async def my_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Показывает текущий профиль пользователя и кнопки для редактирования.
    """
    if await _check_gender_gate(update, context):
        return GENDER_GATE

    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]
    user_details = get_user_details(user_id, uni_id=uni_id)

    if not user_details:
        await update.message.reply_text(
            "Не удалось загрузить твой профиль. Попробуй /start."
        )
        return ConversationHandler.END

    school = user_details.get("phystech_school", "Не указан")
    year = user_details.get("year_as_student", "Не указан")
    bio = user_details.get("bio", "Не заполнено")
    streak = user_details.get("coffee_streak", 0)
    gender = user_details.get("gender")
    gender_display = {"M": "👨 Парень", "F": "👩 Девушка", "skip": "Не указан"}.get(
        gender, "Не указан"
    )

    # Используем HTML теги <b> вместо Markdown звездочек
    profile_text = (
        f"👤 <b>Твой профиль:</b>\n\n"
        f"🏫 <b>Факультет:</b> {html.escape(str(school))}\n"
        f"🎓 <b>Курс:</b> {html.escape(str(year))}\n"
        f"🚻 <b>Пол:</b> {gender_display}\n"
        f"📝 <b>О себе:</b> {html.escape(bio) if bio else 'Не заполнено'}\n\n"
        f"🔥 <b>Coffee Streak:</b> {streak}"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить «О себе»", callback_data="edit_bio")],
        [InlineKeyboardButton("✏️ Изменить пол", callback_data="edit_gender")],
        [InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        profile_text, parse_mode="HTML", reply_markup=reply_markup
    )
    return EDITING_PROFILE


async def edit_bio_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Запрашивает новый текст для "О себе".
    """
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Хорошо, отправь мне новый текст о себе (до 400 символов)."
    )
    return EDITING_BIO


async def edit_bio_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Сохраняет новый текст "О себе" и завершает диалог.
    """
    new_bio = update.message.text
    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]

    if len(new_bio) < 10 or len(new_bio) > 400:
        await update.message.reply_text(
            "Текст должен быть от 10 до 400 символов. Попробуй еще раз."
        )
        return EDITING_BIO

    # Используем новую функцию для обновления только bio
    update_user_bio(user_id, new_bio, uni_id)

    await update.message.reply_text("✅ Отлично, твой профиль обновлен!")
    await show_main_menu_keyboard(update, context, "Главное меню:")
    return ConversationHandler.END


async def edit_gender_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает inline-кнопки для изменения пола."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("👨 Парень", callback_data="profile_gender_M"),
            InlineKeyboardButton("👩 Девушка", callback_data="profile_gender_F"),
        ],
    ]
    await query.edit_message_text(
        "Выбери свой пол:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDITING_PROFILE


async def edit_gender_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет новый пол и возвращает в меню."""
    query = update.callback_query
    await query.answer()
    gender = query.data.replace("profile_gender_", "")  # M, F, or skip
    success = set_user_gender(
        update.effective_user.id, gender, BOT_CONFIG["university_id"]
    )
    if success:
        await query.edit_message_text("✅ Пол обновлен!")
    else:
        await query.edit_message_text(
            "Произошла ошибка при сохранении. Попробуй позже."
        )
    await show_main_menu_keyboard(update, context, "Главное меню:")
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Вот что я умею:\n\n"
        "«☕️ *Найти компанию*» — посмотри, кто уже ищет компанию, или создай свою заявку. "
        "Выбираешь место, дату и время.\n\n"
        "«📂 *Мои заявки*» — тут хранятся все твои кофейные планы. Можно отменить заявку, если планы поменялись.\n\n"
        "«🔍 *Мэтчинг по интересам*» — включи режим поиска, и раз в день я подберу тебе собеседника "
        "на основе ваших интересов. Вы договоритесь о месте и времени прямо в боте.\n\n"
        "Всё просто! Если что-то пошло не так, команда /cancel всегда прервет любое действие."
    )
    await update.message.reply_text(
        help_text, parse_mode="Markdown", disable_web_page_preview=True
    )


async def post_init(app):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Перезапустить бота"),
            BotCommand("cancel", "Отменить текущее действие"),
            BotCommand("help", "Гайд по боту"),
        ]
    )

    app.job_queue.run_repeating(notify_new_matches_job, interval=120, first=25)
    app.job_queue.run_repeating(send_confirmations_job, interval=300, first=30)
    app.job_queue.run_repeating(send_icebreakers, interval=60, first=20)
    app.job_queue.run_repeating(send_reminders, interval=60, first=10)
    app.job_queue.run_repeating(expire_requests, interval=60, first=15)
    app.job_queue.run_repeating(request_feedback, interval=1800, first=60)
    app.job_queue.run_repeating(auto_cancel_job, interval=300, first=40)
    # Мэтчинг по интересам
    app.job_queue.run_repeating(notify_interest_matches_job, interval=120, first=35)
    app.job_queue.run_repeating(remind_interest_proposals_job, interval=1800, first=120)
    app.job_queue.run_repeating(expire_interest_matches_job, interval=1800, first=90)


async def find_company_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _check_gender_gate(update, context):
        return GENDER_GATE

    if not is_user_active(update.effective_user.id, uni_id=BOT_CONFIG["university_id"]):
        if update.callback_query:
            await update.callback_query.answer("Вы заблокированы 🚫", show_alert=True)
        else:
            await update.message.reply_text("🚫 Вы заблокированы.")
        return ConversationHandler.END

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
    shop_details = get_shop_details(shop_id=shop_id, uni_id=BOT_CONFIG["university_id"])

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

    shops = get_active_coffee_shops(uni_id=BOT_CONFIG["university_id"])
    if not shops:
        await query.edit_message_text(
            text="К сожалению сейчас не нашлись активные кофейни, попробуй позже. 😉"
        )
        return ConversationHandler.END

    buttons = []
    for row in shops:
        shop_id = row[0]
        name = row[1]
        promo_label = row[2] if len(row) > 2 else None

        label = f"📍 {name}"
        if promo_label:
            label += f" {promo_label}"
        buttons.append((label, f"shop_{shop_id}"))

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
        parse_mode="Markdown",
    )
    return CHOOSING_DATE


async def create_request_step3_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    user_date_str = update.message.text

    if not re.match(r"^(\d{1,2})\.(\d{1,2})$", user_date_str):
        await update.message.reply_text(
            "Формат даты неверный 😥. Пожалуйста, введи дату как *ДД.ММ*, например: *25.12* или *1.9*",
            parse_mode="Markdown",
        )
        return CHOOSING_DATE

    try:
        now_moscow = datetime.now(MOSCOW_TIMEZONE)
        now_date = now_moscow.date()
        day, month = map(int, user_date_str.split("."))
        year = now_moscow.year
        proposed_date_naive = datetime(year, month, day)

        if proposed_date_naive.date() < now_date:
            year += 1
            proposed_date_naive = datetime(year, month, day)

        if (proposed_date_naive.date() - now_date).days > 14:
            await update.message.reply_text(
                "Давай не будем планировать так далеко 🤓! Выбери дату в пределах следующих 14 дней."
            )
            return CHOOSING_DATE

    except ValueError:
        await update.message.reply_text(
            "Такой даты не существует (например, *31.02*). Пожалуйста, введи корректную дату.",
            parse_mode="Markdown",
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
        parse_mode="Markdown",
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

    uni_id = BOT_CONFIG["university_id"]
    working_hours = get_shop_working_hours(shop_id=shop_id, uni_id=uni_id)
    if is_shop_open_at_time(working_hours, meet_time):
        create_coffee_request(
            creator_user_id=user.id, shop_id=shop_id, meet_time=meet_time, uni_id=uni_id
        )
        success_text = "Готово! ✨\n\n Твоя заявка в игре. Как только кто-то откликнется, я пришлю уведомление. 🔔"
        await show_main_menu_keyboard(update, context, text=success_text)
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Ой, кажется, эта кофейня в это время уже спит 😴. Давай попробуем другое время?"
        )
        return CHOOSING_TIME


# --- Ручной мэтчинг (v1.0 fallback) ---
# Сосуществует с ML-мэтчингом: WHERE status='pending' AND partner_user_id IS NULL
# гарантирует, что ML-смэтчённые заявки сюда не попадут.
async def view_available_requests(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    requests = get_pending_requests(user_id, uni_id=BOT_CONFIG["university_id"])

    if not requests:
        reply_markup = build_inline_keyboard(
            buttons_data=[("Создать свою заявку", "create_new_request")]
        )

        await query.edit_message_text(
            text="Упс, сейчас свободных заявок нет. Похоже, все уже нашли себе компанию. 😢\n\nМожет, создашь свою и станешь первым? ",
            reply_markup=reply_markup,
        )
        return CHOOSING_ACTION

    campus_abbr = {"Покровка": "Покра", "Шаболовская": "Шаб", "Басманная": "Басм", "Мясницкая": "Мясо"}
    lines = []
    buttons = []
    for i, (request_id, shop_name, promo_label, meet_time, streak, similarity) in enumerate(requests, 1):
        meet_time_moscow = meet_time.astimezone(MOSCOW_TIMEZONE)
        date_time_str = meet_time_moscow.strftime("%d.%m %H:%M")

        shop_display = shop_name
        if promo_label:
            shop_display += f" {promo_label}"

        details = ""
        if similarity is not None:
            details += f"⭐{int(similarity)}% "
        if streak >= 1:
            details += f"🔥{streak} "
        if details:
            details += "— "

        lines.append(f"{i}. {details}{shop_display}\n    {date_time_str}")
        if " | " in shop_name:
            campus, shop_part = shop_name.split(" | ", 1)
            short_name = f"{campus_abbr.get(campus, campus)} | {shop_part}"
        else:
            short_name = shop_name
        buttons.append((f"{i}. {short_name} · {date_time_str}", f"accept_{request_id}"))

    buttons.append(("Создать свою заявку", "create_new_request"))
    reply_markup = build_inline_keyboard(buttons_data=buttons)

    text = "Доступные заявки:\n\n" + "\n\n".join(lines)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
    )

    return CHOOSING_REQUEST


def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return text.replace("\\", "\\\\").translate(
        str.maketrans({c: f"\\{c}" for c in escape_chars})
    )


async def show_my_streak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]  # Получаем ID вуза
    # Передаем ID вуза
    user_details = get_user_details(user_id, uni_id=uni_id)

    if not user_details:
        await update.message.reply_text("Произошла ошибка при получении данных.")
        return

    streak = user_details.get("coffee_streak", 0)

    if streak == 0:
        msg = (
            "🔥 *Твой Coffee Streak: 0*\n\n"
            "Ты пока не набрал серию. Чтобы зажечь огонек:\n"
            "1. Сходи на встречу.\n"
            "2. Подтверди участие.\n"
            "3. Не отменяй в последний момент.\n\n"
            "Вперед, за первой встречей! ☕️"
        )
    elif streak < 2:
        msg = (
            f"🔥 *Твой Coffee Streak: {streak}*\n\n"
            "Отличное начало! Твой огонек виден другим пользователям.\n"
            "Так держать! 🚀"
        )
    elif streak < 7:
        msg = (
            f"🔥 *Твой Coffee Streak: {streak}*\n\n"
            "Ты — надежный партнер! Твой огонек виден другим пользователям, "
            "и они знают, что с тобой точно стоит выпить кофе. 😎"
        )
    else:
        msg = (
            f"🔥🔥🔥 *Твой Coffee Streak: {streak}* 🔥🔥🔥\n\n"
            "Да ты легенда нетворкинга! Твоей постоянности можно позавидовать. "
            "Ты входишь в топ самых активных пользователей Физтеха. 🏆"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def my_requests_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _check_gender_gate(update, context):
        return GENDER_GATE

    user_id = update.effective_user.id
    requests = get_user_requests(user_id=user_id, uni_id=BOT_CONFIG["university_id"])

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

                is_confirmed_by_both = (
                    req["is_confirmed_by_creator"] and req["is_confirmed_by_partner"]
                )

                should_show_contact = (
                    time_until_meet <= timedelta(minutes=20)
                ) or is_confirmed_by_both

                if not should_show_contact:
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
        request_id=request_id,
        partner_user_id=partner_user_id,
        uni_id=BOT_CONFIG["university_id"],
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

    success = cancel_request(
        request_id=request_id, user_id=user_id, uni_id=BOT_CONFIG["university_id"]
    )

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

    request_details = get_request_details(
        request_id=request_id, uni_id=BOT_CONFIG["university_id"]
    )
    partner_id = cancel_request_by_creator(
        request_id=request_id,
        creator_user_id=creator_id,
        uni_id=BOT_CONFIG["university_id"],
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

    request_details = get_request_details(
        request_id=request_id, uni_id=BOT_CONFIG["university_id"]
    )

    creator_id = unmatch_request(
        request_id=request_id,
        partner_user_id=partner_id,
        uni_id=BOT_CONFIG["university_id"],
    )

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

    details = get_request_details(
        request_id=request_id, uni_id=BOT_CONFIG["university_id"]
    )
    if not details:
        logger.error(f"ERROR details not found for {request_id}")
        return

    meet_time_moscow = details["meet_time"].astimezone(MOSCOW_TIMEZONE)
    now_moscow = datetime.now(MOSCOW_TIMEZONE)

    is_spontaneous = (meet_time_moscow - now_moscow) < timedelta(minutes=45)

    if is_spontaneous:
        logger.info(
            f"Spontaneous match for {request_id}. Sending contacts immediately."
        )

        msg_alert = "⚡️ *Спонтанная встреча!*\nТак как до встречи осталось мало времени, контакты открываются сразу."

        await context.bot.send_message(
            chat_id=details["creator_user_id"], text=msg_alert, parse_mode="Markdown"
        )
        await context.bot.send_message(
            chat_id=details["partner_user_id"], text=msg_alert, parse_mode="Markdown"
        )

        await send_final_contacts(context, details)
        return

    creator_id = details["creator_user_id"]
    partner_id = details["partner_user_id"]
    shop_name = details["shop_name"]
    meet_time_str = meet_time_moscow.strftime("%H:%M")

    common_text = (
        f"Кофе-мит в «{shop_name}» в {meet_time_str}.\n\n"
        f"ℹ️ *Контакт собеседника скрыт.*\n"
        f"За 2 часа до встречи я пришлю кнопку подтверждения. "
        f"Как только вы оба нажмете «Я приду», контакты откроются."
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
    meetings = get_meetings_for_icebreaker(uni_id=BOT_CONFIG["university_id"])

    if not meetings:
        return

    for meeting in meetings:
        creator_id = meeting["creator_user_id"]
        partner_id = meeting["partner_user_id"]
        request_id = meeting["request_id"]

        # Valentine's icebreakers для встреч 14-16 февраля
        meet_date = meeting["meet_time"].astimezone(MOSCOW_TIMEZONE).date()
        valentine_dates = {
            datetime(2026, 2, 14).date(),
            datetime(2026, 2, 15).date(),
            datetime(2026, 2, 16).date(),
        }
        questions = (
            VALENTINE_ICEBREAKERS
            if meet_date in valentine_dates
            else ICEBREAKER_QUESTIONS
        )
        question = random.choice(questions)

        text_base = (
            f"💡 *Тема для разогрева*\n\n"
            f"Встреча уже совсем скоро! Если не знаете, с чего начать разговор, попробуйте обсудить это:\n\n"
            f"«_{question}_»"
        )

        partner_chat_ids = meeting.get("partner_chat_id")

        if partner_chat_ids is None:
            partner_chat_ids = []
        elif isinstance(partner_chat_ids, int):
            partner_chat_ids = [partner_chat_ids]

        promo_addition = ""

        if partner_chat_ids:
            try:
                discount_val = meeting.get("discount_amount")

                if not discount_val:
                    logger.warning(
                        f"Shop {meeting['shop_name']} has partner_id but no discount_amount!"
                    )
                    discount_str = "?"
                else:
                    discount_str = str(discount_val)

                code = str(random.randint(100000, 999999))

                meet_time_str = (
                    meeting["meet_time"].astimezone(MOSCOW_TIMEZONE).strftime("%H:%M")
                )
                c_user = meeting.get("creator_username") or "Без юзернейма"
                p_user = meeting.get("partner_username") or "Без юзернейма"

                barista_msg = (
                    f"🆕 *Новая встреча Random Coffee*\n"
                    f"⏰ Время: {meet_time_str}\n"
                    f"🔑 Код: `{code}`\n"
                    f"💵 Скидка: {discount_str}%"
                )

                messages_sent = 0
                for admin_id in partner_chat_ids:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id, text=barista_msg, parse_mode="Markdown"
                        )
                        messages_sent += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to send code to specific admin {admin_id}: {e}"
                        )

                save_verification_code(
                    request_id, code, uni_id=BOT_CONFIG["university_id"]
                )

                promo_addition = (
                    f"\n\n🎁 *Бонус от заведения:*\n"
                    f"Ваш код скидки {discount_str}%: `{code}`\n"
                    f"Назовите его на кассе."
                )

                logger.info(
                    f"Generated promo code {code} for shop. Sent to {messages_sent} admins."
                )

            except Exception as e:
                logger.error(f"CRITICAL promo error: {e}")
                promo_addition = ""

        final_text = text_base + promo_addition
        try:
            await context.bot.send_message(
                chat_id=creator_id, text=final_text, parse_mode="Markdown"
            )
            await context.bot.send_message(
                chat_id=partner_id, text=final_text, parse_mode="Markdown"
            )
            logger.info(f"Successfully sent icebreaker for request_id: {request_id}")
        except Exception as e:
            logger.error(f"Failed to send icebreaker for request_id {request_id}: {e}")


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: sending reminders...")
    meetings = get_meetings_for_reminder(uni_id=BOT_CONFIG["university_id"])

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
    exp_requests = expire_pending_requests(uni_id=BOT_CONFIG["university_id"])

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
    meetings_for_feedback = get_meetings_for_feedback(
        uni_id=BOT_CONFIG["university_id"]
    )

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

        success = mark_feedback_as_requested(
            request_id, uni_id=BOT_CONFIG["university_id"]
        )
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

    details = get_request_details(request_id, uni_id=BOT_CONFIG["university_id"])
    if not details:
        await query.edit_message_text("Встреча не найдена или истекла.")
        return

    is_creator = user_id == details.get("creator_user_id")

    final_outcome = None
    if outcome_str == "attended":
        final_outcome = "attended"
    elif outcome_str == "partner_no_show":
        final_outcome = "partner_no_show" if is_creator else "creator_no_show"
    elif outcome_str == "creator_no_show":
        final_outcome = "creator_no_show" if is_creator else "partner_no_show"

    uni_id = BOT_CONFIG["university_id"]
    if final_outcome:
        is_first_update = save_meeting_outcome(request_id, final_outcome, uni_id=uni_id)

        if final_outcome in ["partner_no_show", "creator_no_show"]:
            guilty_id = None
            if final_outcome == "partner_no_show":
                guilty_id = details["partner_user_id"]
            elif final_outcome == "creator_no_show":
                guilty_id = details["creator_user_id"]

            if guilty_id and is_first_update:
                reset_user_streak(guilty_id, uni_id=uni_id)

                new_count = increment_no_show_counter(guilty_id, uni_id=uni_id)
                logger.info(f"User {guilty_id} no_show_count increased to {new_count}")

                if new_count == 2:
                    try:
                        await context.bot.send_message(
                            chat_id=guilty_id,
                            text="⚠️ *Предупреждение*\n\n"
                            "Ваш партнер сообщил, что вы не пришли на встречу.\n"
                            "Пожалуйста, уважайте время других студентов.\n\n"
                            "❗️ **После 3-го пропуска ваш аккаунт будет заблокирован.** "
                            "Сейчас у вас 2 пропуска.",
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.warning(f"Could not send warning to {guilty_id}: {e}")

                elif new_count >= 3:
                    ban_user(guilty_id, uni_id=uni_id)
                    logger.warning(f"BANNED user {guilty_id} (no_shows: {new_count})")
                    try:
                        await context.bot.send_message(
                            chat_id=guilty_id,
                            text="🚫 *Ваш аккаунт заблокирован*\n\n"
                            "Вы пропустили 3 встречи. В связи с систематическими нарушениями "
                            "доступ к Coffee Meet MIPT для вас закрыт навсегда.",
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.warning(f"Could not send ban msg to {guilty_id}: {e}")

            await query.edit_message_text(
                text="Спасибо за честность! Нам жаль, что встреча не состоялась. 😔\n"
                "Мы приняли меры."
            )

        elif final_outcome == "attended":
            if is_first_update:
                increment_streaks(request_id, uni_id=uni_id)
                logger.info(f"Streaks incremented for request {request_id}")

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
        logger.warning(
            f"Unknown feedback outcome_str: {outcome_str} from data: {query.data}"
        )
        await query.edit_message_text(text="Произошла ошибка. Спасибо за попытку!")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cancel_text = "Без проблем, всё отменил. Если надумаешь вернуться — ты знаешь, где меня искать! 👍"
    await show_main_menu_keyboard(update, context, text=cancel_text)
    return ConversationHandler.END


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_env_key = BOT_CONFIG.get("admin_id_env")
    if not admin_env_key:
        logger.warning("Config is missing 'admin_id_env'")
        return

    admin_id_str = os.getenv(admin_env_key)
    if not admin_id_str:
        logger.warning(f"Env variable {admin_env_key} is empty or missing")
        return

    try:
        admin_id = int(admin_id_str)
    except ValueError:
        logger.error(f"Admin ID in {admin_env_key} is not a valid number")
        return

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

    users = get_all_active_users(uni_id=BOT_CONFIG["university_id"])
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
    if user_text in ["☕️ Найти компанию", "📂 Мои заявки", "ℹ️ Гайд", "👤 Мой профиль"]:
        context.user_data.pop("awaiting_feedback_id", None)
        return

    save_feedback_text(request_id, user_text, uni_id=BOT_CONFIG["university_id"])
    context.user_data.pop("awaiting_feedback_id", None)

    await update.message.reply_text("Спасибо! Твой отзыв записан. ❤️")


async def send_confirmations_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: sending confirmation requests...")
    meetings = get_meetings_to_confirm(uni_id=BOT_CONFIG["university_id"])

    if not meetings:
        return

    for meeting in meetings:
        creator_id = meeting["creator_user_id"]
        partner_id = meeting["partner_user_id"]
        request_id = meeting["request_id"]

        meet_time_moscow = meeting["meet_time"].astimezone(MOSCOW_TIMEZONE)
        time_str = meet_time_moscow.strftime("%H:%M")

        text = (
            f"🔔 *Подтверждение встречи*\n\n"
            f"Напоминаю, что сегодня в *{time_str}* у вас запланирован кофе-мит.\n\n"
            f"Чтобы встреча состоялась и вы получили контакты партнера, пожалуйста, "
            f"подтвердите, что вы точно придете."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Я точно приду", callback_data=f"confirm_presence_{request_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Не смогу (отменить)",
                    callback_data=f"cancel_matched_{request_id}",
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=creator_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            await context.bot.send_message(
                chat_id=partner_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            logger.info(f"Sent confirmation request for request_id: {request_id}")
        except Exception as e:
            logger.error(
                f"Failed to send confirmation for request_id {request_id}: {e}"
            )


# --- ML мэтчинг (v2.0) — уведомления ---
# Ручной мэтчинг сразу ставит is_match_notification_sent=TRUE,
# поэтому этот джоб обрабатывает только ML-мэтчи (где FALSE).
async def notify_new_matches_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: checking for new ML matches to notify...")

    matches = get_new_matches_for_notification(uni_id=BOT_CONFIG["university_id"])

    if not matches:
        return

    logger.info(f"Found {len(matches)} new matches to notify.")

    for match in matches:
        request_id = match["request_id"]
        creator_id = match["creator_user_id"]
        partner_id = match["partner_user_id"]
        meet_time = match["meet_time"]

        meet_time_moscow = meet_time.astimezone(MOSCOW_TIMEZONE)
        date_str = meet_time_moscow.strftime("%d.%m")
        time_str = meet_time_moscow.strftime("%H:%M")

        # Получаем имена пользователей
        creator_details = get_user_details(creator_id, BOT_CONFIG["university_id"])
        partner_details = get_user_details(partner_id, BOT_CONFIG["university_id"])

        creator_name = creator_details.get("first_name", "пользователь")
        partner_name = partner_details.get("first_name", "пользователь")

        # Сообщение для создателя заявки
        text_creator = (
            f"🎉 *Отличные новости!*\n\n"
            f"Мы нашли для вас пару на основе общих интересов!\n\n"
            f"👤 Ваш партнер: *{partner_name}*\n"
            f"📅 Дата: *{date_str}* в *{time_str}*\n\n"
            f"Система автоматически подобрала вас, основываясь на ваших интересах. "
            f"Скоро вы получите запрос на подтверждение встречи."
        )

        # Сообщение для партнера
        text_partner = (
            f"🎉 *Отличные новости!*\n\n"
            f"Мы нашли для вас пару на основе общих интересов!\n\n"
            f"👤 Ваш партнер: *{creator_name}*\n"
            f"📅 Дата: *{date_str}* в *{time_str}*\n\n"
            f"Система автоматически подобрала вас, основываясь на ваших интересах. "
            f"Скоро вы получите запрос на подтверждение встречи."
        )

        try:
            await context.bot.send_message(
                chat_id=creator_id,
                text=text_creator,
                parse_mode="Markdown",
            )
            await context.bot.send_message(
                chat_id=partner_id,
                text=text_partner,
                parse_mode="Markdown",
            )
            logger.info(
                f"Sent match notification for request_id: {request_id} (creator: {creator_id}, partner: {partner_id})"
            )
        except Exception as e:
            logger.error(
                f"Failed to send match notification for request_id {request_id}: {e}"
            )


async def send_final_contacts(context: ContextTypes.DEFAULT_TYPE, details: dict):
    creator_id = details["creator_user_id"]
    partner_id = details["partner_user_id"]
    shop_name = html.escape(details["shop_name"])

    meet_time_moscow = details["meet_time"].astimezone(MOSCOW_TIMEZONE)
    time_str = meet_time_moscow.strftime("%H:%M")

    def get_user_mention(user_id, username, first_name):
        safe_name = html.escape(first_name)
        if username:
            return f"@{username}"
        else:
            return f'<a href="tg://user?id={user_id}">{safe_name}</a>'

    creator_mention = get_user_mention(
        creator_id,
        details.get("creator_username"),
        details.get("creator_first_name", "Студент"),
    )

    partner_mention = get_user_mention(
        partner_id,
        details.get("partner_username"),
        details.get("partner_first_name", "Студент"),
    )

    msg_to_creator = (
        f"✅ <b>Встреча подтверждена!</b>\n\n"
        f"Твой партнер: {partner_mention}\n"
        f"Место: {shop_name}\n"
        f"Время: {time_str}\n\n"
        f"Хорошего кофе-мита! ☕️"
    )

    msg_to_partner = (
        f"✅ <b>Встреча подтверждена!</b>\n\n"
        f"Твой партнер: {creator_mention}\n"
        f"Место: {shop_name}\n"
        f"Время: {time_str}\n\n"
        f"Хорошего кофе-мита! ☕️"
    )

    try:
        await context.bot.send_message(
            chat_id=creator_id, text=msg_to_creator, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send final contacts to CREATOR {creator_id}: {e}")

    try:
        await context.bot.send_message(
            chat_id=partner_id, text=msg_to_partner, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send final contacts to PARTNER {partner_id}: {e}")


async def handle_confirmation_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    try:
        # callback_data имеет вид "confirm_presence_123"
        _, _, request_id_str = query.data.rsplit("_", 2)
        request_id = int(request_id_str)
    except (ValueError, IndexError):
        await query.edit_message_text("Ошибка обработки кнопки.")
        return

    details = get_request_details(request_id, uni_id=BOT_CONFIG["university_id"])
    if not details:
        await query.edit_message_text("❌ Эта встреча больше не активна.")
        return

    user_id = update.effective_user.id
    both_confirmed = confirm_meeting_participation(
        request_id, user_id, uni_id=BOT_CONFIG["university_id"]
    )

    if both_confirmed:
        details = get_request_details(request_id, uni_id=BOT_CONFIG["university_id"])
        if details:
            await query.edit_message_text(
                "✅ Вы подтвердили участие! Оба участника готовы. Контакты отправлены отдельным сообщением."
            )
            await send_final_contacts(context, details)
        else:
            await query.edit_message_text("Ошибка: Встреча не найдена.")
    else:
        await query.edit_message_text(
            "✅ Вы подтвердили участие!\n\n"
            "Ждем подтверждения от партнера. Как только он ответит, я пришлю его контакт."
        )


async def auto_cancel_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("JOB: cleanup unconfirmed meetings...")
    cancelled = cancel_unconfirmed_matches(uni_id=BOT_CONFIG["university_id"])

    if not cancelled:
        return

    for meeting in cancelled:
        creator_id = meeting["creator_user_id"]
        partner_id = meeting["partner_user_id"]
        shop_name = meeting["shop_name"]

        conf_creator = meeting["is_confirmed_by_creator"]
        conf_partner = meeting["is_confirmed_by_partner"]

        msg_innocent = (
            f"⚠️ *Встреча отменена*\n\n"
            f"К сожалению, ваш партнер так и не подтвердил участие во встрече в «{shop_name}».\n"
            f"Встреча автоматически отменена, чтобы вы не тратили время и не ехали зря. 😔"
        )

        msg_guilty = (
            f"🚫 *Встреча отменена*\n\n"
            f"Вы не подтвердили участие во встрече в «{shop_name}» вовремя.\n"
            f"Встреча отменена."
        )

        msg_both_silent = (
            f"🚫 *Встреча отменена*\n\n"
            f"Никто из участников не подтвердил встречу в «{shop_name}»."
        )

        try:
            if not conf_creator and not conf_partner:
                await context.bot.send_message(
                    chat_id=creator_id, text=msg_both_silent, parse_mode="Markdown"
                )
                await context.bot.send_message(
                    chat_id=partner_id, text=msg_both_silent, parse_mode="Markdown"
                )

            elif conf_creator and not conf_partner:
                await context.bot.send_message(
                    chat_id=creator_id, text=msg_innocent, parse_mode="Markdown"
                )
                await context.bot.send_message(
                    chat_id=partner_id, text=msg_guilty, parse_mode="Markdown"
                )

            elif not conf_creator and conf_partner:
                await context.bot.send_message(
                    chat_id=creator_id, text=msg_guilty, parse_mode="Markdown"
                )
                await context.bot.send_message(
                    chat_id=partner_id, text=msg_innocent, parse_mode="Markdown"
                )

            logger.info(
                f"Auto-cancelled request {meeting['request_id']} due to lack of confirmation"
            )

        except Exception as e:
            logger.error(
                f"Failed to send cancel notification for req {meeting['request_id']}: {e}"
            )


# --- Мэтчинг по интересам ---


async def interest_match_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Главное меню режима мэтчинга по интересам."""
    if await _check_gender_gate(update, context):
        return GENDER_GATE

    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]

    if not is_user_active(user_id, uni_id=uni_id):
        await update.message.reply_text("🚫 Вы заблокированы.")
        return ConversationHandler.END

    # Есть ли активный interest_match?
    pending_match = get_pending_interest_match(user_id, uni_id)
    if pending_match:
        return await _show_interest_match_status(update, context, pending_match)

    # В режиме поиска?
    is_searching = is_user_searching_interest(user_id, uni_id)
    if is_searching:
        pool_count = get_interest_search_count(uni_id)
        if is_valentine_period():
            text = (
                "💝 Вы в режиме Valentine's мэтчинга!\n\n"
                f"Участников в пуле: {pool_count}\n"
                f"{_get_next_matching_time_str()}\n\n"
                "Алгоритм подберет тебе особенного собеседника 💕"
            )
        else:
            text = (
                "✅ Вы в режиме мэтчинга по интересам.\n\n"
                f"Участников в пуле: {pool_count}\n"
                f"{_get_next_matching_time_str()}\n\n"
                "Вы по-прежнему можете создавать заявки и откликаться на чужие в обычном режиме."
            )
        keyboard = [
            [InlineKeyboardButton("❌ Выйти из режима", callback_data="interest_exit")],
            [InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup)
        return INTEREST_MATCH_MENU

    # Не в режиме — предлагаем войти
    if not has_user_bio(user_id, uni_id):
        await update.message.reply_text(
            "Для участия в мэтчинге по интересам нужно заполнить раздел «О себе» в профиле.\n\n"
            "Перейдите в «👤 Мой профиль» и добавьте информацию о себе."
        )
        return ConversationHandler.END

    pool_count = get_interest_search_count(uni_id)
    if is_valentine_period():
        text = (
            "💝 *Мэтчинг по интересам — Valentine's Special!*\n\n"
            "В честь Дня Святого Валентина алгоритм подберет тебе "
            "особенного собеседника!\n\n"
            f"Участников в пуле: {pool_count}\n\n"
            "Войди в режим поиска — первые мэтчи уже сегодня вечером 💕"
        )
    else:
        text = (
            "🔍 *Мэтчинг по интересам*\n\n"
            "Войдите в режим поиска, и раз в 1-2 дня мы подберем вам собеседника "
            "на основе ваших интересов.\n\n"
            f"Участников в пуле: {pool_count}\n\n"
            "📊 Вы также можете продолжать создавать заявки "
            "и откликаться на чужие в обычном режиме."
        )
    keyboard = [
        [
            InlineKeyboardButton(
                "🔍 Войти в режим поиска", callback_data="interest_enter"
            )
        ],
        [InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        text, reply_markup=reply_markup, parse_mode="Markdown"
    )
    return INTEREST_MATCH_MENU


async def _show_interest_match_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE, match: dict
) -> int:
    """Показывает статус активного interest_match в меню."""
    user_id = update.effective_user.id
    partner_bio = (
        match["user_2_bio"] if match["user_1_id"] == user_id else match["user_1_bio"]
    )
    bio_excerpt = (
        (partner_bio[:150] + "...")
        if partner_bio and len(partner_bio) > 150
        else (partner_bio or "Не указано")
    )
    similarity_pct = display_similarity(match["similarity_score"])

    if match["status"] == "proposed":
        text = (
            f"🎯 У вас есть мэтч по интересам!\n\n"
            f"📝 О партнере: {bio_excerpt}\n"
            f"📊 Совместимость: {similarity_pct}%\n\n"
            f"Чтобы встреча состоялась, нужно договориться о месте и времени."
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "☕ Предложить встречу",
                    callback_data=f"interest_propose_{match['match_id']}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отклонить мэтч",
                    callback_data=f"interest_decline_{match['match_id']}",
                )
            ],
            [InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")],
        ]
    else:  # negotiating
        rounds_left = MAX_NEGOTIATION_ROUNDS - match["negotiation_round"]
        if match["proposed_by"] == user_id:
            meet_time_moscow = match["proposed_meet_time"].astimezone(MOSCOW_TIMEZONE)
            text = (
                f"⏳ Ожидание ответа партнера\n\n"
                f"📝 О партнере: {bio_excerpt}\n"
                f"Ваше предложение:\n"
                f"📍 {match['shop_name']}\n"
                f"📅 {meet_time_moscow.strftime('%d.%m')} в {meet_time_moscow.strftime('%H:%M')}\n"
                f"📊 Совместимость: {similarity_pct}%\n"
                f"🔄 Осталось попыток: {rounds_left}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "❌ Отклонить мэтч",
                        callback_data=f"interest_decline_{match['match_id']}",
                    )
                ],
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")],
            ]
        else:
            meet_time_moscow = match["proposed_meet_time"].astimezone(MOSCOW_TIMEZONE)
            text = (
                f"☕ Предложение встречи!\n\n"
                f"📝 О партнере: {bio_excerpt}\n"
                f"📍 Кофейня: {match['shop_name']}\n"
                f"📅 Дата: {meet_time_moscow.strftime('%d.%m')}\n"
                f"🕐 Время: {meet_time_moscow.strftime('%H:%M')}\n"
                f"📊 Совместимость: {similarity_pct}%\n"
                f"🔄 Осталось попыток: {rounds_left}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Принять",
                        callback_data=f"proposal_accept_{match['match_id']}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Предложить другое",
                        callback_data=f"proposal_counter_{match['match_id']}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Отклонить мэтч",
                        callback_data=f"proposal_decline_{match['match_id']}",
                    )
                ],
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")],
            ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)
    return INTEREST_MATCH_MENU


async def interest_match_enter(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Вход в режим поиска по интересам."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]

    set_interest_search(user_id, uni_id, True)
    pool_count = get_interest_search_count(uni_id)

    if is_valentine_period():
        text = (
            "💝 Вы вошли в Valentine's мэтчинг!\n\n"
            f"Участников в пуле: {pool_count}\n"
            "Алгоритм подберет тебе особенного собеседника — "
            "первые мэтчи уже сегодня вечером 💕"
        )
    else:
        text = (
            "✅ Вы вошли в режим мэтчинга по интересам!\n\n"
            f"Участников в пуле: {pool_count}\n"
            "Раз в 1-2 дня мы подберем вам собеседника на основе ваших интересов.\n\n"
            "📊 Вы также можете продолжать создавать заявки "
            "и откликаться на чужие в обычном режиме."
        )

    await query.edit_message_text(text)
    await show_main_menu_keyboard(update, context, "Главное меню:")
    return ConversationHandler.END


async def interest_match_exit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Выход из режима поиска по интересам."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]

    set_interest_search(user_id, uni_id, False)

    await query.edit_message_text(
        "Вы вышли из режима мэтчинга по интересам. Вы можете вернуться в любой момент."
    )
    await show_main_menu_keyboard(update, context, "Главное меню:")
    return ConversationHandler.END


async def interest_propose_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Начало предложения встречи (выбор кофейни). Entry point для propose и counter."""
    query = update.callback_query
    await query.answer()

    match_id = int(query.data.split("_")[-1])
    uni_id = BOT_CONFIG["university_id"]
    user_id = update.effective_user.id

    match = get_interest_match_by_id(match_id, uni_id)
    if not match or match["status"] not in ("proposed", "negotiating"):
        await query.edit_message_text("Этот мэтч больше не активен.")
        return ConversationHandler.END

    if user_id not in (match["user_1_id"], match["user_2_id"]):
        await query.edit_message_text("У вас нет доступа к этому мэтчу.")
        return ConversationHandler.END

    if match["status"] == "negotiating" and match["proposed_by"] == user_id:
        await query.edit_message_text("Ожидайте ответа партнера на ваше предложение.")
        return ConversationHandler.END

    if match["negotiation_round"] >= MAX_NEGOTIATION_ROUNDS:
        # Если есть предложение от другой стороны — показать Accept/Decline
        if match["status"] == "negotiating" and match["proposed_by"] != user_id:
            meet_time_moscow = match["proposed_meet_time"].astimezone(MOSCOW_TIMEZONE)
            text = (
                f"Достигнут лимит попыток согласования.\n\n"
                f"Вы можете принять текущее предложение партнера:\n"
                f"📍 {match['shop_name']}\n"
                f"📅 {meet_time_moscow.strftime('%d.%m')} в {meet_time_moscow.strftime('%H:%M')}\n\n"
                f"Или отклонить мэтч."
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Принять", callback_data=f"proposal_accept_{match_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Отклонить мэтч",
                        callback_data=f"proposal_decline_{match_id}",
                    )
                ],
            ]
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                "Достигнут лимит попыток согласования. "
                "Мэтч будет автоматически отменен."
            )
        return ConversationHandler.END

    context.user_data["interest_match_id"] = match_id

    shops = get_active_coffee_shops(uni_id=uni_id)
    if not shops:
        await query.edit_message_text("К сожалению, сейчас нет активных кофеен.")
        return ConversationHandler.END

    buttons = []
    for row in shops:
        shop_id = row[0]
        name = row[1]
        promo_label = row[2] if len(row) > 2 else None
        label = f"📍 {name}"
        if promo_label:
            label += f" {promo_label}"
        buttons.append((label, f"ishop_{shop_id}"))

    buttons.append(("⬅️ Назад в меню", "main_menu"))
    reply_markup = build_inline_keyboard(buttons_data=buttons)

    await query.edit_message_text(
        "Выберите кофейню для встречи:", reply_markup=reply_markup
    )
    return INTEREST_PROPOSE_SHOP


async def interest_propose_shop_selected(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Кофейня выбрана, переходим к дате."""
    query = update.callback_query
    await query.answer()

    shop_id = int(query.data.split("_")[1])
    context.user_data["interest_shop_id"] = shop_id

    back_button = build_inline_keyboard([("⬅️ Назад в меню", "main_menu")])

    await query.edit_message_text(
        "Теперь введите дату встречи в формате *ДД.ММ* (например, *25.02*).",
        reply_markup=back_button,
        parse_mode="Markdown",
    )
    return INTEREST_PROPOSE_DATE


async def interest_propose_date(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Дата введена, переходим к времени."""
    user_date_str = update.message.text

    if not re.match(r"^(\d{1,2})\.(\d{1,2})$", user_date_str):
        await update.message.reply_text(
            "Формат даты неверный. Введите как *ДД.ММ*, например: *25.02*",
            parse_mode="Markdown",
        )
        return INTEREST_PROPOSE_DATE

    try:
        now_moscow = datetime.now(MOSCOW_TIMEZONE)
        now_date = now_moscow.date()
        day, month = map(int, user_date_str.split("."))
        year = now_moscow.year
        proposed_date = datetime(year, month, day)

        if proposed_date.date() < now_date:
            year += 1
            proposed_date = datetime(year, month, day)

        if (proposed_date.date() - now_date).days > 14:
            await update.message.reply_text(
                "Выберите дату в пределах следующих 14 дней."
            )
            return INTEREST_PROPOSE_DATE
    except ValueError:
        await update.message.reply_text(
            "Такой даты не существует. Введите корректную дату."
        )
        return INTEREST_PROPOSE_DATE

    context.user_data["interest_date"] = proposed_date

    back_button = build_inline_keyboard([("⬅️ Назад в меню", "main_menu")])
    await update.message.reply_text(
        "Теперь введите время встречи (например, *14:30*).",
        reply_markup=back_button,
        parse_mode="Markdown",
    )
    return INTEREST_PROPOSE_TIME


async def interest_propose_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Время введено — отправляем предложение партнеру."""
    user_time_str = update.message.text
    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]

    time_match = re.match(r"^(\d{1,2}):(\d{1,2})$", user_time_str)
    if not time_match:
        await update.message.reply_text(
            "Формат времени неверный. Введите как *ЧЧ:ММ*, например: *14:30*",
            parse_mode="Markdown",
        )
        return INTEREST_PROPOSE_TIME

    hour, minute = int(time_match.group(1)), int(time_match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("Некорректное время. Часы: 0-23, минуты: 0-59.")
        return INTEREST_PROPOSE_TIME

    chosen_date = context.user_data.get("interest_date")
    if not chosen_date:
        await update.message.reply_text("Произошла ошибка. Попробуйте заново.")
        return ConversationHandler.END

    naive_meet_time = chosen_date.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    meet_time = naive_meet_time.replace(tzinfo=MOSCOW_TIMEZONE)

    if meet_time < datetime.now(MOSCOW_TIMEZONE):
        await update.message.reply_text(
            "Это время уже прошло! Выберите время в будущем."
        )
        return INTEREST_PROPOSE_TIME

    shop_id = context.user_data.get("interest_shop_id")
    match_id = context.user_data.get("interest_match_id")

    if not shop_id or not match_id:
        await update.message.reply_text("Произошла ошибка. Попробуйте заново.")
        return ConversationHandler.END

    working_hours = get_shop_working_hours(shop_id=shop_id, uni_id=uni_id)
    if not is_shop_open_at_time(working_hours, meet_time):
        await update.message.reply_text(
            "Кофейня в это время закрыта. Попробуйте другое время."
        )
        return INTEREST_PROPOSE_TIME

    success = propose_meeting(match_id, shop_id, meet_time, user_id, uni_id)
    if not success:
        await update.message.reply_text(
            "Не удалось отправить предложение. Возможно, мэтч уже не активен."
        )
        return ConversationHandler.END

    # Уведомляем партнера
    match = get_interest_match_by_id(match_id, uni_id)
    if match:
        partner_id = (
            match["user_2_id"] if match["user_1_id"] == user_id else match["user_1_id"]
        )
        proposer_bio = (
            match["user_1_bio"]
            if match["user_1_id"] == user_id
            else match["user_2_bio"]
        )
        proposer_bio = proposer_bio or "Не указано"
        bio_excerpt = (
            (proposer_bio[:150] + "...") if len(proposer_bio) > 150 else proposer_bio
        )
        similarity_pct = display_similarity(match["similarity_score"])
        rounds_left = MAX_NEGOTIATION_ROUNDS - match["negotiation_round"]

        meet_time_moscow = meet_time.astimezone(MOSCOW_TIMEZONE)

        proposal_text = (
            f"☕ Предложение встречи!\n\n"
            f"📝 О партнере: {bio_excerpt}\n"
            f"📍 Кофейня: {match['shop_name']}\n"
            f"📅 Дата: {meet_time_moscow.strftime('%d.%m')}\n"
            f"🕐 Время: {meet_time_moscow.strftime('%H:%M')}\n"
            f"📊 Совместимость: {similarity_pct}%\n"
            f"🔄 Осталось попыток: {rounds_left}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Принять", callback_data=f"proposal_accept_{match_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Предложить другое", callback_data=f"proposal_counter_{match_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отклонить мэтч", callback_data=f"proposal_decline_{match_id}"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=partner_id, text=proposal_text, reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to send proposal to {partner_id}: {e}")

    await update.message.reply_text("✅ Предложение отправлено партнеру!")
    await show_main_menu_keyboard(update, context, "Главное меню:")
    return ConversationHandler.END


async def handle_interest_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонение interest_match (standalone callback)."""
    query = update.callback_query
    await query.answer()

    match_id = int(query.data.split("_")[-1])
    uni_id = BOT_CONFIG["university_id"]
    user_id = update.effective_user.id

    result = decline_interest_match(match_id, uni_id)
    if result:
        await query.edit_message_text(
            "Мэтч отклонен. Вы можете вернуться в режим поиска в любой момент."
        )
        partner_id = (
            result["user_2_id"]
            if result["user_1_id"] == user_id
            else result["user_1_id"]
        )
        try:
            await context.bot.send_message(
                chat_id=partner_id,
                text="К сожалению, ваш партнер отклонил встречу. "
                "Вы можете вернуться в режим поиска по интересам.",
            )
        except Exception as e:
            logger.error(f"Failed to notify partner {partner_id} about decline: {e}")
    else:
        await query.edit_message_text("Этот мэтч больше не активен.")


async def handle_proposal_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принятие предложения встречи (standalone callback)."""
    query = update.callback_query
    await query.answer()

    match_id = int(query.data.split("_")[-1])
    uni_id = BOT_CONFIG["university_id"]

    request_id = accept_meeting_proposal(match_id, uni_id)
    if not request_id:
        await query.edit_message_text(
            "Не удалось принять предложение. Мэтч уже не активен."
        )
        return

    await query.edit_message_text("✅ Предложение принято! Встреча создана.")
    await notify_users_about_pairing(request_id=request_id, context=context)


async def handle_proposal_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонение предложения встречи (standalone callback)."""
    query = update.callback_query
    await query.answer()

    match_id = int(query.data.split("_")[-1])
    uni_id = BOT_CONFIG["university_id"]
    user_id = update.effective_user.id

    result = decline_interest_match(match_id, uni_id)
    if result:
        await query.edit_message_text("Предложение отклонено. Мэтч отменен.")
        partner_id = (
            result["user_2_id"]
            if result["user_1_id"] == user_id
            else result["user_1_id"]
        )
        try:
            await context.bot.send_message(
                chat_id=partner_id,
                text="К сожалению, ваш партнер отклонил предложение встречи. Мэтч отменен. "
                "Вы можете вернуться в режим поиска по интересам.",
            )
        except Exception as e:
            logger.error(
                f"Failed to notify partner {partner_id} about proposal decline: {e}"
            )
    else:
        await query.edit_message_text("Этот мэтч больше не активен.")


async def notify_interest_matches_job(context: ContextTypes.DEFAULT_TYPE):
    """Джоб: отправка уведомлений о новых interest_matches."""
    logger.info("JOB: checking for new interest matches to notify...")
    matches = get_new_interest_matches_for_notification(
        uni_id=BOT_CONFIG["university_id"]
    )
    if not matches:
        return

    logger.info(f"Found {len(matches)} new interest matches to notify.")

    for match in matches:
        match_id = match["match_id"]
        user_1_id = match["user_1_id"]
        user_2_id = match["user_2_id"]
        similarity_pct = display_similarity(match["similarity_score"])

        user_1_bio = match.get("user_1_bio") or "Не указано"
        user_2_bio = match.get("user_2_bio") or "Не указано"
        bio_1_excerpt = (
            (user_1_bio[:150] + "...") if len(user_1_bio) > 150 else user_1_bio
        )
        bio_2_excerpt = (
            (user_2_bio[:150] + "...") if len(user_2_bio) > 150 else user_2_bio
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "☕ Предложить встречу",
                    callback_data=f"interest_propose_{match_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отклонить мэтч", callback_data=f"interest_decline_{match_id}"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if is_valentine_period():
            text_1 = (
                f"💝 Романтический мэтч!\n\n"
                f"Мы нашли тебе особенного собеседника!\n\n"
                f"📝 О партнере: {bio_2_excerpt}\n"
                f"📊 Совместимость: {similarity_pct}%\n\n"
                f"Самое время договориться о встрече 💕"
            )
            text_2 = (
                f"💝 Романтический мэтч!\n\n"
                f"Мы нашли тебе особенного собеседника!\n\n"
                f"📝 О партнере: {bio_1_excerpt}\n"
                f"📊 Совместимость: {similarity_pct}%\n\n"
                f"Самое время договориться о встрече 💕"
            )
        else:
            text_1 = (
                f"🎯 Мэтчинг по интересам!\n\n"
                f"Мы нашли для вас собеседника с похожими интересами!\n\n"
                f"📝 О партнере: {bio_2_excerpt}\n"
                f"📊 Совместимость: {similarity_pct}%\n\n"
                f"Чтобы встреча состоялась, нужно договориться о месте и времени.\n"
                f"Один из вас предложит детали встречи, а другой подтвердит."
            )
            text_2 = (
                f"🎯 Мэтчинг по интересам!\n\n"
                f"Мы нашли для вас собеседника с похожими интересами!\n\n"
                f"📝 О партнере: {bio_1_excerpt}\n"
                f"📊 Совместимость: {similarity_pct}%\n\n"
                f"Чтобы встреча состоялась, нужно договориться о месте и времени.\n"
                f"Один из вас предложит детали встречи, а другой подтвердит."
            )

        try:
            await context.bot.send_message(
                chat_id=user_1_id, text=text_1, reply_markup=reply_markup
            )
            await context.bot.send_message(
                chat_id=user_2_id, text=text_2, reply_markup=reply_markup
            )
            logger.info(f"Sent interest match notification for match_id: {match_id}")
        except Exception as e:
            logger.error(
                f"Failed to send interest match notification for match_id {match_id}: {e}"
            )


async def remind_interest_proposals_job(context: ContextTypes.DEFAULT_TYPE):
    """Джоб: напоминание партнеру, который не ответил на предложение встречи (>6 часов)."""
    logger.info("JOB: checking for stale interest proposals to remind...")
    uni_id = BOT_CONFIG["university_id"]
    stale = get_stale_interest_proposals(uni_id)
    if not stale:
        return

    logger.info(f"Found {len(stale)} stale proposals to send reminders.")

    for row in stale:
        match_id = row["match_id"]
        user_1_id = row["user_1_id"]
        user_2_id = row["user_2_id"]
        proposed_by = row["proposed_by"]
        shop_name = row["shop_name"]
        meet_time = row["proposed_meet_time"]

        # Партнер, который должен ответить — тот, кто НЕ предложил
        recipient_id = user_2_id if proposed_by == user_1_id else user_1_id

        meet_time_moscow = meet_time.astimezone(MOSCOW_TIMEZONE)

        text = (
            f"⏰ Напоминание: вам предложили встречу "
            f"в «{shop_name}» ({meet_time_moscow.strftime('%d.%m')} "
            f"в {meet_time_moscow.strftime('%H:%M')}).\n\n"
            f"Партнер ждет вашего ответа."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Принять", callback_data=f"proposal_accept_{match_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Предложить другое", callback_data=f"proposal_counter_{match_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отклонить мэтч", callback_data=f"proposal_decline_{match_id}"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=recipient_id, text=text, reply_markup=reply_markup
            )
            mark_proposal_reminder_sent(match_id, uni_id)
            logger.info(
                f"Sent proposal reminder for match_id={match_id} to user {recipient_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to send proposal reminder for match_id {match_id}: {e}"
            )


async def expire_interest_matches_job(context: ContextTypes.DEFAULT_TYPE):
    """Джоб: экспирация interest_matches по таймаутам."""
    logger.info("JOB: checking for expired interest matches...")
    expired = expire_interest_matches(uni_id=BOT_CONFIG["university_id"])
    if not expired:
        return

    logger.info(f"Expired {len(expired)} interest matches.")

    for match_id, user_1_id, user_2_id in expired:
        text = (
            "⏰ Время на согласование встречи истекло.\n\n"
            "Вы можете вернуться в режим поиска по интересам."
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔍 Вернуться в поиск", callback_data="interest_reenter"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.send_message(
                chat_id=user_1_id, text=text, reply_markup=reply_markup
            )
            await context.bot.send_message(
                chat_id=user_2_id, text=text, reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(
                f"Failed to notify about expired interest match {match_id}: {e}"
            )


async def handle_interest_reenter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повторный вход в режим поиска по интересам (из кнопки после экспирации/отклонения)."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    uni_id = BOT_CONFIG["university_id"]

    if not has_user_bio(user_id, uni_id):
        await query.edit_message_text(
            "Для участия в мэтчинге по интересам нужно заполнить раздел «О себе» в профиле.\n\n"
            "Перейдите в «👤 Мой профиль» и добавьте информацию о себе."
        )
        return

    # Проверяем, нет ли уже активного мэтча
    active_match = get_pending_interest_match(user_id, uni_id)
    if active_match:
        await query.edit_message_text(
            "У вас уже есть активный мэтч по интересам. "
            "Откройте «🔍 Мэтчинг по интересам» для подробностей."
        )
        return

    set_interest_search(user_id, uni_id, True)
    pool_count = get_interest_search_count(uni_id)

    await query.edit_message_text(
        f"✅ Вы снова в режиме поиска!\n\n"
        f"Участников в пуле: {pool_count}\n"
        f"{_get_next_matching_time_str()}"
    )


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def back_to_main_menu_from_anywhere(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Fallback для кнопок меню, не являющихся entry_point'ами (сейчас только "ℹ️ Гайд").
    Все остальные кнопки меню — entry_points с allow_reentry=True,
    поэтому переключение между режимами происходит напрямую.
    """
    await show_main_menu_keyboard(update, context, "Главное меню:")
    return ConversationHandler.END


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to configuration file", required=True)
    args = parser.parse_args()

    global BOT_CONFIG
    BOT_CONFIG = load_config(args.config)

    init_db_pool()

    token_env_key = BOT_CONFIG.get("bot_token_env")
    token = os.getenv(token_env_key)

    if not token:
        logger.error(f"Token not found in env variable: {token_env_key}")
        return

    app = Application.builder().token(token).post_init(post_init).build()

    find_handler = MessageHandler(
        filters.Regex("^☕️ Найти компанию$"), find_company_start
    )
    my_requests_handler = MessageHandler(
        filters.Regex("^📂 Мои заявки$"), my_requests_start
    )
    my_profile_handler = MessageHandler(
        filters.Regex("^👤 Мой профиль$"), my_profile_start
    )

    # Фильтр, который ловит любую главную кнопку меню для выхода из текущего состояния
    MENU_BUTTONS_FILTER = filters.Regex(
        "^(☕️ Найти компанию|📂 Мои заявки|👤 Мой профиль|ℹ️ Гайд|🔍 Мэтчинг по интересам)$"
    )

    # 2. Сценарий регистрации
    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            # Исключаем кнопки меню из ввода, чтобы срабатывал fallback
            REGISTER_SCHOOL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    register_school,
                )
            ],
            REGISTER_YEAR: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    register_year,
                )
            ],
            REGISTER_GENDER: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    register_gender,
                )
            ],
            REGISTER_BIO: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, register_bio
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_BUTTONS_FILTER, back_to_main_menu_from_anywhere),
        ],
        allow_reentry=True,
    )

    # 3. Единый ConversationHandler для всех режимов (кроме регистрации)
    #
    # Все entry_points в одном handler'e + allow_reentry=True означает,
    # что нажатие любой кнопки меню НАПРЯМУЮ переключает режим
    # без промежуточного "Главное меню:".
    interest_match_handler = MessageHandler(
        filters.Regex("^🔍 Мэтчинг по интересам$"), interest_match_menu
    )
    conv_handler = ConversationHandler(
        entry_points=[
            find_handler,
            my_requests_handler,
            my_profile_handler,
            interest_match_handler,
            CallbackQueryHandler(interest_propose_start, pattern="^interest_propose_"),
            CallbackQueryHandler(interest_propose_start, pattern="^proposal_counter_"),
        ],
        states={
            # --- Поиск кофе ---
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
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    create_request_step3_time,
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            CHOOSING_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    create_request_step4_validate,
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
            CHOOSING_REQUEST: [
                CallbackQueryHandler(handle_accept_request, pattern="^accept_"),
                CallbackQueryHandler(
                    create_request_step1_shop, pattern="^create_new_request$"
                ),
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
            # --- Профиль ---
            EDITING_PROFILE: [
                CallbackQueryHandler(edit_bio_prompt, pattern="^edit_bio$"),
                CallbackQueryHandler(edit_gender_prompt, pattern="^edit_gender$"),
                CallbackQueryHandler(edit_gender_save, pattern="^profile_gender_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            EDITING_BIO: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    edit_bio_save,
                )
            ],
            # --- Мэтчинг по интересам ---
            INTEREST_MATCH_MENU: [
                CallbackQueryHandler(interest_match_enter, pattern="^interest_enter$"),
                CallbackQueryHandler(interest_match_exit, pattern="^interest_exit$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            INTEREST_PROPOSE_SHOP: [
                CallbackQueryHandler(interest_propose_shop_selected, pattern="^ishop_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            INTEREST_PROPOSE_DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    interest_propose_date,
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            INTEREST_PROPOSE_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER,
                    interest_propose_time,
                ),
                CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            ],
            # --- Gender gate ---
            GENDER_GATE: [
                CallbackQueryHandler(handle_gender_gate, pattern="^set_gender_"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            # Ловит "ℹ️ Гайд" — единственную кнопку меню, не являющуюся entry_point
            MessageHandler(MENU_BUTTONS_FILTER, back_to_main_menu_from_anywhere),
        ],
        allow_reentry=True,
    )

    # Добавляем в строгом порядке
    app.add_handler(registration_conv)
    app.add_handler(conv_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Гайд$"), help_command))

    app.add_handler(
        CallbackQueryHandler(skip_feedback_handler, pattern="^skip_feedback$")
    )
    app.add_handler(CallbackQueryHandler(handle_feedback, pattern="^feedback_"))

    app.add_handler(
        CallbackQueryHandler(handle_confirmation_button, pattern="^confirm_presence_")
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_cancel_request_as_creator, pattern="^cancel_matched_"
        )
    )

    # Standalone callbacks для мэтчинга по интересам (не требуют multi-step flow)
    app.add_handler(
        CallbackQueryHandler(handle_interest_decline, pattern="^interest_decline_")
    )
    app.add_handler(
        CallbackQueryHandler(handle_proposal_accept, pattern="^proposal_accept_")
    )
    app.add_handler(
        CallbackQueryHandler(handle_proposal_decline, pattern="^proposal_decline_")
    )
    app.add_handler(
        CallbackQueryHandler(handle_interest_reenter, pattern="^interest_reenter$")
    )

    feedback_filter = filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER
    app.add_handler(MessageHandler(feedback_filter, process_feedback_text))

    async def error_handler(
        _update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if isinstance(context.error, NetworkError):
            logger.warning(f"Transient network error (PTB will retry): {context.error}")
            return
        logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)

    app.add_error_handler(error_handler)

    logger.info(
        "Starting the bot. Reference to bot: https://t.me/random_coffee_mipt_bot"
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
