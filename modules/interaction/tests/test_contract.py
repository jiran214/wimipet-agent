import asyncio
import copy
import hashlib
import json
import unittest
import uuid
from support import *
from protocol import ProtocolError


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock,self.model,self.resources=Clock(),Model(),MemoryResources()
        self.body=Body(self.clock)
        self.capture=Capture(self.resources,self.clock)
        self.e=create_interaction(model=self.model,resolver=self.resources,body=self.body,capture=self.capture,
                                  clock=lambda:self.clock.mono,epoch_ms=lambda:self.clock.epoch,timeout=.5)
        for instance in ('instance-one','instance-two','instance-three'):
            result=await self.send('RegisterCharacter',dict(package=PACKAGE,loaded=self.body.loaded(instance)))
            self.assertTrue(result['payload']['accepted'])

    async def asyncTearDown(self):
        report=await self.e.close()
        self.assertEqual(report['pendingTasks'],0)

    def request(self,kind,payload=None,ttl=60000,rid=None):
        return dict(protocol=PROTOCOL,sessionId=self.e.sessionId,requestId=rid or uuid.uuid4().hex,
                    expiresAtMs=self.clock.epoch+ttl,type=kind,payload=payload or {})

    async def send(self,kind,payload=None,**kwargs):
        return await self.e.dispatch(self.request(kind,payload,**kwargs))

    async def input(self,text='hello',recipient='all',attachments=None,**kwargs):
        return await self.send('UserInput',dict(kind='message',text=text,recipient=recipient,attachments=attachments or []),**kwargs)

    def view(self,instance=None):
        return self.e.getView(dict(type='character',instanceId=instance) if instance else dict(type='user'))

    async def policy(self,paused=False,revision=None):
        return await self.send('SetObservationPolicy',dict(policyRevision=revision or self.e.policyRevision+1,
            paused=paused,scope='active-window',cooldownMs=10000,excludedRegions=[]))

    async def test_common_fixture_is_unchanged_and_same_package_has_three_instances(self):
        common=ROOT.parents[1]/'docs/contracts/fixtures/v1/character-package.json'
        self.assertEqual(hashlib.sha256(common.read_bytes()).hexdigest(),hashlib.sha256((ROOT/'fixtures/character-package.v1.json').read_bytes()).hexdigest())
        self.assertEqual(len(self.e.roles),3)
        self.assertEqual({r['packageId'] for r in self.e.roles.values()},{PACKAGE['packageId']})
        self.assertEqual({a['actionId'] for r in self.e.roles.values() for a in r['supportedActions']},{'idle','wave'})

    async def test_image_only_input_formal_messages_epoch_and_seq(self):
        item=self.resources.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','分享.png',self.e.sessionId)
        receipt=await self.input('',attachments=[item])
        self.assertTrue(receipt['payload']['accepted'])
        self.assertEqual(receipt['type'],'InputReceipt')
        await drain(self.e)
        messages=self.view()['messages']
        self.assertEqual([m['seq'] for m in messages],[1,2,3,4])
        self.assertEqual(messages[0]['text'],'')
        self.assertEqual(messages[0]['attachments'],[item])
        self.assertEqual(messages[0]['atMs'],self.clock.epoch)
        self.assertTrue(all('messageId' in m and 'speaker' in m and 'time' not in m for m in messages))
        self.assertEqual({m['speaker']['instanceId'] for m in messages[1:]},set(self.e.roles))
        self.assertEqual({c['payload']['actionId'] for c in self.body.commands},{'wave'})

    async def test_private_then_public_filters_generation_and_role_views(self):
        await self.input('私密月亮','instance-one')
        await drain(self.e)
        self.assertEqual(self.view('instance-two')['messages'],[])
        self.assertEqual(self.view('instance-two')['processing']['tasks'],[])
        await self.input('公开话题')
        await drain(self.e)
        for role,context,_ in self.model.calls[1:]:
            self.assertNotIn('私密月亮',json.dumps(context,ensure_ascii=False))
        self.assertEqual(len(self.view('instance-two')['messages']),4)
        self.assertEqual(len(self.view()['messages']),6)
        self.assertNotIn('contexts',self.view())

    async def test_private_followup_and_story_share_single_list(self):
        await self.send('UserInput',dict(kind='story-event',text='雨天发现绿植',recipient='all',attachments=[]))
        await drain(self.e)
        await self.input('这是秘密','instance-one')
        await drain(self.e)
        await self.input('接着说','instance-one')
        await drain(self.e)
        kinds={m['kind'] for m in self.model.calls[-1][1]}
        self.assertIn('story-event',kinds)
        self.assertIn('这是秘密',json.dumps(self.model.calls[-1][1],ensure_ascii=False))

    async def test_subscribe_is_async_filtered_atomic_and_unsubscribable(self):
        updates=[]
        unsub=self.e.subscribe(dict(type='character',instanceId='instance-two'),updates.append)
        self.assertEqual(updates,[])
        snapshot=self.view('instance-two')
        await self.input('只有第一只知道','instance-one')
        await drain(self.e)
        await until(lambda:len(updates)>0)
        self.assertTrue(all(v['messages']==[] for v in updates))
        self.assertTrue(all(v['revision']>=snapshot['revision'] for v in updates))
        updates[-1]['messages'].append({'text':'外部篡改'})
        self.assertNotIn('外部篡改',json.dumps(self.view(),ensure_ascii=False))
        unsub()
        count=len(updates)
        await self.input('公开')
        await drain(self.e)
        self.assertEqual(len(updates),count)

    async def test_user_view_subscribe_then_snapshot_never_misses_current_revision(self):
        updates=[]
        unsubscribe=self.e.subscribe(dict(type='user'),updates.append)
        await self.input()
        immediate=self.view()
        await drain(self.e)
        await until(lambda:any(v['revision']==self.view()['revision'] for v in updates))
        self.assertGreaterEqual(updates[-1]['revision'],immediate['revision'])
        unsubscribe()

    async def test_request_dedup_conflict_and_expiry(self):
        req=self.request('UserInput',dict(kind='message',text='一次',recipient='all',attachments=[]))
        first=await self.e.dispatch(req)
        again=await self.e.dispatch(copy.deepcopy(req))
        self.assertEqual(first,again)
        await drain(self.e)
        self.assertEqual(len(self.view()['messages']),4)
        conflicting=copy.deepcopy(req);conflicting['payload']['text']='另一次'
        conflict=await self.e.dispatch(conflicting)
        self.assertEqual(conflict['payload']['error']['code'],'conflict')
        self.clock.advance(61)
        expired=await self.e.dispatch(req)
        self.assertEqual(expired['payload']['error']['code'],'stale')

    async def test_inflight_duplicate_is_one_execution(self):
        gate=asyncio.Event()
        original=self.resources.resolve
        async def blocked(ref,session):
            await gate.wait()
            return await original(ref,session)
        self.resources.resolve=blocked
        item=self.resources.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','a.png',self.e.sessionId)
        req=self.request('UserInput',dict(kind='message',text='',recipient='instance-one',attachments=[item]))
        one=asyncio.create_task(self.e.dispatch(req))
        await until(lambda:any(category=='resource' for _,category in self.e.tasks.values()))
        two=asyncio.create_task(self.e.dispatch(copy.deepcopy(req)))
        gate.set()
        self.assertEqual(await one,await two)
        await drain(self.e)
        self.assertEqual(len(self.view()['messages']),2)

    async def test_capacity_refuses_without_evicting_live_records(self):
        self.e.dedup_capacity=len(self.e.requests)
        existing=next(iter(self.e.requests.values()))
        result=await self.input()
        self.assertEqual(result['payload']['error']['code'],'unavailable')
        self.assertIn(existing,list(self.e.requests.values()))

    async def test_unicode_text_and_persona_limits(self):
        accepted=await self.input('🌱'*4000,'instance-one')
        self.assertTrue(accepted['payload']['accepted'])
        await drain(self.e)
        rejected=await self.input('🌱'*4001,'instance-one')
        self.assertEqual(rejected['payload']['error']['code'],'invalid-input')
        package=copy.deepcopy(PACKAGE);package['character']['persona']='🌱'*20000
        receipt=await self.send('RegisterCharacter',dict(package=package,loaded=self.body.loaded('long-persona',package)))
        self.assertTrue(receipt['payload']['accepted'])
        self.assertEqual(len(self.e.roles['long-persona']['persona']),20000)
        package['character']['persona']+='x'
        reject=await self.send('RegisterCharacter',dict(package=package,loaded=self.body.loaded('long-persona',package)))
        self.assertFalse(reject['payload']['accepted'])
        self.assertEqual(len(self.e.roles['long-persona']['persona']),20000)

    async def test_attachment_limits_resolver_mismatch_and_empty_input(self):
        item=self.resources.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','a.png',self.e.sessionId)
        variants=[[item,item],[dict(item,mime='image/webp')],[dict(item,sizeBytes=2*1048576+1)],
                  [dict(item,sizeBytes=1)],[dict(item,contentRef='https://example.test/image.png')]]
        for attachments in variants:
            response=await self.input('',attachments=attachments)
            self.assertFalse(response['payload']['accepted'])
        self.assertFalse((await self.input(' '))['payload']['accepted'])
        self.assertEqual(self.view()['messages'],[])

    async def test_missing_dependency_and_unknown_capability_explicitly_rejected(self):
        caps=self.e.getCapabilities()
        self.assertEqual(caps['protocol'],PROTOCOL)
        self.assertEqual(caps['limits']['personaCodePoints'],20000)
        self.assertFalse((await self.send('UnknownOperation'))['payload']['accepted'])
        bad=self.request('UserInput',dict(kind='message',text='hi',recipient='all',attachments=[],requiredCapabilities=['teleport']))
        self.assertEqual((await self.e.dispatch(bad))['payload']['error']['code'],'unsupported-capability')
        bad['protocol']='wimipet/9'
        self.assertEqual((await self.e.dispatch(bad))['payload']['error']['code'],'unsupported-version')
        item=self.resources.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','a.png',self.e.sessionId)
        self.e.resolver=None
        self.assertEqual((await self.input('',attachments=[item]))['payload']['error']['code'],'unsupported-capability')
        self.e.resolver=self.resources

    async def test_model_must_choose_loaded_action_and_no_hardcoded_nod(self):
        self.model.result=dict(text='invalid action',actionId='nod')
        await self.input()
        await drain(self.e)
        self.assertEqual(self.body.commands,[])
        self.assertTrue(all(j['error']['code']=='unsupported-capability' for j in self.e.jobs))
        loaded=self.body.loaded('unsupported');loaded['supportedActions'].append(dict(actionId='look',meaning='看',allowedStates=['standing']))
        response=await self.send('RegisterCharacter',dict(package=PACKAGE,loaded=loaded))
        self.assertEqual(response['payload']['error']['code'],'unsupported-capability')

    async def test_full_action_fact_paths_and_terminal_cannot_revive(self):
        for path in (['queued','accepted','completed'],['rejected'],['queued','interrupted'],['accepted','failed'],['queued','failed']):
            await self.input('动作','instance-one')
            await drain(self.e)
            command=self.body.commands[-1]
            for status in path:
                event=self.body.event(command,status)
                response=await self.e.dispatch(event)
                self.assertTrue(response['payload']['accepted'],response)
                self.assertEqual(await self.e.dispatch(event),response)
            action=self.e.actions[-1]
            self.assertEqual(action['history'],['requested']+path)
            revive=await self.e.dispatch(self.body.event(command,'accepted'))
            self.assertEqual(revive['payload']['error']['code'],'conflict')
        self.assertFalse(any(m['kind']=='action-feedback' for m in self.view('instance-two')['messages']))

    async def test_action_invalid_transition_wrong_instance_and_loop_completion(self):
        self.model.result=dict(actionId='idle')
        await self.input('循环','instance-one');await drain(self.e)
        command=self.body.commands[-1]
        self.assertFalse((await self.e.dispatch(self.body.event(command,'completed')))['payload']['accepted'])
        self.assertTrue((await self.e.dispatch(self.body.event(command,'accepted')))['payload']['accepted'])
        self.assertFalse((await self.e.dispatch(self.body.event(command,'completed')))['payload']['accepted'])
        wrong=self.body.event(command,'interrupted');wrong['payload']['instanceId']='instance-two'
        self.assertEqual((await self.e.dispatch(wrong))['payload']['error']['code'],'not-found')
        self.assertTrue((await self.e.dispatch(self.body.event(command,'interrupted')))['payload']['accepted'])

    async def test_transport_failure_is_request_error_not_fake_body_fact(self):
        self.body.fail=True
        await self.input('动作','instance-one');await drain(self.e)
        self.assertEqual(self.e.actions[-1]['status'],'requested')
        self.assertEqual(self.e.actions[-1]['requestError']['code'],'unavailable')

    async def test_model_failure_timeout_peer_progress_and_cancellation(self):
        self.model.fail={'instance-one'}
        self.model.gates['instance-two']=asyncio.Event()
        receipt=await self.input()
        self.assertTrue(receipt['payload']['accepted'])
        await drain(self.e)
        self.assertEqual({j['instanceId']:j['status'] for j in self.e.jobs},
                         {'instance-one':'failed','instance-two':'timeout','instance-three':'done'})
        self.assertNotIn('secret-key',json.dumps(self.e.diagnostics()))
        self.model.fail.clear()
        request=self.request('UserInput',dict(kind='message',text='取消',recipient='instance-two',attachments=[]))
        await self.e.dispatch(request)
        await until(lambda:len(self.e.jobs)==4)
        cancel=await self.send('CancelRequest',dict(targetRequestId=request['requestId']))
        self.assertTrue(cancel['payload']['accepted'])
        await drain(self.e)
        self.assertEqual(self.e.jobs[-1]['status'],'cancelled')

    async def test_monotonic_deadline_prevents_late_publish_even_if_wall_clock_moves_back(self):
        gate=asyncio.Event();self.model.gates['instance-one']=gate
        await self.input('短截止','instance-one',ttl=1000)
        await until(lambda:len(self.model.calls)==1)
        self.clock.mono+=2;self.clock.epoch-=200000
        gate.set();await drain(self.e)
        self.assertEqual(len(self.view()['messages']),1)
        self.assertEqual(self.e.jobs[-1]['status'],'stale')

    async def test_auto_bounded_user_interrupt_and_removal(self):
        await self.send('StartAutoConversation',dict(rounds=5));await drain(self.e)
        self.assertEqual(len(self.view()['messages']),5)
        gate=asyncio.Event();self.model.gates['instance-one']=gate
        await self.send('StartAutoConversation',dict(rounds=12))
        await until(lambda:len(self.model.calls)==6)
        await self.input('用户插话','instance-three');await drain(self.e)
        self.assertFalse(self.view()['processing']['auto'])
        self.assertEqual(self.view()['messages'][-1]['speaker']['instanceId'],'instance-three')
        await self.input('移除前','instance-one')
        await until(lambda:len(self.model.calls)==8)
        await self.send('RemoveCharacter',dict(instanceId='instance-one'))
        gate.set();await drain(self.e)
        self.assertEqual(self.e.jobs[-1]['status'],'stale')

    async def test_policy_confirmation_failure_and_no_false_pause(self):
        self.assertTrue(self.view()['processing']['observation']['analysisPaused'])
        self.capture.policy_gate=asyncio.Event()
        task=asyncio.create_task(self.policy(False))
        await until(lambda:self.e.requestedPolicy is not None)
        observation=self.view()['processing']['observation']
        self.assertIsNone(observation['confirmed'])
        self.assertTrue(observation['analysisPaused'])
        self.assertFalse((await self.send('ObserveDesktop'))['payload']['accepted'])
        self.capture.policy_gate.set()
        self.assertTrue((await task)['payload']['accepted'])
        self.assertFalse(self.view()['processing']['observation']['confirmed']['paused'])
        self.capture.fail_policy=True
        failure=await self.policy(True)
        self.assertFalse(failure['payload']['accepted'])
        actual=self.view()['processing']['observation']
        self.assertFalse(actual['confirmed']['paused'])
        self.assertTrue(actual['analysisPaused'])
        self.assertIsNotNone(actual['error'])

    async def test_policy_revision_pause_resume_drops_inflight_capture_and_analysis(self):
        await self.policy()
        self.capture.capture_gate=asyncio.Event()
        await self.send('ObserveDesktop',dict(force=True))
        await until(lambda:len(self.capture.calls)==1)
        await self.policy(True);await self.policy(False)
        self.capture.capture_gate.set();await drain(self.e)
        self.assertEqual(self.view()['messages'],[])
        self.assertEqual(self.e.jobs[-1]['status'],'stale')
        gate=asyncio.Event();self.model.gates={k:gate for k in self.e.roles}
        await self.send('ObserveDesktop',dict(force=True))
        await until(lambda:len(self.model.calls)==3)
        await self.policy(True);gate.set();await drain(self.e)
        self.assertEqual(len(self.view()['messages']),1)
        self.assertTrue(all(j['status']=='stale' for j in self.e.jobs if j.get('instanceId')))

    async def test_capture_metadata_cooldown_self_filter_and_strategy_mismatch(self):
        await self.policy()
        self.assertTrue((await self.send('ObserveDesktop',dict(force=True)))['payload']['accepted'])
        await drain(self.e)
        first=self.view()['messages'][0]
        self.assertEqual(first['kind'],'desktop-observation')
        self.assertEqual(first['observation']['capturedAtMs'],self.clock.epoch)
        self.assertEqual(first['observation']['source'],'simulation')
        self.assertFalse((await self.send('ObserveDesktop'))['payload']['accepted'])
        self.clock.advance(11)
        self.assertTrue((await self.send('ObserveDesktop'))['payload']['accepted']);await drain(self.e)
        self.assertFalse((await self.send('ObserveDesktop',dict(source='bubble')))['payload']['accepted'])
        self.capture.wrong_revision=True
        self.assertFalse((await self.policy(True))['payload']['accepted'])
        self.assertTrue(self.e.analysisPaused)

    async def test_reset_new_session_isolates_late_work_old_requests_and_resources(self):
        gate=asyncio.Event();self.model.gates['instance-one']=gate;self.model.ignore_cancel=True
        old_req=self.request('UserInput',dict(kind='message',text='旧会话',recipient='instance-one',attachments=[]))
        await self.e.dispatch(old_req)
        await until(lambda:len(self.model.calls)==1)
        item=self.resources.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','a.png',self.e.sessionId)
        old_session=self.e.sessionId
        reset=await self.send('ResetSession')
        self.assertNotEqual(old_session,reset['payload']['newSessionId'])
        gate.set();await drain(self.e)
        self.assertEqual(self.view()['messages'],[])
        self.assertEqual((await self.e.dispatch(old_req))['payload']['error']['code'],'stale')
        with self.assertRaises(ProtocolError):await self.resources.resolve(item['contentRef'],old_session)
        self.assertTrue(self.e.analysisPaused)

    async def test_hard_timeout_provider_ignoring_cancel_cannot_override_result(self):
        gate=asyncio.Event();self.model.gates['instance-one']=gate;self.model.ignore_cancel=True
        self.e.timeout=.1
        await self.input('超时','instance-one')
        await until(lambda:self.e.jobs and self.e.jobs[-1]['status']=='timeout')
        gate.set();await drain(self.e)
        self.assertEqual(len(self.view()['messages']),1)

    async def test_closed_service_rejects_requests_and_stops_subscriptions(self):
        updates=[]
        self.e.subscribe(dict(type='user'),updates.append)
        report=await self.e.close()
        self.assertEqual(report['pendingTasks'],0)
        self.assertEqual((await self.input())['payload']['error']['code'],'unavailable')
        self.assertEqual(updates,[])

    async def test_malformed_registration_is_structured_and_preserves_running_instances(self):
        for package,loaded in (([],{}),({},[]),(None,None)):
            response=await self.send('RegisterCharacter',dict(package=package,loaded=loaded))
            self.assertFalse(response['payload']['accepted'])
        self.assertEqual(len(self.e.roles),3)

    async def test_reset_while_capture_pending_rejects_old_feedback_and_capture(self):
        await self.input('动作','instance-one');await drain(self.e)
        old_event=self.body.event(self.body.commands[-1],'accepted')
        await self.policy()
        self.capture.capture_gate=asyncio.Event()
        await self.send('ObserveDesktop',dict(force=True))
        await until(lambda:len(self.capture.calls)==1)
        await self.send('ResetSession')
        self.capture.capture_gate.set();await drain(self.e)
        self.assertEqual(self.view()['messages'],[])
        self.assertEqual((await self.e.dispatch(old_event))['payload']['error']['code'],'stale')

    async def test_action_event_conflict_is_rejected_without_second_fact(self):
        await self.input('动作','instance-one');await drain(self.e)
        event=self.body.event(self.body.commands[-1],'queued')
        self.assertTrue((await self.e.dispatch(event))['payload']['accepted'])
        conflicting=copy.deepcopy(event);conflicting['payload']['status']='accepted'
        self.assertEqual((await self.e.dispatch(conflicting))['payload']['error']['code'],'conflict')
        self.assertEqual(self.e.actions[-1]['status'],'queued')

    async def test_observation_request_can_be_cancelled_before_capture(self):
        await self.policy()
        self.capture.capture_gate=asyncio.Event()
        request=self.request('ObserveDesktop',dict(force=True))
        await self.e.dispatch(request)
        await until(lambda:len(self.capture.calls)==1)
        await self.send('CancelRequest',dict(targetRequestId=request['requestId']))
        await drain(self.e)
        self.assertEqual(self.e.jobs[-1]['status'],'cancelled')
        self.assertEqual(self.view()['messages'],[])

    async def test_resource_cleanup_failure_is_reported_and_close_retries(self):
        original=self.resources.releaseSession
        async def failing(session):
            raise RuntimeError('private bridge error')
        self.resources.releaseSession=failing
        old_session=self.e.sessionId
        response=await self.send('ResetSession')
        self.assertTrue(response['payload']['accepted'])
        self.assertEqual(response['payload']['cleanupError']['code'],'unavailable')
        self.assertIn(old_session,self.e.resourceCleanupPending)
        self.resources.releaseSession=original
        report=await self.e.close()
        self.assertEqual(report['resourceCleanupPending'],[])
        self.assertEqual(report['resourceErrors'],[])

    async def test_events_keep_source_identity_and_monotonic_sequence_across_reset(self):
        one=await self.input('第一条','instance-one');await drain(self.e)
        reset=await self.send('ResetSession')
        two=await self.input('新会话','instance-one');await drain(self.e)
        self.assertEqual(one['sourceInstanceId'],two['sourceInstanceId'])
        self.assertLess(one['sourceSeq'],reset['sourceSeq'])
        self.assertLess(reset['sourceSeq'],two['sourceSeq'])
        self.assertEqual(self.view()['messages'][0]['seq'],1)

    async def test_structured_capture_error_is_preserved_without_analyzing_image(self):
        await self.policy()
        async def unavailable(req):
            return dict(req,type='CaptureResult',payload=dict(error=dict(code='unavailable',
                        message='本次不能保证排除自身输出',retryable=True)))
        self.capture.capture=unavailable
        await self.send('ObserveDesktop',dict(force=True));await drain(self.e)
        self.assertEqual(self.e.jobs[-1]['error']['code'],'unavailable')
        self.assertEqual(self.view()['messages'],[])
        self.assertEqual(self.model.calls,[])

    async def test_malformed_action_reason_cannot_partially_commit_state(self):
        await self.input('动作','instance-one');await drain(self.e)
        event=self.body.event(self.body.commands[-1],'accepted')
        event['payload']['reasonCode']={'invalid':'object'}
        response=await self.e.dispatch(event)
        self.assertFalse(response['payload']['accepted'])
        self.assertEqual(self.e.actions[-1]['status'],'requested')


if __name__=='__main__':unittest.main()
