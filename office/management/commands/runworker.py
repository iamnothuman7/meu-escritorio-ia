import os
import signal
import socket
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from office.instagram import process_one_publication, recover_interrupted_publications
from office.models import Event, Run, SystemState, Task, WorkerLease
from office.services import process_one, schedule_due


@contextmanager
def instance_lock():
    directory = Path(settings.DATA_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stream = (directory / "worker.lock").open("a+b")
    acquired = False
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise CommandError("Outro executor já está ativo neste diretório de dados.") from None
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class Command(BaseCommand):
    help = "Executa a fila local e rotinas com um único executor protegido por lock do sistema."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Verifica rotinas e processa no máximo uma tarefa.")
        parser.add_argument("--interval", type=float, default=2.0)

    def handle(self, *args, **options):
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True

        if not options["once"]:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, stop)
        with instance_lock():
            owner = f"{socket.gethostname()}:{os.getpid()}"
            WorkerLease.objects.update_or_create(pk=1, defaults={"owner": owner, "heartbeat": timezone.now()})
            SystemState.objects.get_or_create(pk=1)
            recover_interrupted_publications()
            # A prior process exited while running. Do not blindly repeat a potentially consumed request.
            for task in Task.objects.filter(status="running"):
                Task.objects.filter(pk=task.pk, status="running").update(status="blocked", updated_at=timezone.now())
                Run.objects.filter(task=task, status="running").update(status="blocked", finished_at=timezone.now(),
                    error="Executor anterior interrompido. Revise o resultado e o consumo antes de solicitar nova execução.")
                Event.objects.create(workspace=task.workspace, task=task, kind="run.interrupted",
                    message="Execução anterior interrompida; revisão necessária antes de repetir.")
            self.stdout.write(self.style.SUCCESS("Executor Nabio Office ativo. Ctrl+C encerra após a etapa corrente."))
            try:
                while not stopping:
                    close_old_connections()
                    now = timezone.now()
                    WorkerLease.objects.filter(pk=1, owner=owner).update(heartbeat=now)
                    SystemState.objects.filter(pk=1).update(worker_heartbeat=now)
                    schedule_due(now)
                    worked = process_one_publication() or process_one()
                    if options["once"]:
                        break
                    if not worked:
                        time.sleep(max(0.2, min(options["interval"], 10)))
            finally:
                WorkerLease.objects.filter(pk=1, owner=owner).delete()
                SystemState.objects.filter(pk=1).update(worker_heartbeat=None)
                close_old_connections()
                self.stdout.write("Executor encerrado.")
