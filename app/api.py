"""Client-facing HTTP contract, independent of vendor transport."""
from __future__ import annotations
import logging
from typing import Any
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from .jobs import ImageJobs, UploadPart

logger=logging.getLogger("wiro-service")


class PublishedFiles(StaticFiles):
    def lookup_path(self, path):
        # Only a completed output may be downloaded, never a partial artifact.
        if '.pending' in path.split('/'):
            return '', None
        return super().lookup_path(path)

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    inputImageUrls: list[str] = Field(default_factory=list)
    maxImages: int = Field(default=1, ge=1, le=15)
    resolution: str | None = None
    aspectRatio: str | None = None
    watermark: bool = False
    callbackUrl: str | None = None
    wait: bool = True
    timeoutSeconds: int | None = Field(default=None, ge=0)
    download: bool = True

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value not in {"2k", "4k"}:
            raise ValueError("resolution must be 2k or 4k")
        return value

    @field_validator("inputImageUrls")
    @classmethod
    def validate_input_image_urls(cls, value: list[str]) -> list[str]:
        if len(value) > 14:
            raise ValueError("inputImageUrls supports up to 14 entries")
        return value


class AdminKeyUpdateRequest(BaseModel):
    apiKey: str = Field(..., min_length=8, max_length=512)

    @field_validator("apiKey")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        api_key = value.strip()
        if any(character.isspace() for character in api_key):
            raise ValueError("apiKey must not contain whitespace")
        return api_key




def make_app(jobs: ImageJobs, output_dir, *, read_key, update_key):
    app = FastAPI(title="Wiro Image Gateway", version="0.1.0")
    app.mount("/files", PublishedFiles(directory=output_dir), name="files")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        metadata = read_key()
        return {
            "ok": True,
            "provider": jobs.provider.name,
            "model": jobs.provider.model,
            "keyConfigured": metadata["configured"],
            "keySource": metadata["source"],
            "outputDir": str(output_dir),
        }


    @app.get("/v1/admin/key")
    async def admin_get_key_status() -> dict[str, Any]:
        return {
            "ok": True,
            "key": read_key(),
        }


    @app.put("/v1/admin/key")
    async def admin_update_key(request: AdminKeyUpdateRequest) -> dict[str, Any]:
        metadata = update_key(request.apiKey)
        logger.info(
            "Wiro API key updated: source=%s fingerprint=%s length=%s",
            metadata["source"],
            metadata["fingerprint"],
            metadata["length"],
        )
        return {
            "ok": True,
            "key": metadata,
        }


    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "ok": True,
            "models": [
                {
                    "id": jobs.provider.model,
                    "provider": jobs.provider.name,
                    "type": "image",
                    "supports": ["text-to-image", "image-to-image", "multi-reference-image"],
                }
            ],
        }


    @app.post("/v1/images/generate")
    async def generate_image(request: GenerateRequest) -> dict[str, Any]:
        return await jobs.start(
            prompt=request.prompt,
            max_images=request.maxImages,
            watermark=request.watermark,
            resolution=request.resolution,
            aspect_ratio=request.aspectRatio,
            callback_url=request.callbackUrl,
            input_image_urls=request.inputImageUrls,
            uploads=[],
            wait=request.wait,
            timeout_seconds=request.timeoutSeconds,
            download=request.download,
        )


    @app.post("/v1/images/edit")
    async def edit_image(
        prompt: str = Form(...),
        inputImage: list[UploadFile] | None = File(default=None),
        inputImageUrl: list[str] | None = Form(default=None),
        maxImages: int = Form(default=1),
        resolution: str | None = Form(default=None),
        aspectRatio: str | None = Form(default=None),
        watermark: bool = Form(default=False),
        callbackUrl: str | None = Form(default=None),
        wait: bool = Form(default=True),
        timeoutSeconds: int | None = Form(default=None),
        download: bool = Form(default=True),
    ) -> dict[str, Any]:
        if not 1 <= maxImages <= 15:
            raise HTTPException(status_code=400, detail="maxImages must be between 1 and 15")
        if resolution not in {None, "", "2k", "4k"}:
            raise HTTPException(status_code=400, detail="resolution must be 2k or 4k")

        uploads: list[UploadPart] = []
        for upload in inputImage or []:
            content = await upload.read()
            uploads.append(
                UploadPart(
                    filename=upload.filename or f"input-{len(uploads)}.png",
                    content=content,
                    content_type=upload.content_type or "application/octet-stream",
                )
            )

        image_urls = inputImageUrl or []
        if len(image_urls) > 14:
            raise HTTPException(status_code=400, detail="inputImageUrl supports up to 14 entries")

        return await jobs.start(
            prompt=prompt,
            max_images=maxImages,
            watermark=watermark,
            resolution=resolution or None,
            aspect_ratio=aspectRatio,
            callback_url=callbackUrl,
            input_image_urls=image_urls,
            uploads=uploads,
            wait=wait,
            timeout_seconds=timeoutSeconds,
            download=download,
        )


    @app.get("/v1/tasks/{task_token}")
    async def task_detail(
        task_token: str,
        download: bool = Query(default=False),
    ) -> dict[str, Any]:
        return await jobs.lookup(task_token, download=download)
    return app
