"""Official Sign in with ChatGPT for an owner-operated installation."""
import base64
from contextlib import contextmanager, suppress
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4
import webbrowser

import requests


ISSUER = 'https://auth.openai.com'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
JWKS = ISSUER + '/.well-known/jwks.json'
RESOURCE = 'https://api.openai.com/v1'
DEFAULT_MODEL = 'gpt-6.1-sol'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
PLAN_SCOPES = {'offline_access', 'resource.invoke', 'chatgpt.tokens.use.direct'}
CALLBACK = '/auth/callback'
FILE_LIMIT = 128 * 1024


class PlanError(Exception):
    def __init__(self, message, *, uncertain=False, code=''):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


def profile_directory():
    override = os.environ.get('NABI_CHATGPT_DIR')
    if override:
        directory = Path(override).expanduser()
    elif os.environ.get('NABI_DATA_DIR'):
        directory = Path(os.environ['NABI_DATA_DIR']) / 'siwc'
    elif os.name == 'nt':
        directory = Path(os.environ['LOCALAPPDATA']) / 'NabiOffice' / 'siwc'
    else:
        directory = Path.home() / '.config' / 'nabi-office' / 'siwc'
    directory = directory.resolve()
    if directory.is_relative_to(Path(__file__).resolve().parents[1]):
        raise PlanError('A sessão ChatGPT precisa ficar fora da pasta do repositório.')
    return directory


def _protect_directory(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != 'nt':
        directory.chmod(0o700)
        return
    try:
        identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], stderr=subprocess.DEVNULL, timeout=8)
        sid = re.search(rb'S-1-[0-9-]+', identity).group().decode('ascii')
        subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
    except (OSError, subprocess.SubprocessError, AttributeError):
        raise PlanError('O Windows não permitiu proteger a pasta da sessão ChatGPT.') from None


def _write_atomic(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.session-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def _read_profile(path):
    try:
        if os.name != 'nt' and path.stat().st_mode & 0o077:
            raise PlanError('O arquivo da sessão ChatGPT precisa de permissão 0600.')
        with path.open('rb') as stream:
            raw = stream.read(FILE_LIMIT + 1)
        return _decode_profile(raw)
    except FileNotFoundError:
        return {}


def _decode_profile(raw):
    try:
        value = json.loads(raw) if len(raw) <= FILE_LIMIT else None
        if not isinstance(value, dict) or value.get('issuer') != ISSUER:
            raise ValueError
        for name in ('client_id', 'subject', 'ext_agent_host_id'):
            if not isinstance(value.get(name), str) or not value[name]:
                raise ValueError
        if not re.fullmatch(r'oaiapp_[A-Za-z0-9_-]+', value['client_id']):
            raise ValueError
        UUID(value['ext_agent_host_id'].removeprefix('urn:uuid:'))
        scopes = value.get('scopes', [])
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise ValueError
        if value.get('access_token'):
            for field in ('access_token', 'refresh_token'):
                token = value.get(field)
                if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
                    raise ValueError
            if not math.isfinite(float(value.get('expires_at', 0))):
                raise ValueError
        return value
    except (ValueError, TypeError):
        raise PlanError('Arquivo de sessão ChatGPT inválido.') from None


@contextmanager
def _session_lock(directory):
    with (directory / 'session.lock').open('a+b') as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _host_id(directory):
    path = directory / 'host-id.json'
    if not path.exists():
        _write_atomic(path, {'id': uuid4().urn})
    try:
        return UUID(json.loads(path.read_text(encoding='utf-8'))['id']).urn
    except (ValueError, KeyError, TypeError):
        raise PlanError('Identificador local da instalação inválido.') from None


def init_host():
    directory = profile_directory()
    _protect_directory(directory)
    with _session_lock(directory):
        _host_id(directory)


def _http(method, url, *, uncertain=False, **kwargs):
    try:
        return requests.request(method, url, allow_redirects=False, timeout=(8, 75 if uncertain else 20), **kwargs)
    except requests.RequestException:
        raise PlanError('Conexão com a OpenAI indisponível.', uncertain=uncertain) from None


def _object(response):
    try:
        value = response.json()
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError):
        pass
    return {}


def _token(data):
    with _http('POST', TOKEN, data={**data, 'resource': RESOURCE}) as response:
        payload = _object(response)
        if response.status_code != 200:
            code = payload.get('error')
            revoked = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                       'refresh_token_invalidated', 'refresh_token_reused'}
            if isinstance(code, str) and code in revoked:
                raise PlanError('A autorização ChatGPT expirou. Conecte a conta novamente.', code=code)
            raise PlanError(f'A autorização ChatGPT falhou (HTTP {response.status_code}).')
        return payload


