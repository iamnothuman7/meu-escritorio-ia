import sqlite3
import zipfile

from django.core.management.base import BaseCommand, CommandError

from office.backup import create_backup, restore_backup


class Command(BaseCommand):
    help = "Cria backup consistente ou restaura para uma pasta vazia de laboratório."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Caminho do arquivo ZIP de saída")
        parser.add_argument("--restore", metavar="ARCHIVE", help="Arquivo ZIP a restaurar")
        parser.add_argument("--target", help="Pasta vazia para a restauração de laboratório")

    def handle(self, *args, **options):
        try:
            if options["restore"]:
                if not options["target"] or options["output"]:
                    raise ValueError("Use --restore ARQUIVO --target PASTA_VAZIA.")
                result = restore_backup(options["restore"], options["target"])
                self.stdout.write(self.style.SUCCESS(f"Restauração verificada e pausada: {result}"))
            else:
                if options["target"]:
                    raise ValueError("--target somente pode ser usado com --restore.")
                result = create_backup(options["output"])
                self.stdout.write(self.style.SUCCESS(f"Backup verificado: {result}"))
        except (ValueError, OSError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            raise CommandError(str(exc)) from exc
