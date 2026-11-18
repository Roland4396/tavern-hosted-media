"""Composition root. Keep the existing Wiro endpoint and opaque task-token namespace."""
import logging
import os
from .api import make_app
from .jobs import ImageJobs
from .delivery import LocalDelivery
from .wiro import Wiro
from .keys import key_metadata, write_api_key
from .settings import settings

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s %(name)s %(message)s")
delivery = LocalDelivery(settings.output_dir, settings.public_base_url,timeout=settings.request_timeout,max_bytes=settings.max_download_bytes,
                         fetch_error="Failed to download Wiro output",size_error="Downloaded image exceeds WIRO_MAX_DOWNLOAD_MB")
jobs = ImageJobs(Wiro(),delivery,timeout=settings.task_timeout,interval=settings.poll_interval)
app = make_app(jobs,settings.output_dir,read_key=key_metadata,update_key=write_api_key)
