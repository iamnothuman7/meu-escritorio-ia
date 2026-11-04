import csv
import hashlib
import hmac
import os
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Agent, Approval, Delivery, Event, FinancialEntry, Lead, Project, Routine, Run, Settlement, SystemState, Task, Workspace
from . import services


def health(request):
    return JsonResponse({'status': 'ok', 'product': 'Nabi Office'})


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
                workspace = services.bootstrap_workspace(user, name='Nabi Office')
                request.session['workspace_id'] = str(workspace.pk)
            login(request, user)
            return redirect('/')
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    return render(request, 'office/auth.html', {'setup': True, 'error': error, 'hosted':os.environ.get('NABI_HTTPS')=='1'})


def login_route(request):
    from django.contrib.auth.views import LoginView
    if not get_user_model().objects.exists():
        return redirect('/setup/')
    return LoginView.as_view(template_name='office/auth.html',extra_context={'hosted':os.environ.get('NABI_HTTPS')=='1'})(request)


def active_workspace(request):
    spaces = Workspace.objects.filter(owner=request.user).order_by('created_at')
    space = spaces.filter(pk=request.session.get('workspace_id')).first() if request.session.get('workspace_id') else None
    if space is None:
        space = spaces.first()
    if space is None:
        space = services.bootstrap_workspace(request.user)
    return space


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
    return {'w':w,'workspaces':Workspace.objects.filter(owner=request.user),'agents':agents,'all_agents':agents,'page':page,'pending_count':Approval.objects.filter(workspace=w,status='pending').count(),'state':state,'worker_online':worker_online,'now':timezone.localtime(),'offline':w.ai_mode == 'offline','hosted':os.environ.get('NABI_HTTPS')=='1'}


@login_required
def dashboard(request):
    c = context(request, 'today')
    w = c['w']
    tasks = Task.objects.filter(workspace=w).select_related('agent')
    c.update(task_count=tasks.exclude(status__in=['completed','cancelled']).count(), deadline_count=tasks.filter(due_date__lte=timezone.localdate()+timedelta(days=3)).exclude(status__in=['completed','cancelled']).count(), completed_count=tasks.filter(status='completed').count(), priorities=tasks.exclude(status__in=['completed','cancelled']).order_by('due_date','-created_at')[:3], events=Event.objects.filter(workspace=w).order_by('-created_at')[:6], agents=c['agents'][:6])
    return render(request, 'office/dashboard.html', c)


@login_required
def team(request):
    return render(request, 'office/team.html', context(request, 'team'))


@login_required
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


@login_required
@require_POST
def create_task(request):
    w = active_workspace(request)
    agent = get_object_or_404(Agent,pk=request.POST.get('agent'),workspace=w,enabled=True)
    title = request.POST.get('title','').strip()[:180]
    objective = request.POST.get('objective','').strip()[:12000]
    kind = request.POST.get('kind','general')
    if not title or not objective or kind not in ['general','content','summary','finance','template']:
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


@login_required
def task_detail(request, pk):
    c = context(request,'tasks')
    task = get_object_or_404(Task,pk=pk,workspace=c['w'])
    c.update(task=task,runs=Run.objects.filter(task=task).order_by('-started_at'),deliveries=Delivery.objects.filter(workspace=c['w'],task=task).order_by('-created_at'),events=Event.objects.filter(workspace=c['w'],task=task).order_by('created_at'))
    return render(request,'office/task_detail.html',c)


@login_required
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


@login_required
def approvals(request):
    c = context(request,'approvals')
    items = list(Approval.objects.filter(workspace=c['w'],status='pending').select_related('delivery','delivery__task').order_by('-id')[:100])
    for item in items:
        item.current_hash = services.delivery_hash(item.delivery)
    c.update(items=items)
    return render(request,'office/approvals.html',c)


@login_required
@require_POST
def decision(request, pk):
    w = active_workspace(request)
    approval = get_object_or_404(Approval,pk=pk,workspace=w,delivery__workspace=w)
    try:
        services.approve_delivery(approval.delivery,request.POST.get('decision'),request.POST.get('hash',''))
        messages.success(request,'Decisão registrada para esta versão da entrega.')
    except (ValidationError,ValueError) as exc:
        messages.error(request,str(exc))
    return redirect('/approvals/')


@login_required
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


