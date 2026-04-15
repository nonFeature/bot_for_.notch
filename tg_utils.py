from aiogram.exceptions import TelegramBadRequest


async def safe_edit_text(message, text: str, reply_markup=None) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise
