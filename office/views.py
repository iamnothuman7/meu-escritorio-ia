import csv
import hashlib
import hmac
import os
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from xml.sax.saxutils import escape
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache

from .models import Agent, Approval, Delivery, Event, FinancialEntry, InstagramPublication, Lead, Project, Routine, Run, Settlement, SystemState, Task, Workspace
from . import chatgpt_plan, credentials, instagram, services
from .studio_scope import supports_studio


def office_required(view):
    @login_required
    @wraps(view)
    def dispatch(request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect('platform_admin')
        return view(request, *args, **kwargs)
    return dispatch


class OfficeLoginView(LoginView):
    redirect_authenticated_user = True

    def get_success_url(self):
        if self.request.user.is_superuser:
            return reverse('platform_admin')
        next_url = self.get_redirect_url()
        if next_url and urlsplit(next_url).path not in ('', '/', '/login/', '/access/'):
            return next_url
        return reverse('office_selector')


def health(request):
    return JsonResponse({'status': 'ok', 'product': 'Nabio Office'})


def webmanifest(request):
    response=JsonResponse({'id':'/','name':'Nabio Office','short_name':'Nabio Office','description':'Seu escritório de inteligência artificial. Empresas, equipe e decisões em um só lugar.','lang':'pt-BR','start_url':reverse('office_selector'),'scope':'/','display':'standalone','background_color':'#0B1020','theme_color':'#0B1020','icons':[{'src':'/static/office/brand/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any'},{'src':'/static/office/brand/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any'}]},content_type='application/manifest+json')
    return response


def service_worker(request):
    path=settings.BASE_DIR/'office/static/office/sw.js'
    response=HttpResponse(path.read_text(encoding='utf-8'),content_type='application/javascript')
    response['Cache-Control']='no-cache'
    response['Service-Worker-Allowed']='/'
    return response


def offline_page(request):
    return render(request,'office/offline.html')


def favicon(request):
    return FileResponse(open(settings.BASE_DIR/'office/static/office/brand/favicon.ico','rb'),content_type='image/x-icon')


def setup(request):
    if get_user_model().objects.exists():
        return redirect('/login/')
    token=os.environ.get('NABI_SETUP_TOKEN','')
    if os.environ.get('NABI_HTTPS')=='1' and not token:
        return render(request,'office/activation_required.html',status=403)
    if token and not request.session.get('setup_authorized'):
        if not hmac.compare_digest(request.GET.get('token','').encode('utf-8'),token.encode('utf-8')):
            return render(request,'office/activation_required.html',status=403)
        request.session['setup_authorized']=True
        return redirect('/setup/')
    error = ''
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        try:
            if not re.fullmatch(r'[\w.@+-]{3,80}', username):
                raise ValidationError('Use um usuário de 3 a 80 caracteres.')
            user = get_user_model()(username=username, first_name=request.POST.get('name', '').strip()[:80])
            user.is_staff = True
            validate_password(password, user)
            with transaction.atomic():
                if get_user_model().objects.exists():
                    return redirect('/login/')
                user.set_password(password)
                user.save()
                workspace = services.bootstrap_workspace(user)
                request.session['workspace_id'] = str(workspace.pk)
            login(request, user)
            return redirect('/app/')
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    return render(request, 'office/auth.html', {'setup': True, 'error': error, 'hosted':os.environ.get('NABI_HTTPS')=='1'})


def login_route(request):
    return OfficeLoginView.as_view(template_name='office/auth.html',extra_context={'hosted':os.environ.get('NABI_HTTPS')=='1'})(request)


def active_workspace(request):
    spaces = Workspace.objects.filter(owner=request.user).order_by('created_at')
    space = spaces.filter(pk=request.session.get('workspace_id')).first() if request.session.get('workspace_id') else None
    if space is None:
        space = spaces.first()
    if space is None:
        space = services.bootstrap_workspace(request.user)
    return space


@office_required
@never_cache
def office_selector(request):
    workspaces = list(Workspace.objects.filter(owner=request.user).order_by('name', 'pk'))
    if not workspaces:
        workspaces = [active_workspace(request)]
    choices = [dict(workspace=workspace, studio_enabled=supports_studio(workspace)) for workspace in workspaces]
    choices.sort(key=lambda choice: not choice['studio_enabled'])
    return render(request, 'office/office_select.html', {'office_choices': choices})


def context(request, page):
    w = active_workspace(request)
    task_qs = Task.objects.filter(workspace=w)
    agents = list(Agent.objects.filter(workspace=w).order_by('id'))
    for agent in agents:
        agent.current_task = task_qs.filter(agent=agent, status__in=['running','queued','review','blocked']).order_by('-updated_at').first()
        agent.last_delivery = Delivery.objects.filter(workspace=w, task__agent=agent).order_by('-created_at').first()
    state, _ = SystemState.objects.get_or_create(pk=1)
    heartbeat = state.worker_heartbeat
    worker_online = bool(heartbeat and heartbeat > timezone.now() - timedelta(seconds=30))
    is_htmx=request.headers.get('HX-Request')=='true' and request.headers.get('HX-History-Restore-Request')!='true'
    return {'w':w,'workspaces':Workspace.objects.filter(owner=request.user),'agents':agents,'all_agents':agents,'page':page,'pending_count':Approval.objects.filter(workspace=w,status='pending',expires_at__gt=timezone.now()).count(),'state':state,'worker_online':worker_online,'now':timezone.localtime(),'offline':w.ai_mode == 'offline','hosted':os.environ.get('NABI_HTTPS')=='1','is_htmx':is_htmx,'base_template':'office/shell.html' if is_htmx else 'office/base.html','studio_enabled':supports_studio(w)}


def attach_research_citations(deliveries):
    for delivery in deliveries:
        delivery.research_citations = []
    researched = {(item.task_id, item.body): item for item in deliveries if item.kind == 'research'}
    if not researched:
        return
    runs = Run.objects.filter(task_id__in={task_id for task_id, _ in researched}, status='completed').order_by('pk')
    for run in runs:
        delivery = researched.get((run.task_id, run.output))
        if delivery and run.usage.get('research_status'):
            delivery.research_citations = run.usage.get('citations', [])
            delivery.research_missing_sources = not delivery.research_citations


@office_required
def dashboard(request):
    c = context(request, 'today')
    w = c['w']
    tasks = Task.objects.filter(workspace=w).select_related('agent')
    c.update(task_count=tasks.exclude(status__in=['completed','cancelled']).count(), deadline_count=tasks.filter(due_date__lte=timezone.localdate()+timedelta(days=3)).exclude(status__in=['completed','cancelled']).count(), completed_count=tasks.filter(status='completed').count(), priorities=tasks.exclude(status__in=['completed','cancelled']).order_by('due_date','-created_at')[:3], events=Event.objects.filter(workspace=w).order_by('-created_at')[:6])
    return render(request, 'office/dashboard.html', c)


@office_required
def team(request):
    return render(request, 'office/team.html', context(request, 'team'))


@office_required
def tasks(request):
    c = context(request, 'tasks')
    items = Task.objects.filter(workspace=c['w']).select_related('agent').order_by('-created_at')
    q = request.GET.get('q','').strip()[:120]
    status = request.GET.get('status','')
    if q:
        items = items.filter(Q(title__icontains=q)|Q(objective__icontains=q))
    if status:
        items = items.filter(status=status)
    c.update(items=items[:100], q=q, filter_status=status, tab='tasks')
    return render(request, 'office/tasks.html', c)


def record_event(w, kind, message, task=None):
    Event.objects.create(workspace=w,kind=kind,message=message,task=task)


@office_required
@require_POST
def create_task(request):
    w = active_workspace(request)
    agent = get_object_or_404(Agent,pk=request.POST.get('agent'),workspace=w,enabled=True)
    title = request.POST.get('title','').strip()[:180]
    objective = request.POST.get('objective','').strip()[:12000]
    kind = request.POST.get('kind','general')
    if not title or not objective or kind not in ['general','content','research','summary','finance','template']:
        messages.error(request,'Informe um título, um objetivo e um tipo válido.')
        return redirect('/tasks/')
    # Browser-generated key also prevents duplicate form submission after a retry.
    key = request.POST.get('request_id','')[:100]
    digest = hashlib.sha256(json.dumps(dict(request.POST),sort_keys=True).encode()).hexdigest()
    seen = request.session.get('task_requests',{})
    if key and key in seen:
        if seen[key]['hash'] != digest:
            return HttpResponse('Chave já usada para outra solicitação.',status=409)
        return redirect(f'/tasks/{seen[key]["id"]}/')
    try:
        due = date.fromisoformat(request.POST['due_date']) if request.POST.get('due_date') else None
        task = Task.objects.create(workspace=w,agent=agent,title=title,objective=objective,inputs=request.POST.get('inputs','')[:20000],kind=kind,mode='offline' if kind in ['summary','finance','template'] else w.ai_mode,priority=request.POST.get('priority','normal') if request.POST.get('priority') in ['low','normal','high'] else 'normal',due_date=due)
        record_event(w,'task.created',f'Tarefa criada: {title}',task)
        if key:
            seen[key] = {'hash':digest,'id':str(task.pk)}
            request.session['task_requests'] = dict(list(seen.items())[-50:])
        if request.POST.get('enqueue'):
            services.queue_task(task)
        return redirect(f'/tasks/{task.pk}/')
    except (ValidationError,ValueError) as exc:
        messages.error(request,str(exc))
        return redirect('/tasks/')


@office_required
def task_detail(request, pk):
    c = context(request,'tasks')
    task = get_object_or_404(Task,pk=pk,workspace=c['w'])
    deliveries = list(Delivery.objects.filter(workspace=c['w'],task=task).select_related('generated_image')
                      .defer('generated_image__data').order_by('-created_at'))
    attach_research_citations(deliveries)
    c.update(task=task,runs=Run.objects.filter(task=task).order_by('-started_at'),deliveries=deliveries,
             events=Event.objects.filter(workspace=c['w'],task=task).order_by('created_at'),
             openai_key=credentials.has_api_key(request.user.pk,'openai'),
             image_pending=any(getattr(item.generated_image, 'status', '') in ('queued','running')
                               for item in deliveries if hasattr(item,'generated_image')))
    return render(request,'office/task_detail.html',c)


@office_required
@require_POST
def task_action(request, pk):
    w = active_workspace(request)
    task = get_object_or_404(Task,pk=pk,workspace=w)
    action = request.POST.get('action')
    try:
        if action == 'run':
            # Retry is explicit and uses the current workspace mode.
            if task.status != 'running':
                task.mode = 'offline' if task.kind in ['summary','finance','template'] else w.ai_mode
                task.save(update_fields=['mode'])
            services.queue_task(task)
            messages.success(request,'Tarefa enviada à fila. O executor processa uma por vez.')
        elif action in ['pause','cancel'] and task.status not in ['completed','cancelled']:
            task.status = 'paused' if action == 'pause' else 'cancelled'
            task.save(update_fields=['status','updated_at'])
            record_event(w,'task.'+task.status,'Interrupção solicitada pelo usuário.',task)
    except (ValidationError,ValueError) as exc:
        messages.error(request,str(exc))
    return redirect(f'/tasks/{task.pk}/')


@office_required
def approvals(request):
    c = context(request,'approvals')
    items = list(Approval.objects.filter(workspace=c['w'],status='pending',expires_at__gt=timezone.now())
                 .select_related('delivery__task__agent','delivery__generated_image')
                 .defer('delivery__generated_image__data').order_by('-id')[:100])
    attach_research_citations([item.delivery for item in items])
    ig = instagram.connection_status(c['w'])
    for item in items:
        item.current_hash = services.delivery_hash(item.delivery)
        image = getattr(item.delivery,'generated_image',None)
        if image and image.status=='ready':
            item.publish_caption = instagram.caption_for(item.delivery)
    publications = list(InstagramPublication.objects.filter(workspace=c['w']).select_related('delivery').order_by('-created_at')[:20])
    c.update(items=items,openai_key=credentials.has_api_key(request.user.pk,'openai'),instagram_connection=ig,
             publications=publications,publication_pending=any(item.status in ('queued','uploading','waiting','checking','publishing') for item in publications))
    return render(request,'office/approvals.html',c)


@office_required
@require_POST
def decision(request, pk):
    w = active_workspace(request)
    approval = get_object_or_404(Approval,pk=pk,workspace=w,delivery__workspace=w)
    try:
        if request.POST.get('decision')=='publish':
            instagram.approve_and_queue(approval.delivery,request.POST.get('hash',''),request.POST.get('instagram_account_id',''))
            messages.success(request,'Entrega aprovada e enviada à fila do Instagram.')
        else:
            services.approve_delivery(approval.delivery,request.POST.get('decision'),request.POST.get('hash',''))
            messages.success(request,'Decisão registrada para esta versão da entrega.')
    except (ValidationError,ValueError) as exc:
        messages.error(request,str(exc))
    return redirect('/approvals/')


@office_required
@require_POST
def edit_delivery(request, pk):
    w = active_workspace(request)
    delivery = get_object_or_404(Delivery,pk=pk,workspace=w)
    try:
        with transaction.atomic():
            changed = Delivery.objects.filter(pk=pk,workspace=w,version=int(request.POST.get('version',0))).update(body=request.POST.get('body','')[:40000],version=delivery.version+1,updated_at=timezone.now())
            if not changed:
                return HttpResponse('A entrega foi alterada. Recarregue antes de editar.',status=409)
            Approval.objects.filter(delivery=delivery,status__in=['pending','approved']).update(status='expired')
            delivery.refresh_from_db()
            Approval.objects.create(workspace=w,delivery=delivery,payload_hash=services.delivery_hash(delivery),expires_at=timezone.now()+timedelta(days=7))
            delivery.task.status='review'
            delivery.task.save(update_fields=['status'])
            record_event(w,'draft.updated','Entrega editada; uma nova revisão é necessária.',delivery.task)
        messages.success(request,'Entrega atualizada e aprovação anterior invalidada.')
    except (ValidationError,ValueError) as exc:
        messages.error(request,str(exc))
    return redirect(f'/tasks/{delivery.task_id}/')


@office_required
def download_delivery(request, pk):
    delivery = get_object_or_404(Delivery,pk=pk,workspace=active_workspace(request))
    response = HttpResponse(delivery.body, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="nabi-entrega-{delivery.pk}.txt"'
    return response


@office_required
def work(request):
    c = context(request,'work')
    tab = request.GET.get('tab','content')
    if tab not in ['content','leads','finance','projects','routines']:
        tab = 'content'
    w = c['w']
    c.update(tab=tab,leads=Lead.objects.filter(workspace=w).order_by('-created_at')[:100],projects=Project.objects.filter(workspace=w).order_by('-created_at')[:100],contents=Delivery.objects.filter(workspace=w,kind='content').select_related('task').order_by('-created_at')[:100],routines=Routine.objects.filter(workspace=w).select_related('agent'),totals=services.finance_totals(w))
    if tab == 'routines':
        c['provider_ready'] = services.provider_ready(w)
    entries = list(FinancialEntry.objects.filter(workspace=w).prefetch_related('settlements').order_by('-created_at')[:100])
    for e in entries:
        e.paid_cents = sum(s.amount_cents for s in e.settlements.all())
        e.is_overdue = bool(e.due_date and e.due_date < timezone.localdate() and e.remaining_cents>0)
    c['entries'] = entries
    return render(request,'office/work.html',c)


def cents(value):
    value = Decimal(value.replace(',','.'))
    if not value.is_finite() or value <= 0 or value > 100000000 or value.as_tuple().exponent < -2:
        raise ValueError('Informe um valor positivo com até duas casas decimais.')
    return int(value*100)


@office_required
@require_POST
def work_action(request):
    w = active_workspace(request)
    action = request.POST.get('action')
    tab = request.POST.get('tab','content')
    try:
        if action == 'lead':
            name = request.POST.get('company','').strip()[:180]
            if not name: raise ValueError('Informe a empresa.')
            website = request.POST.get('website','').strip()[:500]
            if website and not website.startswith(('http://','https://')): raise ValueError('Use um site iniciado em https:// ou http://.')
            if Lead.objects.filter(workspace=w,company__iexact=name,city__iexact=request.POST.get('city','').strip()).exists(): raise ValueError('Já existe uma empresa com esse nome e cidade. Revise o cadastro existente.')
            source=request.POST.get('source_url','').strip()[:500]
            if source and not source.startswith(('http://','https://')): raise ValueError('A fonte deve ser uma URL http:// ou https://.')
            Lead.objects.create(workspace=w,company=name,segment=request.POST.get('segment','')[:120],city=request.POST.get('city','')[:120],website=website,contact=request.POST.get('contact','')[:200],source_url=source,notes=request.POST.get('notes','')[:4000])
        elif action == 'lead_stage':
            lead=get_object_or_404(Lead,pk=request.POST.get('id'),workspace=w)
            stage=request.POST.get('stage')
            if stage not in ['new','qualified','contacted','proposal','won','closed']: raise ValueError('Etapa inválida.')
            if lead.do_not_contact and stage=='contacted': raise ValueError('Este contato está bloqueado.')
            lead.stage=stage
            lead.do_not_contact=bool(request.POST.get('do_not_contact'))
            lead.save()
        elif action == 'project':
            name=request.POST.get('name','').strip()[:180]
            if not name: raise ValueError('Informe o nome do projeto.')
            Project.objects.create(workspace=w,name=name,description=request.POST.get('description','')[:5000],due_date=request.POST.get('due_date') or None)
        elif action == 'project_status':
            project=get_object_or_404(Project,pk=request.POST.get('id'),workspace=w)
            if request.POST.get('status') not in ['active','paused','done']: raise ValueError('Estado inválido.')
            project.status=request.POST['status']
            project.save()
        elif action == 'finance':
            description=request.POST.get('description','').strip()[:200]
            if not description: raise ValueError('Informe a descrição.')
            kind=request.POST.get('kind')
            if kind not in ['income','expense']: raise ValueError('Tipo inválido.')
            FinancialEntry.objects.create(workspace=w,description=description,kind=kind,amount_cents=cents(request.POST.get('amount','0')),due_date=request.POST.get('due_date') or None)
        elif action == 'settle':
            with transaction.atomic():
                entry=get_object_or_404(FinancialEntry,pk=request.POST.get('id'),workspace=w)
                total=Settlement.objects.filter(entry=entry).aggregate(n=Sum('amount_cents'))['n'] or 0
                amount=cents(request.POST.get('amount','0'))
                reverse=bool(request.POST.get('reverse'))
                reason=request.POST.get('reason','').strip()[:500]
                if reverse and (amount>total or not reason): raise ValueError('O estorno exige motivo e não pode ultrapassar o valor liquidado.')
                if not reverse and amount>entry.amount_cents-total: raise ValueError('O valor supera o saldo da obrigação.')
                Settlement.objects.create(entry=entry,amount_cents=-amount if reverse else amount,paid_on=date.fromisoformat(request.POST.get('paid_on') or str(timezone.localdate())),reason=reason)
        elif action == 'content':
            agent=Agent.objects.filter(workspace=w,slug='luma').first()
            title=request.POST.get('title','').strip()[:180]
            body=request.POST.get('body','').strip()[:20000]
            if not title or not body: raise ValueError('Informe título e legenda.')
            task=Task.objects.create(workspace=w,agent=agent,title=title,objective='Rascunho editorial cadastrado pelo usuário.',kind='content',mode='offline',status='review')
            delivery=Delivery.objects.create(workspace=w,task=task,title=title,body=body,kind='content')
            Approval.objects.create(workspace=w,delivery=delivery,payload_hash=services.delivery_hash(delivery),expires_at=timezone.now()+timedelta(days=7))
        elif action == 'routine':
            agent=get_object_or_404(Agent,pk=request.POST.get('agent'),workspace=w,enabled=True)
            interval=int(request.POST.get('interval_minutes',240))
            if interval<30 or interval>10080: raise ValueError('Use um intervalo entre 30 minutos e 7 dias.')
            kind=request.POST.get('kind','content')
            if kind not in ['content','general','research','summary','finance','template']: raise ValueError('Tipo inválido.')
            name=request.POST.get('name','').strip()[:180]
            objective=request.POST.get('objective','').strip()[:5000]
            if not name or not objective: raise ValueError('Informe nome e objetivo da rotina.')
            Routine.objects.create(workspace=w,agent=agent,name=name,objective=objective,kind=kind,interval_minutes=interval,enabled=False)
        elif action == 'team_activate':
            if not request.POST.get('confirm'):
                raise ValueError('Confirme a geração recorrente para ativar a equipe.')
            services.activate_team(w)
            record_event(w,'team.activated','Equipe recorrente ativada. Os rascunhos serão enviados à fila de revisão.')
            messages.success(request,'Equipe ativada. As primeiras tarefas entram na fila em instantes.')
            return redirect('/work/?tab=routines')
        elif action == 'routine_run':
            routine=get_object_or_404(Routine,pk=request.POST.get('id'),workspace=w)
            services.run_routine_now(routine)
            messages.success(request,'Execução incluída na fila. A entrega aparecerá em Aprovações.')
            return redirect('/work/?tab=routines')
        elif action == 'routine_toggle':
            routine=get_object_or_404(Routine,pk=request.POST.get('id'),workspace=w)
            enabling=not routine.enabled
            if enabling and routine.kind not in services.LOCAL_KINDS and not services.provider_ready(w): raise ValueError('Conecte e autorize um provedor de IA antes de ativar esta rotina.')
            if enabling and not request.POST.get('confirm'): raise ValueError('Confirme a execução recorrente para ativar.')
            routine.enabled=enabling
            routine.next_run=timezone.now()+timedelta(minutes=routine.interval_minutes) if enabling else None
            routine.save()
        else:
            raise ValueError('Ação desconhecida.')
        record_event(w,'record.updated','Registro de '+{'content':'conteúdo','leads':'comercial','finance':'financeiro','projects':'projeto','routines':'rotina'}.get(tab,'trabalho')+' atualizado pelo usuário.')
        messages.success(request,'Salvo no seu escritório.')
    except (ValueError,InvalidOperation,ValidationError) as exc:
        messages.error(request,str(exc))
    return redirect('/work/?tab='+tab)


@office_required
def settings_view(request):
    c=context(request,'settings')
    c['budget']=c['w'].policy.get('budget',{})
    c['usage']=Run.objects.filter(task__workspace=c['w']).order_by('-started_at')[:20]
    c['plan']=services.plan_connection(c['w'])
    c['provider_ready']=services.provider_ready(c['w'])
    c['openai_key']=credentials.has_api_key(request.user.pk,'openai')
    c['claude_key']=credentials.has_api_key(request.user.pk,'claude')
    c['instagram_connection']=instagram.connection_status(c['w'])
    return render(request,'office/settings.html',c)


@office_required
@require_POST
@sensitive_post_parameters('api_key','instagram_access_token')
def settings_action(request):
    w=active_workspace(request)
    action=request.POST.get('action')
    try:
        if action in ['brand','new_workspace']:
            name=request.POST.get('name','').strip()[:120]
            color=request.POST.get('brand_color','#36b6a1')
            if not name or not re.fullmatch(r'#[0-9a-fA-F]{6}',color): raise ValueError('Nome e cor são obrigatórios.')
            if 'art_background' in request.POST:
                background=request.POST.get('art_background','').strip().upper()
                details=request.POST.get('art_details','').strip().upper() or '#FFFFFF'
                if (background and not re.fullmatch(r'#[0-9A-F]{6}',background)) or not re.fullmatch(r'#[0-9A-F]{6}',details):
                    raise ValueError('Informe as cores das artes no formato #RRGGBB.')
            if action=='new_workspace':
                w=services.bootstrap_workspace(request.user,name=name)
                request.session['workspace_id']=str(w.pk)
            w.name=name
            w.segment=request.POST.get('segment','')[:120]
            w.description=request.POST.get('description','')[:12000]
            w.brand_color=color
            if 'art_background' in request.POST:
                w.policy={**w.policy}
                if background:
                    w.policy['brand_art']={'background':background,'details':details}
                else:
                    w.policy.pop('brand_art',None)
            if request.FILES.get('logo'):
                file=request.FILES['logo']
                # Accept only raster signatures; never render uploaded SVG/HTML.
                signature=file.read(12)
                file.seek(0)
                if file.size>2*1024*1024 or not (signature.startswith(b'\x89PNG\r\n\x1a\n') or signature.startswith(b'\xff\xd8\xff')): raise ValueError('Use uma logo PNG ou JPEG de até 2 MB.')
                w.logo=file
            w.save()
        elif action=='agent':
            agent=get_object_or_404(Agent,pk=request.POST.get('id'),workspace=w)
            name=request.POST.get('name','').strip()[:80]
            role=request.POST.get('role','').strip()[:120]
            instructions=request.POST.get('instructions','').strip()[:12000]
            if not name or not role or not instructions: raise ValueError('Preencha nome, função e instruções.')
            if str(agent.version)!=request.POST.get('version'): return HttpResponse('Configuração alterada. Recarregue antes de editar.',status=409)
            agent.name=name
            agent.role=role
            agent.instructions=instructions
            agent.enabled=bool(request.POST.get('enabled'))
            agent.version+=1
            agent.save()
        elif action=='api_key':
            mode=request.POST.get('api_mode','')
            if mode not in ('openai','claude'):
                raise ValueError('Escolha OpenAI API ou Claude API.')
            if request.POST.get('clear')=='1':
                credentials.delete_api_key(request.user.pk,mode)
                messages.success(request,'Chave removida da sua conta.')
            else:
                credentials.save_api_key(request.user.pk,mode,request.POST.get('api_key',''))
                messages.success(request,'Chave salva na sua conta.')
            return redirect('/settings/#connections')
        elif action=='image_automation':
            mode=request.POST.get('image_mode','off')
            if mode not in ('off','brand','openai'):
                raise ValueError('Escolha um modo de criação de imagens.')
            if mode=='openai' and (not request.POST.get('confirm_image') or not credentials.has_api_key(request.user.pk,'openai')):
                raise ValueError('Cadastre sua chave da OpenAI API e confirme a cobrança de imagens.')
            w.policy={**w.policy,'image_automation':mode}
            w.save(update_fields=['policy'])
        elif action=='web_research':
            enabled=request.POST.get('enabled')=='1'
            if enabled and not services.plan_connection(w)['connected']:
                raise ValueError('Autorize o ChatGPT neste espaço para ativar pesquisa.')
            w.policy={**w.policy,'web_research':enabled}
            w.save(update_fields=['policy'])
        elif action=='instagram':
            if request.POST.get('clear')=='1':
                instagram.disconnect(w)
                messages.success(request,'Conta do Instagram desconectada.')
            else:
                instagram.connect(w,request.POST.get('instagram_access_token',''),request.POST.get('instagram_account_id',''))
                messages.success(request,'Conta do Instagram conectada.')
            return redirect('/settings/#instagram')
        elif action=='provider':
            mode=request.POST.get('ai_mode','offline')
            if mode not in ['offline','chatgpt','openai','claude']: raise ValueError('Escolha uma modalidade válida.')
            model=request.POST.get('ai_model','').strip()[:160]
            if mode=='chatgpt':
                if not services.plan_connection(w)['connected']:
                    raise ValueError('A conta ChatGPT ainda precisa ser autorizada pelo proprietário.')
                if not request.POST.get('plan_confirmed'):
                    raise ValueError('Autorize a geração com o plano ChatGPT para esta empresa.')
                models=chatgpt_plan.list_models(owner_id=w.owner_id)
                if not model:
                    model=models[0]['slug'] if models else ''
                if not any(item['slug']==model for item in models):
                    raise ValueError('O modelo não está no catálogo desta conta ChatGPT.')
                w.policy={**w.policy,'chatgpt_confirmed':True}
            elif mode!='offline' and not model:
                raise ValueError('Informe o ID do modelo validado na sua conta.')
            w.ai_mode=mode
            w.ai_model=model
            if mode in ('openai','claude'):
                if not request.POST.get('confirmed'): raise ValueError('Confirme a política antes de selecionar um provedor.')
                budget={key:request.POST.get(key,'') for key in ['expires_at','limit_usd','input_per_million','output_per_million']}
                for key in ['limit_usd','input_per_million','output_per_million']:
                    n=Decimal(budget[key])
                    if not n.is_finite() or n<0 or (key=='limit_usd' and n==0): raise ValueError('Valores de política inválidos.')
                expires=date.fromisoformat(budget['expires_at'])
                if expires<=timezone.localdate(): raise ValueError('A validade precisa ser futura.')
                budget['expires_at']=timezone.make_aware(timezone.datetime.combine(expires,timezone.datetime.min.time())).isoformat()
                budget.update(confirmed=True,provider=mode,model=model,max_output_tokens=1600,max_input_chars=24000,max_calls_per_day=12)
                w.policy={**w.policy,'budget':budget}
            w.save()
        else:
            raise ValueError('Ação desconhecida.')
        record_event(w,'settings.updated','Configuração atualizada pelo proprietário.')
        messages.success(request,'Configuração salva.')
    except (ValueError,InvalidOperation,ValidationError,chatgpt_plan.PlanError) as exc:
        messages.error(request,str(exc))
    return redirect('/settings/')


@office_required
@require_POST
def switch_workspace(request):
    try:
        workspace_id = int(request.POST.get('workspace', ''))
    except (TypeError, ValueError):
        raise Http404 from None
    workspace = get_object_or_404(Workspace, pk=workspace_id, owner=request.user)
    open_studio = request.POST.get('destination') == 'studio'
    if open_studio and not supports_studio(workspace):
        raise Http404
    request.session['workspace_id'] = str(workspace.pk)
    return redirect('office_3d' if open_studio else '/app/')


@office_required
@require_POST
def pause(request):
    w=active_workspace(request)
    with transaction.atomic():
        Workspace.objects.filter(pk=w.pk,owner=request.user).update(paused=~F('paused'))
        w.refresh_from_db(fields=['paused'])
        record_event(w,'office.paused' if w.paused else 'office.resumed','Escritório pausado.' if w.paused else 'Escritório retomado.')
    return redirect(request.POST.get('return_to') if request.POST.get('return_to') in ['/app/','/tasks/','/settings/'] else '/app/')


@office_required
def logo(request, pk):
    w=get_object_or_404(Workspace,pk=pk,owner=request.user)
    if not w.logo: raise Http404()
    return FileResponse(w.logo.open('rb'),content_type='image/png' if w.logo.name.lower().endswith('.png') else 'image/jpeg')


@office_required
def export_data(request):
    w=active_workspace(request)
    from django.core.serializers import serialize
    models=[Workspace,Agent,Task,Run,Delivery,Approval,Event,Routine,Lead,Project,FinancialEntry,Settlement]
    data={'format':'nabi-office-export-v1','exported_at':timezone.now().isoformat(),'workspace':w.name,'records':{}}
    for model in models:
        if model==Workspace: qs=model.objects.filter(pk=w.pk)
        elif model==Run: qs=model.objects.filter(task__workspace=w)
        elif model==Settlement: qs=model.objects.filter(entry__workspace=w)
        else: qs=model.objects.filter(workspace=w)
        fields=[f.name for f in model._meta.fields if f.name not in ['owner','policy','logo']]
        data['records'][model.__name__]=json.loads(serialize('json',qs,fields=fields))
    response=JsonResponse(data,json_dumps_params={'ensure_ascii':False,'indent':2})
    response['Content-Disposition']='attachment; filename="nabi-escritorio.json"'
    return response


@office_required
@require_POST
def backup_download(request):
    if settings.HTTPS_ENABLED or not request.user.is_staff:
        return HttpResponse('O backup completo exige acesso de administrador da instalação.',status=403)
    from .backup import create_backup
    path=create_backup()
    return FileResponse(open(path,'rb'),as_attachment=True,filename=Path(path).name)


@office_required
def post_art(request, pk):
    w=active_workspace(request)
    d=get_object_or_404(Delivery,pk=pk,workspace=w,kind='content')
    # Native, deterministic SVG layout from the user's approved title. No stock imagery or invented facts.
    title=d.title[:140]
    import textwrap
    lines=textwrap.wrap(title,22)[:5]
    spans=''.join(f'<tspan x="90" dy="92">{escape(line)}</tspan>' for line in lines)
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080"><rect width="1080" height="1080" fill="#132e36"/><circle cx="1010" cy="40" r="410" fill="{w.brand_color}" opacity=".18"/><rect x="90" y="90" width="56" height="8" rx="4" fill="{w.brand_color}"/><text x="90" y="165" fill="#ecf4f0" font-family="Arial,sans-serif" font-size="30">{escape(w.name)}</text><text x="90" y="280" fill="#f5f7f1" font-family="Arial,sans-serif" font-weight="bold" font-size="76">{spans}</text><path d="M90 880H990" stroke="{w.brand_color}" stroke-width="2"/><text x="90" y="955" fill="#c3d4d1" font-family="Arial,sans-serif" font-size="25">{escape(w.segment or 'Uma ideia. Novas possibilidades.')}</text></svg>'''
    response=HttpResponse(svg,content_type='image/svg+xml')
    response['Content-Disposition']=f'attachment; filename="nabi-post-{d.pk}.svg"'
    return response


@office_required
def api_dashboard(request):
    w=active_workspace(request)
    return JsonResponse({'workspace':str(w.pk),'tasks':list(Task.objects.filter(workspace=w).values('id','title','status')[:100]),'pending':Approval.objects.filter(workspace=w,status='pending').count(),'paused':services.is_paused(w)})
