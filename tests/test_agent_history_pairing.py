"""Prepared source-isolated transcript-pairing checks; OWNER execution only.

Run: python test_agent_history_pairing.py /path/to/source-copy
Source baseline: dev9d63892; no app import, DB, providers, credentials or writes.
Same six checks before/after the scratch patch; no test changes between runs.
Expected baseline: 4 assertion failures, 2 passes, exit1. Patched:6 OK, exit0.
These are expected outcomes, NOT remotely observed execution.
"""
from __future__ import annotations
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path.cwd()


def extract(path, selected, namespace):
    tree = ast.parse((ROOT/path).read_text())
    nodes = [n for n in tree.body if isinstance(n,(ast.AsyncFunctionDef,ast.FunctionDef)) and n.name in selected]
    if {n.name for n in nodes} != set(selected): raise RuntimeError('Required source functions absent')
    module = ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*nodes],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(ROOT/path),'exec'),namespace)


def call(cid, name='synthetic_read'):
    return {'id':cid,'name':name,'arguments':{'synthetic':True}}


def row(ident, role, content='', calls=None, cid=None):
    return SimpleNamespace(id=ident,role=role,content=content,tool_calls=calls,tool_name=cid)


class HistoryPairingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager=SimpleNamespace(recent=AsyncMock())
        self.ns={'json':json,'agent_messages':self.manager,'settings':SimpleNamespace(AGENT_HISTORY_LIMIT=20)}
        extract('app/agent/tools.py',{'to_openai_call'},self.ns)
        extract('app/agent/session.py',{'_call_id','load_history'},self.ns)

    def assistant(self, content, calls=()):
        result={'role':'assistant','content':content}
        if calls:result['tool_calls']=[self.ns['to_openai_call'](c,c['id']) for c in calls]
        return result

    async def history(self, rows):
        before=deepcopy([vars(r) for r in rows]);self.manager.recent.return_value=rows
        result=await self.ns['load_history'](SimpleNamespace(id=7))
        self.manager.recent.assert_awaited_once_with(7,20)
        self.assertEqual([vars(r) for r in rows],before,'history normalization must not mutate stored row snapshots')
        return result

    async def test_complete_batch_preserves_calls_results_and_ordinary_order(self):
        a,b=call('a'),call('b')
        result=await self.history([row(1,'user','request'),row(2,'assistant','planning',[a,b]),row(3,'tool','B',cid='b'),row(4,'tool','A',cid='a'),row(5,'assistant','answer')])
        self.assertEqual(result,[{'role':'user','content':'request'},self.assistant('planning',[a,b]),{'role':'tool','tool_call_id':'b','content':'B'},{'role':'tool','tool_call_id':'a','content':'A'},self.assistant('answer')])

    async def test_partial_batch_strips_unanswered_call_without_inventing_result(self):
        a,b=call('a'),call('b')
        result=await self.history([row(1,'assistant','planning',[a,b]),row(2,'tool','A',cid='a')])
        self.assertEqual(result,[self.assistant('planning',[a]),{'role':'tool','tool_call_id':'a','content':'A'}])

    async def test_leading_result_cannot_answer_later_call(self):
        result=await self.history([row(1,'tool','old result',cid='a'),row(2,'assistant','new planning',[call('a')])])
        self.assertEqual(result,[self.assistant('new planning')])

    async def test_intervening_user_breaks_call_result_pair(self):
        result=await self.history([row(1,'assistant','planning',[call('a')]),row(2,'user','another turn'),row(3,'tool','late result',cid='a')])
        self.assertEqual(result,[self.assistant('planning'),{'role':'user','content':'another turn'}])

    async def test_duplicate_result_is_not_replayed_twice(self):
        a=call('a')
        result=await self.history([row(1,'assistant','planning',[a]),row(2,'tool','first result',cid='a'),row(3,'tool','duplicate result',cid='a')])
        self.assertEqual(result,[self.assistant('planning',[a]),{'role':'tool','tool_call_id':'a','content':'first result'}])

    async def test_reused_id_pairs_only_with_its_local_batch(self):
        old,new=call('a','old_read'),call('a','new_read')
        result=await self.history([row(1,'assistant','old planning',[old]),row(2,'user','new request'),row(3,'assistant','new planning',[new]),row(4,'tool','new result',cid='a')])
        self.assertEqual(result,[self.assistant('old planning'),{'role':'user','content':'new request'},self.assistant('new planning',[new]),{'role':'tool','tool_call_id':'a','content':'new result'}])


if __name__=='__main__':unittest.main(verbosity=2)
