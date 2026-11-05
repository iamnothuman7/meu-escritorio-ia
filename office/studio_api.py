"""Session-authenticated JSON bridge from the 3D office to the existing queue.

GET /api/v1/studio/state/ returns the active company's roster and task status.
POST /api/v1/studio/tasks/ accepts workspace_id, agent_id, title, description
and optional kind and request_id. A retry returns the same task when the
request_id and payload match.
Deliveries and decisions remain in the normal Tasks and Approvals screens.
"""

import hashlib
import json
import re
from base64 import urlsafe_b64encode
from datetime import timedelta
from functools import wraps

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import transaction
from django.db.models import Case, Count, OuterRef, Subquery, Value, When
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from . import services
from .models import AI_MODES, TASK_STATUSES, Agent, Approval, Event, SystemState, Task
from .studio_scope import supports_studio
from .views import active_workspace, office_required, record_event


MAX_BODY_BYTES = 64 * 1024
ACTIVE_STATUSES = ('running', 'queued', 'review', 'blocked')
REQUEST_ID = re.compile(r'[a-zA-Z0-9_-]{1,100}')
SCREEN_LINKS = {'tasks_url': '/tasks/', 'approvals_url': '/approvals/'}
RESEARCH_AGENTS = ('radar', 'luma')


def _error(message, status):
    return JsonResponse({'error': message}, status=status)


def _studio_endpoint(method):
    def decorator(view):
        @office_required
        @wraps(view)
        def scoped(request):
            if request.method != method:
                response = _error('Método não permitido.', 405)
                response['Allow'] = method
                return response
            workspace = active_workspace(request)
            if not supports_studio(workspace):
                return _error('Escritório 3D indisponível nesta empresa.', 404)
            return view(request, workspace)

        @never_cache
        @wraps(view)
        def dispatch(request):
            response = scoped(request)
            # Adapt the shared office authentication redirects for JSON clients.
            if response.status_code == 302:
                if request.user.is_authenticated:
                    return _error('Acesso restrito a contas operacionais.', 403)
                return _error('Entre na sua conta para acessar o escritório.', 401)
            return response

        return dispatch
    return decorator


def _task_data(task):
    return {'id': task.pk, 'title': task.title, 'status': task.status, 'url': f'/tasks/{task.pk}/'}


def _research_available(workspace, provider_connected):
    return workspace.ai_mode == 'chatgpt' and workspace.policy.get('web_research') is True and provider_connected


def _task_kinds(agent, research_available):
    kinds = ['general']
    if agent.slug == 'luma':
        kinds.append('content')
    if research_available and agent.slug in RESEARCH_AGENTS:
        kinds.append('research')
    return kinds


@_studio_endpoint('GET')
def studio_state(request, workspace):
    tasks = Task.objects.filter(workspace=workspace)
    provider_connected = services.provider_ready(workspace)
    research_available = _research_available(workspace, provider_connected)
    active_tasks = tasks.filter(agent_id=OuterRef('pk'), status__in=ACTIVE_STATUSES).order_by(
        Case(*(When(status=status, then=Value(rank)) for rank, status in enumerate(ACTIVE_STATUSES))),
        '-updated_at', '-pk',
    )
    agents = list(Agent.objects.filter(workspace=workspace).annotate(
        current_task_id=Subquery(active_tasks.values('pk')[:1]),
    ).order_by('pk'))
    current_tasks = tasks.in_bulk(agent.current_task_id for agent in agents if agent.current_task_id)
    counts = {agent.pk: dict.fromkeys((status for status, _ in TASK_STATUSES), 0) for agent in agents}
    for group in tasks.filter(agent_id__in=counts).order_by().values('agent_id', 'status').annotate(total=Count('pk')):
        if group['status'] in counts[group['agent_id']]:
            counts[group['agent_id']][group['status']] = group['total']

    roster = []
    for agent in agents:
        task = current_tasks.get(agent.current_task_id)
        status = task.status if task else 'idle'
        roster.append({'id': agent.pk, 'slug': agent.slug, 'name': agent.name, 'role': agent.role,
                       'color': agent.color, 'avatar': agent.avatar, 'enabled': agent.enabled,
                       'task_kinds': _task_kinds(agent, research_available),
                       'status': status if agent.enabled else 'disabled', 'task_counts': counts[agent.pk],
                       'current_task': _task_data(task) if task else None})

    now = timezone.now()
    return JsonResponse({
        'workspace': {'id': workspace.pk, 'name': workspace.name, 'paused': services.is_paused(workspace)},
        'agents': roster,
        'pending_approvals': Approval.objects.filter(workspace=workspace, status='pending',
                                                     expires_at__gt=now).count(),
        'provider': {'label': dict(AI_MODES).get(workspace.ai_mode, 'Sem IA'),
                     'connected': provider_connected},
        'worker_online': SystemState.objects.filter(pk=1, worker_heartbeat__gt=now - timedelta(seconds=30)).exists(),
        **SCREEN_LINKS,
    })


