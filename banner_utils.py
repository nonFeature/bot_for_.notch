from pathlib import Path

from aiogram.types import FSInputFile, Message

BANNERS_DIR = Path("banners").resolve()
_PHOTO_EXTS = ("jpg", "jpeg", "png", "webp")
_VIDEO_EXTS = ("mp4", "mov", "m4v")


def _find_banner_path(stems: list[str]) -> tuple[Path, str] | None:
    for stem in stems:
        base = BANNERS_DIR / stem
        for ext in _PHOTO_EXTS:
            p = base.with_suffix(f".{ext}")
            if p.exists() and p.is_file():
                return p, "photo"
        for ext in _VIDEO_EXTS:
            p = base.with_suffix(f".{ext}")
            if p.exists() and p.is_file():
                return p, "video"
    return None


def _expand_stems(stem: str, lang_key: str, aliases: list[str] | None = None) -> list[str]:
    roots: list[str] = [stem]
    if aliases:
        roots.extend([x for x in aliases if x])
    result: list[str] = []
    for root in roots:
        if lang_key:
            result.append(f"{root}_{lang_key}")
            result.append(f"{root}_{lang_key}-1")
            result.append(f"{root}_{lang_key}-2")
        result.append(root)
        result.append(f"{root}-1")
        result.append(f"{root}-2")
    # keep unique and ordered
    out: list[str] = []
    seen: set[str] = set()
    for x in result:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def resolve_banner(stem: str, lang: str = "", aliases: list[str] | None = None) -> tuple[Path, str] | None:
    BANNERS_DIR.mkdir(parents=True, exist_ok=True)
    lang_key = (lang or "").strip().lower()
    stems = _expand_stems(stem, lang_key, aliases)
    return _find_banner_path(stems)


async def send_banner(
    message: Message,
    stem: str,
    lang: str = "",
    aliases: list[str] | None = None,
    logger=None,
    caption: str | None = None,
    reply_markup=None,
    parse_mode: str | None = None,
) -> bool:
    hit = resolve_banner(stem, lang=lang, aliases=aliases)
    if not hit:
        return False
    path, kind = hit
    try:
        media = FSInputFile(path)
        if kind == "photo":
            await message.answer_photo(
                media,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await message.answer_video(
                media,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        return True
    except Exception as exc:
        if logger:
            logger.exception("Failed to send banner '%s': %s", stem, exc)
        return False
