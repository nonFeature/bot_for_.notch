import aiosqlite
from pathlib import Path

DB_PATH = (Path("storage") / "configs.db").resolve()

LANG_EN = "en"
LANG_RU = "ru"

def detect_lang(language_code: str | None) -> str:
    code = (language_code or "").lower()
    return LANG_RU if code.startswith("ru") else LANG_EN

def norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                lang TEXT NOT NULL DEFAULT 'en',
                lang_manual INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_devices (
                user_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                model_norm TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, model_norm)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                config_path TEXT NOT NULL,
                config_json TEXT NOT NULL,
                brand TEXT DEFAULT '',
                model TEXT DEFAULT '',
                model_norm TEXT DEFAULT '',
                mode_id INTEGER DEFAULT 0,
                settings_text TEXT DEFAULT '',
                is_anonymous INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'awaiting_visibility',
                before_path TEXT DEFAULT '',
                before_kind TEXT DEFAULT '',
                after_path TEXT DEFAULT '',
                after_kind TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                reviewed_at TEXT DEFAULT '',
                reviewer_id INTEGER,
                reject_reason TEXT DEFAULT '',
                submitter_username TEXT DEFAULT '',
                submitter_name TEXT DEFAULT ''
            )
            """
        )
        cur = await db.execute("PRAGMA table_info(submissions)")
        cols = {str(row[1]) for row in await cur.fetchall()}
        if "reject_reason" not in cols:
            await db.execute("ALTER TABLE submissions ADD COLUMN reject_reason TEXT DEFAULT ''")
        if "submitter_username" not in cols:
            await db.execute("ALTER TABLE submissions ADD COLUMN submitter_username TEXT DEFAULT ''")
        if "submitter_name" not in cols:
            await db.execute("ALTER TABLE submissions ADD COLUMN submitter_name TEXT DEFAULT ''")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER,
                user_id INTEGER NOT NULL,
                author_label TEXT NOT NULL,
                is_anonymous INTEGER NOT NULL DEFAULT 0,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                config_json TEXT NOT NULL,
                brand TEXT DEFAULT '',
                model TEXT DEFAULT '',
                model_norm TEXT DEFAULT '',
                mode_id INTEGER DEFAULT 0,
                settings_text TEXT DEFAULT '',
                approved_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

async def set_lang_with_mode(user_id: int, lang: str, manual: bool) -> None:
    lang = LANG_RU if lang == LANG_RU else LANG_EN
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users(user_id, lang, lang_manual) VALUES(?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang, lang_manual=excluded.lang_manual
            """,
            (int(user_id), lang, 1 if manual else 0),
        )
        await db.commit()

async def set_lang(user_id: int, lang: str) -> None:
    await set_lang_with_mode(user_id, lang, manual=True)

async def resolve_lang(user_id: int, language_code: str | None) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT lang, lang_manual FROM users WHERE user_id = ?", (int(user_id),))
        row = await cur.fetchone()
    inferred = detect_lang(language_code)
    if not row:
        await set_lang_with_mode(user_id, inferred, manual=False)
        return inferred
    lang = row[0] if row[0] in (LANG_EN, LANG_RU) else LANG_EN
    is_manual = bool(int(row[1] or 0))
    if is_manual:
        return lang
    if lang != inferred:
        await set_lang_with_mode(user_id, inferred, manual=False)
        return inferred
    return lang

async def add_user_device(user_id: int, model: str, created_at: str) -> bool:
    model_text = " ".join(str(model or "").strip().split())
    model_norm = norm(model_text)
    if not model_norm:
        return False
    existing = await list_user_devices(user_id)
    exists = model_norm in {norm(x) for x in existing}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_devices(user_id, model, model_norm, created_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, model_norm) DO UPDATE SET model = excluded.model
            """,
            (int(user_id), model_text, model_norm, str(created_at)),
        )
        await db.commit()
    return not exists

async def list_user_devices(user_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT model FROM user_devices WHERE user_id = ? ORDER BY model ASC",
            (int(user_id),),
        )
        rows = await cur.fetchall()
    return [str(r[0]) for r in rows]

async def remove_user_device(user_id: int, model: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM user_devices WHERE user_id = ? AND model_norm = ?",
            (int(user_id), norm(model)),
        )
        await db.commit()
    return int(cur.rowcount) > 0

async def list_users_for_model(model: str) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM user_devices WHERE model_norm = ?",
            (norm(model),),
        )
        rows = await cur.fetchall()
    return [int(r[0]) for r in rows]

async def create_submission(
    user_id: int,
    original_name: str,
    config_path: str,
    config_json: str,
    brand: str,
    model: str,
    mode_id: int,
    settings_text: str,
    created_at: str,
    submitter_username: str = "",
    submitter_name: str = "",
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO submissions(
                user_id, original_name, config_path, config_json, brand, model, model_norm,
                mode_id, settings_text, status, created_at, submitter_username, submitter_name
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_visibility', ?, ?, ?)
            """,
            (
                int(user_id),
                str(original_name),
                str(config_path),
                str(config_json),
                str(brand or ""),
                str(model or ""),
                norm(model),
                int(mode_id),
                str(settings_text or ""),
                str(created_at),
                str(submitter_username or ""),
                str(submitter_name or ""),
            ),
        )
        await db.commit()
        return int(cur.lastrowid)