def _id_claims(token, client_id, *, nonce=None, allow_expired=False):
    try:
        import jwt
    except ImportError:
        raise PlanError('Instale PyJWT com suporte a criptografia para validar o login.') from None
    try:
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
            raise ValueError
        with _http('GET', JWKS) as response:
            keys = _object(response).get('keys', []) if response.status_code == 200 else []
        matching = [key for key in keys if key.get('kid') == header['kid'] and key.get('kty') == 'RSA']
        if len(matching) != 1:
            raise ValueError
        key = jwt.PyJWK.from_dict(matching[0], algorithm='RS256').key
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=client_id, issuer=ISSUER,
                            options={'require': ['iss', 'aud', 'exp', 'sub'], 'verify_exp': not allow_expired})
        if nonce is not None and not hmac.compare_digest(str(claims.get('nonce', '')), nonce):
            raise ValueError
        audience = claims['aud']
        if (isinstance(audience, list) and len(audience) > 1 or 'azp' in claims) and claims.get('azp') != client_id:
            raise ValueError
        if not isinstance(claims['sub'], str) or not claims['sub']:
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        raise PlanError('Não foi possível validar a identidade assinada pela OpenAI.') from None


def _apply_tokens(profile, payload):
    try:
        scopes = payload['scope'].split() if 'scope' in payload else profile.get('scopes', [])
        expires_in = int(payload['expires_in'])
        access_token, refresh_token = payload['access_token'], payload['refresh_token']
        if (not PLAN_SCOPES.issubset(scopes) or not (1 <= expires_in <= 86400)
                or not all(isinstance(token, str) and token for token in (access_token, refresh_token))
                or str(payload.get('token_type', '')).lower() != 'bearer'):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PlanError('A autorização não concedeu uma sessão renovável para usar o plano ChatGPT.') from None
    return {**profile, 'scopes': scopes, 'access_token': access_token, 'refresh_token': refresh_token,
            'id_token': payload.get('id_token') or profile.get('id_token'),
            'expires_at': time.time() + expires_in, 'earliest_refresh_at': payload.get('earliest_refresh_at')}


