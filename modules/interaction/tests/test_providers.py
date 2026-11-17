import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from providers import FixedModel, FixtureCapture, OpenAICompatibleModel


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_provider_failure_silence_and_capture(self):
        model=FixedModel()
        model.behavior['r']='silent'
        self.assertEqual(await model.generate({'id':'r'},[]),{})
        shot=await FixtureCapture().capture('desktop',2)
        self.assertIn('没有采集真实桌面',shot['description'])
        self.assertTrue((Path(__file__).resolve().parents[1]/'fixtures'/shot['image']).is_file())

    async def test_real_adapter_requires_explicit_configuration(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):OpenAICompatibleModel()

    async def test_real_adapter_encodes_visible_images_and_validates_output(self):
        env={'INTERACTION_MODEL_URL':'https://example.test/v1/chat/completions','INTERACTION_MODEL':'fixture-model','INTERACTION_API_KEY':'test-key'}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return json.dumps({'choices':[{'message':{'content':'{"text":"hello","action":"nod"}'}}]}).encode()
        class Opener:
            def open(self,req,timeout):
                self.req,self.timeout=req,timeout
                return Response()
        opener=Opener()
        with patch.dict(os.environ,env),patch('urllib.request.build_opener',return_value=opener):
            result=await OpenAICompatibleModel().generate({'id':'r','persona':'custom','actions':['nod']},[{'sender':'user','text':'分享','image':'share.png'}])
        self.assertEqual(result,{'text':'hello','action':'nod'})
        data=json.loads(opener.req.data)
        self.assertIn('custom',data['messages'][0]['content'])
        self.assertTrue(data['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('test-key',opener.req.data.decode())
        self.assertEqual(opener.timeout,20)


if __name__=='__main__':unittest.main()
