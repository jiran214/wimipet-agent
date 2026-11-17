import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import Runtime, ROOT

async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)

class AssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime=Runtime(offline=True)
        await self.runtime.start()

    async def asyncTearDown(self):
        report=await self.runtime.close()
        self.assertEqual(report['pendingTasks'],0)

    async def test_real_b_export_loads_in_a_and_c_as_multiple_instances(self):
        script="""import {readFile} from 'node:fs/promises';
        import {parsePackage,fromPackage,confirmDraft,exportPackage} from './modules/character-studio/src/core.mjs';
        const p=await parsePackage(await readFile('./docs/contracts/fixtures/v1/character-package.json','utf8'));
        const project={draft:fromPackage(p),confirmed:p};await confirmDraft(project);
        console.log(JSON.stringify(await exportPackage(project)));"""
        result=await asyncio.to_thread(subprocess.run,['node','--input-type=module','-e',script],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
        package=json.loads(result.stdout)
        one=await self.runtime.load(package)
        two=await self.runtime.load(package)
        self.assertNotEqual(one,two)
        self.assertEqual(len(self.runtime.state()['scene']['roles']),5)
        self.assertTrue(self.runtime.a.renderFrame(one).startswith(b'\x89PNG'))
        broken=copy.deepcopy(package);broken['assets']={}
        with self.assertRaises(ValueError):await self.runtime.load(broken)
        self.assertEqual(len(self.runtime.state()['scene']['roles']),5)

    async def test_three_replies_and_actual_action_completion(self):
        r=self.runtime
        receipt=await r.dispatch(r.envelope('UserInput',dict(kind='message',recipient='all',text='hello',attachments=[])))
        self.assertTrue(receipt['payload']['accepted'])
        await until(lambda:len([a for a in r.state()['view']['processing']['actions'] if a['status']=='completed'])==3)
        view=r.state()['view']
        self.assertEqual(len([m for m in view['messages'] if m['speaker']['type']=='character']),3)
        for action in view['processing']['actions']:
            self.assertEqual(action['history'],['requested','accepted','completed'])

    async def test_image_only_private_then_public_and_policy_rejection(self):
        r=self.runtime;one=r.state()['scene']['roles'][0]['instanceId']
        raw=(ROOT/'modules/interaction/fixtures/share.png').read_bytes()
        image=r.resources.put(raw,'image/png','share.png',r.core.sessionId)
        receipt=await r.dispatch(r.envelope('UserInput',dict(kind='message',recipient=one,text='',attachments=[image])))
        self.assertTrue(receipt['payload']['accepted'])
        await until(lambda:any(m['speaker']['type']=='character' for m in r.state()['view']['messages']))
        await r.dispatch(r.envelope('UserInput',dict(kind='message',recipient='all',text='公开问候',attachments=[])))
        await until(lambda:len([m for m in r.state()['view']['messages'] if m['speaker']['type']=='character'])==4)
        self.assertTrue(all(not m['attachments'] for m in r.core._context(one,'all')))
        policy=copy.deepcopy(r.state()['scene']['observationPolicy']);policy.update(paused=False,policyRevision=2)
        result=await r.dispatch(r.envelope('ObservationPolicy',policy))
        self.assertEqual(result['payload']['error']['code'],'unsupported-capability')
        self.assertTrue(r.state()['scene']['observationPolicy']['paused'])

    async def test_d_cancel_mapping_and_repeat_input(self):
        r=self.runtime
        req=r.envelope('UserInput',dict(kind='story-event',recipient='all',text='下雨了',attachments=[]))
        await r.dispatch(req);await r.dispatch(req)
        receipt=await r.dispatch(r.envelope('CancelRequest',dict(requestId=req['requestId'])))
        self.assertTrue(receipt['payload']['accepted'])
        self.assertEqual(len([m for m in r.state()['view']['messages'] if m['speaker']['type']=='director']),1)

if __name__=='__main__':unittest.main()