@login_required
def download_delivery(request, pk):
    delivery = get_object_or_404(Delivery,pk=pk,workspace=active_workspace(request))
    response = HttpResponse(delivery.body, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="nabi-entrega-{delivery.pk}.txt"'
    return response


@login_required
def work(request):
    c = context(request,'work')
    tab = request.GET.get('tab','content')
    if tab not in ['content','leads','finance','projects','routines']:
        tab = 'content'
    w = c['w']
    c.update(tab=tab,leads=Lead.objects.filter(workspace=w).order_by('-created_at')[:100],projects=Project.objects.filter(workspace=w).order_by('-created_at')[:100],contents=Delivery.objects.filter(workspace=w,kind='content').select_related('task').order_by('-created_at')[:100],routines=Routine.objects.filter(workspace=w).select_related('agent'),totals=services.finance_totals(w))
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


@login_required
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
            if kind not in ['content','general','summary','finance','template']: raise ValueError('Tipo inválido.')
            name=request.POST.get('name','').strip()[:180]
            objective=request.POST.get('objective','').strip()[:5000]
            if not name or not objective: raise ValueError('Informe nome e objetivo da rotina.')
            Routine.objects.create(workspace=w,agent=agent,name=name,objective=objective,kind=kind,interval_minutes=interval,enabled=False)
        elif action == 'routine_toggle':
            routine=get_object_or_404(Routine,pk=request.POST.get('id'),workspace=w)
            enabling=not routine.enabled
            if enabling and routine.kind not in ['summary','finance','template'] and w.ai_mode=='offline': raise ValueError('Escolha e configure um provedor de IA antes de ativar esta rotina.')
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


@login_required
def settings_view(request):
    c=context(request,'settings')
    c['budget']=c['w'].policy.get('budget',{})
    c['data_dir']=str(settings.DATA_DIR)
    c['usage']=Run.objects.filter(task__workspace=c['w']).order_by('-started_at')[:20]
    return render(request,'office/settings.html',c)


@login_required
@require_POST
def settings_action(request):
    w=active_workspace(request)
    action=request.POST.get('action')
    try:
        if action in ['brand','new_workspace']:
            name=request.POST.get('name','').strip()[:120]
            color=request.POST.get('brand_color','#36b6a1')
            if not name or not re.fullmatch(r'#[0-9a-fA-F]{6}',color): raise ValueError('Nome e cor são obrigatórios.')
            if action=='new_workspace':
                w=services.bootstrap_workspace(request.user,name=name)
                request.session['workspace_id']=str(w.pk)
            w.name=name
            w.segment=request.POST.get('segment','')[:120]
            w.description=request.POST.get('description','')[:12000]
            w.brand_color=color
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
        elif action=='provider':
            mode=request.POST.get('ai_mode','offline')
            if mode not in ['offline','openai','claude']: raise ValueError('O login ChatGPT ainda depende de homologação e não pode ser ativado.')
            model=request.POST.get('ai_model','').strip()[:120]
            if mode!='offline' and not model: raise ValueError('Informe o ID do modelo validado na sua conta.')
            w.ai_mode=mode
            w.ai_model=model
            if mode!='offline':
                if not request.POST.get('confirmed'): raise ValueError('Confirme a política antes de selecionar um provedor.')
                budget={key:request.POST.get(key,'') for key in ['expires_at','limit_usd','input_per_million','output_per_million']}
                for key in ['limit_usd','input_per_million','output_per_million']:
                    n=Decimal(budget[key])
                    if not n.is_finite() or n<0: raise ValueError('Valores de política inválidos.')
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
    except (ValueError,InvalidOperation,ValidationError) as exc:
        messages.error(request,str(exc))
    return redirect('/settings/')


@login_required
@require_POST
def switch_workspace(request):
    w=get_object_or_404(Workspace,pk=request.POST.get('workspace'),owner=request.user)
    request.session['workspace_id']=str(w.pk)
    return redirect('/')


@login_required
@require_POST
def pause(request):
    state,_=SystemState.objects.get_or_create(pk=1)
    state.paused=not state.paused
    state.save()
    record_event(active_workspace(request),'office.paused' if state.paused else 'office.resumed','Escritório pausado.' if state.paused else 'Escritório retomado.')
    return redirect(request.POST.get('return_to') if request.POST.get('return_to') in ['/','/tasks/','/settings/'] else '/')


@login_required
def logo(request, pk):
    w=get_object_or_404(Workspace,pk=pk,owner=request.user)
    if not w.logo: raise Http404()
    return FileResponse(w.logo.open('rb'),content_type='image/png' if w.logo.name.lower().endswith('.png') else 'image/jpeg')


@login_required
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


@login_required
@require_POST
def backup_download(request):
    if not request.user.is_staff:
        return HttpResponse('O backup completo exige acesso de administrador da instalação.',status=403)
    from .backup import create_backup
    path=create_backup()
    return FileResponse(open(path,'rb'),as_attachment=True,filename=Path(path).name)


@login_required
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


@login_required
def api_dashboard(request):
    w=active_workspace(request)
    return JsonResponse({'workspace':str(w.pk),'tasks':list(Task.objects.filter(workspace=w).values('id','title','status')[:100]),'pending':Approval.objects.filter(workspace=w,status='pending').count(),'paused':SystemState.objects.filter(pk=1,paused=True).exists()})
