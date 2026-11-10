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

from resolver import LinkResolver, ResolutionError

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)


def csv_set(name: str, default: str = "") -> set[str]:
    return {
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    }


def allowed_user_ids() -> set[int]:
    values = csv_set("ALLOWED_USER_IDS")
    try:
        return {int(value) for value in values}
    except ValueError as exc:
        raise RuntimeError(
            "ALLOWED_USER_IDS mein sirf numeric Telegram user IDs honi chahiye."
        ) from exc


def user_is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    configured = context.application.bot_data.get("allowed_user_ids", set())
    user = update.effective_user
    return not configured or (user is not None and user.id in configured)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not user_is_allowed(update, context) or update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "AroLinks URL bhejein. Main redirects follow karke final public link dunga.\n\n"
        "Example:\nhttps://arolinks.com/jFHPD\n\n"
        "CAPTCHA ko bot solve ya bypass nahi karega."
    )


async def resolve_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not user_is_allowed(update, context):
        return

    match = URL_RE.search(message.text or "")
    if not match:
        await message.reply_text("Message mein valid http:// ya https:// link bhejein.")
        return

    status = await message.reply_text("Link resolve ho raha hai… (thoda wait karein)")
    resolver: LinkResolver = context.application.bot_data["resolver"]

    try:
        result = await resolver.resolve(match.group(0))
        await status.edit_text(
            _format_result(result),
            disable_web_page_preview=True,
        )
    except ResolutionError as exc:
        await status.edit_text(f"Resolve nahi hua: {exc}")
    except Exception:
        LOGGER.exception("Unexpected resolution failure")
        await status.edit_text("Unexpected error aaya. Server logs check karein.")


def _format_result(result) -> str:
    if result.final:
        return f"✅ Final link:\n{result.url}\n\nMethod: {result.method}"
    if result.method == "captcha":
        return (
            "⚠️ Is link par CAPTCHA hai (bot ise solve nahi kar sakta).\n\n"
            "Neeche wala page kholein, CAPTCHA + 'Continue'/'Get Link' khud daba kar "
            "final link le lein:\n"
            f"{result.url}\n\n"
            "Note: Fresh browser me flow shuru se chal sakta hai."
        )
    return (
        "⚠️ Automatic resolve poora nahi hua (countdown/ads/verification).\n\n"
        "Yahan tak pohcha — ise kholkar baaki steps khud poore karein:\n"
        f"{result.url}"
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Telegram update failed", exc_info=context.error)


async def post_init(application: Application) -> None:
    resolver: LinkResolver = application.bot_data["resolver"]
    await resolver.start()
    LOGGER.info("Browser resolver started")


async def post_shutdown(application: Application) -> None:
    resolver: LinkResolver = application.bot_data["resolver"]
    await resolver.close()
    LOGGER.info("Browser resolver stopped")


def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN set karein. .env.example dekhein.")

    hosts = csv_set("ALLOWED_HOSTS", "arolinks.com,www.arolinks.com")
    resolver = LinkResolver(
        allowed_hosts=hosts,
        timeout_seconds=float(os.getenv("RESOLVE_TIMEOUT_SECONDS", "75")),
        max_concurrent=int(os.getenv("MAX_CONCURRENT_RESOLUTIONS", "2")),
    )

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["resolver"] = resolver
    application.bot_data["allowed_user_ids"] = allowed_user_ids()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, resolve_message)
    )
    application.add_error_handler(on_error)

    LOGGER.info("Starting Telegram bot for hosts: %s", ", ".join(sorted(hosts)))
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
