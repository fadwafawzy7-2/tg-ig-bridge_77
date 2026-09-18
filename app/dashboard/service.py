"""Dashboard read/write operations. Telegram handlers stay thin."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.channel import Channel
from app.models.daily_limit import DailyLimit
from app.models.enums import ChannelStatus, ContentType, ProductStatus, PostPublishStatus, StoryStatus
from app.models.product import Product
from app.models.scheduled_post import ScheduledPost
from app.models.story import Story
from app.models.published_post import PublishedPost
from app.models.error_log import ErrorLog
from app.telegram.channel_service import add_channel, disable_channel, enable_channel, list_channels
from app.dashboard.settings_service import get_value, set_value, PUBLISH_MODE_KEY
from app.queue.operations import publish_now, skip, retry_failed, reprioritize, schedule


_STATUS_AR = {
    ProductStatus.DISCOVERED: "مكتشف", ProductStatus.PARSED: "محلل",
    ProductStatus.VALIDATED: "متحقق", ProductStatus.ELIGIBLE: "مؤهل",
    ProductStatus.QUEUED: "بالانتظار", ProductStatus.SCHEDULED: "مجدول",
    ProductStatus.PUBLISHED: "منشور", ProductStatus.SKIPPED: "متخطى",
    ProductStatus.DUPLICATE: "مكرر", ProductStatus.REJECTED: "مرفوض",
    ProductStatus.FAILED: "فاشل",
}
_TYPE_AR = {ContentType.POST: "منشور", ContentType.REEL: "ريلز", ContentType.STORY: "قصة"}

def _status_ar(status: ProductStatus) -> str:
    return _STATUS_AR.get(status, "غير معروف")

async def channels_text(session: AsyncSession) -> str:
    rows = await list_channels(session)
    if not rows:
        return "لا توجد قنوات مضافة."
    lines = ["القنوات:"]
    for c in rows:
        state = "مفعلة" if c.status == ChannelStatus.ACTIVE else "معطلة"
        handle = f"@{c.channel_username}" if c.channel_username else "بدون معرف"
        lines.append(f"#{c.id} — {c.channel_title} ({handle}) — {state}")
    return "\n".join(lines)

async def products_text(session: AsyncSession, limit: int = 10) -> str:
    rows = (await session.execute(select(Product).order_by(Product.discovered_at.desc()).limit(limit))).scalars().all()
    if not rows:
        return "لا توجد منتجات."
    lines = ["آخر المنتجات:"]
    for p in rows:
        lines.append(f"{p.product_code} — {p.title or 'بدون عنوان'} — {_status_ar(p.status)} — التقييم: {p.score if p.score is not None else '-'}")
    return "\n".join(lines)


async def product_by_code(session: AsyncSession, product_code: str) -> Product | None:
    code = product_code.strip().upper()
    return (await session.execute(select(Product).where(Product.product_code == code))).scalar_one_or_none()

async def queue_text(session: AsyncSession, limit: int = 10) -> str:
    rows = (await session.execute(select(Product).where(Product.status.in_([ProductStatus.ELIGIBLE, ProductStatus.QUEUED, ProductStatus.SCHEDULED])).order_by(Product.score.desc().nullslast(), Product.id.asc()).limit(limit))).scalars().all()
    if not rows:
        return "قائمة الانتظار فارغة."
    lines = ["قائمة الانتظار:"]
    for p in rows:
        lines.append(f"{p.product_code} — {p.title or 'بدون عنوان'} — {_status_ar(p.status)} — {p.score if p.score is not None else '-'}")
    return "\n".join(lines)

async def limits_text(session: AsyncSession, today) -> str:
    rows = (await session.execute(select(DailyLimit).where(DailyLimit.date == today).order_by(DailyLimit.content_type))).scalars().all()
    values = {r.content_type: r for r in rows}
    lines = [f"الحدود اليومية — {today}"]
    for typ in ContentType:
        r = values.get(typ)
        lines.append(f"{_TYPE_AR[typ]}: {r.max_allowed if r else 0} (منشور: {r.published_count if r else 0})")
    return "\n".join(lines)

async def set_limit(session: AsyncSession, target_date, content_type: ContentType, maximum: int) -> None:
    row = (await session.execute(select(DailyLimit).where(DailyLimit.date == target_date, DailyLimit.content_type == content_type))).scalar_one_or_none()
    if row:
        row.max_allowed = maximum
    else:
        session.add(DailyLimit(date=target_date, content_type=content_type, max_allowed=maximum, published_count=0))
    await session.commit()

async def report_text(session: AsyncSession, target_date) -> str:
    counts = {}
    for status in ProductStatus:
        counts[status.value] = await session.scalar(select(func.count()).select_from(Product).where(Product.status == status))
    published = await session.scalar(select(func.count()).select_from(PublishedPost))
    failed = await session.scalar(select(func.count()).select_from(ErrorLog).where(ErrorLog.resolved.is_(False)))
    return (f"تقرير {target_date}\n"
            f"المنتجات المنشورة: {published or 0}\n"
            f"الأخطاء غير المعالجة: {failed or 0}\n"
            f"مكتشفة: {counts['DISCOVERED']} | محللة: {counts['PARSED']}\n"
            f"متحققة: {counts['VALIDATED']} | مؤهلة: {counts['ELIGIBLE']}\n"
            f"بالانتظار: {counts['QUEUED']} | مجدولة: {counts['SCHEDULED']}\n"
            f"مرفوضة: {counts['REJECTED']} | متخطاة: {counts['SKIPPED']} | فاشلة: {counts['FAILED']}")

async def publish_mode(session: AsyncSession) -> str:
    return (await get_value(session, PUBLISH_MODE_KEY, "REVIEW")) or "REVIEW"
