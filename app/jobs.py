"""Hosted-image task lifecycle. No provider wire dialect, files, chat or GPU state."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Protocol

from fastapi import HTTPException


@dataclass(frozen=True)
class UploadPart:
    filename: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class ImageInput:
    prompt: str
    max_images: int
    watermark: bool
    resolution: str | None
    aspect_ratio: str | None
    callback_url: str | None
    input_image_urls: list[str]
    uploads: list[UploadPart]


@dataclass(frozen=True)
class Ticket:
    id: Any
    token: str


@dataclass(frozen=True)
class Asset:
    index: int
    id: str
    url: str
    content_type: str | None


@dataclass(frozen=True)
class Progress:
    task: dict
    finished: bool
    assets: tuple[Asset, ...]
    failure: dict | None
    timeout_detail: dict


class Provider(Protocol):
    name: str
    label: str
    model: str
    async def submit(self, inputs: ImageInput) -> Ticket: ...
    async def observe(self, token: str) -> Progress: ...


class Delivery(Protocol):
    async def save(self, assets: tuple[Asset,...]) -> list[dict]: ...


class ImageJobs:
    def __init__(self, provider: Provider, delivery: Delivery, *, timeout: int, interval: float):
        self.provider, self.delivery = provider, delivery
        self.timeout, self.interval = timeout, interval

    def identity(self):
        return {'provider': self.provider.name, 'model': self.provider.model}

    async def start(self, *, wait: bool, timeout_seconds: int | None, download: bool, **inputs):
        # Exactly one submit, even if observation or delivery subsequently fails.
        ticket = await self.provider.submit(ImageInput(**inputs))
        result = {'ok': True, **self.identity(), 'taskId': ticket.id, 'taskToken': ticket.token}
        if not wait:
            return result
        try:
            return await self.finish(ticket, result, timeout_seconds, download)
        except HTTPException as error:
            # The caller may recover by GET, without sending a second generation.
            # Public bodies and existing Wiro taskToken values stay compatible.
            if (len(ticket.token) <= 2048 and ticket.token.isascii()
                    and all(33 <= ord(ch) < 127 for ch in ticket.token)):
                error.headers = {**(error.headers or {}), 'X-Media-Task-Token':ticket.token}
            raise

    async def finish(self, ticket: Ticket, result: dict, timeout_seconds, download):
        timeout = self.timeout if timeout_seconds is None else timeout_seconds
        started = time.monotonic()
        while True:
            state = await self.provider.observe(ticket.token)
            if state.finished:
                return {**result, 'task': state.task,
                        'images': await self.delivery.save(state.assets) if download else []}
            if state.failure is not None:
                raise HTTPException(status_code=502, detail=state.failure)
            if timeout > 0 and time.monotonic()-started > timeout:
                raise HTTPException(status_code=504, detail={
                    'message': self.provider.label+' task polling timed out', 'timeoutSeconds':timeout,
                    'taskToken':ticket.token, **state.timeout_detail})
            await asyncio.sleep(self.interval)

    async def lookup(self, token: str, *, download: bool):
        state = await self.provider.observe(token)
        return {'ok': True, **self.identity(), 'taskToken':token, 'finished':state.finished,
                'task':state.task,'images':await self.delivery.save(state.assets) if download and state.finished else []}