def _await_authorization(host_id, previous, timeout, on_browser_open=None):
    state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(3)
            super().setup()

        def log_message(self, *_args):
            pass

        def do_GET(self):
            valid = False
            try:
                url = urlsplit(self.path)
                expected_host = f'127.0.0.1:{self.server.server_port}'
                if url.path == CALLBACK and self.headers.get('Host') == expected_host:
                    params = parse_qs(url.query, max_num_fields=12)
                    if len(params.get('state', [])) == 1 and hmac.compare_digest(params['state'][0].encode(), state.encode()):
                        valid = True
                        if any(len(values) != 1 for values in params.values()) or 'error' in params or 'code' not in params:
                            result['error'] = True
                        else:
                            result.update({key: values[0] for key, values in params.items() if key in ('code', 'client_id')})
            except ValueError:
                pass
            self.send_response(200 if valid else 400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(('Retorno recebido. Veja o resultado no terminal.' if valid else 'Retorno de autorização inválido.').encode('utf-8'))

    with HTTPServer(('127.0.0.1', 0), CallbackHandler) as server:
        server.timeout = 1
        redirect_uri = f'http://127.0.0.1:{server.server_port}{CALLBACK}'
        issued_client = previous.get('client_id')
        query = {'client_id': issued_client or 'dynamic_agent_client', 'ext_agent_host_id': host_id,
                 'response_type': 'code', 'redirect_uri': redirect_uri, 'scope': SCOPES, 'resource': RESOURCE,
                 'state': state, 'nonce': nonce, 'code_challenge_method': 'S256', 'code_challenge': challenge}
        if issued_client:
            if previous.get('id_token'):
                query['id_token_hint'] = previous['id_token']
            if not PLAN_SCOPES.issubset(previous.get('scopes', [])):
                query['prompt'] = 'consent'
        else:
            query['agent_name_hint'] = 'NABIO Office AI'
        if not webbrowser.open(AUTHORIZE + '?' + urlencode(query)):
            raise PlanError('Não foi possível abrir o navegador do computador local.')
        if on_browser_open:
            on_browser_open()
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise PlanError('Tempo de autorização encerrado. Inicie novamente para conectar.')
    if 'error' in result:
        raise PlanError('A autorização ChatGPT não foi concluída.')
    client_id = result.get('client_id') or issued_client
    if not client_id or not re.fullmatch(r'oaiapp_[A-Za-z0-9_-]+', client_id) or (issued_client and client_id != issued_client):
        raise PlanError('A OpenAI retornou um registro de cliente inesperado.')
    payload = _token({'grant_type': 'authorization_code', 'client_id': client_id, 'code': result['code'],
                      'code_verifier': verifier, 'redirect_uri': redirect_uri})
    claims = _id_claims(payload.get('id_token', ''), client_id, nonce=nonce)
    if previous and claims['sub'] != previous['subject']:
        raise PlanError('A conta autorizada é diferente do registro selecionado.')
    identity = {'issuer': ISSUER, 'client_id': client_id, 'subject': claims['sub'],
                'email': claims.get('email') or '', 'ext_agent_host_id': host_id}
    return _apply_tokens(identity, payload)


def login(timeout=300, *, on_browser_open=None):
    # Fail before opening the browser if the verification dependency is missing.
    try:
        import jwt  # noqa: F401
        import cryptography  # noqa: F401
    except ImportError:
        raise PlanError('Instale PyJWT[crypto] antes de conectar o plano ChatGPT.') from None
    directory = profile_directory()
    _protect_directory(directory)
    path = directory / 'session.json'
    with _session_lock(directory):
        profile = _await_authorization(_host_id(directory), _read_profile(path), timeout, on_browser_open)
        _write_atomic(path, profile)
    return status()


def import_profile(raw, *, owner_id):
    if type(owner_id) is not int or owner_id < 1:
        raise PlanError('Informe o ID do dono local desta autorização.')
    profile = _decode_profile(raw)
    _require_plan(profile)
    claims = _id_claims(profile.get('id_token', ''), profile['client_id'], allow_expired=True)
    if claims['sub'] != profile['subject']:
        raise PlanError('O arquivo não corresponde à identidade assinada.')
    directory = profile_directory()
    _protect_directory(directory)
    with _session_lock(directory):
        destination = _host_id(directory)
        if destination == profile['ext_agent_host_id']:
            raise PlanError('A VM precisa de um identificador próprio. Transfira somente session.json.')
        path = directory / 'session.json'
        previous = _read_profile(path)
        if previous and (previous['client_id'], previous['subject']) != (profile['client_id'], profile['subject']):
            raise PlanError('Já existe outra autorização ChatGPT nesta instalação.')
        if previous.get('owner_id') not in (None, owner_id):
            raise PlanError('Já existe um vínculo com outro dono nesta instalação.')
        if previous.get('refresh_token') and float(previous.get('expires_at', 0)) > float(profile.get('expires_at', 0)):
            raise PlanError('A VM já possui uma sessão mais recente.')
        profile['ext_agent_host_id'] = destination
        profile['owner_id'] = owner_id
        profile['email'] = claims.get('email') or ''
        _write_atomic(path, profile)
    return status()


def _require_plan(profile):
    if not profile or not PLAN_SCOPES.issubset(profile.get('scopes', [])) or not profile.get('access_token') or not profile.get('refresh_token'):
        raise PlanError('Conecte o plano ChatGPT antes de executar esta tarefa.')


def status():
    try:
        profile = _read_profile(profile_directory() / 'session.json')
        _require_plan(profile)
        return {'connected': True, 'email': profile.get('email', ''), 'expires_at': profile.get('expires_at'),
                'owner_id': profile.get('owner_id')}
    except (PlanError, OSError):
        return {'connected': False, 'email': '', 'expires_at': None, 'owner_id': None}


def _access_token():
    directory = profile_directory()
    path = directory / 'session.json'
    if not path.is_file():
        raise PlanError('Conecte o plano ChatGPT antes de executar esta tarefa.')
    try:
        with _session_lock(directory):
            profile = _read_profile(path)
            _require_plan(profile)
            if profile['ext_agent_host_id'] != _host_id(directory):
                raise PlanError('Importe a sessão para o identificador desta instalação.')
            if float(profile.get('expires_at', 0)) <= time.time() + 90:
                try:
                    payload = _token({'grant_type': 'refresh_token', 'client_id': profile['client_id'],
                                      'refresh_token': profile['refresh_token']})
                except PlanError as exc:
                    if exc.code:
                        for field in ('access_token', 'refresh_token', 'id_token', 'scopes', 'expires_at'):
                            profile.pop(field, None)
                        _write_atomic(path, profile)
                    raise
                profile = _apply_tokens(profile, payload)
                _write_atomic(path, profile)
            return profile['access_token']
    except (OSError, TypeError, ValueError):
        raise PlanError('Não foi possível carregar a sessão protegida do plano ChatGPT.') from None


def _models(token):
    with _http('GET', RESOURCE + '/models', headers={'Authorization': f'Bearer {token}'}) as response:
        if response.status_code != 200:
            raise _inference_error(response.status_code, _object(response))
        models = _object(response).get('models', [])
        if not isinstance(models, list):
            raise PlanError('O catálogo da conta ChatGPT não pôde ser interpretado.')
        return [{'slug': item['slug'], 'display_name': item.get('display_name') or item['slug']}
                for item in models
                if isinstance(item, dict) and item.get('visibility') == 'list' and isinstance(item.get('slug'), str)]


def list_models():
    return _models(_access_token())


def _inference_error(status_code, payload, *, uncertain=False):
    nested = payload.get('response')
    error = payload.get('error') or (nested.get('error') if isinstance(nested, dict) else None) or payload
    code = error.get('code') if isinstance(error, dict) else ''
    if not isinstance(code, str):
        code = ''
    messages = {
        'subscription_sharing_usage_limit_exceeded': 'Limite de uso do plano ChatGPT atingido. Confira Configurações → Uso no ChatGPT.',
        'subscription_sharing_user_not_eligible': 'A conta ou workspace selecionado não está elegível para uso do plano nesta integração.',
        'subscription_sharing_usage_unavailable': 'A OpenAI não conseguiu verificar a disponibilidade do plano ChatGPT.',
        'subscription_sharing_unsupported_capability': 'O plano ChatGPT recusou o modelo ou recurso solicitado.',
    }
    fallback = {401: 'A autorização ChatGPT não foi aceita. Confira a conexão da conta.',
                403: 'O plano ChatGPT recusou o acesso a esta integração.',
                429: 'Limite de uso do plano ChatGPT atingido. Confira Configurações → Uso no ChatGPT.',
                503: 'A rota de uso do plano ChatGPT está temporariamente indisponível.'}
    message = messages.get(code, fallback.get(status_code, f'O plano ChatGPT interrompeu a solicitação (HTTP {status_code}).'))
    return PlanError(message, uncertain=uncertain, code=code if code in messages else '')


def _stream_response(response, model):
    data_lines = []
    received = 0
    for line in response.iter_lines(chunk_size=1024):
        received += len(line)
        if received > 4_000_000:
            raise PlanError('A resposta excedeu o limite local.', uncertain=True)
        if line.startswith(b'data:'):
            data_lines.append(line[5:].lstrip())
        if line or not data_lines:
            continue
        try:
            event = json.loads(b'\n'.join(data_lines))
        except ValueError:
            raise PlanError('A OpenAI retornou um evento inválido.', uncertain=True) from None
        data_lines.clear()
        if event.get('type') in ('response.failed', 'response.incomplete', 'error'):
            raise _inference_error(200, event, uncertain=True)
        if event.get('type') != 'response.completed':
            continue
        completed = event.get('response') or {}
        if completed.get('status') not in (None, 'completed'):
            raise PlanError('A OpenAI não confirmou uma resposta concluída.', uncertain=True)
        body = '\n'.join(part.get('text', '') for item in completed.get('output', [])
                         if item.get('type') == 'message' for part in item.get('content', [])
                         if part.get('type') == 'output_text')
        if not body.strip():
            raise PlanError('O plano ChatGPT não devolveu texto para revisão.', uncertain=True)
        usage = completed.get('usage') or {}
        return {'body': body, 'usage': {'input_tokens': usage.get('input_tokens'),
                                        'output_tokens': usage.get('output_tokens'),
                                        'request_id': completed.get('id', ''), 'model': model,
                                        'billing': 'chatgpt_plan'}}
    raise PlanError('A transmissão terminou antes de confirmar a entrega.', uncertain=True)


def generate(model, instructions, prompt):
    token = _access_token()
    if not model:
        choices = [item['slug'] for item in _models(token)]
        if not choices:
            raise PlanError('Nenhum modelo elegível foi retornado para a conta ChatGPT.')
        model = DEFAULT_MODEL if DEFAULT_MODEL in choices else choices[0]
    payload = {'model': model, 'instructions': instructions, 'input': [{'role': 'user', 'content': prompt}],
               'store': False, 'stream': True}
    try:
        with _http('POST', RESOURCE + '/responses', uncertain=True, stream=True, json=payload,
                   headers={'Authorization': f'Bearer {token}', 'Accept': 'text/event-stream'}) as response:
            if response.status_code != 200:
                raise _inference_error(response.status_code, _object(response), uncertain=response.status_code >= 500)
            return _stream_response(response, model)
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        raise PlanError('A transmissão do plano ChatGPT foi interrompida.', uncertain=True) from None
