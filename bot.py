import asyncio
import datetime as dt
import html
import json
import logging
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import *
from banner_utils import resolve_banner, send_banner
from db import *
from localization import *
from menus import *
from admin_panel import *
from tg_utils import safe_edit_text

STORAGE_ROOT = Path("storage").resolve()
STORAGE_CONFIGS_DIR = STORAGE_ROOT / "configs"
STORAGE_PROOFS_DIR = STORAGE_ROOT / "proofs"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
STORAGE_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_PROOFS_DIR.mkdir(parents=True, exist_ok=True)

_LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
if LOG_ENABLED:
    logging.basicConfig(
        level=_LOG_LEVEL_MAP.get(LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
else:
    logging.disable(logging.CRITICAL)
logger = logging.getLogger("notch_bot")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
MAX_PROOF_MEDIA = 3
VIDEO_PROOF_WAIT_SEC = 1.2
VIDEO_PROOF_BUFFER = {}
VIDEO_PROOF_TASKS = {}
LIST_PAGE_SIZE = 10

class Flow(StatesGroup):
    waiting_config = State()
    waiting_visibility = State()
    waiting_before = State()
    waiting_device_add = State()
    waiting_admin_reject_reason = State()

def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return cleaned[:120] or "file.bin"

async def send_main_menu(message: Message, lang: str) -> None:
    if is_admin(message.from_user.id):
        try:
            await message.answer(t(lang, "menu_admin"), reply_markup=admin_keyboard(lang))
        except Exception:
            logger.exception("Failed to send admin keyboard for user_id=%s", getattr(message.from_user, "id", None))
    sent = await send_banner(
        message,
        "main_menu",
        lang=lang,
        logger=logger,
        caption=t(lang, "welcome"),
        reply_markup=main_menu(lang),
    )
    if not sent:
        await message.answer(t(lang, "welcome"), reply_markup=main_menu(lang))


async def _show_section(
    query: CallbackQuery,
    lang: str,
    text: str,
    reply_markup,
    stem: str | None = None,
    aliases: list[str] | None = None,
    parse_mode: str | None = None,
) -> None:
    if stem:
        hit = resolve_banner(stem, lang=lang, aliases=aliases)
        if hit:
            path, kind = hit
            try:
                media = (
                    InputMediaPhoto(media=FSInputFile(path), caption=text, parse_mode=parse_mode)
                    if kind == "photo"
                    else InputMediaVideo(media=FSInputFile(path), caption=text, parse_mode=parse_mode)
                )
                await query.message.edit_media(media=media, reply_markup=reply_markup)
                return
            except TelegramBadRequest as exc:
                lowered = str(exc).lower()
                if "message is not modified" in lowered:
                    return
                can_fallback = (
                    "there is no media in the message to edit" in lowered
                    or "message can't be edited" in lowered
                    or "bad request: message to edit not found" in lowered
                )
                if not can_fallback:
                    logger.exception("Failed to edit section media '%s': %s", stem, exc)
                sent = await send_banner(
                    query.message,
                    stem,
                    lang=lang,
                    aliases=aliases,
                    logger=logger,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                if sent:
                    try:
                        await query.message.edit_reply_markup(reply_markup=None)
                    except Exception:
                        pass
                    return
    await safe_edit_text(query.message, text, reply_markup=reply_markup, parse_mode=parse_mode)

def _format_settings(cfg: dict) -> str:
    mode_id = int(cfg.get("mode_id", 0))
    lines = [f"mode_id: {mode_id}"]
    if mode_id != 0:
        lines.append(f"offset_x: {cfg.get('offset_x', '0')}")
        lines.append(f"offset_y: {cfg.get('offset_y', '0')}")
        lines.append(f"width: {cfg.get('width', '0')}")
        lines.append(f"height: {cfg.get('height', '0')}")
    return "\n".join(lines)

def _author_view(lang: str, author_label: str, is_anonymous: int, user_id: int) -> str:
    if int(is_anonymous):
        return html.escape(t(lang, "author_hidden"))
    raw = str(author_label or "").strip()
    if raw.startswith("@"):
        return html.escape(raw)
    name = raw if raw else str(user_id)
    safe_name = html.escape(name)
    if user_id > 0:
        return f'<a href="tg://user?id={int(user_id)}">{safe_name}</a>'
    return safe_name

def _cleanup_submission_proofs_files(sub) -> None:
    paths = {str(sub[12] or "").strip(), str(sub[14] or "").strip()}
    for raw_path in paths:
        if not raw_path:
            continue
        try:
            p = Path(raw_path)
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            continue

async def _save_media_proof(message: Message, sub_id: int) -> tuple[Path, str] | None:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if message.video:
        tg_file = await bot.get_file(message.video.file_id)
        path = STORAGE_PROOFS_DIR / f"proof_{sub_id}_{stamp}.mp4"
        await bot.download_file(tg_file.file_path, destination=path)
        return path, "video"
    if message.photo:
        photo = message.photo[-1]
        tg_file = await bot.get_file(photo.file_id)
        path = STORAGE_PROOFS_DIR / f"proof_{sub_id}_{stamp}.jpg"
        await bot.download_file(tg_file.file_path, destination=path)
        return path, "photo"
    return None

async def _finalize_submission_from_proofs(message: Message, state: FSMContext, lang: str, sub_id: int) -> None:
    sub = await get_submission(sub_id)
    await state.clear()
    await message.answer(t(lang, "sent_to_moderation"))
    await send_main_menu(message, lang)
    if sub:
        await notify_admins_pending(bot, sub_id, str(sub[6] or ""), str(sub[5] or ""))

async def _render_recent_configs(query: CallbackQuery, lang: str, page: int) -> None:
    page = max(0, int(page))
    offset = page * LIST_PAGE_SIZE
    rows = await list_recent_configs_page(LIST_PAGE_SIZE + 1, offset)
    if not rows and page > 0:
        await _render_recent_configs(query, lang, page - 1)
        return
    if not rows:
        await safe_edit_text(query.message,t(lang, "empty"), reply_markup=configs_menu(lang))
        return
    has_next = len(rows) > LIST_PAGE_SIZE
    page_rows = rows[:LIST_PAGE_SIZE]
    lines = [t(lang, "cfg_list_header")]
    cfg_ids: list[int] = []
    for cid, brand, model, approved_at, author, anon, user_id in page_rows:
        cfg_ids.append(int(cid))
        author_view = _author_view(lang, str(author), int(anon), int(user_id or 0))
        lines.append(f"#{cid} | {html.escape(str(brand))} {html.escape(str(model))} | {author_view}")
    await safe_edit_text(query.message,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=configs_result_menu(
            lang,
            cfg_ids,
            "menu:configs",
            page=page,
            page_callback_prefix="cfg:recent_page",
            has_prev=page > 0,
            has_next=has_next,
        ),
    )

async def _render_brand_configs(query: CallbackQuery, lang: str, brand_idx: int, page: int) -> None:
    page = max(0, int(page))
    brands = await list_brands()
    if brand_idx < 0 or brand_idx >= len(brands):
        await query.answer()
        return
    brand = str(brands[brand_idx])
    offset = page * LIST_PAGE_SIZE
    rows = await list_configs_by_brand_page(brand, LIST_PAGE_SIZE + 1, offset)
    if not rows and page > 0:
        await _render_brand_configs(query, lang, brand_idx, page - 1)
        return
    if not rows:
        await safe_edit_text(query.message,t(lang, "brand_empty"), reply_markup=configs_menu(lang))
        return
    has_next = len(rows) > LIST_PAGE_SIZE
    page_rows = rows[:LIST_PAGE_SIZE]
    lines = [t(lang, "cfg_list_header")]
    cfg_ids: list[int] = []
    for cid, model, approved_at, author, anon, user_id in page_rows:
        cfg_ids.append(int(cid))
        author_view = _author_view(lang, str(author), int(anon), int(user_id or 0))
        lines.append(f"#{cid} | {html.escape(str(model))} | {author_view}")
    await safe_edit_text(query.message,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=configs_result_menu(
            lang,
            cfg_ids,
            "cfg:brand",
            page=page,
            page_callback_prefix=f"cfg:brand_cfg:{brand_idx}",
            has_prev=page > 0,
            has_next=has_next,
        ),
    )

async def _render_device_configs(query: CallbackQuery, lang: str, user_id: int, page: int) -> None:
    page = max(0, int(page))
    devices = await list_user_devices(user_id)
    if not devices:
        await safe_edit_text(query.message,t(lang, "my_devices_empty"), reply_markup=devices_menu(lang))
        return
    offset = page * LIST_PAGE_SIZE
    rows = await list_configs_for_models_page(devices, LIST_PAGE_SIZE + 1, offset)
    if not rows and page > 0:
        await _render_device_configs(query, lang, user_id, page - 1)
        return
    if not rows:
        await safe_edit_text(query.message,t(lang, "devices_search_empty"), reply_markup=devices_menu(lang))
        return
    has_next = len(rows) > LIST_PAGE_SIZE
    page_rows = rows[:LIST_PAGE_SIZE]
    lines = [t(lang, "devices_search_header")]
    cfg_ids: list[int] = []
    for cid, brand, model, approved_at, author, anon, user_id in page_rows:
        cfg_ids.append(int(cid))
        author_view = _author_view(lang, str(author), int(anon), int(user_id or 0))
        lines.append(f"#{cid} | {html.escape(str(brand))} {html.escape(str(model))} | {author_view}")
    await safe_edit_text(query.message,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=configs_result_menu(
            lang,
            cfg_ids,
            "menu:devices",
            page=page,
            page_callback_prefix="dev:check_page",
            has_prev=page > 0,
            has_next=has_next,
        ),
    )


def _video_proof_key(message: Message, sub_id: int) -> tuple[int, int, int]:
    return (int(message.chat.id), int(message.from_user.id), int(sub_id))

async def _finalize_video_proofs(key: tuple[int, int, int], state: FSMContext, lang: str) -> None:
    await asyncio.sleep(VIDEO_PROOF_WAIT_SEC)
    pack = VIDEO_PROOF_BUFFER.pop(key, None)
    VIDEO_PROOF_TASKS.pop(key, None)
    if not pack:
        return
    items: list[Message] = list(pack.get("items", []))
    if not items:
        return
    items.sort(key=lambda m: int(m.message_id))
    last_message = items[-1]
    chat_id, _, sub_id = key
    before = await _save_media_proof(items[0], sub_id)
    after = await _save_media_proof(items[1], sub_id) if len(items) >= 2 else None
    if not before:
        await bot.send_message(chat_id, t(lang, "proof_required"))
        return
    await set_submission_before(sub_id, str(before[0]), str(before[1]))
    if after:
        await set_submission_after(sub_id, str(after[0]), str(after[1]))
    await _finalize_submission_from_proofs(last_message, state, lang, sub_id)

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    logger.info("cmd_start received user_id=%s chat_id=%s", getattr(message.from_user, "id", None), message.chat.id)
    await state.clear()
    lang = await resolve_lang(message.from_user.id, message.from_user.language_code)
    await send_main_menu(message, lang)

@dp.message(Command("lang"))
async def cmd_lang(message: Message) -> None:
    lang = await resolve_lang(message.from_user.id, message.from_user.language_code)
    await message.answer(t(lang, "lang_pick"), reply_markup=lang_keyboard())

@dp.callback_query(F.data.startswith("lang:"))
async def cb_lang(query: CallbackQuery) -> None:
    new_lang = LANG_RU if query.data == "lang:ru" else LANG_EN
    await set_lang(query.from_user.id, new_lang)
    await safe_edit_text(query.message,t(new_lang, "lang_set"))
    await query.answer()

@dp.callback_query(F.data == "menu:main")
async def cb_main_menu(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await _show_section(
        query,
        lang,
        t(lang, "welcome"),
        main_menu(lang),
        stem="main_menu",
    )
    await query.answer()

@dp.callback_query(F.data == "menu:configs")
async def cb_menu_configs(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await _show_section(
        query,
        lang,
        t(lang, "configs_title"),
        configs_menu(lang),
        stem="configs",
    )
    await query.answer()

@dp.callback_query(F.data == "menu:devices")
async def cb_menu_devices(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await _show_section(
        query,
        lang,
        t(lang, "devices_title"),
        devices_menu(lang),
        stem="my _devices",
        aliases=["my_devices", "devices"],
    )
    await query.answer()

@dp.callback_query(F.data == "menu:suggest")
async def cb_menu_suggest(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.waiting_config)
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await safe_edit_text(query.message,
        t(lang, "suggest_send_config"),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t(lang, "menu_back"), callback_data="menu:main")]]
        ),
    )
    await query.answer()

@dp.callback_query(F.data == "cfg:recent")
async def cb_cfg_recent(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await _render_recent_configs(query, lang, 0)
    await query.answer()


@dp.callback_query(F.data.startswith("cfg:recent_page:"))
async def cb_cfg_recent_page(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    raw_page = query.data.split(":")[-1]
    if not raw_page.isdigit():
        await query.answer()
        return
    await _render_recent_configs(query, lang, int(raw_page))
    await query.answer()

@dp.callback_query(F.data == "cfg:brand")
async def cb_cfg_brand(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    brands = await list_brands()
    if not brands:
        await safe_edit_text(query.message,t(lang, "brand_empty"), reply_markup=configs_menu(lang))
        await query.answer()
        return
    await safe_edit_text(query.message,t(lang, "brands_hint"), reply_markup=brands_menu(lang, brands, 0))
    await query.answer()

@dp.callback_query(F.data.startswith("cfg:brand_page:"))
async def cb_cfg_brand_page(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    brands = await list_brands()
    if not brands:
        await safe_edit_text(query.message,t(lang, "brand_empty"), reply_markup=configs_menu(lang))
        await query.answer()
        return
    raw_page = query.data.split(":")[-1]
    if not raw_page.isdigit():
        await query.answer()
        return
    page = int(raw_page)
    await safe_edit_text(query.message,t(lang, "brands_hint"), reply_markup=brands_menu(lang, brands, page))
    await query.answer()

@dp.callback_query(F.data.startswith("cfg:brand_pick:"))
async def cb_cfg_brand_pick(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    raw_idx = query.data.split(":")[-1]
    if not raw_idx.isdigit():
        await query.answer()
        return
    idx = int(raw_idx)
    brands = await list_brands()
    if idx < 0 or idx >= len(brands):
        await query.answer()
        return
    await _render_brand_configs(query, lang, idx, 0)
    await query.answer()

@dp.callback_query(F.data.startswith("cfg:brand_cfg:"))
async def cb_cfg_brand_cfg_page(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer()
        return
    _, _, raw_brand_idx, raw_page = parts
    if not raw_brand_idx.isdigit() or not raw_page.isdigit():
        await query.answer()
        return
    await _render_brand_configs(query, lang, int(raw_brand_idx), int(raw_page))
    await query.answer()

@dp.callback_query(F.data.startswith("cfg:get:"))
async def cb_cfg_get(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    raw_id = query.data.split(":")[-1]
    if not raw_id.isdigit():
        await query.answer()
        return
    cfg_id = int(raw_id)
    row = await get_config_for_send(cfg_id)
    if not row:
        await query.answer(t(lang, "not_found"), show_alert=True)
        return
    name, path = str(row[0]), Path(str(row[1]))
    if not path.exists():
        await query.answer(t(lang, "not_found"), show_alert=True)
        return
    await query.message.answer_document(FSInputFile(path), caption=f"#{cfg_id} {name}")
    await query.answer()

@dp.callback_query(F.data == "noop")
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer()

@dp.callback_query(F.data == "dev:add")
async def cb_dev_add(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await state.set_state(Flow.waiting_device_add)
    await safe_edit_text(query.message,t(lang, "ask_device"), parse_mode="HTML")
    await query.answer()

@dp.callback_query(F.data == "dev:list")
async def cb_dev_list(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    devices = await list_user_devices(query.from_user.id)
    if not devices:
        await safe_edit_text(query.message,t(lang, "my_devices_empty"), reply_markup=devices_menu(lang))
        await query.answer()
        return
    await safe_edit_text(query.message,
        "\n".join([t(lang, "my_devices_header")] + [f"- {d}" for d in devices]),
        reply_markup=devices_menu(lang),
    )
    await query.answer()

@dp.callback_query(F.data == "dev:check")
async def cb_dev_check(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await _render_device_configs(query, lang, query.from_user.id, 0)
    await query.answer()


@dp.callback_query(F.data.startswith("dev:check_page:"))
async def cb_dev_check_page(query: CallbackQuery) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    raw_page = query.data.split(":")[-1]
    if not raw_page.isdigit():
        await query.answer()
        return
    await _render_device_configs(query, lang, query.from_user.id, int(raw_page))
    await query.answer()

@dp.callback_query(F.data.startswith("vis:"))
async def cb_visibility(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    st = await state.get_state()
    if st != Flow.waiting_visibility.state:
        await query.answer()
        return
    data = await state.get_data()
    sub_id = int(data.get("sub_id", 0) or 0)
    if query.data == "vis:cancel":
        await state.clear()
        await safe_edit_text(query.message,t(lang, "welcome"), reply_markup=main_menu(lang))
        await query.answer()
        return
    if sub_id == 0:
        await state.clear()
        await safe_edit_text(query.message,t(lang, "save_failed"), reply_markup=main_menu(lang))
        await query.answer()
        return
    is_anon = query.data == "vis:hide"
    await set_submission_anonymity(sub_id, is_anon)
    await state.set_state(Flow.waiting_before)
    await safe_edit_text(query.message,t(lang, "send_before"))
    await query.answer()

@dp.callback_query(F.data == "admin:queue")
async def cb_admin_queue(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await handle_admin_queue(query, state, lang)

@dp.callback_query(F.data.startswith("admin:queue_page:"))
async def cb_admin_queue_page(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    raw_page = query.data.split(":")[-1]
    if not raw_page.isdigit():
        await query.answer()
        return
    await handle_admin_queue_page(query, state, lang, int(raw_page))

@dp.callback_query(F.data.startswith("admin:sub:"))
async def cb_admin_sub(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer()
        return
    _, _, raw_sub_id, raw_page = parts
    if not raw_sub_id.isdigit() or not raw_page.isdigit():
        await query.answer()
        return
    await handle_admin_submission_view(query, state, lang, int(raw_sub_id), int(raw_page))


@dp.callback_query(F.data.startswith("admin:do_approve:"))
async def cb_admin_do_approve(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer()
        return
    raw_sub_id = parts[2]
    raw_page = parts[3]
    if not raw_sub_id.isdigit() or not raw_page.isdigit():
        await query.answer()
        return
    await handle_admin_approve_action(query, state, lang, int(raw_sub_id), int(raw_page), bot)


@dp.callback_query(F.data.startswith("admin:do_reject:"))
async def cb_admin_do_reject(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    if not is_admin(query.from_user.id):
        await state.clear()
        await query.answer(t(lang, "admin_only"), show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer()
        return
    raw_sub_id = parts[2]
    raw_page = parts[3]
    if not raw_sub_id.isdigit() or not raw_page.isdigit():
        await query.answer()
        return
    sub_id = int(raw_sub_id)
    queue_page = int(raw_page)
    sub = await get_submission(sub_id)
    if not sub or str(sub[11]) != "pending_review":
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    await state.update_data(admin_reject_sub_id=sub_id, admin_reject_page=queue_page)
    await state.set_state(Flow.waiting_admin_reject_reason)
    await query.answer()
    await query.message.answer(t(lang, "admin_reject_reason_prompt", id=sub_id))

@dp.callback_query(F.data == "admin:view")
async def cb_admin_view(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await handle_admin_view_start(query, state, lang)

@dp.callback_query(F.data == "admin:approve")
async def cb_admin_approve(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await handle_admin_approve_start(query, state, lang)


@dp.callback_query(F.data == "admin:reject")
async def cb_admin_reject(query: CallbackQuery, state: FSMContext) -> None:
    lang = await resolve_lang(query.from_user.id, query.from_user.language_code)
    await handle_admin_reject_start(query, state, lang)

@dp.message(F.text)
async def handle_text_menu(message: Message, state: FSMContext) -> None:
    lang = await resolve_lang(message.from_user.id, message.from_user.language_code)
    text = str(message.text or "")
    handled_admin = await handle_admin_text(message, state, lang)
    if handled_admin:
        return
    st = await state.get_state()

    if st == Flow.waiting_admin_reject_reason.state:
        if not is_admin(message.from_user.id):
            await state.clear()
            await message.answer(t(lang, "admin_only"))
            return
        reason = text.strip()
        if not reason:
            await message.answer(t(lang, "admin_reject_reason_empty"))
            return
        data = await state.get_data()
        sub_id = int(data.get("admin_reject_sub_id", 0) or 0)
        if sub_id <= 0:
            await state.clear()
            await message.answer(t(lang, "admin_not_found"))
            return
        sub = await get_submission(sub_id)
        if not sub or str(sub[11]) != "pending_review":
            await state.clear()
            await message.answer(t(lang, "admin_not_found"))
            return
        updated = await reject_submission(
            sub_id,
            message.from_user.id,
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            reason,
        )
        if not updated:
            await state.clear()
            await message.answer(t(lang, "admin_not_found"))
            return
        try:
            await bot.send_message(int(sub[1]), t(lang, "rejected_user_reason", id=sub_id, reason=reason))
        except Exception:
            pass
        _cleanup_submission_proofs_files(sub)
        await state.clear()
        await message.answer(t(lang, "admin_rejected", id=sub_id))
        await message.answer(t(lang, "menu_admin"), reply_markup=admin_menu_inline(lang))
        return

    if st == Flow.waiting_device_add.state:
        created = await add_user_device(
            message.from_user.id,
            text,
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        await state.clear()
        await message.answer(
            t(lang, "device_added" if created else "device_exists", model=text),
            reply_markup=devices_menu(lang),
        )
        return

@dp.message(F.document)
async def handle_config_upload(message: Message, state: FSMContext) -> None:
    st = await state.get_state()
    if st != Flow.waiting_config.state:
        return
    lang = await resolve_lang(message.from_user.id, message.from_user.language_code)
    doc = message.document
    fname = str(doc.file_name or "")
    if not fname.lower().endswith(".notch"):
        await message.answer(t(lang, "only_notch"), parse_mode="HTML")
        return
    path = STORAGE_CONFIGS_DIR / f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_filename(fname)}"
    try:
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, destination=path)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise RuntimeError("bad")
        brand = str(cfg.get("brand", "")).strip()
        model = str(cfg.get("model", "")).strip()
        mode_id = int(cfg.get("mode_id", 0))
        if not model:
            raise RuntimeError("bad")
        settings_text = _format_settings(cfg)
        sub_id = await create_submission(
            user_id=message.from_user.id,
            original_name=fname,
            config_path=str(path),
            config_json=json.dumps(cfg, ensure_ascii=False),
            brand=brand,
            model=model,
            mode_id=mode_id,
            settings_text=settings_text,
            created_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            submitter_username=str(message.from_user.username or ""),
            submitter_name=str(message.from_user.full_name or ""),
        )
        await state.update_data(sub_id=sub_id)
        await state.set_state(Flow.waiting_visibility)
        await message.answer(
            t(lang, "suggest_config_received", id=sub_id, model=model, brand=brand or "-"),
        )
        await message.answer(settings_text)
        await message.answer(t(lang, "choose_visibility"), reply_markup=visibility_menu(lang))
    except Exception:
        await message.answer(t(lang, "save_failed"))

@dp.message(F.video | F.photo)
async def handle_proof_media(message: Message, state: FSMContext) -> None:
    st = await state.get_state()
    if st != Flow.waiting_before.state:
        return
    lang = await resolve_lang(message.from_user.id, message.from_user.language_code)
    data = await state.get_data()
    sub_id = int(data.get("sub_id", 0) or 0)
    if sub_id == 0:
        await state.clear()
        await send_main_menu(message, lang)
        return
    if st == Flow.waiting_before.state and message.media_group_id:
        key = _video_proof_key(message, sub_id)
        pack = VIDEO_PROOF_BUFFER.get(key)
        if not pack:
            pack = {"items": []}
            VIDEO_PROOF_BUFFER[key] = pack
        pack["items"].append(message)
        if len(pack["items"]) > MAX_PROOF_MEDIA:
            VIDEO_PROOF_BUFFER.pop(key, None)
            old_task = VIDEO_PROOF_TASKS.pop(key, None)
            if old_task:
                old_task.cancel()
            await message.answer(t(lang, "proof_media_limit", max=MAX_PROOF_MEDIA))
            return
        old_task = VIDEO_PROOF_TASKS.get(key)
        if old_task:
            old_task.cancel()
        VIDEO_PROOF_TASKS[key] = asyncio.create_task(_finalize_video_proofs(key, state, lang))
        return
    proof = await _save_media_proof(message, sub_id)
    if not proof:
        await message.answer(t(lang, "proof_required"))
        return
    proof_path, kind = proof
    await set_submission_before(sub_id, str(proof_path), kind)
    await set_submission_after(sub_id, str(proof_path), kind)
    await _finalize_submission_from_proofs(message, state, lang, sub_id)

@dp.errors()
async def on_error(event: ErrorEvent):
    if isinstance(event.exception, TelegramBadRequest):
        if "message is not modified" in str(event.exception).lower():
            return True
    logger.exception("Unhandled error: %s", event.exception)
    return False

async def main() -> None:
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

