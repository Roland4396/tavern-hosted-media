"""Wiro-only forms, credentials and provider task interpretation."""
from __future__ import annotations
from typing import Any
import httpx
import uuid
from fastapi import HTTPException
from .jobs import ImageInput, UploadPart, Ticket, Asset, Progress
from .keys import require_api_key
from .settings import settings

def build_wiro_parts(
    *,
    prompt: str,
    max_images: int,
    watermark: bool,
    resolution: str | None,
    aspect_ratio: str | None,
    callback_url: str | None,
    input_image_urls: list[str],
    uploads: list[UploadPart],
) -> list[tuple[str, tuple[str | None, bytes | str, str | None]]]:
    total_images = len(input_image_urls) + len(uploads) + max_images
    if total_images > 15:
        raise HTTPException(
            status_code=400,
            detail="Wiro limit exceeded: input images plus maxImages must be <= 15",
        )

    parts: list[tuple[str, tuple[str | None, bytes | str, str | None]]] = [
        ("prompt", (None, prompt.strip(), None)),
        ("maxImages", (None, str(max_images), None)),
        ("watermark", (None, "true" if watermark else "false", None)),
    ]

    if resolution:
        parts.append(("resolution", (None, resolution, None)))
    if aspect_ratio:
        parts.append(("aspectRatio", (None, aspect_ratio, None)))
    if callback_url:
        parts.append(("callbackUrl", (None, callback_url, None)))
    for image_url in input_image_urls:
        parts.append(("inputImage", (None, image_url, None)))
    for upload in uploads:
        parts.append(("inputImage", (upload.filename, upload.content, upload.content_type)))

    return parts


async def run_wiro(parts: list[tuple[str, tuple[str | None, bytes | str, str | None]]]) -> dict[str, Any]:
    api_key = require_api_key()
    url = f"{settings.base_url}/Run/{settings.model}"
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.post(
            url,
            headers={"x-api-key": api_key},
            files=parts,
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Wiro Run request failed",
                "statusCode": response.status_code,
                "body": response.text[:2000],
            },
        )

    payload = response.json()
    if payload.get("result") is not True:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Wiro Run returned unsuccessful result",
                "errors": payload.get("errors", []),
                "payload": payload,
            },
        )
    return payload


async def get_task_detail(task_token: str) -> dict[str, Any]:
    api_key = require_api_key()
    url = f"{settings.base_url}/Task/Detail"
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
            },
            json={"tasktoken": task_token},
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Wiro Task/Detail request failed",
                "statusCode": response.status_code,
                "body": response.text[:2000],
            },
        )

    payload = response.json()
    if payload.get("result") is not True:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Wiro Task/Detail returned unsuccessful result",
                "errors": payload.get("errors", []),
                "payload": payload,
            },
        )
    return payload


def first_task(detail: dict[str, Any]) -> dict[str, Any] | None:
    tasklist = detail.get("tasklist")
    if not isinstance(tasklist, list) or not tasklist:
        return None
    task = tasklist[0]
    return task if isinstance(task, dict) else None


def task_status(detail: dict[str, Any]) -> str | None:
    task = first_task(detail)
    status = task.get("status") if task else None
    return status if isinstance(status, str) else None


def task_outputs(detail: dict[str, Any]) -> list[dict[str, Any]]:
    task = first_task(detail)
    outputs = task.get("outputs") if task else None
    if not isinstance(outputs, list):
        return []
    return [output for output in outputs if isinstance(output, dict)]


def is_finished(detail: dict[str, Any]) -> bool:
    status = task_status(detail)
    if status == "task_postprocess_end":
        return True
    if task_outputs(detail) and status in {"task_end", "task_postprocess_start"}:
        return True
    return False


def is_failed(detail: dict[str, Any]) -> bool:
    status = task_status(detail)
    if status in {"task_cancel", "task_canceled", "task_cancelled", "task_failed"}:
        return True
    task = first_task(detail)
    debug_error = task.get("debugerror") if task else None
    return bool(debug_error)


def compact_task(detail: dict[str, Any]) -> dict[str, Any]:
    task = first_task(detail) or {}
    return {
        "id": task.get("id"),
        "uuid": task.get("uuid"),
        "status": task.get("status"),
        "elapsedSeconds": task.get("elapsedseconds"),
        "totalCost": task.get("totalcost"),
        "outputs": task_outputs(detail),
        "debugError": task.get("debugerror") or None,
    }



class Wiro:
    name = "wiro"
    label = "Wiro"
    model = settings.model

    async def submit(self, inputs: ImageInput) -> Ticket:
        parts = build_wiro_parts(**vars(inputs))
        try:
            result = await run_wiro(parts)
        except (httpx.RequestError, ValueError) as exc:
            # Do not retry a Run that may already have been accepted.
            raise HTTPException(502, "Wiro submission result unknown",headers={'X-Media-Dispatch':'submitted-or-uncertain'}) from exc
        token = result.get('socketaccesstoken')
        if not isinstance(token, str) or not token:
            raise HTTPException(status_code=502, detail="Wiro Run response missing socketaccesstoken",
                                headers={'X-Media-Dispatch':'submitted-or-uncertain'})
        return Ticket(result.get('taskid'),token)

    async def observe(self, token: str) -> Progress:
        try:
            detail = await get_task_detail(token)
        except (httpx.RequestError, ValueError) as exc:
            raise HTTPException(502, "Wiro task observation unavailable") from exc
        task = compact_task(detail)
        assets = tuple(Asset(i,str(o.get('id') or o.get('uuid') or task['id'] or uuid.uuid4().hex),
                             o['url'],o.get('contenttype'))
                       for i,o in enumerate(task['outputs']) if isinstance(o.get('url'),str) and o['url'])
        failure = ({"message":"Wiro task failed","status":task['status'],"task":first_task(detail)}
                   if is_failed(detail) else None)
        return Progress(task,is_finished(detail),assets,failure,{"status":task['status'],"detail":detail})
