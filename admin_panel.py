import datetime as dt
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaVideo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config import ADMIN_IDS
from db import *
from localization import *

logger = logging.getLogger("notch_bot")

def is_admin(user_id: int) -> bool:
    return int(user_id) in set(ADMIN_IDS)

def admin_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "menu_admin"), style="primary")]],
        resize_keyboard=True,
        selective=True,
    )

def admin_menu_inline(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "admin_queue_btn"), callback_data="admin:queue", style="primary")],
            [InlineKeyboardButton(text=t(lang, "admin_refresh_btn"), callback_data="admin:queue", style="success")],
            [InlineKeyboardButton(text=t(lang, "menu_back"), callback_data="menu:main", style="danger")],
        ]
    )

async def ensure_admin_callback(query: CallbackQuery, state: FSMContext, lang: str) -> bool:
    if is_admin(query.from_user.id):
        return True
    await state.clear()
    await query.answer(t(lang, "admin_only"), show_alert=True)
    return False

async def notify_admins_pending(bot: Bot, sub_id: int, model: str, brand: str) -> None:
    if not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"Pending submission #{sub_id}\nBrand: {brand}\nModel: {model}\nOpen admin panel in bot keyboard.",
            )
        except Exception:
            continue

async def handle_admin_queue(query: CallbackQuery, state: FSMContext, lang: str) -> None:
    await handle_admin_queue_page(query, state, lang, 0)

async def handle_admin_queue_page(
    query: CallbackQuery,
    state: FSMContext,
    lang: str,
    page: int,
    page_size: int = 10,
) -> None:
    if not await ensure_admin_callback(query, state, lang):
        return
    page = max(0, int(page))
    offset = page * page_size
    rows = await list_pending_submissions_page(page_size + 1, offset)
    if not rows and page > 0:
        await handle_admin_queue_page(query, state, lang, page - 1, page_size)
        return
    if not rows:
        await query.message.edit_text(t(lang, "admin_queue_empty"), reply_markup=admin_menu_inline(lang))
        await query.answer()
        return
    has_next = len(rows) > page_size
    page_rows = rows[:page_size]
    lines = [t(lang, "admin_queue_header")]
    row_buttons: list[list[InlineKeyboardButton]] = []
    for sid, model, brand, user_id, created_at in page_rows:
        lines.append(f"#{sid} | {brand} {model} | uid:{user_id} | {created_at}")
        row_buttons.append(
            [
                InlineKeyboardButton(
                    text=f"#{sid} • {brand} {model}".strip(),
                    callback_data=f"admin:sub:{sid}:{page}",
                )
            ]
        )
    kb = admin_menu_inline(lang).inline_keyboard
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text=t(lang, "brand_prev"), callback_data=f"admin:queue_page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=str(page + 1), callback_data="noop"))
    if has_next:
        nav.append(InlineKeyboardButton(text=t(lang, "brand_next"), callback_data=f"admin:queue_page:{page + 1}"))
    menu = InlineKeyboardMarkup(inline_keyboard=[*row_buttons, nav, *kb[1:]])
    await query.message.edit_text("\n".join(lines), reply_markup=menu)
    await query.answer()

async def handle_admin_view_start(query: CallbackQuery, state: FSMContext, lang: str) -> None:
    await handle_admin_queue(query, state, lang)

async def handle_admin_approve_start(query: CallbackQuery, state: FSMContext, lang: str) -> None:
    await handle_admin_queue(query, state, lang)

async def handle_admin_reject_start(query: CallbackQuery, state: FSMContext, lang: str) -> None:
    await handle_admin_queue(query, state, lang)

async def handle_admin_submission_view(
    query: CallbackQuery,
    state: FSMContext,
    lang: str,
    sub_id: int,
    queue_page: int,
) -> None:
    if not await ensure_admin_callback(query, state, lang):
        return
    sub = await get_submission(sub_id)
    if not sub:
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    status = str(sub[11] or "")
    controls: list[list[InlineKeyboardButton]] = []
    if status == "pending_review":
        controls.append(
            [
                InlineKeyboardButton(
                    text=t(lang, "admin_approve_btn"),
                    callback_data=f"admin:do_approve:{sub_id}:{queue_page}",
                ),
                InlineKeyboardButton(
                    text=t(lang, "admin_reject_btn"),
                    callback_data=f"admin:do_reject:{sub_id}:{queue_page}",
                ),
            ]
        )
    controls.append(
        [
            InlineKeyboardButton(
                text=t(lang, "menu_back"),
                callback_data=f"admin:queue_page:{queue_page}",
            )
        ]
    )
    await query.message.edit_text(
        t(
            lang,
            "admin_view_text",
            id=sub[0],
            status=sub[11],
            brand=sub[5] or "-",
            model=sub[6] or "-",
            mode_id=sub[8] or 0,
            author=sub[1],
            anon=int(sub[10] or 0),
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=controls),
    )
    await query.answer()
    await _send_submission_proofs(query.message, sub, lang)

