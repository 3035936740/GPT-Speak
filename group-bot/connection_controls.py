"""Explicit connection checks without restarting QQ or unrelated MCP hosts."""
import asyncio
import json
import os
from pathlib import Path
import time
from console_config import Config,atomic,runtime


async def probe_mcp(root,group,private=False):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from gpt_speak_mcp import integration
    root=Path(root);info=integration(root)
    if not info['ready']:raise ValueError('GPT Speak MCP入口不存在，请检查安装')
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'}
    params=StdioServerParameters(command=info['command'],args=info['arguments'],env=env)
    async def check():
        with open(os.devnull,'w') as errors:
            async with stdio_client(params,errlog=errors) as (read,write):
                async with ClientSession(read,write) as session:
                    initialized=await session.initialize()
                    if initialized.serverInfo.name!='GPT Speak':raise ValueError('MCP服务名称不匹配')
                    result=await session.call_tool('list_conversations' if private else 'get_group_status',{} if private else {'group_id':group})
                    if result.isError:raise ValueError('GPT Speak MCP本地资料检查失败')
                    for part in result.content:
                        try:data=json.loads(getattr(part,'text',''))
                        except ValueError:continue
                        if isinstance(data,dict) and (isinstance(data.get('private_contacts'),list) if private else str(data.get('group_id'))==str(group)):return True
                    raise ValueError('GPT Speak MCP未返回预期的本地资料')
    return await asyncio.wait_for(check(),20)


def reconnect(ui,mode='nonebot'):
    if mode not in {'nonebot','mcp'}:raise ValueError('/reconnect nonebot|mcp')
    cfg=Config(ui.root,ui.group).load();folder=runtime(ui.root,ui.group)
    if mode=='nonebot':
        ui.supervisor.gateway.restart(Config(ui.root).load())
        ui.notice='NoneBot网关已重启，等待客户端回连；所有群聊与私聊共用此连接'
    else:
        try:
            asyncio.run(probe_mcp(ui.root,ui.group))
        except Exception as error:
            leaf=error
            while getattr(leaf,'exceptions',None):leaf=leaf.exceptions[0]
            reason=str(leaf) if isinstance(leaf,ValueError) else type(leaf).__name__
            atomic(folder/'mcp-connection.json',{'ok':False,'checked':time.time(),'error_type':type(leaf).__name__})
            raise ValueError('GPT Speak MCP检查失败：'+reason+'；请检查MCP依赖和本地授权配置') from error
        atomic(folder/'mcp-connection.json',{'ok':True,'checked':time.time()})
        ui.notice='GPT Speak MCP临时会话检查通过，已读取本地群状态；Codex已有连接请在Codex内重连'


def reconnect_private(ui,mode):
    if mode not in {'nonebot','mcp'}:raise ValueError('私聊重连类型无效')
    cfg=Config(ui.root).load();folder=Path(ui.root)/'runtime-private';folder.mkdir(exist_ok=True)
    if mode=='nonebot':
        ui.supervisor.gateway.restart(cfg)
        return {'ok':True,'message':'NoneBot网关已重启，等待客户端回连；所有群聊与私聊共用此连接'}
    try:asyncio.run(probe_mcp(ui.root,0,private=True))
    except Exception as error:
        atomic(folder/'mcp-connection.json',{'ok':False,'checked':time.time(),'error_type':type(error).__name__})
        raise ValueError('GPT Speak MCP检查失败，请检查MCP依赖和本地授权配置') from error
    atomic(folder/'mcp-connection.json',{'ok':True,'checked':time.time()})
    return {'ok':True,'message':'GPT Speak MCP临时会话检查通过，已读取本地联系人列表；Codex已有连接请在Codex内重连'}
