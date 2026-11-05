import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from office import chatgpt_plan as plan


CLIENT = 'oaiapp_test_registration'
TOKENS = {'scope': plan.SCOPES, 'token_type': 'Bearer', 'expires_in': 3600,
          'access_token': 'test-access-token', 'refresh_token': 'test-refresh-token', 'id_token': 'test-identity-token'}


def profile(**values):
    return {**plan._apply_tokens({'client_id': CLIENT, 'issuer': plan.ISSUER, 'subject': 'test-subject',
                                'email': 'example@example.invalid', 'ext_agent_host_id': uuid4().urn}, TOKENS), **values}


def mock_response(data):
    response = Mock()
    response.status_code = 200
    response.json.return_value = data
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


class ChatGPTPlanTests(SimpleTestCase):
    def test_loopback_accepts_only_matching_state_and_exchanges_exact_pkce(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            captured = {}

            def open_browser(url):
                authorize = urlsplit(url)
                self.assertEqual(f'{authorize.scheme}://{authorize.netloc}{authorize.path}', plan.AUTHORIZE)
                query = {k: v[0] for k, v in parse_qs(authorize.query).items()}
                captured.update(query)
                self.assertEqual(query['client_id'], 'dynamic_agent_client')
                self.assertEqual(query['resource'], plan.RESOURCE)
                self.assertTrue(query['redirect_uri'].startswith('http://127.0.0.1:'))

                def callback():
                    params = {'state': 'wrong', 'client_id': CLIENT, 'code': 'bad-code'}
                    bad = requests.get(query['redirect_uri'], params=params, timeout=4)
                    self.assertEqual(bad.status_code, 400)
                    params.update(state=query['state'], code='good-code')
                    good = requests.get(query['redirect_uri'], params=params, timeout=4)
                    self.assertEqual(good.status_code, 200)
                    self.assertNotIn('good-code', good.text)

                captured['callback'] = pool.submit(callback)
                return True

            with patch.object(plan.webbrowser, 'open', side_effect=open_browser), patch.object(plan, '_token', return_value=TOKENS) as exchange, patch.object(plan, '_id_claims', return_value={'sub': 'test-subject'}) as verify:
                result = plan._await_authorization(uuid4().urn, {}, 8)
            captured['callback'].result(timeout=5)
            data = exchange.call_args.args[0]
            self.assertEqual(data['client_id'], CLIENT)
            self.assertEqual(data['redirect_uri'], captured['redirect_uri'])
            self.assertEqual(data['code'], 'good-code')
            digest = hashlib.sha256(data['code_verifier'].encode()).digest()
            self.assertEqual(base64.urlsafe_b64encode(digest).decode().rstrip('='), captured['code_challenge'])
            verify.assert_called_once_with(TOKENS['id_token'], CLIENT, nonce=captured['nonce'])
            self.assertEqual(result['subject'], 'test-subject')

    def test_verify_jwks_signature_audience_and_nonce(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        signing_key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        signing_key['kid'] = 'signed-by-test-provider'
        claims = {'iss': plan.ISSUER, 'aud': CLIENT, 'sub': 'test-subject', 'nonce': 'expected', 'exp': int(time.time()) + 120}
        token = jwt.encode(claims, key, algorithm='RS256', headers={'kid': signing_key['kid']})
        with patch.object(plan, '_http', return_value=mock_response({'keys': [signing_key]})):
            self.assertEqual(plan._id_claims(token, CLIENT, nonce='expected')['sub'], 'test-subject')
            for audience, nonce in ((CLIENT, 'wrong'), ('oaiapp_wrong', 'expected')):
                with self.assertRaises(plan.PlanError):
                    plan._id_claims(token, audience, nonce=nonce)
            different_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            forged = jwt.encode(claims, different_key, algorithm='RS256', headers={'kid': signing_key['kid']})
            with self.assertRaises(plan.PlanError):
                plan._id_claims(forged, CLIENT, nonce='expected')

    def test_import_binds_owner_distinct_host_and_refresh_rotates(self):
        source = profile(expires_at=1)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'NABI_CHATGPT_DIR': directory}), patch.object(plan, '_protect_directory'), patch.object(plan, '_id_claims', return_value={'sub': source['subject'], 'email': source['email']}):
            result = plan.import_profile(json.dumps(source).encode(), owner_id=7)
            path = Path(directory) / 'session.json'
            imported = plan._read_profile(path)
            self.assertNotEqual(imported['ext_agent_host_id'], source['ext_agent_host_id'])
            self.assertEqual(result['owner_id'], 7)
            self.assertNotIn(source['access_token'], str(result))
            with self.assertRaises(plan.PlanError):
                plan.import_profile(json.dumps(source).encode(), owner_id=8)
            with patch.object(plan, '_token', return_value={**TOKENS, 'access_token': 'rotated-test-access', 'refresh_token': 'rotated-test-refresh'}) as renew:
                with self.assertRaises(plan.PlanError):
                    plan._access_token(owner_id=8)
                renew.assert_not_called()
                self.assertEqual(plan._access_token(owner_id=7), 'rotated-test-access')
                self.assertEqual(plan._access_token(owner_id=7), 'rotated-test-access')
            renew.assert_called_once()
            self.assertEqual(plan._read_profile(path)['refresh_token'], 'rotated-test-refresh')
            with self.assertRaises(plan.PlanError):
                plan.import_profile(json.dumps(source).encode(), owner_id=7)

    def test_stream_requires_completed_event_and_omits_unsupported_fields(self):
        response = mock_response({})
        completed = {'type': 'response.completed', 'response': {'status': 'completed', 'id': 'test-response',
                     'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Rascunho de teste'}]}]}}
        response.iter_lines.return_value = [b'data: ' + json.dumps(completed).encode(), b'']
        with patch.object(plan, '_access_token', return_value='test-access'), patch.object(plan, '_http', return_value=response) as request:
            result = plan.generate('gpt-6.1-sol', 'Instruções', 'Pedido', owner_id=7)
        self.assertEqual(result['body'], 'Rascunho de teste')
        sent = request.call_args.kwargs['json']
        self.assertTrue(sent['stream'])
        self.assertFalse(sent['store'])
        self.assertNotIn('max_output_tokens', sent)
        self.assertNotIn('tools', sent)
        self.assertNotIn('research_status', result['usage'])
        self.assertEqual(sent['input'], [{'role': 'user', 'content': 'Pedido'}])
        response.iter_lines.return_value = [b'data: {"type":"response.output_text.delta","delta":"parcial"}', b'']
        with self.assertRaises(plan.PlanError) as failure:
            plan._stream_response(response, 'gpt-6.1-sol')
        self.assertTrue(failure.exception.uncertain)

    def test_research_only_records_public_provider_annotations(self):
        response = mock_response({})
        citation = {'type': 'url_citation', 'url': 'https://example.com/source',
                    'title': 'Fonte oficial', 'start_index': 0, 'end_index': 5}
        final = {'type': 'response.completed', 'response': {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed'},
            {'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'Endereço em texto https://example.org/unverified'},
                {'type': 'output_text', 'text': 'Fonte consultada', 'annotations': [citation,
                    {**citation, 'url': 'http://169.254.169.254/metadata'},
                    {**citation, 'url': 'javascript:alert(1)'},
                ]},
            ]},
        ]}}
        added = {'type': 'response.output_text.annotation.added', 'output_index': 1,
                 'content_index': 1, 'annotation': citation}
        response.iter_lines.return_value = [line for item in (added, final)
                                            for line in (b'data: ' + json.dumps(item).encode(), b'')]
        with patch.object(plan, '_access_token', return_value='test-access'), patch.object(plan, '_http', return_value=response) as request:
            result = plan.generate('gpt-6.1-sol', 'Pesquise fontes públicas', 'Pedido', owner_id=7, web_research=True)
        self.assertEqual(request.call_args.kwargs['json']['tools'], [{'type': 'web_search'}])
        self.assertEqual(result['usage']['research_status'], 'sourced')
        self.assertEqual(result['usage']['sources'], [{'url': citation['url'], 'title': citation['title']}])
        self.assertEqual(len(result['usage']['citations']), 1)
        span = result['usage']['citations'][0]
        self.assertEqual(result['body'][span['start_index']:span['end_index']], 'Fonte')

    def test_research_stream_without_citations_is_explicitly_unverified(self):
        response = mock_response({})
        text = {'type': 'response.output_text.done', 'output_index': 2, 'content_index': 0,
                'text': 'Um site mencionado https://example.org/unverified'}
        completed = {'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}
        searched = {'type': 'response.web_search_call.completed'}
        for events, expected in (((text, completed), 'not_used'), ((searched, text, completed), 'no_sources')):
            with self.subTest(expected=expected):
                response.iter_lines.return_value = [line for item in events
                                                    for line in (b'data: ' + json.dumps(item).encode(), b'')]
                usage = plan._stream_response(response, 'gpt-6.1-sol', web_research=True)['usage']
                self.assertEqual(usage['research_status'], expected)
                self.assertEqual(usage['sources'], [])
                self.assertEqual(usage['citations'], [])

    def test_research_rejection_never_retries_without_tool(self):
        response = mock_response({'error': {'code': 'subscription_sharing_unsupported_capability',
                                            'message': 'Private details should not be returned'}})
        response.status_code = 400
        with patch.object(plan, '_access_token', return_value='test-access'), patch.object(plan, '_http', return_value=response) as request:
            with self.assertRaises(plan.PlanError) as failure:
                plan.generate('gpt-6.1-sol', 'Pesquise fontes públicas', 'Pedido', owner_id=7, web_research=True)
        request.assert_called_once()
        self.assertEqual(failure.exception.code, plan.WEB_RESEARCH_UNAVAILABLE)
        self.assertNotIn('Private details', str(failure.exception))

    def test_stream_accepts_text_events_with_metadata_only_completion(self):
        response = mock_response({})
        events = [
            {'type': 'response.output_text.delta', 'output_index': 1, 'content_index': 0, 'delta': 'Pri'},
            {'type': 'response.output_text.delta', 'output_index': 1, 'content_index': 0, 'delta': 'meiro'},
            {'type': 'response.output_text.done', 'output_index': 1, 'content_index': 0, 'text': 'Primeiro'},
            {'type': 'response.output_item.done', 'output_index': 2, 'item': {
                'type': 'message', 'content': [{'type': 'output_text', 'text': 'Segundo'}],
            }},
            {'type': 'response.completed', 'response': {'status': 'completed', 'output': []}},
        ]
        response.iter_lines.return_value = [line for event in events for line in (b'data: ' + json.dumps(event).encode(), b'')]
        self.assertEqual(plan._stream_response(response, 'gpt-6.1-sol')['body'], 'Primeiro\nSegundo')

    def test_stream_quota_failure_exposes_code_and_sanitizes_provider_text(self):
        response = mock_response({})
        event = {'type': 'response.failed', 'response': {'error': {
            'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'private-provider-details'}}}
        response.iter_lines.return_value = [b'data: ' + json.dumps(event).encode(), b'']
        with self.assertRaises(plan.PlanError) as failure:
            plan._stream_response(response, 'gpt-6.1-sol')
        self.assertEqual(failure.exception.code, 'subscription_sharing_usage_limit_exceeded')
        self.assertNotIn('private-provider-details', str(failure.exception))
