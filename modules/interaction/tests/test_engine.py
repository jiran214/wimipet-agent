import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from engine import Interaction


class Model:
    mode = 'test'
    def __init__(self):
        self.calls = []
        self.delays = {}
        self.fail = set()
        self.action = 'nod'

    async def generate(self, role, context):
        self.calls.append((role['id'], context))
        await asyncio.sleep(self.delays.get(role['id'], .001))
        if role['id'] in self.fail:
            raise RuntimeError('secret credential must not appear')
        return dict(text=role['name']+':'+(context[-1]['text'] if context else 'hello'), action=self.action)


class Capture:
    def __init__(self):
        self.calls=[]
        self.delay=.001
    async def capture(self, scope, scene):
        self.calls.append((scope,scene))
        await asyncio.sleep(self.delay)
        return dict(description='fixture scene',image='desktop.png')


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model, self.capture = Model(), Capture()
        self.now = 100
        roles=json.loads((ROOT/'fixtures/roles.json').read_text(encoding='utf-8'))
        self.engine=Interaction(roles,self.model,self.capture,lambda:self.now,timeout=.5)

    async def asyncTearDown(self):
        await self.engine.close()

    async def settle(self, duration=None):
        async def drain():
            while self.engine.tasks:
                await asyncio.gather(*list(self.engine.tasks),return_exceptions=True)
        await asyncio.wait_for(drain(),3)

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),3)

    async def test_public_image_three_roles_and_order(self):
        self.engine.submit('雨天绿植',kind='image',image='share.png')
        await self.settle()
        self.assertEqual(len(self.model.calls),3)
        self.assertEqual({m['sender'] for m in self.engine.messages[1:]},set(self.engine.roles))
        self.assertEqual([m['seq'] for m in self.engine.messages],[1,2,3,4])
        self.assertEqual(len(self.engine.actions),3)
        self.assertTrue(all(c[1][0]['image']=='share.png' for c in self.model.calls))

    async def test_private_isolation_and_public_output_does_not_quote_private(self):
        self.engine.submit('私密暗号', ['moss'])
        await self.settle()
        self.assertEqual(self.engine.context('spark'),[])
        self.engine.submit('公开话题')
        await self.settle()
        for role,context in self.model.calls[1:]:
            self.assertFalse(any('私密暗号' in m['text'] for m in context))
        self.assertEqual(len(self.engine.viewer('spark')['messages']),4)

    async def test_private_followup_continues_same_list(self):
        self.engine.submit('起点',['moss'])
        await self.settle()
        first=self.engine.messages[0]['id']
        self.engine.submit('继续',['moss'])
        await self.settle()
        self.assertEqual(self.model.calls[-1][1][0]['id'],first)
        self.assertEqual(len(self.engine.messages),4)

    async def test_failure_timeout_do_not_block_peer(self):
        self.model.fail={'moss'}
        self.model.delays['spark']=1
        self.engine.submit('开始')
        await self.settle()
        statuses={j['role']:j['status'] for j in self.engine.jobs}
        self.assertEqual(statuses,dict(moss='failed',spark='timeout',orbit='done'))
        self.assertNotIn('secret',json.dumps(self.engine.snapshot(True)))

    async def test_user_interrupt_cancels_old_generation(self):
        self.model.delays={r:.2 for r in self.engine.roles}
        self.engine.submit('旧输入')
        await self.until(lambda:len(self.model.calls)==3)
        self.engine.submit('插话',['moss'])
        await self.settle()
        self.assertEqual([m['sender'] for m in self.engine.messages],['user','user','moss'])
        self.assertTrue(any(j['status']=='cancelled' for j in self.engine.jobs))

    async def test_auto_bounded_stop_and_interrupt(self):
        self.engine.start_auto(3)
        await self.settle(.55)
        self.assertEqual(len(self.engine.messages),3)
        self.assertFalse(self.engine.snapshot()['auto'])
        self.engine.start_auto(12)
        await self.until(lambda:len(self.engine.messages)==4)
        self.engine.submit('用户优先',['orbit'])
        await self.settle(.2)
        self.assertFalse(self.engine.snapshot()['auto'])
        count=len(self.engine.messages)
        self.engine.cancel()
        await self.settle(.2)
        self.assertEqual(count,len(self.engine.messages))

    async def test_observation_pause_inflight_and_cooldown_scene_change(self):
        self.capture.delay=.2
        self.engine.desktop()
        await self.until(lambda:len(self.capture.calls)==1)
        self.engine.observation(True,'active-window',10)
        await self.settle()
        self.assertFalse(self.engine.messages)
        self.assertEqual(self.engine.jobs[0]['status'],'stale')
        self.assertFalse(self.engine.desktop(force=True))
        self.engine.observation(False,'desktop',10)
        self.now+=11
        self.assertTrue(self.engine.desktop())
        await self.until(lambda:len(self.capture.calls)==2)
        self.assertFalse(self.engine.desktop()) # 新窗口但节流，旧截图仍须过期
        await self.settle()
        self.assertFalse(self.engine.messages)
        self.now+=11
        self.engine.desktop()
        await self.settle()
        self.assertEqual(self.capture.calls[-1][0],'desktop')
        self.assertEqual(self.engine.messages[0]['kind'],'desktop')

    async def test_pause_during_visual_model(self):
        self.model.delays={r:.2 for r in self.engine.roles}
        self.engine.desktop(force=True)
        await self.until(lambda:len(self.model.calls)==3)
        self.engine.observation(True,'active-window',10)
        await self.settle()
        self.assertEqual(len(self.engine.messages),1) # 可保留已采集事件，不发布迟到回复
        self.assertTrue(all(j['status']=='stale' for j in self.engine.jobs if j['role']!='capture'))

    async def test_removed_role_and_uncooperative_late_model(self):
        class LateModel(Model):
            async def generate(self,role,context):
                try:
                    await asyncio.sleep(.02)
                except asyncio.CancelledError:
                    await asyncio.sleep(.001)
                return dict(text='迟到',action='nod')
        self.engine.model=LateModel()
        self.engine.submit('开始',['moss'])
        await asyncio.sleep(.002)
        self.engine.remove('moss')
        await self.settle()
        self.assertEqual(len(self.engine.messages),1)
        self.engine.submit('旧',['spark'])
        await asyncio.sleep(.002)
        self.engine.cancel()
        await self.settle()
        self.assertEqual(len(self.engine.messages),2)

    async def test_action_rejection_not_success_and_no_self_trigger(self):
        self.engine.submit('挥手',['moss'])
        await self.settle()
        action=self.engine.actions[0]
        with self.assertRaises(ValueError):
            self.engine.feedback(action['id'],'completed')
        self.engine.feedback(action['id'],'rejected')
        self.assertEqual(action['status'],'rejected')
        self.assertIn('rejected',self.engine.context('moss')[-1]['text'])
        self.assertEqual(self.engine.context('spark'),[])
        self.assertFalse(self.engine.desktop('bubble'))
        self.assertFalse(self.engine.desktop('action-feedback'))
        self.assertEqual(len(self.model.calls),1)

    async def test_story_followup_reset_and_resource_cleanup(self):
        self.engine.submit('找到绿植',kind='story')
        await self.settle()
        self.engine.submit('刚才发生了什么',['orbit'])
        await self.settle()
        self.assertTrue(any(m['kind']=='story' for m in self.model.calls[-1][1]))
        self.engine.start_auto(12)
        await self.engine.reset()
        await self.settle(.2)
        self.assertEqual(self.engine.messages,[])
        self.assertEqual(self.engine.jobs,[])
        self.assertEqual(len(self.engine.tasks),0)

    async def test_invalid_action_and_config(self):
        self.model.action='teleport'
        self.engine.submit('开始',['moss'])
        await self.settle()
        self.assertEqual(self.engine.actions,[])
        self.assertEqual(self.engine.jobs[0]['action_error'],'unknown-action')
        for targets in ([],['missing']):
            with self.assertRaises(ValueError):self.engine.submit('hi',targets)
        with self.assertRaises(ValueError):self.engine.start_auto(99)
        with self.assertRaises(ValueError):self.engine.observation(False,'secret',1)

    async def test_timeout_cannot_be_overridden_by_provider_ignoring_cancel(self):
        class IgnoresCancel(Model):
            async def generate(self,role,context):
                try:
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    await asyncio.sleep(.02)
                return {'text':'不应发布'}
        self.engine.model=IgnoresCancel()
        self.engine.timeout=.1
        self.engine.submit('输入',['moss'])
        await self.settle()
        self.assertEqual(self.engine.jobs[0]['status'],'timeout')
        self.assertEqual(len(self.engine.messages),1)


if __name__=='__main__':
    unittest.main()
