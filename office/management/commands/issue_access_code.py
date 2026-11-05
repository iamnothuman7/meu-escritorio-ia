import secrets

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from office.access import ACCESS_CODE_LIFETIME, access_code_digest
from office.models import OwnerAccessCode


class Command(BaseCommand):
    help = "Vincula o e-mail ao proprietário existente e emite um código privado de acesso válido por 24 horas."

    def add_arguments(self, parser):
        parser.add_argument("owner", type=int)
        parser.add_argument("--email", required=True)

    def handle(self, *args, **options):
        users = get_user_model().objects
        email = options["email"].strip().casefold()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("Informe um e-mail válido.") from None
        if len(email) > 150:
            raise CommandError("O e-mail excede o tamanho permitido para o login.")
        code = f"{secrets.randbelow(100_000_000):08d}"
        digest = access_code_digest(options["owner"], code)
        with transaction.atomic():
            owner = users.filter(pk=options["owner"], is_active=True, is_staff=True)
            if not owner.update(email=F("email")):
                raise CommandError("Proprietário ativo não encontrado.")
            if users.filter(Q(username__iexact=email) | Q(email__iexact=email)).exclude(pk=options["owner"]).exists():
                raise CommandError("Este e-mail já pertence a outro cadastro.")
            owner.update(username=email, email=email)
            OwnerAccessCode.objects.update_or_create(
                user_id=options["owner"],
                defaults={"digest": digest, "expires_at": timezone.now() + ACCESS_CODE_LIFETIME, "attempts": 0},
            )
        self.stdout.write(code)
