"""Shared stdlib launcher. Paths are relative to this checkout, never the cwd."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def bot_root():
    for path in (ROOT/'group-bot',ROOT/'work/group-bot'):
        if (path/'debug_console.py').is_file():return path
    raise ValueError('group-bot/debug_console.py is missing')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def write_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def initialize():
    bot=bot_root();settings=bot/'settings.json'
    if not settings.exists():
        cfg=read(bot/'settings.example.json');cfg['mcp_env_file']='bot.env';write_new(settings,cfg)
    for name,source in [('persona.txt','persona-default.txt'),('bot.env','bot.env.example')]:
        dest=bot/name
        if not dest.exists():
            with dest.open('xb') as stream:stream.write((bot/source).read_bytes())
    protocol=bot/'reply_protocol.txt';template=bot/'reply_protocol-default.txt'
    if not protocol.exists() and template.is_file():
        with protocol.open('xb') as stream:stream.write(template.read_bytes())
    return bot


def start_bot(args,extra):
    bot=bot_root()
    if args.dry_run:
        print(json.dumps({'bot':str(bot),'webui':not args.cli,'ingress':'nonebot2-onebot-reverse','arguments':extra}));return
    initialize()
    if args.model or args.effort or args.fast:
        cfg=read(bot/'settings.json')
        subprocess.run([sys.executable,'-X','utf8',str(bot/'configure_launch.py'),
            '--model',args.model or cfg['model'],'--effort',args.effort or cfg['reasoning_effort'],
            '--fast',args.fast or ('on' if cfg.get('fast_mode') else 'off')],cwd=bot,check=True)
    if args.cooldown:
        cfg=read(bot/'settings.json')
        for group in cfg['chat_group_whitelist']:
            path=bot/('runtime' if group==cfg['group_id'] else 'runtime-'+str(group))/'NO_COOLDOWN'
            if args.cooldown=='off':path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            else:path.unlink(missing_ok=True)
    command=[sys.executable,'-X','utf8',str(bot/'debug_console.py'),'--root',str(bot)]
    if not args.cli:command.append('--webui')
    command+=extra
    raise SystemExit(subprocess.call(command,cwd=bot))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('entry',choices=['bot','init'])
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--cli',action='store_true');parser.add_argument('--quiet',action='store_true')
    parser.add_argument('--model');parser.add_argument('--effort');parser.add_argument('--fast',choices=['on','off'])
    parser.add_argument('--cooldown',choices=['on','off'])
    args,extra=parser.parse_known_args()
    if extra and args.entry!='bot':parser.error('Unknown arguments: '+' '.join(extra))
    if args.entry=='bot':start_bot(args,extra)
    elif not args.dry_run:initialize()


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        print('Setup/launch failed: '+str(exc),file=sys.stderr);raise SystemExit(1)
