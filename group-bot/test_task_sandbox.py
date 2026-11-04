import asyncio,base64,io,json,tempfile,time,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch,AsyncMock
import task_sandbox as sandbox
import sandbox_task


class SandboxTests(unittest.TestCase):
    def test_zero_step_limit_runs_past_twelve_and_still_checks_call_budget(self):
        async def run(root):
            import bot
            box=type('Box',(),{'work':'/workspace/jobs/a','image':'python:3','name':'unused',
                'start':AsyncMock(),'inputs':AsyncMock(),'close':AsyncMock(),
                'python':AsyncMock(return_value=(0,b'ok')),'outputs':AsyncMock(return_value=[('result.mid',b'MThd\0test')])})()
            cfg={**bot.settings(),'large_task_executor':'podman','large_task_max_steps':0,'_task_project_root':str(bot.ROOT)}
            calls=0
            async def model(cfg,prompt,schema):
                nonlocal calls
                calls+=1
                return {'action':'done' if calls==15 else 'run','code':'print(1)','summary':''}
            with patch.object(sandbox_task,'Sandbox',return_value=box):
                _,status=await sandbox_task.generate(cfg,{'_task_trace_id':'c'*32,'text':'make midi'},[],model,root,[])
                self.assertTrue(status['allowed']);self.assertEqual(calls,15)
                blocked={**cfg,'_reserve_extra_call':lambda:False}
                with self.assertRaisesRegex(ValueError,'call budget'):
                    await sandbox_task.generate(blocked,{'_task_trace_id':'d'*32,'text':'make midi'},[],AsyncMock(return_value={'action':'run','code':'print(1)','summary':''}),root,[])
            self.assertEqual(box.close.await_count,2)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))
    def test_per_task_name_cleanup_ownership_and_failure(self):
        async def run():
            identity='f55433e660bf400f903f279733c0a759'
            with patch('shutil.which',return_value='/usr/bin/docker'):
                box=sandbox.Sandbox({'large_task_executor':'docker','large_task_container_mode':'per_task'},identity)
            self.assertEqual(box.name,'gpt-speak-container-f55433e6-60bf-400f-903f-279733c0a759')
            box.command=AsyncMock(side_effect=[(0,json.dumps([{'Config':{'Labels':{'gpt-speak.task':identity}}}]).encode()),(0,b'')])
            await box.close()
            self.assertEqual(box.command.call_args.args,('rm','--force',box.name))
            box.command=AsyncMock(return_value=(0,b'[{"Config":{"Labels":{}}}]'))
            with self.assertRaisesRegex(ValueError,'归属'):await box.close()
            self.assertEqual(box.command.call_count,1)
        asyncio.run(run())

    def test_build_sends_only_trusted_dockerfile_context(self):
        import tarfile
        async def run():
            cfg={'large_task_executor':'docker','large_task_dockerfile':'FROM python:3.12-slim\nENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple\n'}
            command=AsyncMock(return_value=(0,b'built'))
            with patch('shutil.which',return_value='/usr/bin/docker'),patch.object(sandbox.Sandbox,'command',command):
                result=await sandbox.build_image(cfg)
            self.assertTrue(result['ok']);self.assertEqual(command.call_args.args[:2],('build','--pull=false'))
            with tarfile.open(fileobj=io.BytesIO(command.call_args.kwargs['data'])) as archive:
                self.assertEqual(archive.getnames(),['Dockerfile'])
                self.assertEqual(archive.extractfile('Dockerfile').read().decode(),cfg['large_task_dockerfile'])
        asyncio.run(run())

    def test_per_task_cleanup_retries_even_with_infinite_file_retention(self):
        from task_history import update,cleanup
        with tempfile.TemporaryDirectory() as folder:
            identity=uuid.uuid4().hex
            update(folder,identity,status='failed',sandbox={'executor':'podman','image':'python:3',
                'container':'gpt-speak-container','mode':'per_task'})
            with patch('task_sandbox.remove',side_effect=ValueError('offline')) as remove:
                cleanup(folder,{'large_task_retention_hours':0});remove.assert_called_once()
            with patch('task_sandbox.remove') as remove:
                cleanup(folder,{'large_task_retention_hours':0});remove.assert_called_once()
                cleanup(folder,{'large_task_retention_hours':0});remove.assert_called_once()
            self.assertTrue((Path(folder)/'task-history'/(identity+'.json')).exists())

    def test_tool_failure_always_closes_container(self):
        async def run(root):
            import bot
            box=type('Box',(),{'work':'/workspace/jobs/a','image':'python:3','name':'unused',
                'start':AsyncMock(),'inputs':AsyncMock(),'close':AsyncMock()})()
            cfg={**bot.settings(),'large_task_executor':'docker','large_task_container_mode':'per_task','_task_project_root':str(bot.ROOT)}
            with patch.object(sandbox_task,'Sandbox',return_value=box):
                with self.assertRaises(RuntimeError):
                    await sandbox_task.generate(cfg,{'_task_trace_id':'a'*32,'text':'make midi'},[],AsyncMock(side_effect=RuntimeError('model failed')),root,[])
            box.close.assert_awaited_once()
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_cancelled_task_also_removes_its_container(self):
        async def run(root):
            import bot
            box=type('Box',(),{'work':'/workspace/jobs/a','image':'python:3','name':'unused',
                'start':AsyncMock(),'inputs':AsyncMock(),'close':AsyncMock()})()
            cfg={**bot.settings(),'large_task_executor':'podman','large_task_container_mode':'per_task','_task_project_root':str(bot.ROOT)}
            with patch.object(sandbox_task,'Sandbox',return_value=box):
                with self.assertRaises(asyncio.CancelledError):
                    await sandbox_task.generate(cfg,{'_task_trace_id':'b'*32,'text':'make midi'},[],AsyncMock(side_effect=asyncio.CancelledError()),root,[])
            box.close.assert_awaited_once()
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_names_binary_preview_and_multiple_files_zip(self):
        for name in ('../x','/root/x','C:/x','a\\x','CON.txt','a/../b','x\0'):
            with self.assertRaises(ValueError):sandbox.safe_name(name)
        self.assertIsNone(sandbox.preview(b'MThd\0binary'))
        self.assertEqual(sandbox.preview(b'text'),'text')
        name,raw=sandbox.package([('a.mid',b'MThd\0data'),('nested/readme.txt',b'hi')],10000)
        self.assertEqual(name,'deliverables.zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertEqual(z.namelist(),['a.mid','nested/readme.txt'])
        with self.assertRaises(ValueError):sandbox.package([('a',b'x'*20)],10)

    def test_container_has_no_host_mount_network_privileges_or_credentials(self):
        async def run():
            with patch('shutil.which',return_value='/usr/bin/docker'):
                box=sandbox.Sandbox({'large_task_executor':'docker'},'a'*32)
            calls=[];exists=False
            async def command(*args,**kw):
                nonlocal exists
                calls.append(args)
                if args[0]=='info':return 0,b'{"OSType":"linux"}'
                if args[0]=='run':exists=True;return 0,b'container'
                if args[0]=='image':return 0,b'[{"Id":"abc"}]'
                if args[0]=='inspect':return ((0,json.dumps([{'Image':'abc','State':{'Running':True},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],'CapAdd':['CHOWN'],'SecurityOpt':['no-new-privileges']},'Config':{'Labels':{'gpt-speak.shared':'true'}}}]).encode()) if exists else (1,b''))
                return 0,b''
            box.command=command;await box.start()
            args=next(c for c in calls if c[0]=='run')
            for flag in ('--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pull=never'):
                self.assertIn(flag,args)
            self.assertFalse(set(args)&{'--privileged','--volume','--mount','-v','--env','--env-file'})
            self.assertIn('--user='+str(box.uid)+':'+str(box.uid),calls[-1])
            self.assertNotEqual(box.uid,0);self.assertEqual(box.name,'gpt-speak-container')
        asyncio.run(run())

    def test_restart_starts_stopped_owned_container_and_verifies_it(self):
        async def run():
            with patch('shutil.which',return_value='/usr/bin/docker'):
                box=sandbox.Sandbox({'large_task_executor':'docker'},'a'*32)
            row = {'Image':'abc','State':{'Running':False},
                   'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],
                                 'CapAdd':['CHOWN'],'SecurityOpt':['no-new-privileges']},
                   'Config':{'Labels':{'gpt-speak.shared':'true'}}}
            async def command(*args,**kwargs):
                return {'info':(0,b'{"OSType":"linux"}'),'inspect':(0,json.dumps([row]).encode()),
                        'image':(0,b'[{"Id":"abc"}]'),'start':(0,b''),'exec':(0,b'')}[args[0]]
            box.command=AsyncMock(side_effect=command)
            await box.ensure(restart=True)
            self.assertIn(('start',box.name), [call.args for call in box.command.await_args_list])
            box.command.reset_mock()
            row['Config']['Labels'].clear()
            with self.assertRaises(ValueError):
                await box.ensure(restart=True)
            self.assertNotIn(('start',box.name),[call.args for call in box.command.await_args_list])
        asyncio.run(run())

    def test_tool_loop_generates_real_files_not_model_tokens_and_keeps_usage_category(self):
        async def run(root):
            import bot
            class FakeBox:
                work='/workspace/jobs/'+'a'*32
                image='python:3';name='gpt-speak-task-'+'a'*32
                start=AsyncMock();inputs=AsyncMock();close=AsyncMock()
                python=AsyncMock(return_value=(0,b'parsed MIDI'))
                outputs=AsyncMock(return_value=[('result.mid',b'MThd\0test')])
            results=iter([{'action':'python','code':'print(1)','summary':''},{'action':'done','code':'','summary':'完成'}])
            async def model(cfg,prompt,schema):
                self.assertEqual(cfg['_usage_category'],'task')
                self.assertEqual(cfg['max_prompt_chars'],128000)
                self.assertIn('sandbox_files',prompt)
                if 'tool_observations' in prompt:self.assertIn('parsed MIDI',prompt)
                return next(results)
            cfg={**bot.settings(),'large_task_executor':'podman','_task_project_root':str(bot.ROOT)}
            target={'_id':'123','_task_trace_id':'a'*32,'text':'写midi','_task_inputs':[{'name':'source.mid','data':base64.b64encode(b'MThd\0').decode(),'binary':True}]}
            with patch.object(sandbox_task,'Sandbox',return_value=FakeBox()):
                reply,status=await sandbox_task.generate(cfg,target,[],model,root,[])
            self.assertTrue(status['allowed']);self.assertEqual(reply.segments[0]['data']['name'],'result.mid')
            folder=Path(root)/'generated-files'/('a'*32)
            self.assertFalse((folder/'draft.txt').exists())
            self.assertEqual((folder/'result.mid').read_bytes(),b'MThd\0test')
            state=json.loads((Path(root)/'task-history'/('a'*32+'.json')).read_text(encoding='utf-8'))
            self.assertTrue(state['binary']);self.assertEqual(state['stage'],'生成完成')
            self.assertEqual(state['input_files'],[{'name':'source.mid','bytes':5,'status':'staged','binary':True}])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_expiry_cleans_only_its_container_and_workspace_zero_retains(self):
        from task_history import update,cleanup
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);identity=uuid.uuid4().hex
            update(root,identity,status='failed',created=time.time()-10000,finished=time.time()-10000,
                sandbox={'executor':'podman','image':'python:3','wsl':''})
            output=root/'generated-files'/identity;output.mkdir(parents=True);(output/'file.mid').write_bytes(b'MThd')
            with patch('task_sandbox.remove') as remove:
                cleanup(root,{'large_task_retention_hours':0});remove.assert_not_called();self.assertTrue(output.exists())
                cleanup(root,{'large_task_retention_hours':2});remove.assert_called_once();self.assertFalse(output.exists())

    def test_probe_missing_runtime_is_explicit_and_never_executes_host_code(self):
        with patch('shutil.which',return_value=None):
            result=asyncio.run(sandbox.probe({'large_task_executor':'docker'}))
        self.assertFalse(result['ok']);self.assertFalse(result['ready'])

    def test_tar_export_rejects_symlinks_and_traversal_without_extracting(self):
        import tarfile
        for name,kind in [('../escape',tarfile.REGTYPE),('link',tarfile.SYMTYPE)]:
            data=io.BytesIO()
            with tarfile.open(fileobj=data,mode='w') as archive:
                row=tarfile.TarInfo(name);row.type=kind;row.linkname='/etc/passwd';archive.addfile(row)
            with self.assertRaises(ValueError):sandbox.unpack(data.getvalue(),100)

    def test_host_requires_explicit_acknowledgement(self):
        from task_host import Host
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):Host({'large_task_executor':'host'},'a'*32,folder)
            with self.assertRaises(ValueError):Host({'large_task_executor':'docker','large_task_host_acknowledged':True},'a'*32,folder)
            host=Host({'large_task_executor':'host','large_task_host_acknowledged':True},'a'*32,folder)
            self.assertIn('无容器隔离',host.image)

    def test_health_rejects_unsafe_shared_container_before_any_user_code(self):
        async def run():
            with patch('shutil.which',return_value='/usr/bin/docker'):
                box=sandbox.Sandbox({'large_task_executor':'docker'},'a'*32)
            async def command(*args,**kw):
                if args[0]=='info':return 0,b'{"OSType":"linux"}'
                return 0,json.dumps([{'Config':{'Labels':{'gpt-speak.shared':'true'}},'HostConfig':{'Privileged':True}}]).encode()
            box.command=AsyncMock(side_effect=command)
            with self.assertRaisesRegex(ValueError,'隔离配置'):await box.ensure()
            self.assertFalse(any(call.args[0]=='exec' for call in box.command.call_args_list))
        asyncio.run(run())

    def test_guides_are_html_and_stylesheet_is_served_without_inline_csp_exception(self):
        from debug_webui import DebugWebUI
        from console_config import atomic
        from urllib.request import urlopen
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
            cfg.update(webui_port=0,webui_key='guide-test');atomic(root/'settings.json',cfg)
            server=DebugWebUI(root,lambda _:None);server.start()
            try:
                for kind in ('docker','podman'):
                    with urlopen(server.base_url+'/'+kind+'-guide.html') as response:
                        self.assertEqual(response.headers.get_content_type(),'text/html')
                        text=response.read().decode();self.assertIn('sandbox-guide.css',text);self.assertNotIn('<style>',text)
                with urlopen(server.base_url+'/sandbox-guide.css') as response:self.assertEqual(response.headers.get_content_type(),'text/css')
            finally:server.close()


if __name__=='__main__':unittest.main()
