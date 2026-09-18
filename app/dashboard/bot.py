"""One-shot Telegram dashboard for ephemeral scheduled runners.

All visible UI is Arabic. Dashboard state (update offset and pending text
input) is stored in the existing `settings` table so GitHub Actions runners
can start/finish without losing operator state.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.caption import Caption
from app.models.channel import Channel
from app.models.enums import CaptionValidationStatus, ChannelStatus, ContentType, ProductStatus, PostPublishStatus
from app.models.product import Product
from app.models.scheduled_post import ScheduledPost
from app.models.setting import Setting
from app.dashboard.auth import parse_admin_ids, is_admin
from app.dashboard.bot_api import TelegramBotAPI
from app.dashboard.callback_data import encode, decode
from app.dashboard import texts as T
from app.dashboard import service
from app.dashboard.settings_service import get_value, set_value, PUBLISH_MODE_KEY
from app.telegram.channel_service import disable_channel, enable_channel
from app.telegram.manual_capture import ingest_forwarded_message
from app.queue.operations import publish_now, schedule, skip, retry_failed, reprioritize
from app.instagram.client import GraphAPIInstagramClient
from app.instagram.publisher_service import publish_scheduled_post

logger = logging.getLogger(__name__)

OFFSET_KEY = "dashboard_update_offset"

COMMANDS = [
    {"command": "search", "description": "🔍 بحث عن منتج برمزه"},
]
AWAITING_PREFIX = "dashboard_awaiting:"


def _kb(rows):
    return {"inline_keyboard": rows}


def _btn(text, data):
    return {"text": text, "callback_data": data}


class Dashboard:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.admin_ids = parse_admin_ids(self.settings.TELEGRAM_DASHBOARD_ADMIN_IDS)
        self.api = TelegramBotAPI(self.settings.TELEGRAM_BOT_TOKEN)

    def menu(self):
        return _kb([
            [_btn(T.SEARCH_PRODUCT, encode("ask_product_search"))],
        ])

    async def close(self) -> None:
        await self.api.close()

    async def _get_offset(self) -> int | None:
        async with AsyncSessionLocal() as db:
            value = await get_value(db, OFFSET_KEY)
        return int(value) if value and value.isdigit() else None

    async def _set_offset(self, value: int) -> None:
        async with AsyncSessionLocal() as db:
            await set_value(db, OFFSET_KEY, str(value), "Telegram dashboard getUpdates offset")

    async def _get_awaiting(self, uid: int) -> str | None:
        async with AsyncSessionLocal() as db:
            return await get_value(db, f"{AWAITING_PREFIX}{uid}")

    async def _set_awaiting(self, uid: int, action: str) -> None:
        async with AsyncSessionLocal() as db:
            await set_value(db, f"{AWAITING_PREFIX}{uid}", action, "Pending Telegram dashboard input")

    async def _clear_awaiting(self, uid: int) -> None:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(select(Setting).where(Setting.key == f"{AWAITING_PREFIX}{uid}"))
            ).scalar_one_or_none()
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def start(self, chat_id: int):
        await self.api.send_message(chat_id, T.WELCOME, self.menu())

    async def handle_message(self, message: dict) -> bool:
        user = message.get("from") or {}
        uid = user.get("id")
        chat = message.get("chat", {}).get("id")
        if not is_admin(uid, self.admin_ids):
            await self.api.send_message(chat, T.UNAUTHORIZED)
            return True
        text = (message.get("text") or "").strip()
        if text in {"/start", "/menu"}:
            await self.start(chat)
            return True
        if text == "/search":
            await self._set_awaiting(uid, "product_search")
            await self.api.send_message(chat, "أرسل رمز المنتج كما هو، مثال: PRD-ABCDEFGH")
            return True
        is_forward = bool(message.get("forward_origin") or message.get("forward_from_chat"))
        has_media = bool(message.get("photo") or message.get("video"))
        if is_forward or has_media:
            return await self._handle_capture(chat, uid, message)
        pending = await self._get_awaiting(uid)
        if pending:
            ok = await self.handle_input(chat, uid, pending, text)
            if ok:
                await self._clear_awaiting(uid)
            return ok
        await self.start(chat)
        return True

    async def _handle_capture(self, chat: int, uid: int, message: dict) -> bool:
        """A product forwarded (or sent directly with a photo/video) to the
        bot: create it, then ask what to publish it as. Captioning,
        validation, and scheduling happen automatically on the next cycle
        — this only records the person's POST/REEL/STORY choice.
        """
        await self._clear_awaiting(uid)
        async with AsyncSessionLocal() as db:
            try:
                result = await ingest_forwarded_message(db, self.api, message)
            except Exception:
                # Any failure here - duplicate key, a transient DB/pooler
                # hiccup, etc. - must NOT block the Telegram update offset
                # forever. Retrying the *same* update automatically tends to
                # hit the *same* failure again every cycle, which both spams
                # the person with repeated error messages and starves every
                # update queued behind this one. So we skip past it (offset
                # still advances) and ask the person to forward it again if
                # it truly was a one-off blip - forwarding is idempotent.
                logger.exception("Manual capture failed")
                await db.rollback()
                await self.api.send_message(
                    chat, "تعذر معالجة هالرسالة. حوّليها من جديد وجرب تاني.", self.menu()
                )
                return True

            if result.outcome == "created":
                buttons = [
                    [
                        _btn("📷 بوست", encode("capture_type", f"POST:{result.product_id}")),
                        _btn("🎬 ريلز", encode("capture_type", f"REEL:{result.product_id}")),
                    ],
                    [
                        _btn("⭐ قصة", encode("capture_type", f"STORY:{result.product_id}")),
                        _btn("❌ إلغاء", encode("capture_type", f"CANCEL:{result.product_id}")),
                    ],
                ]
                await self.api.send_message(chat, "تم استلام المنتج ✅\nشو بدك تنشره؟", _kb(buttons))
                return True
            if result.outcome == "duplicate_linked":
                product = await db.get(Product, result.product_id) if result.product_id else None
                code = product.product_code if product else "؟"
                await self.api.send_message(chat, f"هاد المنتج مكرر — موجود أصلاً برمز {code}.", self.menu())
                return True
            if result.outcome == "ignored":
                await self.api.send_message(chat, "ما قدرت ألاقي منتج بهالرسالة (لازم سعر وعنوان واضحين بالنص).", self.menu())
                return True
            await self.api.send_message(chat, f"صار خطأ بمعالجة المنتج: {result.error or 'غير معروف'}", self.menu())
            return True

    async def handle_input(self, chat: int, uid: int, action: str, text: str) -> bool:
        async with AsyncSessionLocal() as db:
            try:
                if action.startswith("capture_caption:"):
                    pid = int(action.split(":", 1)[1])
                    product = await db.get(Product, pid)
                    if not product:
                        raise ValueError
                    caption_text = text.strip()
                    if not caption_text:
                        await self.api.send_message(chat, "النص فاضي — اكتب الكابشن يلي بدك تنشره:")
                        return True
                    db.add(Caption(
                        product_id=product.id,
                        text=caption_text,
                        version=1,
                        is_selected=True,
                        generated_by="manual",
                        validation_status=CaptionValidationStatus.PASSED,
                    ))
                    product.status = ProductStatus.VALIDATED
                    await db.commit()
                    await self.api.send_message(
                        chat,
                        f"تم ✅ رمز المنتج: {product.product_code}\n"
                        "رح يتجدول وينشر تلقائياً بالدورة الجاية (خلال ~15 دقيقة) حسب النوع يلي اخترته.",
                        self.menu(),
                    )
                    return True
                if action.startswith("limit:"):
                    typ = ContentType(action.split(":", 1)[1])
                    value = int(text)
                    if value < 0:
                        raise ValueError
                    today = datetime.now(ZoneInfo(self.settings.SCHEDULER_TIMEZONE)).date()
                    await service.set_limit(db, today, typ, value)
                    await self.api.send_message(chat, T.DONE, self.menu())
                    return True
                if action == "product_search":
                    product = await service.product_by_code(db, text)
                    if not product:
                        await self.api.send_message(chat, "لم يتم العثور على منتج بهذا الرمز.", self.menu())
                        return True
                    await self.api.send_message(chat, f"رمز المنتج: {product.product_code}\nالمنتج: {product.title or 'بدون عنوان'}\nالحالة: {product.status.value}\nالمصدر: {product.source_channel_name}\nرسالة المصدر: {product.source_message_id}", self.menu())
                    return True
                if action == "score":
                    pid, value = [int(x.strip()) for x in text.split(" ", 1)]
                    product = await db.get(Product, pid)
                    if not product:
                        raise ValueError
                    await reprioritize(db, product, value)
                    await self.api.send_message(chat, T.DONE, self.menu())
                    return True
                await self.api.send_message(chat, T.INVALID, self.menu())
                return True
            except Exception:
                logger.exception("Dashboard input failed")
                await db.rollback()
                await self.api.send_message(chat, "تعذر تنفيذ العملية. أرسل البيانات مرة أخرى.", self.menu())
                return True

    async def handle_callback(self, callback: dict) -> bool:
        user = callback.get("from") or {}
        uid = user.get("id")
        msg = callback.get("message") or {}
        chat = msg.get("chat", {}).get("id")
        mid = msg.get("message_id")
        if not is_admin(uid, self.admin_ids):
            await self.api.answer_callback(callback.get("id"), T.UNAUTHORIZED)
            return True
        decoded = decode(callback.get("data"))
        if not decoded:
            await self.api.answer_callback(callback.get("id"), T.INVALID)
            return True
        action, value = decoded
        try:
            await self.api.answer_callback(callback.get("id"))
        except Exception:
            # Telegram rejects answering a callback that has gone stale
            # (e.g. pressed right before the job's ~5-minute gap, so it
            # wasn't picked up until the next job started). That must not
            # stop the actual button action below from running, and must
            # not block the update offset either.
            logger.warning("answerCallbackQuery failed (likely a stale callback); continuing anyway")
        async with AsyncSessionLocal() as db:
            try:
                if action == "capture_type":
                    ct_raw, pid_raw = value.split(":", 1)
                    product = await db.get(Product, int(pid_raw))
                    if not product:
                        raise ValueError
                    if ct_raw == "CANCEL":
                        product.status = ProductStatus.REJECTED
                        await db.commit()
                        await self.api.edit_message(chat, mid, "تم الإلغاء.")
                        return True
                    product.preferred_content_type = ContentType(ct_raw)
                    await db.commit()
                    await self._set_awaiting(uid, f"capture_caption:{product.id}")
                    label = {"POST": "بوست 📷", "REEL": "ريلز 🎬", "STORY": "قصة ⭐"}[ct_raw]
                    await self.api.edit_message(
                        chat, mid,
                        f"تمام، رح يُنشر كـ {label} — رمز المنتج: {product.product_code}\n\n"
                        f"هلق اكتب النص (الكابشن) يلي بدك تنشره مع هالمنتج:",
                    )
                    return True
                if action == "channels":
                    rows = await service.list_channels(db)
                    buttons = []
                    for c in rows:
                        target = "DISABLE" if c.status == ChannelStatus.ACTIVE else "ENABLE"
                        label = f"#{c.id} {'🟢 مفعلة' if c.status == ChannelStatus.ACTIVE else '⚪ معطلة'}"
                        buttons.append([_btn(label, encode("set_channel", f"{target}:{c.id}"))])
                    note = "\n\nملاحظة: القنوات بتتسجّل هون تلقائياً أول ما تحوّل منشور منها للبوت — ما في حاجة تضيفها يدوياً."
                    await self.api.edit_message(chat, mid, await service.channels_text(db) + note, _kb(buttons))
                    return True
                if action == "set_channel":
                    target, raw_id = value.split(":", 1)
                    channel = await db.get(Channel, int(raw_id))
                    if not channel:
                        raise ValueError
                    if target == "DISABLE":
                        await disable_channel(db, channel.id)
                    else:
                        await enable_channel(db, channel.id)
                    await self.api.send_message(chat, await service.channels_text(db), self.menu())
                    return True
                if action == "products":
                    await self.api.edit_message(chat, mid, await service.products_text(db), _kb([[_btn(T.SEARCH_PRODUCT, encode("ask_product_search"))], [_btn(T.QUEUE, encode("queue"))], [_btn(T.BACK, encode("menu"))]]))
                    return True
                if action == "ask_product_search":
                    await self._set_awaiting(uid, "product_search")
                    await self.api.send_message(chat, "أرسل رمز المنتج كما هو، مثال: PRD-ABCDEFGH")
                    return True
                if action == "queue":
                    products = (await db.execute(select(Product).where(Product.status.in_([ProductStatus.ELIGIBLE, ProductStatus.QUEUED, ProductStatus.SCHEDULED])).order_by(Product.score.desc().nullslast(), Product.id.asc()).limit(10))).scalars().all()
                    buttons = [[_btn(f"#{p.id} — {p.title or 'بدون عنوان'}", encode("product", p.id))] for p in products]
                    buttons.append([_btn(T.BACK, encode("menu"))])
                    await self.api.edit_message(chat, mid, await service.queue_text(db), _kb(buttons))
                    return True
                if action == "product":
                    p = await db.get(Product, int(value))
                    if not p:
                        raise ValueError
                    text = f"رمز المنتج: {p.product_code}\nالمنتج #{p.id}\n{p.title or 'بدون عنوان'}\nالحالة: {p.status.value}\nالتقييم: {p.score if p.score is not None else '-'}\n\n{p.description or ''}"
                    buttons = [[_btn(T.PUBLISH_NOW, encode("publish", p.id)), _btn(T.SCHEDULE, encode("schedule", p.id))], [_btn(T.SKIP, encode("skip", p.id)), _btn(T.RETRY, encode("retry", p.id))], [_btn(T.BACK, encode("queue"))]]
                    await self.api.edit_message(chat, mid, text, _kb(buttons))
                    return True
                if action in {"publish", "schedule", "skip", "retry"}:
                    p = await db.get(Product, int(value))
                    if not p:
                        raise ValueError
                    if action == "publish":
                        result = await publish_now(db, p)
                        if result.outcome == "scheduled":
                            post = (await db.execute(select(ScheduledPost).where(ScheduledPost.product_id == p.id, ScheduledPost.status == PostPublishStatus.SCHEDULED).order_by(ScheduledPost.id.desc()).limit(1))).scalars().first()
                            if post:
                                outcome = await publish_scheduled_post(db, post.id, GraphAPIInstagramClient.from_settings())
                                await self.api.send_message(chat, f"{T.DONE}: {outcome.outcome}", self.menu())
                                return True
                    elif action == "schedule":
                        result = await schedule(db, p)
                    elif action == "skip":
                        result = await skip(db, p, reason="قرار من لوحة التحكم")
                    else:
                        result = await retry_failed(db, p)
                    await self.api.send_message(chat, f"{T.DONE}: {result.outcome}", self.menu())
                    return True
                if action == "settings":
                    current = await service.publish_mode(db)
                    await self.api.edit_message(chat, mid, f"الإعدادات\n\nوضع النشر: {'تلقائي' if current == 'AUTO' else 'مراجعة'}\nالمنطقة الزمنية: {self.settings.SCHEDULER_TIMEZONE}", _kb([[_btn(T.MODE, encode("mode"))], [_btn(T.LIMITS, encode("limits"))], [_btn(T.BACK, encode("menu"))]]))
                    return True
                if action == "mode":
                    current = await service.publish_mode(db)
                    await self.api.edit_message(chat, mid, f"وضع النشر الحالي: {'تلقائي' if current == 'AUTO' else 'مراجعة'}", _kb([[_btn(T.AUTO, encode("setmode", "AUTO")), _btn(T.REVIEW, encode("setmode", "REVIEW"))], [_btn(T.BACK, encode("menu"))]]))
                    return True
                if action == "setmode":
                    mode = value if value in {"AUTO", "REVIEW"} else "REVIEW"
                    await set_value(db, PUBLISH_MODE_KEY, mode, "Instagram publish mode selected from Telegram dashboard")
                    await self.api.send_message(chat, f"تم تغيير وضع النشر إلى: {'تلقائي' if mode == 'AUTO' else 'مراجعة'}", self.menu())
                    return True
                if action == "limits":
                    today = datetime.now(ZoneInfo(self.settings.SCHEDULER_TIMEZONE)).date()
                    buttons = [[_btn("منشور", encode("asklimit", "POST")), _btn("ريلز", encode("asklimit", "REEL")), _btn("قصة", encode("asklimit", "STORY"))], [_btn(T.BACK, encode("menu"))]]
                    await self.api.edit_message(chat, mid, await service.limits_text(db, today), _kb(buttons))
                    return True
                if action == "asklimit":
                    await self._set_awaiting(uid, f"limit:{value}")
                    await self.api.send_message(chat, f"أرسل الحد الأقصى اليومي لـ {value} (رقم صحيح):")
                    return True
                if action == "report":
                    today = datetime.now(ZoneInfo(self.settings.SCHEDULER_TIMEZONE)).date()
                    await self.api.edit_message(chat, mid, await service.report_text(db, today), _kb([[_btn(T.BACK, encode("menu"))]]))
                    return True
                if action == "menu":
                    await self.api.edit_message(chat, mid, T.WELCOME, self.menu())
                    return True
                return True
            except Exception:
                logger.exception("Dashboard callback failed")
                await db.rollback()
                await self.api.send_message(chat, "تعذر تنفيذ العملية. حاول مرة أخرى.", self.menu())
                return True

    async def run_once(self) -> int:
        if not self.settings.TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        if not self.admin_ids:
            raise RuntimeError("TELEGRAM_DASHBOARD_ADMIN_IDS must contain at least one id")
        await self.api.delete_webhook()
        await self.api.set_my_commands(COMMANDS)
        offset = await self._get_offset()
        processed = 0
        while True:
            updates = await self.api.get_updates(offset=offset, timeout=0)
            if not updates:
                break
            for update in updates:
                ok = True
                if "message" in update:
                    ok = await self.handle_message(update["message"])
                elif "callback_query" in update:
                    ok = await self.handle_callback(update["callback_query"])
                if not ok:
                    # Leave this update as the next item; actions are designed
                    # to be idempotent, but failed network/provider calls should
                    # not be silently discarded.
                    raise RuntimeError(f"Dashboard update {update.get('update_id')} failed")
                offset = int(update["update_id"]) + 1
                await self._set_offset(offset)
                processed += 1
        return processed

    async def run(self):  # compatibility entrypoint for local permanent mode
        try:
            while True:
                await self.run_once()
                await asyncio.sleep(10)
        finally:
            await self.close()


async def main():
    dashboard = Dashboard()
    try:
        await dashboard.run()
    finally:
        await dashboard.close()


if __name__ == "__main__":
    asyncio.run(main())
