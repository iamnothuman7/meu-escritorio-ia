from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from office.models import Workspace


class Command(BaseCommand):
    help = 'Habilita ou desabilita a vista Claw3D para um escritório específico.'

    def add_arguments(self, parser):
        parser.add_argument('--workspace', type=int, required=True)
        parser.add_argument('--owner', type=int, required=True)
        parser.add_argument('--disable', action='store_true')

    @transaction.atomic
    def handle(self, **options):
        workspace = Workspace.objects.select_for_update().filter(
            pk=options['workspace'], owner_id=options['owner'], owner__is_superuser=False,
        ).first()
        if workspace is None:
            raise CommandError('Escritório não encontrado para esse proprietário.')
        policy = workspace.policy
        if not isinstance(policy, dict):
            raise CommandError('A configuração do escritório não está no formato esperado.')
        if options['disable']:
            policy.pop('claw3d_studio', None)
        else:
            policy['claw3d_studio'] = True
        workspace.save(update_fields=['policy'])
        self.stdout.write(self.style.SUCCESS(f'Vista Claw3D {"desabilitada" if options["disable"] else "habilitada"} para {workspace.name}.'))
