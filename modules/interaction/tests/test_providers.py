import asyncio
import json
import os
import unittest
from unittest.mock import patch
from support import *
from providers import FixedModel, OpenAICompatibleModel
from protocol import ProtocolError


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_provider_silence_and_loaded_capability_only(self):
        model=FixedModel()
        role=dict(instanceId='r',name='角色',supportedActions=[])
        model.behavior['r']='silent'
        self.assertEqual(await model.generate(role,[],'session'),{})
        model.behavior['r']='normal'
        result=await model.generate(role,[],'session')
        self.assertIsNone(result['actionId'])

    async def test_real_requires_configuration(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):OpenAICompatibleModel(MemoryResources())

    async def test_real_adapter_resolves_reference_and_encodes_picture(self):
        bridge=MemoryResources()
        item=bridge.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','a.png','session')
        env=dict(INTERACTION_MODEL_URL='https://example.test/v1/chat/completions',INTERACTION_MODEL='test',INTERACTION_API_KEY='test-key')
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        client=MagicMock()
        client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"text":"hello","actionId":null}'))])
        model=OpenAICompatibleModel(bridge,env,client=client)
        result=await model.generate(dict(instanceId='r',name='角色',persona='custom',supportedActions=[]),[dict(text='',attachments=[item])],'session')
        self.assertEqual(result['text'],'hello')
        data=client.chat.completions.create.call_args.kwargs
        self.assertEqual(data['model'],'test')
        self.assertIn('custom',data['messages'][0]['content'])
        self.assertTrue(data['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('test-key',json.dumps(data))
        self.assertEqual(model.activeRequests,0)

    async def test_resource_lifecycle_and_session_isolation(self):
        bridge=MemoryResources()
        raw=(ROOT/'fixtures/share.png').read_bytes()
        item=bridge.put(raw,'image/png','a.png','one')
        self.assertEqual(await bridge.resolve(item['contentRef'],'one'),raw)
        with self.assertRaises(ProtocolError):await bridge.resolve(item['contentRef'],'two')
        await bridge.releaseSession('one')
        with self.assertRaises(ProtocolError):await bridge.resolve(item['contentRef'],'one')
