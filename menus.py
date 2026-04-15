from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from localization import LANG_EN, LANG_RU, TEXT, t

BRANDS_PER_PAGE = 8

def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=TEXT[LANG_EN]["lang_button_en"], callback_data="lang:en"),
                InlineKeyboardButton(text=TEXT[LANG_RU]["lang_button_ru"], callback_data="lang:ru"),
            ]
        ]
    )

def main_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(lang, "menu_configs"), callback_data="menu:configs"),
                InlineKeyboardButton(text=t(lang, "menu_devices"), callback_data="menu:devices"),
            ],
            [
                InlineKeyboardButton(
                    text=f"{t(lang, 'menu_suggest')}",
                    callback_data="menu:suggest",
                    style="primary",
                )
            ],
        ]
    )

def configs_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(lang, "cfg_by_brand"), callback_data="cfg:brand"),
                InlineKeyboardButton(text=t(lang, "cfg_recent"), callback_data="cfg:recent"),
            ],
            [
                InlineKeyboardButton(
                    text=f"{t(lang, 'menu_back')}",
                    callback_data="menu:main",
                    style="danger",
                )
            ],
        ]
    )

def devices_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{t(lang, 'dev_add')}",
                    callback_data="dev:add",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(text=t(lang, "dev_list"), callback_data="dev:list"),
                InlineKeyboardButton(text=t(lang, "dev_check"), callback_data="dev:check"),
            ],
            [
                InlineKeyboardButton(
                    text=f"{t(lang, 'menu_back')}",
                    callback_data="menu:main",
                    style="danger",
                )
            ],
        ]
    )

def brands_menu(lang: str, brands: list[str], page: int = 0) -> InlineKeyboardMarkup:
    total = len(brands)
    last_page = max((total - 1) // BRANDS_PER_PAGE, 0)
    page = max(0, min(page, last_page))
    start = page * BRANDS_PER_PAGE
    end = min(start + BRANDS_PER_PAGE, total)

    rows: list[list[InlineKeyboardButton]] = []
    for idx, brand in enumerate(brands[start:end], start=start):
        rows.append([InlineKeyboardButton(text=brand, callback_data=f"cfg:brand_pick:{idx}")])
    if total > BRANDS_PER_PAGE:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text=t(lang, "brand_prev"), callback_data=f"cfg:brand_page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{last_page + 1}", callback_data="noop"))
        if page < last_page:
            nav.append(InlineKeyboardButton(text=t(lang, "brand_next"), callback_data=f"cfg:brand_page:{page + 1}"))
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                text=f"🔴 {t(lang, 'menu_back')}",
                callback_data="menu:configs",
                style="danger",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def visibility_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "show_author"), callback_data="vis:show")],
            [InlineKeyboardButton(text=t(lang, "hide_author"), callback_data="vis:hide")],
            [InlineKeyboardButton(text=t(lang, "menu_cancel"), callback_data="vis:cancel")],
        ]
    )

def configs_result_menu(
    lang: str,
    cfg_ids: list[int],
    back_callback: str = "menu:configs",
    page: int | None = None,
    page_callback_prefix: str = "",
    has_prev: bool = False,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cfg_id in cfg_ids:
        rows.append(
            [InlineKeyboardButton(text=t(lang, "cfg_download_btn", id=cfg_id), callback_data=f"cfg:get:{cfg_id}")]
        )
    if page is not None and page_callback_prefix:
        nav: list[InlineKeyboardButton] = []
        if has_prev:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "brand_prev"),
                    callback_data=f"{page_callback_prefix}:{page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=str(page + 1), callback_data="noop"))
        if has_next:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "brand_next"),
                    callback_data=f"{page_callback_prefix}:{page + 1}",
                )
            )
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                text=f"{t(lang, 'menu_back')}",
                callback_data=back_callback,
                style="danger",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)