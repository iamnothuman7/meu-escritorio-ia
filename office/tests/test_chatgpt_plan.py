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
                self.assertEqual(plan._access_token(), 'rotated-test-access')
                self.assertEqual(plan._access_token(), 'rotated-test-access')
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
            result = plan.generate('gpt-6.1-sol', 'Instruções', 'Pedido')
        self.assertEqual(result['body'], 'Rascunho de teste')
        sent = request.call_args.kwargs['json']
        self.assertTrue(sent['stream'])
        self.assertFalse(sent['store'])
        self.assertNotIn('max_output_tokens', sent)
        self.assertEqual(sent['input'], [{'role': 'user', 'content': 'Pedido'}])
        response.iter_lines.return_value = [b'data: {"type":"response.output_text.delta","delta":"parcial"}', b'']
        with self.assertRaises(plan.PlanError) as failure:
            plan._stream_response(response, 'gpt-6.1-sol')
        self.assertTrue(failure.exception.uncertain)

    def test_stream_quota_failure_exposes_code_and_sanitizes_provider_text(self):
        response = mock_response({})
        event = {'type': 'response.failed', 'response': {'error': {
            'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'private-provider-details'}}}
        response.iter_lines.return_value = [b'data: ' + json.dumps(event).encode(), b'']
        with self.assertRaises(plan.PlanError) as failure:
            plan._stream_response(response, 'gpt-6.1-sol')
        self.assertEqual(failure.exception.code, 'subscription_sharing_usage_limit_exceeded')
        self.assertNotIn('private-provider-details', str(failure.exception))
