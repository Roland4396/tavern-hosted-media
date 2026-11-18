"""Materialize provider outputs; never submits or polls a generation job."""
import hashlib
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from .jobs import Asset


class LocalDelivery:
    def __init__(self, directory, base_url, *, timeout, max_bytes, fetch_error, size_error):
        self.directory, self.base_url = Path(directory), base_url
        self.timeout, self.max_bytes = timeout, max_bytes
        self.fetch_error, self.size_error = fetch_error, size_error
        self.directory.mkdir(parents=True,exist_ok=True)

    @staticmethod
    def filename(a: Asset):
        ext={'image/jpeg':'.jpg','image/webp':'.webp','image/png':'.png'}.get((a.content_type or '').lower())
        if not ext:
            ext = Path(urlparse(a.url).path).suffix.lower()
            ext = '.jpg' if ext=='.jpeg' else ext if ext in ('.jpg','.png','.webp') else '.png'
        stable = a.id if re.fullmatch(r'[\w.-]{1,180}',a.id) else hashlib.sha256(a.id.encode()).hexdigest()
        return f'{stable}-{a.index}{ext}'

    async def save(self, assets):
        saved=[]
        async with httpx.AsyncClient(timeout=self.timeout,follow_redirects=True) as client:
            for a in assets:
                filename=self.filename(a); path=self.directory/filename
                # Complete outputs are published atomically. Concurrent lookups may
                # fetch but never replace a previously delivered artifact.
                tmp=None
                try:
                    async with client.stream('GET',a.url) as r:
                        if r.status_code >= 400:
                            # Read at most a short provider diagnostic, not a full error download.
                            prefix=bytearray()
                            async for chunk in r.aiter_bytes(chunk_size=1024):
                                prefix.extend(chunk[:1024-len(prefix)])
                                if len(prefix)>=1024: break
                            raise HTTPException(502,{'message':self.fetch_error,'statusCode':r.status_code,
                                                    'url':a.url,'body':prefix.decode(r.encoding or 'utf-8',errors='replace')[:1000]})
                        size=0
                        # Same filesystem even if output_dir itself is a bind mount.
                        # The HTTP adapter never serves the private staging directory.
                        staging = self.directory/'.pending'
                        staging.mkdir(mode=0o700,exist_ok=True)
                        new = staging/uuid.uuid4().hex
                        with new.open('xb') as f:
                            tmp=new
                            async for chunk in r.aiter_bytes():
                                size+=len(chunk)
                                if size>self.max_bytes:raise HTTPException(502,{'message':self.size_error,'url':a.url})
                                f.write(chunk)
                            f.flush();os.fsync(f.fileno())
                        try:os.link(tmp,path)
                        except FileExistsError:pass
                        saved.append({'sourceUrl':a.url,'filename':filename,'path':str(path),'url':f'{self.base_url}/files/{filename}',
                                      'contentType':a.content_type or r.headers.get('content-type'),'size':path.stat().st_size})
                except httpx.RequestError as exc:
                    raise HTTPException(502,{'message':self.fetch_error,'url':a.url}) from exc
                finally:
                    if tmp is not None:tmp.unlink(missing_ok=True)
        return saved
