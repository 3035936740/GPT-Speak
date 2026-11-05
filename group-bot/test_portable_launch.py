"""Installer plans and platform boundaries; never install software or contact QQ."""
import importlib.util
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import zipfile
import platform_runtime

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/manage.py'
if not SCRIPT.exists():SCRIPT=Path(__file__).resolve().parents[1]/'scripts/manage.py'
spec=importlib.util.spec_from_file_location('portable_manage',SCRIPT)
manage=importlib.util.module_from_spec(spec);spec.loader.exec_module(manage)


class PortableLaunchTests(unittest.TestCase):
    @unittest.skipUnless(manage.os.name=='nt','Windows WSL launcher')
    def test_wsl_start_uses_named_distribution_and_service_without_shell(self):
        cfg={'mode':'wsl','distribution':'Ubuntu-22.04','service':'yonagi-napcat.service'}
        expected=['wsl.exe','--distribution','Ubuntu-22.04','--user','root','--exec','systemctl','start','yonagi-napcat.service']
        self.assertEqual(manage.napcat_command(cfg),expected)
        with patch.object(manage,'napcat_ready',return_value=False),patch.object(manage,'launch_settings',return_value={'napcat':cfg}),patch.object(manage.subprocess,'run') as run:
            manage.start_napcat(quiet=True)
        self.assertEqual(run.call_args.args[0],expected)
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertNotIn('shell',run.call_args.kwargs)
        for key,value in [('distribution','--help'),('service','bad.service;echo')]:
            with self.assertRaises(ValueError):manage.napcat_command({**cfg,key:value})

    def fixture(self,folder):
        root=Path(folder);bot=root/'group-bot';bot.mkdir()
        (bot/'debug_console.py').touch()
        (bot/'settings.example.json').write_text('{"enabled":false}',encoding='utf-8')
        (bot/'persona-default.txt').write_text('yonagi',encoding='utf-8')
        (bot/'napcat.env.example').write_text('NAPCAT_HOST=http://127.0.0.1:3000\n',encoding='utf-8')
        return root,bot

    def test_initialize_is_relative_idempotent_and_preserves_persona_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            with patch.object(manage,'ROOT',root):
                manage.initialize()
                self.assertEqual(json.loads((bot/'settings.json').read_text())['mcp_env_file'],'napcat.env')
                (bot/'persona.txt').write_text('custom persona',encoding='utf-8')
                (bot/'settings.json').write_text('{"keep":123}',encoding='utf-8')
                manage.initialize()
            self.assertEqual((bot/'persona.txt').read_text(),'custom persona')
            self.assertEqual((bot/'settings.json').read_text(),'{"keep":123}')

    def test_autostart_uses_effective_onebot_host_for_http_ws_and_inherited_env(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            env=root/'remote.env';env.write_text('NAPCAT_HOST=ws://192.168.0.105:3001\nNAPCAT_TOKEN=secret\n',encoding='utf-8')
            cases=[
                ({'napcat_connection_mode':'http','napcat_host':'192.168.0.105'},False),
                ({'napcat_connection_mode':'ws','napcat_host':'qq.example.test'},False),
                ({'napcat_connection_mode':'current'},False),
                ({'napcat_connection_mode':'http','napcat_host':''},False),
                ({'napcat_connection_mode':'ws','napcat_host':'::1'},True),
                ({'napcat_connection_mode':'http','napcat_host':'[::1]'},True),
                ({'napcat_connection_mode':'current','napcat_host':'127.0.0.1'},True),
                ({'napcat_connection_mode':'http','napcat_host':'localhost'},True),
            ]
            with patch.object(manage,'ROOT',root),patch.object(manage,'napcat_ready') as probe,patch.object(manage.subprocess,'run') as run:
                for cfg,expected in cases:
                    with self.subTest(cfg=cfg):
                        (bot/'settings.json').write_text(json.dumps({**cfg,'mcp_env_file':'../remote.env'}),encoding='utf-8')
                        enabled,reason=manage.napcat_autostart(bot)
                        self.assertEqual(enabled,expected)
                        self.assertNotIn('secret',reason)
                probe.assert_not_called();run.assert_not_called()

    def test_remote_bot_launch_does_not_probe_or_start_old_local_wsl_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            (bot/'settings.json').write_text(json.dumps({'napcat_connection_mode':'http','napcat_host':'192.168.0.105'}),encoding='utf-8')
            (root/'launch.local.json').write_text(json.dumps({'napcat':{'mode':'wsl','distribution':'Ubuntu-22.04','service':'yonagi-napcat.service'}}),encoding='utf-8')
            args=argparse.Namespace(dry_run=False,cli=True,skip_napcat=False,model=None,effort=None,fast=None,cooldown=None)
            with patch.object(manage,'ROOT',root),patch.object(manage,'napcat_ready') as probe,patch.object(manage.subprocess,'run') as run,patch.object(manage.subprocess,'Popen') as popen,patch.object(manage.subprocess,'call',return_value=0) as call:
                with self.assertRaises(SystemExit) as stopped:manage.start_bot(args,[])
                self.assertEqual(stopped.exception.code,0)
                probe.assert_not_called();run.assert_not_called();popen.assert_not_called()
                self.assertIn(str(bot/'debug_console.py'),call.call_args.args[0])

    def test_autostart_opt_out_and_unreadable_settings_are_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            with patch.object(manage,'ROOT',root):
                (root/'launch.local.json').write_text('{"napcat":{"auto_start":false}}')
                self.assertFalse(manage.napcat_autostart(bot)[0])
                (root/'launch.local.json').write_text('{}')
                self.assertFalse(manage.napcat_autostart(bot,skip=True)[0])
                (bot/'settings.json').write_text('{broken')
                self.assertFalse(manage.napcat_autostart(bot)[0])
                (bot/'settings.json').write_text('{"napcat_connection_mode":"current","mcp_env_file":"missing.env"}')
                self.assertFalse(manage.napcat_autostart(bot)[0])

    def test_bot_dry_run_reports_remote_skip_without_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            (bot/'settings.json').write_text('{"napcat_connection_mode":"ws","napcat_host":"192.168.0.105"}')
            before=sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            args=argparse.Namespace(dry_run=True,cli=False,skip_napcat=False)
            output=io.StringIO()
            with patch.object(manage,'ROOT',root),patch.object(manage,'napcat_ready') as probe,patch.object(manage.subprocess,'run') as run,patch.object(manage.subprocess,'call') as call,contextlib.redirect_stdout(output):
                manage.start_bot(args,[])
            self.assertFalse(json.loads(output.getvalue())['auto_napcat'])
            probe.assert_not_called();run.assert_not_called();call.assert_not_called()
            self.assertEqual(before,sorted(str(p.relative_to(root)) for p in root.rglob('*')))

    def test_existing_launch_config_blocks_reinstallation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(manage.subprocess,'run') as run:
            root=Path(folder);(root/'launch.local.json').write_text('{"existing":true}')
            with patch.object(manage,'ROOT',root):
                with self.assertRaises(ValueError):manage.install_docker_napcat()
            self.assertEqual((root/'launch.local.json').read_text(),'{"existing":true}')
            self.assertEqual(run.call_count,1)

    def test_docker_install_keeps_volumes_and_exposes_only_loopback_ports(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(manage.subprocess,'run') as run:
            root=Path(folder)
            with patch.object(manage,'ROOT',root):manage.install_docker_napcat()
            cfg=json.loads((root/'runtime/napcat-docker/compose.json').read_text())['services']['napcat']
            self.assertTrue(all(p.startswith('127.0.0.1:') for p in cfg['ports']))
            self.assertIn('127.0.0.1:3000:3000',cfg['ports']);self.assertEqual(len(cfg['volumes']),3)
            self.assertGreater(len(cfg['environment']['WEBUI_TOKEN']),20)
            launch=json.loads((root/'launch.local.json').read_text())['napcat']
            self.assertFalse(Path(launch['compose_file']).is_absolute())
            self.assertIn('pull',run.call_args.args[0])

    def test_dry_run_has_no_writes_downloads_or_process_launches(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(manage.subprocess,'run') as run,patch.object(manage,'download') as download:
            root=Path(folder)
            with patch.object(manage,'ROOT',root):manage.install_napcat('docker',True)
            run.assert_not_called();download.assert_not_called();self.assertEqual(list(root.iterdir()),[])

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'bad.zip'
            with zipfile.ZipFile(archive,'w') as stream:stream.writestr('../escape','bad')
            with self.assertRaises(ValueError):manage.safe_extract(archive,Path(folder)/'target')
            self.assertFalse((Path(folder)/'escape').exists())

    def test_posix_process_options_and_job_cleanup(self):
        with patch.object(platform_runtime.os,'name','posix'),patch.object(platform_runtime.os,'killpg',create=True) as kill,patch.object(platform_runtime.signal,'SIGKILL',9,create=True):
            self.assertEqual(platform_runtime.process_options(),{'start_new_session':True})
            job=platform_runtime.PosixJob();job.add(Mock(pid=123));job.close();job.close()
            kill.assert_called_once_with(123,9)


if __name__=='__main__':unittest.main()
