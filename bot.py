from __future__ import annotations

import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from resolver import LinkResolver, ResolveError, ResolveResult

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)


def allowed_user_ids() -> set[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "")
    ids = {item.strip() for item in raw.split(",") if item.strip()}
    try:
        return {int(value) for value in ids}
    except ValueError as exc:
        raise RuntimeError(
            "ALLOWED_USER_IDS me sirf numeric IDs honi chahiye."
        ) from exc


def user_is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    configured = context.application.bot_data.get("allowed_user_ids", set())
    user = update.effective_user
    return not configured or (user is not None and user.id in configured)


def format_result(result: ResolveResult) -> str:
    if result.status == "final":
        return f"✅ Final link:\n{result.url}"
    if result.status == "captcha":
        return (
            "⚠️ Is link par CAPTCHA aa gaya (bot ise solve nahi karta).\n\n"
            "Yeh page kholein, CAPTCHA solve karke final link le lein:\n"
            f"{result.url}"
        )
    note = f"\n\n({result.note})" if result.note else ""
    return (
        "⚠️ Final link automatically nahi mil paya.\n\n"
        f"Yeh page kholkar try karein:\n{result.url}{note}"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not user_is_allowed(update, context) or update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "AroLinks link bhejein, main final link nikalne ki koshish karunga.\n\n"
        "Example:\nhttps://arolinks.com/jFHPD"
    )


async def resolve_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not user_is_allowed(update, context):
        return

    match = URL_RE.search(message.text or "")
    if not match:
        await message.reply_text("Ek valid arolinks link bhejein.")
        return

    status = await message.reply_text("⏳ Resolve ho raha hai, thoda wait karein…")
    resolver: LinkResolver = context.application.bot_data["resolver"]

    try:
        result = await resolver.resolve(match.group(0))
        await status.edit_text(format_result(result), disable_web_page_preview=True)
    except ResolveError as exc:
        await status.edit_text(f"❌ {exc}")
    except Exception:
        LOGGER.exception("Unexpected resolution failure")
        await status.edit_text("❌ Unexpected error. Server logs check karein.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Telegram update failed", exc_info=context.error)


def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN set karein (.env.example dekhein).")

    resolver = LinkResolver(
        timeout_seconds=float(os.getenv("RESOLVE_TIMEOUT_SECONDS", "60"))
    )

    application = Application.builder().token(token).build()
    application.bot_data["resolver"] = resolver
    application.bot_data["allowed_user_ids"] = allowed_user_ids()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, resolve_message)
    )
    application.add_error_handler(on_error)

    LOGGER.info("Starting AroLinks resolver bot")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
