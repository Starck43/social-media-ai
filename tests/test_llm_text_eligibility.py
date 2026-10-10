"""NEW actual PR96 routing regressions via exact AST + stdlib local doubles.

Run: python tests/test_llm_text_eligibility.py
No app/SQLAlchemy/DB/provider imports, historical tests, schema or network.
"""
import ast
import asyncio
import builtins
import logging
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Optional
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]


class HTTPStatusError(Exception):
    def __init__(self, code):
        self.response = NS(status_code=code)


class TimeoutException(Exception):
    pass


class TransportError(Exception):
    pass


def source_functions(path, names, scope):
    tree = ast.parse((ROOT/path).read_text())
    nodes = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/path),'exec'),scope)
    return scope


class LLMTextEligibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = {'Any':Any,'Optional':Optional,'LLMModel':NS(id=0), 'logger':Mock(spec=logging.Logger),
            'httpx':NS(HTTPStatusError=HTTPStatusError,TimeoutException=TimeoutException,TransportError=TransportError),
            'settings':NS(AGENT_MODEL=''), '_is_tools_not_supported_error':lambda error:False}
        names={'_cap_text','_eligible_text_model','default_model_sort_key','chat_with_fallback','resolve_model'}
        source_functions('app/services/ai/llm_client.py',names,self.scope)
        model_scope={}
        source_functions('app/models/analysis/llm_model.py',{'_model_type_to_capabilities'},model_scope)
        tree=ast.parse((ROOT/'app/models/analysis/llm_model.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LLMModel')
        self.capabilities=next(n for n in cls.body if getattr(n,'name',None)=='capabilities')
        self.capabilities.decorator_list=[]
        exec(compile(ast.Module(body=[self.capabilities],type_ignores=[]),'<actual-capabilities-property>','exec'),model_scope)
        self.real_capabilities=model_scope['capabilities']
        self.filters=[];self.allowed={'text','image','video'};self.models=[];self.called=[];self.errors={}
        self.scope['LLMModel'].objects=NS(filter=self.query)
        self.scope['_llm_models']=AsyncMock(side_effect=lambda query:list(self.models))
        self.scope['_allowed_model_types']=AsyncMock(side_effect=lambda:self.allowed)
        self.scope['LLMClientFactory']=NS(create=self.create)

    def query(self, **filters):
        self.filters.append(filters)
        return NS(order_by=lambda *args:NS())

    def model(self, identity, model_type='text', *, default=False, provider_active=True, missing_provider=False, cost=0.1):
        row=NS(id=identity,model_type=model_type,is_default=default,input_cost_per_1k=cost,
            output_cost_per_1k=cost,provider=None if missing_provider else NS(is_active=provider_active,name='local-double'),model_id='local')
        row.capabilities=self.real_capabilities(row)
        return row

    def create(self, model):
        self.called.append(model.id)
        async def chat(messages, **kwargs):
            if model.id in self.errors:raise self.errors[model.id]
            return {'model':model.id,'messages':messages,'kwargs':kwargs}
        return NS(chat=chat)

    async def resolve(self):
        return await self.scope['resolve_model']()

    async def chat(self, preferred=None, **kw):
        return await self.scope['chat_with_fallback']([{'role':'user','content':'local'}],preferred_model=preferred,**kw)

    def test_text_predicate_real_capability_property(self):
        for raw in ['text','image','text,image','text,video','image,video','text, image']:
            with self.subTest(raw=raw):self.assertTrue(self.scope['_cap_text'](self.model(1,raw)))
        for raw in ['','embedding','video','unknown']:
            with self.subTest(raw=raw):self.assertFalse(self.scope['_cap_text'](self.model(1,raw)))

    async def test_allowed_sole_preferred_multimodal_is_called(self):
        chosen=self.model(11,'text,image',default=True);self.models=[chosen]
        self.assertEqual((await self.chat(chosen))['model'],11);self.assertEqual(self.called,[11])
        self.assertEqual(self.filters,[{'is_active':True}])

    async def test_allowed_preferred_multimodal_not_replaced_by_plain_fallback(self):
        chosen=self.model(11,'text,image',default=True);self.models=[self.model(8,cost=0),chosen]
        self.assertEqual((await self.chat(chosen))['model'],11);self.assertEqual(self.called,[11])

    async def test_automatic_resolution_accepts_default_multimodal(self):
        chosen=self.model(11,'text,image',default=True);self.models=[self.model(8,cost=0),chosen]
        self.assertIs(await self.resolve(),chosen);self.assertEqual(self.called,[])

    async def test_tier_extra_capability_wins_over_preferred_and_default(self):
        self.allowed={'text','image'};banned=self.model(11,'text,video',default=True);plain=self.model(8)
        self.models=[banned,plain]
        self.assertIs(await self.resolve(),plain);self.assertEqual((await self.chat(banned))['model'],8)
        self.assertEqual(self.called,[8])

    async def test_text_only_plan_rejects_image_only_and_multimodal(self):
        self.allowed={'text'};plain=self.model(8);self.models=[self.model(11,'text,image',default=True),self.model(2,'image'),plain]
        self.assertIs(await self.resolve(),plain);self.assertEqual((await self.chat(self.models[0]))['model'],8)

    async def test_empty_allowed_set_is_not_unfiltered(self):
        self.allowed=set();self.models=[self.model(1)]
        self.assertIsNone(await self.resolve())
        with self.assertRaisesRegex(RuntimeError,'No LLM models available'):await self.chat()
        self.assertEqual(self.called,[])

    async def test_none_tier_allows_multimodal_but_not_pure_video(self):
        self.allowed=None;chosen=self.model(11,'text,image',default=True);self.models=[self.model(1,'video',default=True),chosen]
        self.assertIs(await self.resolve(),chosen);self.assertEqual((await self.chat(chosen))['model'],11)

    async def test_active_provider_guard_shared_by_both_automatic_paths(self):
        for missing in [False,True]:
            with self.subTest(missing=missing):
                banned=self.model(1,default=True,provider_active=False,missing_provider=missing);plain=self.model(8)
                self.models=[banned,plain];self.called=[]
                self.assertIs(await self.resolve(),plain);self.assertEqual((await self.chat(banned))['model'],8)
                self.assertEqual(self.called,[8])

    async def test_legacy_image_only_text_support_preserved_when_tier_allows(self):
        self.allowed={'image'};image=self.model(2,'image');self.models=[image]
        self.assertIs(await self.resolve(),image);self.assertEqual((await self.chat(image))['model'],2)

    async def test_default_flag_and_lowest_id_resolution_order_preserved(self):
        self.models=[self.model(3),self.model(2)];self.assertIs(await self.resolve(),self.models[1])
        flagged=self.model(9,default=True);self.models.append(flagged)
        self.assertIs(await self.resolve(),flagged);self.assertEqual((await self.chat())['model'],9)

    async def test_preferred_failure_retains_cost_sorted_fallback_and_arguments(self):
        preferred=self.model(11,'text,image',default=True);cheap=self.model(8,cost=0.01);expensive=self.model(2,cost=1)
        self.models=[expensive,cheap,preferred];self.errors[11]=HTTPStatusError(429)
        result=await self.chat(preferred,tools=[{'name':'local'}],temperature=0.2)
        self.assertEqual(self.called,[11,8]);self.assertEqual(result['kwargs'],{'tools':[{'name':'local'}],'temperature':0.2})

    async def test_nonretryable_http_error_not_hidden(self):
        chosen=self.model(11);self.models=[chosen,self.model(8)];self.errors[11]=HTTPStatusError(400)
        with self.assertRaises(HTTPStatusError):await self.chat(chosen)
        self.assertEqual(self.called,[11])

    async def test_actual_task_cancellation_propagates_without_next_provider(self):
        chosen=self.model(11,'text,image');self.models=[chosen,self.model(8)];entered=asyncio.Event();blocked=asyncio.Event()
        def create(model):
            self.called.append(model.id)
            async def chat(*args,**kwargs):entered.set();await blocked.wait()
            return NS(chat=chat)
        self.scope['LLMClientFactory']=NS(create=create)
        task=asyncio.create_task(self.chat(chosen));await asyncio.wait_for(entered.wait(),1);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.called,[11])

    async def test_explicit_env_override_semantics_unchanged(self):
        explicit=self.model(11,'video',provider_active=False);self.models=[explicit];self.allowed=set()
        self.scope['settings'].AGENT_MODEL='explicit'
        self.assertIs(await self.resolve(),explicit)
        self.assertEqual(self.filters,[{'name':'explicit'}]);self.scope['_allowed_model_types'].assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
