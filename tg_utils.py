from aiogram.exceptions import TelegramBadRequest


async def safe_edit_text(message, text: str, reply_markup=None, parse_mode: str | None = None) -> bool:
    try:
        has_text = getattr(message, "text", None) is not None
        has_media = bool(
            getattr(message, "photo", None)
            or getattr(message, "video", None)
            or getattr(message, "document", None)
            or getattr(message, "animation", None)
        )
        if has_text and not has_media:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except TelegramBadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            return False
        if "there is no text in the message to edit" in lowered:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        raise
