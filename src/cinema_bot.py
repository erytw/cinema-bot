"""
Telegram bot for searching movies and TV shows information.

This module implements a Telegram bot that allows users to search for information
about movies and TV shows, view ratings, get streaming links, and track their
search history.
"""

import asyncio
import logging
import sys
import html
from os import getenv
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, URLInputFile, CallbackQuery, InputMediaPhoto
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.movie_searcher import MovieSearcher
from src.db import init_db, add_search, get_user_history, get_user_stats

BOT_TOKEN = getenv("BOT_TOKEN")
KINOPOISK_UNOFFICIAL_API_KEY = getenv("KINOPOISK_UNOFFICIAL_API_KEY")

MOVIE_CAP: int | str | None = getenv("MOVIE_CAP")
if MOVIE_CAP and MOVIE_CAP.isnumeric():
    MOVIE_CAP = int(MOVIE_CAP)
else:
    MOVIE_CAP = 3

LINK_CAP: int | str | None = getenv("LINK_CAP")
if LINK_CAP and LINK_CAP.isnumeric():
    LINK_CAP = int(LINK_CAP)
else:
    LINK_CAP = 1


searcher = MovieSearcher(
    KINOPOISK_UNOFFICIAL_API_KEY, LINK_CAP, MOVIE_CAP, logging.getLogger("searcher")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
async_session, init_tables = init_db()


# Define states for pagination
class PaginationStates(StatesGroup):
    """
    State group for managing pagination in different views.

    Attributes:
        history: State for history pagination
        stats: State for statistics pagination
        search: State for search results pagination
    """

    history = State()
    stats = State()
    search = State()


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """
    This handler receives messages with `/start` command
    """
    start_message = """Привет\\! Я бот, который поможет тебе найти фильмы и сериалы\\. 🎬🍿
Просто напиши мне название фильма или сериала, и я предоставлю тебе информацию о нем,
рейтинг 🌟 и ссылки для просмотра 🔗\\.
Используй /help, чтобы узнать больше о моих возможностях\\."""
    await message.answer(start_message, parse_mode=ParseMode.MARKDOWN_V2)


@dp.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """
    This handler receives messages with `/help` command
    """
    help_message = """Я бот для поиска информации о фильмах и сериалах\\.

**Доступные команды:**
\\- /start: Начать работу с ботом и получить приветствие\\! 👋
\\- /help: Получить информацию о том, как использовать бота\\. ℹ️
\\- /history: Показать историю твоих поисковых запросов\\. 📜
\\- /stats: Показать статистику фильмов, которые ты искал\\. 📊

Просто отправь мне название фильма или сериала, и я предоставлю тебе информацию о нем, рейтинг 🌟 и ссылки для просмотра 🔗\\.

**Пример использования:**
Чтобы найти информацию о фильме "Interstellar", просто отправь мне сообщение "Interstellar"\\.

Приятного просмотра\\! 😊"""
    await message.answer(help_message, parse_mode=ParseMode.MARKDOWN_V2)


@dp.message(Command("history"))
async def command_history_handler(message: Message, state: FSMContext) -> None:
    """
    This handler receives messages with `/history` command and shows the user's search history.
    """
    await state.set_state(PaginationStates.history)
    await state.update_data(page=0)

    await show_history_page(message, state)


async def show_history_page(message_or_query, state: FSMContext):
    """
    Show a page of user history with pagination controls
    """
    data = await state.get_data()
    page = data.get("page", 0)

    is_callback = isinstance(message_or_query, CallbackQuery)
    user_id = message_or_query.from_user.id

    page_size = 20

    async with async_session() as session:
        all_history = await get_user_history(session, user_id, None)

    if not all_history:
        text = "Твоя история поисков пуста. Начни искать фильмы! 🎥"
        if is_callback:
            await message_or_query.message.edit_text(text, parse_mode=ParseMode.HTML)
            await message_or_query.answer()
        else:
            await message_or_query.answer(text, parse_mode=ParseMode.HTML)
        return

    total_items = len(all_history)
    total_pages = (total_items + page_size - 1) // page_size

    page = max(0, min(page, total_pages - 1))
    await state.update_data(page=page)

    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_items)
    page_items = all_history[start_idx:end_idx]

    history_message = (
        f"<b>Твоя история поисков (страница {page + 1}/{total_pages}):</b>\n"
    )
    for i, search in enumerate(page_items, start_idx + 1):
        if search.film_name and search.film_year:
            history_message += (
                f"{i}. {html.escape(search.film_name)} ({search.film_year}) - "
                f"{search.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        else:
            history_message += (
                f"{i}. Не найдено ({html.escape(search.query)}) - "
                f"{search.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )

    keyboard = []
    row = []

    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data="history_prev"))

    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data="history_next"))

    if row:
        keyboard.append(row)

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

    if is_callback:
        await message_or_query.message.edit_text(
            history_message, parse_mode=ParseMode.HTML, reply_markup=markup
        )
        await message_or_query.answer()
    else:
        await message_or_query.answer(
            history_message, parse_mode=ParseMode.HTML, reply_markup=markup
        )