async def get_submission(sub_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, user_id, original_name, config_path, config_json, brand, model, model_norm,
                   mode_id, settings_text, is_anonymous, status, before_path, before_kind,
                   after_path, after_kind, created_at, reject_reason, submitter_username, submitter_name
            FROM submissions
            WHERE id = ?
            """,
            (int(sub_id),),
        )
        row = await cur.fetchone()
    return row

async def get_active_submission_for_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, status
            FROM submissions
            WHERE user_id = ? AND status IN ('awaiting_visibility', 'awaiting_before')
            ORDER BY id DESC LIMIT 1
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
    return row

async def set_submission_anonymity(sub_id: int, is_anonymous: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE submissions
            SET is_anonymous = ?, status = 'awaiting_before'
            WHERE id = ?
            """,
            (1 if is_anonymous else 0, int(sub_id)),
        )
        await db.commit()

async def set_submission_before(sub_id: int, path: str, kind: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE submissions
            SET before_path = ?, before_kind = ?, status = 'awaiting_after'
            WHERE id = ?
            """,
            (str(path), str(kind), int(sub_id)),
        )
        await db.commit()

async def set_submission_after(sub_id: int, path: str, kind: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE submissions
            SET after_path = ?, after_kind = ?, status = 'pending_review'
            WHERE id = ?
            """,
            (str(path), str(kind), int(sub_id)),
        )
        await db.commit()

async def list_pending_submissions(limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, model, brand, user_id, created_at
            FROM submissions
            WHERE status = 'pending_review'
            ORDER BY id ASC LIMIT ?
            """,
            (int(limit),),
        )
        rows = await cur.fetchall()
    return rows

async def reject_submission(sub_id: int, reviewer_id: int, reviewed_at: str, reason: str = "") -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE submissions
            SET status = 'rejected', reviewer_id = ?, reviewed_at = ?, reject_reason = ?
            WHERE id = ? AND status = 'pending_review'
            """,
            (int(reviewer_id), str(reviewed_at), str(reason or ""), int(sub_id)),
        )
        await db.commit()
        return int(cur.rowcount or 0) > 0

async def approve_submission(sub_id: int, reviewer_id: int, reviewed_at: str) -> tuple[int, int, str]:
    sub = await get_submission(sub_id)
    if not sub:
        raise RuntimeError("submission not found")
    if str(sub[11]) != "pending_review":
        raise RuntimeError("submission not pending")

    user_id = int(sub[1])
    model = str(sub[6] or "")
    is_anonymous = int(sub[10] or 0)
    submitter_username = str(sub[18] or "").strip()
    submitter_name = str(sub[19] or "").strip()
    if is_anonymous:
        author_label = "anonymous"
    elif submitter_username:
        author_label = f"@{submitter_username.lstrip('@')}"
    elif submitter_name:
        author_label = submitter_name
    else:
        author_label = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO configs(
                submission_id, user_id, author_label, is_anonymous, original_name, stored_path, config_json,
                brand, model, model_norm, mode_id, settings_text, approved_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(sub[0]),
                user_id,
                author_label,
                is_anonymous,
                str(sub[2]),
                str(sub[3]),
                str(sub[4]),
                str(sub[5] or ""),
                model,
                str(sub[7] or ""),
                int(sub[8] or 0),
                str(sub[9] or ""),
                str(reviewed_at),
            ),
        )
        cfg_id = int(cur.lastrowid)
        await db.execute(
            """
            UPDATE submissions
            SET status = 'approved', reviewer_id = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (int(reviewer_id), str(reviewed_at), int(sub_id)),
        )
        await db.commit()
    return cfg_id, user_id, model

async def list_recent_configs_page(limit: int = 20, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, brand, model, approved_at, author_label, is_anonymous, user_id
            FROM configs
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (int(limit), int(offset)),
        )
        return await cur.fetchall()

async def list_brands() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT DISTINCT brand FROM configs WHERE brand <> '' ORDER BY brand COLLATE NOCASE ASC"
        )
        rows = await cur.fetchall()
    return [str(r[0]) for r in rows]

async def list_configs_by_brand_page(brand: str, limit: int = 20, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, model, approved_at, author_label, is_anonymous, user_id
            FROM configs
            WHERE lower(brand) = lower(?)
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (str(brand), int(limit), int(offset)),
        )
        return await cur.fetchall()

async def get_config_for_send(cfg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT original_name, stored_path FROM configs WHERE id = ?",
            (int(cfg_id),),
        )
        return await cur.fetchone()

async def list_configs_for_models_page(models: list[str], limit: int = 20, offset: int = 0):
    norms = []
    seen = set()
    for model in models:
        key = norm(model)
        if not key or key in seen:
            continue
        seen.add(key)
        norms.append(key)
    if not norms:
        return []
    placeholders = ",".join("?" for _ in norms)
    query = f"""
        SELECT id, brand, model, approved_at, author_label, is_anonymous, user_id
        FROM configs
        WHERE model_norm IN ({placeholders})
        ORDER BY id DESC LIMIT ? OFFSET ?
    """
    params = [*norms, int(limit), int(offset)]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        return await cur.fetchall()

async def list_pending_submissions_page(limit: int = 20, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, model, brand, user_id, created_at
            FROM submissions
            WHERE status = 'pending_review'
            ORDER BY id ASC LIMIT ? OFFSET ?
            """,
            (int(limit), int(offset)),
        )
        rows = await cur.fetchall()
    return rows