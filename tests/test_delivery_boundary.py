import asyncio
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi import HTTPException

from app.api import make_app
from app.jobs import ImageJobs, Progress, Ticket


class Other:
    """An entirely different provider: no Wiro wire schema, key reader, or GPU."""
    name, label, model='alternate','Alternate','fixture-image'

    def __init__(self):self.actions=[];self.ready=True
    async def submit(self,inputs):
        self.actions.append(('accepted',inputs.prompt));return Ticket(27,'job-27')
    async def observe(self,token):
        self.actions.append(('query',token))
        return Progress({'id':27},self.ready,(),None,{})


class Output:
    def __init__(self):self.fail=False
    async def save(self,assets):
        if self.fail:raise HTTPException(502,'artifact temporarily unavailable')
        return [{'filename':'fixture.png'}]


class UncoupledDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.folder=Path(tmp.name)
        self.vendor,self.output=Other(),Output()
        self.jobs=ImageJobs(self.vendor,self.output,timeout=1,interval=.01)
        app=make_app(self.jobs,self.folder,read_key=lambda:{'configured':False,'source':'none'},update_key=lambda x:{})
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated')
        self.addAsyncCleanup(self.client.aclose)

    async def test_alternate_provider_and_recovery(self):
        self.output.fail=True
        r=await self.client.post('/v1/images/generate',json={'prompt':'one'})
        self.assertEqual(r.status_code,502);token=r.headers['X-Media-Task-Token']
        self.output.fail=False
        r=await self.client.get('/v1/tasks/'+token+'?download=true')
        self.assertEqual(r.status_code,200);self.assertEqual((r.json()['provider'],r.json()['images'][0]['filename']),('alternate','fixture.png'))
        self.assertEqual(self.vendor.actions,[('accepted','one'),('query','job-27'),('query','job-27')])

    async def test_cancelled_wait_does_not_resubmit(self):
        self.vendor.ready=False
        t=asyncio.create_task(self.client.post('/v1/images/generate',json={'prompt':'two'}))
        for _ in range(20):
            if len(self.vendor.actions)>1:break
            await asyncio.sleep(.005)
        t.cancel()
        with self.assertRaises(asyncio.CancelledError):await t
        self.assertEqual(sum(x[0]=='accepted' for x in self.vendor.actions),1)
        count=len(self.vendor.actions);await asyncio.sleep(.04);self.assertEqual(len(self.vendor.actions),count)

    async def test_static_route_exposes_completed_artifacts_only(self):
        p=self.folder/'.pending';p.mkdir();(p/'partial').write_text('fixture')
        self.assertEqual((await self.client.get('/files/.pending/partial')).status_code,404)
        self.assertFalse(self.vendor.actions)

if __name__=='__main__':unittest.main()
