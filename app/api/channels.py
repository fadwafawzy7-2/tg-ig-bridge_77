"""
Channel management endpoints — dynamic add/list/enable/disable of the
Telegram channels being monitored.

Plain CRUD only; this is not the "Telegram Dashboard" (a separate,
later-phase feature) — just the minimal interface needed to add/remove
channels without editing the database by hand. Channels are never hard-
deleted: "removing" one calls `disable_channel()` (soft, reversible).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.enums import ChannelStatus
from app.telegram import channel_service

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelCreateRequest(BaseModel):
    telegram_channel_id: int
    channel_title: str = Field(min_length=1, max_length=500)
    channel_username: str | None = None


class ChannelResponse(BaseModel):
    id: int
    telegram_channel_id: int
    channel_username: str | None
    channel_title: str
    status: ChannelStatus

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ChannelResponse])
async def list_channels_endpoint(
    status: ChannelStatus | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[ChannelResponse]:
    channels = await channel_service.list_channels(db, status=status)
    return [ChannelResponse.model_validate(c) for c in channels]


@router.post("", response_model=ChannelResponse, status_code=201)
async def add_channel_endpoint(
    payload: ChannelCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    channel = await channel_service.add_channel(
        db,
        telegram_channel_id=payload.telegram_channel_id,
        channel_title=payload.channel_title,
        channel_username=payload.channel_username,
    )
    return ChannelResponse.model_validate(channel)


@router.post("/{channel_id}/enable", response_model=ChannelResponse)
async def enable_channel_endpoint(
    channel_id: int, db: AsyncSession = Depends(get_db)
) -> ChannelResponse:
    channel = await channel_service.enable_channel(db, channel_id)
    return ChannelResponse.model_validate(channel)


@router.post("/{channel_id}/disable", response_model=ChannelResponse)
async def disable_channel_endpoint(
    channel_id: int, db: AsyncSession = Depends(get_db)
) -> ChannelResponse:
    channel = await channel_service.disable_channel(db, channel_id)
    return ChannelResponse.model_validate(channel)
