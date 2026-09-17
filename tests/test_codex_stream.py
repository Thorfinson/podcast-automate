import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.call_activity import CallActivity
from podcast_automate.codex import CodexAdapter
from podcast_automate.errors import AppError
from podcast_automate.model_trace import ModelTrace, trace_view
from podcast_automate.models import Contract, RuntimeSettings


class Result(Contract):
    reason: str


SERVER = r'''
import json, os, sys, time
mode = os.environ.get('PLA_STREAM_TEST', 'ok')
assert not any(k in os.environ for k in ('OPENAI_API_KEY','CODEX_API_KEY','OPENROUTER_API_KEY'))
if sys.argv[1:] == ['login','status']:
    print('Logged in using ChatGPT'); sys.exit()
if sys.argv[1:] == ['--version']:
    print('codex-cli fixture'); sys.exit()
assert 'app-server' in sys.argv
def emit(value): print(json.dumps(value), flush=True)
def notice(method, **params):
    emit({'method':method,'params':{'threadId':'t','turnId':'u',**params}})
for line in sys.stdin:
    request = json.loads(line)
    method, p = request['method'], request.get('params',{})
    if method == 'initialized': continue
    result = {}
    if method == 'config/read':
        result = {'config':{'mcp_servers':{'personal':{'enabled':True}}}}
    elif method == 'thread/start':
        assert p['ephemeral'] and p['sandbox']=='read-only' and p['approvalPolicy']=='never'
        assert p['model']=='gpt-6-astra' and p['config']['model_reasoning_effort']=='xhigh'
        assert p['config']['mcp_servers']['personal']['enabled'] is False
        assert p['selectedCapabilityRoots']==[] and p['environments']==[]
        result = {'modelProvider':'openai','model':p['model'],'thread':{'id':'t','ephemeral':True}}
    elif method == 'turn/start':
        assert p['summary']=='concise' and p['effort']=='xhigh'
        assert p['outputSchema']['additionalProperties'] is False
        if mode == 'rpc_error':
            emit({'id':request['id'],'error':{'code':-32602,'message':'invalid_json_schema test-only-secret'}})
            continue
        emit({'id':request['id'],'result':{'turn':{'id':'u','status':'inProgress'}}})
        notice('turn/started',turn={'id':'u','status':'inProgress'})
        if mode in ('hang','cancel'):
            time.sleep(20); continue
        notice('item/reasoning/textDelta',itemId='private',delta='PRIVATE-REASONING-MUST-NOT-APPEAR')
        notice('item/reasoning/summaryTextDelta',itemId='r',summaryIndex=0,delta='Ich prüfe ')
        notice('item/reasoning/summaryTextDelta',itemId='r',summaryIndex=0,delta='die Belege.')
        if mode == 'retry': notice('error',error={'message':'temporary rate_limit; retrying'})
        if mode == 'search':
            notice('item/completed',item={'id':'web1','type':'webSearch','query':'actual query','action':{'type':'search','query':'actual query'}})
        notice('item/agentMessage/delta',itemId='a',delta='{"reason":"Alpha')
        time.sleep(.05)
        notice('item/agentMessage/delta',itemId='a',delta=' Beta"}')
        if mode == 'disconnect': sys.exit(0)
        text = '{"reason":"Alpha Beta"}' if mode!='invalid' else '{}'
        notice('item/completed',item={'id':'a','type':'agentMessage','phase':'final_answer','text':text})
        notice('thread/tokenUsage/updated',tokenUsage={'total':{'inputTokens':12,'outputTokens':8,'cachedInputTokens':2,'reasoningOutputTokens':3}})
        if mode == 'failed':
            notice('turn/completed',turn={'id':'u','status':'failed','error':{'message':'usageLimitExceeded'}})
        else:
            notice('turn/completed',turn={'id':'u','status':'completed','error':None})
        continue
    emit({'id':request['id'],'result':result})
'''


class CodexStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        script = self.root / 'server.py'
        script.write_text(SERVER, encoding='utf-8')
        self.adapter = CodexAdapter(RuntimeSettings(codex_model='gpt-6-astra', text_timeout_seconds=4),
                                    reasoning_effort='xhigh')
        mock = patch.object(self.adapter, 'command', return_value=[sys.executable, str(script)])
        mock.start(); self.addCleanup(mock.stop)

    def call(self, **kwargs):
        return self.adapter.structured('Synthetic test only', Result, self.root/'call', prompt_version='test', **kwargs)

    def test_stream_visible_before_completion_and_final_validated_separately(self):
        snapshots = []
        original = CallActivity.stream_delta
        def observe(activity, kind, delta, stream):
            original(activity, kind, delta, stream)
            activity.trace.flush()
            snapshots.append((delta, trace_view(self.root/'call'), (self.root/'call/response.json').exists()))
        with patch.object(CallActivity, 'stream_delta', observe), patch.dict(os.environ, {'OPENAI_API_KEY':'test-only-secret'}):
            result, metadata = self.call()
        self.assertEqual(result.reason, 'Alpha Beta')
        partial = next(row for row in snapshots if row[0]=='{"reason":"Alpha')
        self.assertFalse(partial[2])
        self.assertIn('Einordnung: Alpha', str(partial[1]))
        self.assertEqual(metadata['transport'], 'app_server')
        self.assertEqual(metadata['usage']['input_tokens'], 12)
        self.assertNotIn('PRIVATE-REASONING', (self.root/'call/model_trace.json').read_text())
        diagnostic = json.loads((self.root/'call/diagnostics.json').read_text())
        self.assertEqual(diagnostic['stream_deltas'], 4)
        self.assertIn('first_content_at', diagnostic)

    def test_partial_output_never_becomes_saved_result_on_disconnect_or_failure(self):
        for mode in ('disconnect','failed','invalid','rpc_error'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'PLA_STREAM_TEST':mode}), self.assertRaises(AppError):
                self.call()
            self.assertFalse((self.root/'call/response.json').exists())
            self.assertFalse((self.root/'call/response.pending.json').exists())
        self.assertNotIn('test-only-secret', (self.root/'call/failure.json').read_text())

    def test_observed_search_and_retryable_error(self):
        with patch.dict(os.environ, {'PLA_STREAM_TEST':'search'}):
            _, metadata = self.call(search=True)
        self.assertEqual(metadata['web_search_requests'], 1)
        with patch.dict(os.environ, {'PLA_STREAM_TEST':'retry'}):
            self.assertEqual(self.call()[0].reason, 'Alpha Beta')

    def test_timeout_and_cancel_stop_owned_server(self):
        self.adapter.settings = self.adapter.settings.model_copy(update={'text_timeout_seconds':1})
        with patch.dict(os.environ, {'PLA_STREAM_TEST':'hang'}), self.assertRaises(AppError) as error:
            self.call()
        self.assertEqual(error.exception.code, 'timeout')
        self.adapter.cancel_check = lambda: True
        with self.assertRaises(AppError) as error:
            self.call()
        self.assertEqual(error.exception.code, 'interrupted')


class StructuredTraceTests(unittest.TestCase):
    def test_blank_stream_data_is_counted_without_becoming_visible_text(self):
        with tempfile.TemporaryDirectory() as path:
            activity = CallActivity(Path(path), 'ResearchDecision', 'test')
            activity.stream_delta('text', '{"reason":"Lesen", "windows":[', 'a')
            activity.trace.flush()
            visible = trace_view(Path(path))
            received = activity.diagnostics['last_nonblank_delta_at']
            activity.stream_delta('text', '   \n   ', 'a')
            activity.trace.flush()
            self.assertEqual(trace_view(Path(path)), visible)
            self.assertEqual(activity.diagnostics['last_nonblank_delta_at'], received)
            self.assertGreater(activity.diagnostics['stream_whitespace_chars'], 0)
            self.assertEqual(activity.diagnostics['stream_deltas'], 2)
            activity.finish('interrupted')

    def test_single_character_chunks_nested_values_and_escape_boundaries(self):
        with tempfile.TemporaryDirectory() as path:
            trace = ModelTrace(Path(path), 'test')
            value = {'action':'read','web_queries':[], 'answer':{'summary':'Größe: "ja"',
                     'findings':[{'statement':'Eine Aussage.'}]}, 'windows':[{'reference':'internal-only'}]}
            for char in json.dumps(value, ensure_ascii=True):
                trace.append_structured(char, 'a')
            trace.finish()
            rows = trace_view(Path(path))['lines']
            self.assertEqual([r['text'] for r in rows], ['Weitere Quellenabschnitte lesen',
                'Zwischenstand: Größe: "ja"','Feststellung: Eine Aussage.'])
            self.assertNotIn('internal-only', str(rows))

    def test_partial_values_redacted_and_plain_commentary_supported(self):
        with tempfile.TemporaryDirectory() as path:
            trace = ModelTrace(Path(path), 'test', secrets=('my-test-secret',))
            trace.append_structured('{"reason":"Suche my-test-', 'a')
            trace.flush()
            self.assertNotIn('my-test-', (Path(path)/'model_trace.json').read_text())
            trace.append_structured('secret genauer"}', 'a')
            trace.append_structured('Eine normale Zwischenmeldung.', 'b')
            trace.finish()
            saved = (Path(path)/'model_trace.json').read_text()
            self.assertNotIn('my-test-secret', saved)
            self.assertIn('Eine normale Zwischenmeldung.', saved)


if __name__ == '__main__':
    unittest.main()
