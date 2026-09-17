"""Offline regression tests: no desktop actions or network calls."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class Message:
    def __init__(self, calls=(), content=None):
        self.tool_calls = list(calls)
        self.content = content

    def model_dump(self, **kwargs):
        return {"role": "assistant", "content": self.content,
                "tool_calls": [{"id": c.id} for c in self.tool_calls]}


def call(name, arguments="{}"):
    return SimpleNamespace(id=name, function=SimpleNamespace(name=name, arguments=arguments))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        fake_openai = ModuleType('openai')
        fake_openai.OpenAI = Mock()
        fake_openai.APIError = type('APIError', (Exception,), {})
        fake_tools = ModuleType('tools')
        fake_tools.call_tool = Mock()
        fake_tools.get_tool_schemas = lambda: []
        spec = importlib.util.spec_from_file_location('jarvis_under_test', ROOT / 'llm.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, openai=fake_openai, tools=fake_tools):
            spec.loader.exec_module(self.module)
        with patch.dict('os.environ', GEMINI_API_KEY='offline-test'):
            self.jarvis = self.module.Jarvis()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def image(self, name):
        path = Path(self.tmp.name) / name
        path.write_bytes(name.encode())
        return str(path)

    def responses(self, *messages):
        self.jarvis.client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=m)]) for m in messages]

    def test_internal_checks_do_not_flood_requested_screenshot(self):
        names = ['click_at', 'type_text', 'describe_screen', 'browser_fill_and_submit'] * 2
        paths = [self.image(str(i)) for i in range(9)]
        self.module.call_tool.side_effect = [
            {'status': 'done', '_attachment_path': p} for p in paths]
        self.responses(*(Message([call(n)]) for n in names),
                       Message([call('take_screenshot')]), Message(content='Done'))
        self.assertEqual(self.jarvis.ask('Do the task and send one screenshot'), 'Done')
        self.assertEqual(self.jarvis.last_attachments, [paths[-1]])
        self.assertTrue(Path(paths[-1]).exists())
        self.assertTrue(all(not Path(p).exists() for p in paths[:-1]))

    def test_repeated_explicit_captures_keep_only_last_image(self):
        paths = [self.image(str(i)) for i in range(3)]
        self.module.call_tool.side_effect = [{'_attachment_path': p} for p in paths]
        self.responses(Message([call('take_screenshot'), call('screenshot_tab'),
                                call('take_screenshot')]), Message(content='Here it is'))
        self.jarvis.ask('Send one screenshot')
        self.assertEqual(self.jarvis.last_attachments, [paths[-1]])
        self.assertTrue(all(not Path(p).exists() for p in paths[:-1]))

    def test_invalid_arguments_are_recoverable(self):
        self.module.call_tool.return_value = {'status': 'done'}
        self.responses(Message([call('get_current_datetime', '{bad')]),
                       Message([call('get_current_datetime')]), Message(content='Done'))
        self.assertEqual(self.jarvis.ask('What time is it?'), 'Done')
        self.module.call_tool.assert_called_once_with('get_current_datetime', {})
        result = json.loads(self.jarvis.history[3]['content'])
        self.assertIn('Invalid tool arguments', result['error'])

    def test_next_turn_does_not_resend_old_attachment(self):
        old = self.image('old')
        self.jarvis.last_attachments = [old]
        self.responses(Message(content='Hello'))
        self.jarvis.ask('Hello')
        self.assertEqual(self.jarvis.last_attachments, [])
        self.assertFalse(Path(old).exists())

    def test_close_application_registration(self):
        registry = {}
        package = ModuleType('stub_tools')
        def tool(schema):
            def register(func):
                registry[schema['name']] = func
                return func
            return register
        package.tool = tool
        spec = importlib.util.spec_from_file_location('stub_tools.basic', ROOT / 'tools/basic.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stub_tools=package):
            spec.loader.exec_module(module)
        self.assertIs(registry['close_application'], module.close_application)
        with patch.object(module.subprocess, 'run') as run:
            run.return_value = SimpleNamespace(returncode=0)
            result = registry['close_application'](name='notepad', force=True)
        self.assertEqual(result['status'], 'closed')
        run.assert_called_once_with(['taskkill', '/IM', 'notepad.exe', '/F'],
                                    capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
