from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from office import legal_prospecting, services
from office.models import Agent, Routine, Workspace


LEGACY_NAME = 'Pesquisa pública de escritórios de advocacia'


class Command(BaseCommand):
    help = 'Configura somente a prospecção jurídica do Radar em uma empresa específica.'

    def add_arguments(self, parser):
        parser.add_argument('--workspace', required=True, type=int, help='ID da empresa autorizada.')
        parser.add_argument('--owner', required=True, type=int, help='ID do titular da empresa.')
        parser.add_argument('--disable', action='store_true', help='Desativa somente a rotina jurídica selecionada.')

    @transaction.atomic
    def handle(self, **options):
        scope = Workspace.objects.filter(pk=options['workspace'], owner_id=options['owner'], owner__is_superuser=False)
        # SQLite takes a write lock here, before looking for a matching routine.
        if scope.update(paused=F('paused')) != 1:
            raise CommandError('Escritório não encontrado para esse titular.')
        workspace = scope.select_related('owner').get()
        radar = Agent.objects.filter(workspace=workspace, slug='radar').first()
        if radar is None:
            raise CommandError('O agente Radar não existe nesta empresa.')

        candidates = list(Routine.objects.filter(workspace=workspace).filter(
            Q(kind=legal_prospecting.KIND) | Q(agent=radar, kind='research', name=LEGACY_NAME),
        ).order_by('pk')[:2])
        if len(candidates) > 1:
            raise CommandError('Existe mais de uma rotina de prospecção jurídica. Revise as rotinas antes de configurar.')
        routine = candidates[0] if candidates else None
        if routine and routine.agent_id != radar.pk:
            raise CommandError('A rotina jurídica existente não está vinculada ao Radar.')

        if options['disable']:
            if routine and (routine.enabled or routine.next_run is not None):
                routine.enabled = False
                routine.next_run = None
                routine.save(update_fields=['enabled', 'next_run'])
            self.stdout.write(self.style.SUCCESS(f'Prospecção jurídica desativada na empresa {workspace.pk}.'))
            return

        if not workspace.owner.is_active or services.is_paused(workspace):
            raise CommandError('A empresa ou o executor está inativo ou pausado.')
        if not radar.enabled:
            raise CommandError('Ative o Radar nesta empresa antes de configurar a rotina.')
        if not isinstance(workspace.policy, dict):
            raise CommandError('A configuração da empresa não está no formato esperado.')
        if workspace.ai_mode != 'chatgpt' or not services.provider_ready(workspace):
            raise CommandError('Conecte e autorize o plano ChatGPT nesta empresa.')

        if workspace.policy.get('web_research') is not True:
            workspace.policy = {**workspace.policy, 'web_research': True}
            workspace.save(update_fields=['policy'])

        if routine is None:
            routine = Routine.objects.create(
                workspace=workspace, agent=radar, kind=legal_prospecting.KIND, name=legal_prospecting.ROUTINE_NAME,
                objective=legal_prospecting.ROUTINE_OBJECTIVE, interval_minutes=legal_prospecting.INTERVAL_MINUTES,
                enabled=True, next_run=timezone.now(),
            )
        else:
            schedule = routine.next_run if routine.enabled and routine.kind == legal_prospecting.KIND else None
            desired = {
                'kind': legal_prospecting.KIND, 'name': legal_prospecting.ROUTINE_NAME,
                'objective': legal_prospecting.ROUTINE_OBJECTIVE, 'interval_minutes': legal_prospecting.INTERVAL_MINUTES,
                'enabled': True, 'next_run': schedule or timezone.now(),
            }
            changes = [field for field, value in desired.items() if getattr(routine, field) != value]
            for field in changes:
                setattr(routine, field, desired[field])
            if changes:
                routine.save(update_fields=changes)

        self.stdout.write(self.style.SUCCESS(
            f'Rotina jurídica {routine.pk} habilitada na empresa {workspace.pk}. '
            f'Próxima execução elegível em {routine.next_run.isoformat()}, sujeita à fila e à cota do plano.'
        ))
