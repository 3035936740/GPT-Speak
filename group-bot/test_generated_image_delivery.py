import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import uuid
from unittest.mock import AsyncMock,Mock,patch
from PIL import Image
from image_jobs import resolve_output,create_image,terminate_job
from generated_image_store import store_image,remove_image,cleanup_images,ImageCleanup,maintain_images,next_daily_time


class GeneratedImageDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=self.base/'codex/generated_images';self.thread=str(uuid.uuid4());self.folder=self.root/self.thread
        self.folder.mkdir(parents=True);self.runtime=self.base/'bot/runtime';self.runtime.mkdir(parents=True)
        self.start=time.time()-1;self.events=[{'type':'thread.started','thread_id':self.thread}]
        resolver=patch('image_jobs.resolve_codex_executable',return_value='test.exe')
        resolver.start();self.addCleanup(resolver.stop)
    def tearDown(self):self.temp.cleanup()
    def image(self,folder=None,name=None):
        path=(folder or self.folder)/(name or 'exec-'+str(uuid.uuid4())+'.png')
        Image.new('RGB',(10,10),'blue').save(path);return path
    def stamp(self,path,created):
        record=path.with_suffix('.json');data=json.loads(record.read_text());data['created']=created
        record.write_text(json.dumps(data))

    def test_empty_or_invalid_model_path_recovers_this_threads_file(self):
        image=self.image()
        for reported in ('',str(self.base/'does-not-exist.png'),'../../../outside.png'):
            result,source,thread=resolve_output({'image_path':reported},self.events,self.start,[],self.root)
            self.assertEqual(result,image.resolve());self.assertEqual(source,'thread_output');self.assertEqual(thread,self.thread)

    def test_relative_paths_are_based_on_dynamic_generation_roots(self):
        image=self.image()
        for path in (image.name,self.thread+'/'+image.name,'generated_images/'+self.thread+'/'+image.name):
            result,source,_=resolve_output({'image_path':path},self.events,self.start,[],self.root)
            self.assertEqual(result,image.resolve());self.assertEqual(source,'reported_path')

    def test_other_group_files_old_input_and_bad_images_never_recovered(self):
        other=self.root/str(uuid.uuid4());other.mkdir();self.image(other)
        old=self.image();os.utime(old,(self.start-10,self.start-10))
        bad=self.folder/('exec-'+str(uuid.uuid4())+'.png');bad.write_bytes(b'not-an-image')
        with self.assertRaises(RuntimeError):resolve_output({},self.events,self.start,[],self.root)
        current=self.image()
        with self.assertRaises(RuntimeError):resolve_output({},self.events,self.start,[current],self.root)
        with self.assertRaises(RuntimeError):resolve_output({'image_path':str(current)},[],self.start,[],self.root)

    def test_two_outputs_without_a_selected_result_fail_without_guessing(self):
        self.image();self.image()
        with self.assertRaisesRegex(RuntimeError,'multiple'):resolve_output({},self.events,self.start,[],self.root)

    def test_archived_paths_remain_valid_after_moving_bot_directory(self):
        original=self.image();owned=store_image(self.runtime,original,self.thread,uuid.uuid4().hex,self.root)
        relative=owned.relative_to(self.runtime)
        target=self.base/'moved/runtime';shutil.copytree(self.runtime,target)
        self.assertEqual((target/relative).read_bytes(),original.read_bytes())
        record=json.loads((target/relative).with_suffix('.json').read_text())
        self.assertFalse(Path(record['source_relative_path']).is_absolute())
        self.assertFalse(Path(record['artifact']).is_absolute())

    def test_scheduled_cleanup_removes_only_its_temp_and_matching_tool_source(self):
        original=self.image();owned=store_image(self.runtime,original,self.thread,uuid.uuid4().hex,self.root)
        other=self.root/str(uuid.uuid4());other.mkdir();unrelated=self.image(other)
        self.assertTrue(remove_image(self.runtime,owned,self.root));self.assertFalse(original.exists())
        self.assertFalse(owned.exists());self.assertFalse(owned.with_suffix('.json').exists());self.assertTrue(unrelated.exists())
        with self.assertRaises(ValueError):remove_image(self.runtime,unrelated,self.root)

    def test_restart_cleanup_deletes_expired_keeps_fresh_and_periodic_is_two_hours(self):
        owned=store_image(self.runtime,self.image(),self.thread,uuid.uuid4().hex,self.root)
        now=time.time();self.stamp(owned,now-7201)
        cfg={'generated_image_ttl_hours':2,'generated_image_cleanup_interval_hours':2}
        with patch('generated_image_store.generated_root',return_value=self.root):
            maintenance=ImageCleanup(self.runtime);self.assertEqual(maintenance.tick(cfg,now),1)
            fresh=store_image(self.runtime,self.image(),self.thread,uuid.uuid4().hex,self.root)
            self.assertEqual(maintenance.tick(cfg,now+7199),0);self.assertTrue(fresh.exists())
            # This file is only one hour old at the next two-hour check.
            self.stamp(fresh,now+3600);self.assertEqual(maintenance.tick(cfg,now+7200),0)
            self.assertEqual(maintenance.tick(cfg,now+14400),1);self.assertFalse(fresh.exists())

    def test_minutes_interval_and_restart_expiry_use_created_not_modified_time(self):
        owned=store_image(self.runtime,self.image(),self.thread,uuid.uuid4().hex,self.root)
        now=time.time();self.stamp(owned,now-3590)
        cfg={'generated_image_ttl_hours':1,'generated_image_cleanup_mode':'interval','generated_image_cleanup_interval_minutes':15}
        with patch('generated_image_store.generated_root',return_value=self.root):
            worker=ImageCleanup(self.runtime);self.assertEqual(worker.tick(cfg,now),0)
            os.utime(owned,(now+20,now+20));self.assertEqual(worker.tick(cfg,now+20),0)
            self.assertEqual(ImageCleanup(self.runtime).tick(cfg,now+20),1)
            self.assertFalse(owned.exists())

    def test_daily_clock_is_beijing_and_only_expires_at_daily_check_after_startup(self):
        from datetime import datetime,timezone
        now=datetime(2026,10,14,13,0,tzinfo=timezone.utc).timestamp() # Beijing21:00
        cfg={'generated_image_ttl_hours':1,'generated_image_cleanup_mode':'daily','generated_image_cleanup_daily_time':'22:00'}
        owned=store_image(self.runtime,self.image(),self.thread,uuid.uuid4().hex,self.root);self.stamp(owned,now-3590)
        with patch('generated_image_store.generated_root',return_value=self.root):
            worker=ImageCleanup(self.runtime);worker.tick(cfg,now)
            self.assertEqual(worker.next_due,now+3600)
            self.assertEqual(worker.tick(cfg,now+60),0);self.assertTrue(owned.exists())
            self.assertEqual(worker.tick(cfg,now+3600),1)
            self.assertEqual(worker.next_due,now+3600+86400)

    def test_per_image_expiry_uses_next_actual_deadline(self):
        now=time.time();owned=store_image(self.runtime,self.image(),self.thread,uuid.uuid4().hex,self.root)
        self.stamp(owned,now-3590)
        cfg={'generated_image_ttl_hours':1,'generated_image_cleanup_mode':'expiry'}
        with patch('generated_image_store.generated_root',return_value=self.root):
            worker=ImageCleanup(self.runtime);worker.tick(cfg,now)
            self.assertEqual(worker.next_due,now+10)
            self.assertEqual(worker.tick(cfg,now+9),0);self.assertEqual(worker.tick(cfg,now+10),1)

    def test_windows_mode_startup_catches_up_then_leaves_schedule_to_os(self):
        now=time.time();owned=store_image(self.runtime,self.image(),self.thread,uuid.uuid4().hex,self.root)
        self.stamp(owned,now-3590)
        cfg={'generated_image_ttl_hours':1,'generated_image_cleanup_mode':'windows'}
        with patch('generated_image_store.generated_root',return_value=self.root):
            worker=ImageCleanup(self.runtime);worker.tick(cfg,now)
            self.assertIsNone(worker.next_due);self.assertEqual(worker.tick(cfg,now+9999),0);self.assertTrue(owned.exists())
            self.assertEqual(ImageCleanup(self.runtime).tick(cfg,now+9999),1)
        saved=json.loads((self.runtime/'image-cleanup-state.json').read_text())
        self.assertEqual(saved['reason'],'startup');self.assertEqual(saved['mode'],'windows')

    def test_cleanup_continues_without_qq_connection(self):
        async def run():
            entered=asyncio.Event()
            def tick(*args):entered.set();return 0
            with patch.object(ImageCleanup,'tick',side_effect=tick):
                task=asyncio.create_task(maintain_images(self.runtime,lambda:{}))
                try:await asyncio.wait_for(entered.wait(),1)
                finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
        asyncio.run(run())

    def test_actual_image_job_resolves_empty_final_path_and_preserves_failed_tool_rule(self):
        async def run():
            original=self.image();process=AsyncMock();process.returncode=0
            process.communicate.return_value=(('\n'.join(json.dumps(row) for row in self.events)).encode(),b'')
            cfg={'codex_exe':'test.exe','model':'test-model','reasoning_effort':'low',
                '_image_research_evidence':{'facts':'已核实：银发，蓝色外套','source_url':'https://example.com/character'}}
            with patch('image_jobs.resolve_codex_executable',return_value='resolved-codex.exe'),\
                    patch('image_jobs.asyncio.create_subprocess_exec',return_value=process) as launch,patch('image_jobs.generated_root',return_value=self.root),patch('generated_image_store.generated_root',return_value=self.root):
                owned=await create_image(cfg,self.runtime,'小猫',[])
            self.assertEqual(launch.call_args.args[0],'resolved-codex.exe')
            sent_prompt=process.communicate.call_args.args[0].decode('utf-8')
            self.assertIn('已核实：银发，蓝色外套',sent_prompt)
            self.assertIn('https://example.com/character',sent_prompt)
            self.assertIn('小猫',sent_prompt)
            self.assertEqual(owned.read_bytes(),original.read_bytes());self.assertEqual(owned.parent,self.runtime/'temp/ai-images')
            diagnostic=json.loads((self.runtime/'image-job-diagnostics.json').read_text())
            self.assertTrue(diagnostic['verified_result']);self.assertEqual(diagnostic['resolved_source'],'thread_output')
            self.assertFalse(Path(diagnostic['artifact_relative_path']).is_absolute())
            process.communicate.return_value=(('\n'.join(json.dumps(row) for row in self.events+[{'type':'item.completed','item':{'type':'command_execution'}}])).encode(),b'')
            with patch('image_jobs.asyncio.create_subprocess_exec',return_value=process):
                with self.assertRaisesRegex(RuntimeError,'Unexpected tool'):await create_image(cfg,self.runtime,'小猫',[])
        asyncio.run(run())

    def test_timeout_writes_diagnostics_and_cleanup_wait_is_bounded_when_kill_denied(self):
        async def run():
            process=Mock();process.returncode=None;process.pid=123
            process.communicate=AsyncMock(side_effect=asyncio.TimeoutError())
            async def stuck():await asyncio.Event().wait()
            process.wait=stuck;process.kill.side_effect=PermissionError('test restricted process')
            killer=Mock();killer.returncode=1;killer.wait=AsyncMock(return_value=1)
            real_wait=asyncio.wait_for
            async def limited(awaitable,timeout):return await real_wait(awaitable,min(timeout,.01))
            cfg={'codex_exe':'test.exe','model':'test-model','reasoning_effort':'low','image_timeout_seconds':.01}
            with patch('image_jobs.asyncio.create_subprocess_exec',side_effect=[process,killer]),patch('image_jobs.asyncio.wait_for',limited):
                with self.assertRaisesRegex(RuntimeError,'timed out'):await create_image(cfg,self.runtime,'小猫',[])
            diagnostic=json.loads((self.runtime/'image-job-diagnostics.json').read_text())
            self.assertEqual(diagnostic['status'],'timeout');self.assertFalse(diagnostic['subprocess_terminated'])
            self.assertFalse(diagnostic['verified_result'])
        asyncio.run(run())


if __name__=='__main__':unittest.main()