@dp.message(Command("stats"))
async def command_stats_handler(message: Message, state: FSMContext) -> None:
    """
    This handler receives messages with `/stats` command and shows statistics on films suggested to the user.
    """
    await state.set_state(PaginationStates.stats)
    await state.update_data(page=0)

    await show_stats_page(message, state)


async def show_stats_page(message_or_query, state: FSMContext):
    """
    Show a page of film stats with pagination controls
    """
    data = await state.get_data()
    page = data.get("page", 0)

    is_callback = isinstance(message_or_query, CallbackQuery)
    user_id = message_or_query.from_user.id

    # Get stats
    page_size = 20

    async with async_session() as session:
        user_stats = await get_user_stats(session, user_id)

    if not user_stats:
        text = "У тебя нет статистики просмотров. Начни искать фильмы! 🎥"
        if is_callback:
            await message_or_query.message.edit_text(text, parse_mode=ParseMode.HTML)
            await message_or_query.answer()
        else:
            await message_or_query.answer(text, parse_mode=ParseMode.HTML)
        return

    total_items = len(user_stats)
    total_pages = (total_items + page_size - 1) // page_size

    page = max(0, min(page, total_pages - 1))
    await state.update_data(page=page)

    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_items)
    page_items = user_stats[start_idx:end_idx]

    page_info = f"{page + 1}/{total_pages}"
    stats_message = f"<b>Статистика фильмов (страница {page_info}):</b>\n"
    for i, (film_name, film_year, count) in enumerate(page_items, start_idx + 1):
        stats_message += (
            f"{i}. {html.escape(film_name)} ({film_year}) - "
            f"показан {count} раз(а)\n"
        )

    keyboard = []
    row = []

    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data="stats_prev"))

    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data="stats_next"))

    if row:
        keyboard.append(row)

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

    if is_callback:
        await message_or_query.message.edit_text(
            stats_message, parse_mode=ParseMode.HTML, reply_markup=markup
        )
        await message_or_query.answer()
    else:
        await message_or_query.answer(
            stats_message, parse_mode=ParseMode.HTML, reply_markup=markup
        )


@dp.message()
async def movie_search_handler(message: Message, state: FSMContext) -> None:
    """Handle movie search requests."""
    logging.info(
        "Received message: %s from %s(%s)",
        message.text,
        message.from_user.full_name,
        message.from_user.id,
    )
    user_id = message.from_user.id  # type: ignore
    query = message.text.strip()  # type: ignore

    film_info: list[dict] = []
    try:
        film_info = await searcher.fetch_movies(query)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logging.error("API request error: %s", repr(e))
        film_info = [{"error": "Не удалось обработать запрос"}]
    except ValueError as e:
        logging.error("Value error: %s", repr(e))
        film_info = [{"error": "Некорректные данные запроса"}]
    except Exception as e:
        logging.error("Unexpected error: %s", repr(e))
        film_info = [{"error": "Произошла неизвестная ошибка"}]

    await state.set_state(PaginationStates.search)
    await state.update_data(page=0, query=query, film_info=film_info)

    await show_search_page(message, state)

    async with async_session() as session:
        await add_search(session, user_id, query)


async def get_film_caption(film):
    """Generate caption text for a film."""
    if "error" in film:
        return film["error"]

    caption = ""
    film_name = film.get("nameRu", film.get("nameEn", None))
    film_year = film.get("year", None)
    film_rating = film.get("rating", None)
    film_description = film.get("description", None)
    links = await searcher.fetch_movie_links(film)

    if film_name and film_year:
        film_id = film.get("filmId")
        if film_id:
            kinopoisk_url = f"https://www.kinopoisk.ru/film/{film_id}/"
            caption += (
                f'<a href="{kinopoisk_url}">'
                f"<b>{html.escape(film_name)} ({film_year})</b></a>\n"
            )
        else:
            caption += f"<b>{html.escape(film_name)} ({film_year})</b>\n"
    if film_rating and film_rating != "null":
        caption += f"<b>Рейтинг</b>: {film_rating}\n"

    max_description_length = 1024 - len(caption) - sum(len(link) for link in links)
    if film_description:
        caption += f"<blockquote>{html.escape(film_description[:max_description_length])}</blockquote>\n"

    for link in links:
        caption += f'<a href="{link}">🔗Посмотреть</a>\n'

    return caption, film_name, film_year