def _request_fields(body):
    payload = json.loads(body.decode('utf-8'))
    if not isinstance(payload, dict) or payload.keys() - {'workspace_id', 'agent_id', 'title', 'description', 'kind', 'request_id'}:
        raise ValueError('A solicitação contém campos inválidos.')
    workspace_id = payload.get('workspace_id')
    agent_id = payload.get('agent_id')
    title = payload.get('title')
    description = payload.get('description')
    kind = payload.get('kind', 'general')
    request_id = payload.get('request_id', '')
    if type(workspace_id) is not int or not 0 < workspace_id < 2**63:
        raise ValueError('Empresa inválida. Recarregue o escritório.')
    if type(agent_id) is not int or not 0 < agent_id < 2**63:
        raise ValueError('Selecione um agente válido.')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 180:
        raise ValueError('Informe um título de até 180 caracteres.')
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 12000:
        raise ValueError('Informe uma descrição de até 12.000 caracteres.')
    if kind not in ('general', 'content', 'research'):
        raise ValueError('Selecione um tipo de tarefa válido.')
    if not isinstance(request_id, str) or (request_id and not REQUEST_ID.fullmatch(request_id)):
        raise ValueError('Identificador de solicitação inválido.')
    for value in (title, description):
        if '\x00' in value:
            raise ValueError('O título e a descrição devem conter texto válido.')
        value.encode('utf-8')
    return workspace_id, agent_id, title.strip(), description.strip(), kind, request_id


def _submitted(task, status):
    return JsonResponse({'task': _task_data(task), **SCREEN_LINKS}, status=status)


def _digest(text):
    return urlsafe_b64encode(hashlib.sha256(text.encode()).digest()[:16]).decode().rstrip('=')


@_studio_endpoint('POST')
@csrf_protect
def studio_submit(request, workspace):
    if request.content_type != 'application/json':
        return _error('Envie o conteúdo como application/json.', 415)
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BODY_BYTES:
            return _error('A solicitação excede o tamanho permitido.', 413)
        body = request.body
    except ValueError:
        return _error('Tamanho de solicitação inválido.', 400)
    except RequestDataTooBig:
        return _error('A solicitação excede o tamanho permitido.', 413)
    if len(body) > MAX_BODY_BYTES:
        return _error('A solicitação excede o tamanho permitido.', 413)
    try:
        workspace_id, agent_id, title, description, kind, request_id = _request_fields(body)
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        return _error('O conteúdo da solicitação não é um JSON válido.', 400)
    except ValueError as exc:
        return _error(str(exc), 400)

    if workspace_id != workspace.pk:
        return _error('A empresa ativa mudou. Recarregue o escritório antes de enviar.', 409)
    agent = Agent.objects.filter(pk=agent_id, workspace=workspace).first()
    if agent is None:
        return _error('Agente indisponível nesta empresa.', 404)
    research_available = kind == 'research' and _research_available(workspace, services.provider_ready(workspace))
    available_kinds = _task_kinds(agent, research_available)

    prefix = f'studio.request.{_digest(request_id)}.' if request_id else ''
    event_kind = prefix + _digest(json.dumps([agent_id, title, description, kind])) if prefix else 'task.created'

    try:
        with transaction.atomic():
            # In SQLite the initial INSERT acquires the writer lock before
            # checking the retry key, serializing concurrent submissions.
            task = Task.objects.create(workspace=workspace, agent=agent, title=title, objective=description,
                                       kind=kind, priority='normal', mode=workspace.ai_mode)
            if prefix:
                previous = Event.objects.filter(workspace=workspace, kind__startswith=prefix,
                                                task__workspace=workspace).select_related('task').first()
                if previous:
                    transaction.set_rollback(True)
                    if previous.kind != event_kind:
                        return _error('Identificador já usado para outra solicitação.', 409)
                    return _submitted(previous.task, 200)
            if kind not in available_kinds:
                transaction.set_rollback(True)
                return _error('Este tipo de tarefa não está disponível para o agente selecionado.', 400)
            record_event(workspace, event_kind, f'Tarefa criada no escritório 3D: {title}', task)
            task = services.queue_task(task)
    except ValidationError as exc:
        return _error(' '.join(exc.messages), 409)
    return _submitted(task, 201)
