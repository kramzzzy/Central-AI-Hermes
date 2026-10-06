"""Model routing keeps Michael's chat selection within his fixed identity."""
import ast
import os
from pathlib import Path
from unittest.mock import patch
import unittest

tree=ast.parse((Path(__file__).parents[1]/'whatsapp-text-supervisor.py').read_text())
node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TextSupervisor')

class IdleThread:
    def __init__(self,**kwargs):pass
    def start(self):pass

class TextModelProfileTests(unittest.TestCase):
    def create(self,env):
        import threading
        from types import SimpleNamespace
        scope={'os':os,'threading':SimpleNamespace(Lock=threading.Lock,Thread=IdleThread),
               'atexit':SimpleNamespace(register=lambda fn:None)}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual native text supervisor>','exec'),scope)
        cls=scope['TextSupervisor']
        cls.start=lambda self,route:None
        with patch.dict(os.environ,env,clear=True):return cls()

    def test_default_preserves_existing_pc_profile(self):
        supervisor=self.create({})
        self.assertEqual(supervisor.routes,{'mark':'leo-whatsapp-text','michael':'team-whatsapp-michael-business'})

    def test_live_chat_profile_changes_only_michael_route(self):
        supervisor=self.create({'LEO_MICHAEL_TEXT_PROFILE':'team-whatsapp-michael-text','LEO_WHATSAPP_GROUP':'selected-team'})
        self.assertEqual(supervisor.routes,{'mark':'leo-whatsapp-text','michael':'team-whatsapp-michael-text','team':'team-whatsapp-social'})

    def test_arbitrary_profile_is_rejected_before_workers_start(self):
        for value in ['leo','../../leo','team-someone-else','']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.create({'LEO_MICHAEL_TEXT_PROFILE':value})