def create_pagination_keyboard(page, total_pages, prefix="search"):
    """Create pagination keyboard for navigation."""
    keyboard = []
    row = []

    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}_prev"))

    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}_next"))

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


async def show_search_page(message_or_query, state: FSMContext):
    """
    Show search results with pagination controls
    """
    user_id = message_or_query.from_user.id  # type: ignore
    data = await state.get_data()
    page = data.get("page", 0)
    query = data.get("query", "")
    film_info = data.get("film_info", [])

    is_callback = isinstance(message_or_query, CallbackQuery)

    if not film_info:
        text = "Фильм не найден!"
        if is_callback:
            await message_or_query.message.edit_text(text, parse_mode=ParseMode.HTML)
            await message_or_query.answer()
        else:
            await message_or_query.answer(text, parse_mode=ParseMode.HTML)
        async with async_session() as session:
            await add_search(session, user_id, query)
        return

    total_pages = len(film_info)
    page = max(0, min(page, total_pages - 1))
    await state.update_data(page=page)

    film = film_info[page]
    caption, film_name, film_year = await get_film_caption(film)
    logging.info(
        "Found: %s (%s) for %s",
        film_name,
        film_year,
        message_or_query.from_user.full_name,
    )
    markup = create_pagination_keyboard(page, total_pages)

    film_poster = film.get("posterUrlPreview", "")
    if is_callback and film_poster:
        await message_or_query.message.edit_media(
            media=InputMediaPhoto(
                media=URLInputFile(film_poster),
                caption=caption,
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=markup,
        )
    elif is_callback:
        await message_or_query.message.edit_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    elif film_poster:
        await message_or_query.answer_photo(
            photo=URLInputFile(film_poster),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    else:
        await message_or_query.answer(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    async with async_session() as session:
        await add_search(session, user_id, query, film_name, film_year)


@dp.callback_query(lambda c: c.data == "history_prev")
async def history_prev_page(callback_query: CallbackQuery, state: FSMContext):
    """Handle previous page navigation in history view."""
    data = await state.get_data()
    page = data.get("page", 0)
    await state.update_data(page=max(0, page - 1))
    await show_history_page(callback_query, state)


@dp.callback_query(lambda c: c.data == "history_next")
async def history_next_page(callback_query: CallbackQuery, state: FSMContext):
    """Handle next page navigation in history view."""
    data = await state.get_data()
    page = data.get("page", 0)
    await state.update_data(page=page + 1)
    await show_history_page(callback_query, state)


@dp.callback_query(lambda c: c.data == "stats_prev")
async def stats_prev_page(callback_query: CallbackQuery, state: FSMContext):
    """Handle previous page navigation in stats view."""
    data = await state.get_data()
    page = data.get("page", 0)
    await state.update_data(page=max(0, page - 1))
    await show_stats_page(callback_query, state)


@dp.callback_query(lambda c: c.data == "stats_next")
async def stats_next_page(callback_query: CallbackQuery, state: FSMContext):
    """Handle next page navigation in stats view."""
    data = await state.get_data()
    page = data.get("page", 0)
    await state.update_data(page=page + 1)
    await show_stats_page(callback_query, state)


@dp.callback_query(lambda c: c.data == "search_prev")
async def search_prev_page(callback_query: CallbackQuery, state: FSMContext):
    """Handle previous page navigation in search results."""
    data = await state.get_data()
    page = data.get("page", 0)
    await state.update_data(page=max(0, page - 1))
    await show_search_page(callback_query, state)


@dp.callback_query(lambda c: c.data == "search_next")
async def search_next_page(callback_query: CallbackQuery, state: FSMContext):
    """Handle next page navigation in search results."""
    data = await state.get_data()
    page = data.get("page", 0)
    await state.update_data(page=page + 1)
    await show_search_page(callback_query, state)


async def main() -> None:
    """Start the bot."""
    await init_tables()

    bot = Bot(
        token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )  # type: ignore
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
