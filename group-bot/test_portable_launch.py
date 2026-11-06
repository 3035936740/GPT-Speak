"""Portable bot startup and platform process cleanup."""
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
    def test_initialize_keeps_existing_settings_and_uses_generic_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            (bot/'settings.json').write_text('{"custom":true}',encoding='utf-8')
            (bot/'persona.txt').write_text('custom persona',encoding='utf-8')
            with patch.object(manage,'ROOT',root):manage.initialize()
            self.assertEqual((bot/'persona.txt').read_text(),'custom persona')
            self.assertEqual(json.loads((bot/'settings.json').read_text()),{'custom':True})
            self.assertTrue((bot/'bot.env').exists())

    def test_dry_run_reports_reverse_ingress_without_starting_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            output=io.StringIO()
            args=argparse.Namespace(dry_run=True,cli=False)
            with patch.object(manage,'ROOT',root),patch.object(manage.subprocess,'call') as launch,contextlib.redirect_stdout(output):
                manage.start_bot(args,[])
            self.assertEqual(json.loads(output.getvalue())['ingress'],'nonebot2-onebot-reverse')
            launch.assert_not_called()

    def fixture(self,folder):
        root=Path(folder);bot=root/'group-bot';bot.mkdir()
        (bot/'debug_console.py').touch()
        (bot/'settings.example.json').write_text('{"enabled":false}',encoding='utf-8')
        (bot/'persona-default.txt').write_text('yonagi',encoding='utf-8')
        (bot/'bot.env.example').write_text('# local environment\n',encoding='utf-8')
        return root,bot

    def test_posix_process_options_and_job_cleanup(self):
        with patch.object(platform_runtime.os,'name','posix'),patch.object(platform_runtime.os,'killpg',create=True) as kill,patch.object(platform_runtime.signal,'SIGKILL',9,create=True):
            self.assertEqual(platform_runtime.process_options(),{'start_new_session':True})
            job=platform_runtime.PosixJob();job.add(Mock(pid=123));job.close();job.close()
            kill.assert_called_once_with(123,9)

if __name__=='__main__':unittest.main()