async def _send_submission_proofs(message: Message, sub, lang: str) -> None:
    before_path = str(sub[12] or "")
    before_kind = str(sub[13] or "")
    after_path = str(sub[14] or "")
    after_kind = str(sub[15] or "")
    before_exists = bool(before_path and Path(before_path).exists())
    after_exists = bool(after_path and Path(after_path).exists())
    if before_exists and after_exists and before_kind == "video" and after_kind == "video":
        if before_path == after_path:
            await message.answer_video(FSInputFile(before_path), caption=t(lang, "admin_proof"))
        else:
            await message.answer_media_group(
                [
                    InputMediaVideo(media=FSInputFile(before_path), caption=t(lang, "admin_proof")),
                    InputMediaVideo(media=FSInputFile(after_path), caption=t(lang, "admin_proof")),
                ]
            )
        return
    if before_exists:
        if before_kind == "video":
            await message.answer_video(FSInputFile(before_path), caption=t(lang, "admin_proof"))
        elif before_kind == "photo":
            await message.answer_photo(FSInputFile(before_path), caption=t(lang, "admin_proof"))
    if after_exists and after_path != before_path:
        if after_kind == "video":
            await message.answer_video(FSInputFile(after_path), caption=t(lang, "admin_proof"))
        elif after_kind == "photo":
            await message.answer_photo(FSInputFile(after_path), caption=t(lang, "admin_proof"))

def _cleanup_submission_proofs(sub) -> None:
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

async def handle_admin_approve_action(
    query: CallbackQuery,
    state: FSMContext,
    lang: str,
    sub_id: int,
    queue_page: int,
    bot: Bot,
) -> None:
    if not await ensure_admin_callback(query, state, lang):
        return
    sub = await get_submission(sub_id)
    if not sub:
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    try:
        cfg_id, owner_id, model = await approve_submission(
            sub_id,
            query.from_user.id,
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    except Exception:
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    try:
        await bot.send_message(owner_id, t(lang, "approved_user", id=cfg_id))
    except Exception:
        pass
    _cleanup_submission_proofs(sub)
    users = await list_users_for_model(model)
    for uid in users:
        if uid == owner_id:
            continue
        try:
            await bot.send_message(uid, t(lang, "notify_model", model=model, id=cfg_id))
        except Exception:
            continue
    await query.answer(t(lang, "admin_approved", id=sub_id, cfg_id=cfg_id), show_alert=True)
    await handle_admin_queue_page(query, state, lang, queue_page)

async def handle_admin_reject_action(
    query: CallbackQuery,
    state: FSMContext,
    lang: str,
    sub_id: int,
    queue_page: int,
    bot: Bot,
) -> None:
    if not await ensure_admin_callback(query, state, lang):
        return
    sub = await get_submission(sub_id)
    if not sub:
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    try:
        updated = await reject_submission(
            sub_id,
            query.from_user.id,
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "",
        )
    except Exception as exc:
        logger.exception("Reject failed for sub_id=%s: %s", sub_id, exc)
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    if not updated:
        await query.answer(t(lang, "admin_not_found"), show_alert=True)
        return
    try:
        await bot.send_message(int(sub[1]), t(lang, "rejected_user", id=sub_id))
    except Exception:
        pass
    _cleanup_submission_proofs(sub)
    await query.answer(t(lang, "admin_rejected", id=sub_id), show_alert=True)
    await handle_admin_queue_page(query, state, lang, queue_page)

async def handle_admin_text(message: Message, state: FSMContext, lang: str) -> bool:
    text = str(message.text or "")

    if is_admin(message.from_user.id) and text == t(lang, "menu_admin"):
        await state.clear()
        await message.answer(t(lang, "menu_admin"), reply_markup=admin_menu_inline(lang))
        return True

    return False
