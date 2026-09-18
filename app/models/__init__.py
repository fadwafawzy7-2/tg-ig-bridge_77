"""
Domain model package.

Every model module must be imported here so that `Base.metadata` (in
`app.db.base`) is fully populated before Alembic's autogenerate — or
`Base.metadata.create_all()` in tests — inspects it. `alembic/env.py`
imports this package for exactly that reason.
"""

from app.models.analytics import Analytics
from app.models.caption import Caption
from app.models.channel import Channel
from app.models.daily_limit import DailyLimit
from app.models.error_log import ErrorLog
from app.models.media import Media
from app.models.product import Product
from app.models.product_source_message import ProductSourceMessage
from app.models.published_post import PublishedPost
from app.models.scan_state import ScanState
from app.models.scheduled_post import ScheduledPost
from app.models.setting import Setting
from app.models.source_message import SourceMessage
from app.models.story import Story

__all__ = [
    "Analytics",
    "Caption",
    "Channel",
    "DailyLimit",
    "ErrorLog",
    "Media",
    "Product",
    "ProductSourceMessage",
    "PublishedPost",
    "ScanState",
    "ScheduledPost",
    "Setting",
    "SourceMessage",
    "Story",
]
