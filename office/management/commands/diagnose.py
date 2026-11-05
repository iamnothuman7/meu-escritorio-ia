import json
import platform
import shutil
import sqlite3

import django
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = "Exibe diagnóstico local sanitizado, sem credenciais nem conteúdo das empresas."

    def handle(self, *args, **options):
        report = {
            "application": "Nabio Office",
            "python": platform.python_version(),
            "django": django.get_version(),
            "sqlite": sqlite3.sqlite_version,
            "checked_at": timezone.now().isoformat(),
            "data_directory": str(settings.DATA_DIR),
            "free_disk_bytes": shutil.disk_usage(settings.DATA_DIR).free,
            "database": "unavailable",
            "worker": "unknown",
            "providers": {"status": "not_checked", "live_request_sent": False},
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA integrity_check")
                report["database"] = "ok" if cursor.fetchone()[0] == "ok" else "integrity_error"
            from office.models import SystemState, Workspace
            state = SystemState.objects.filter(pk=1).first()
            heartbeat = state.worker_heartbeat if state else None
            report["office_paused"] = bool(state and state.paused)
            report["worker"] = {
                "heartbeat": heartbeat.isoformat() if heartbeat else None,
                "recent": bool(heartbeat and (timezone.now() - heartbeat).total_seconds() < 60),
            }
            report["workspace_count"] = Workspace.objects.count()
            report["providers"]["configured_modes"] = sorted(set(Workspace.objects.values_list("ai_mode", flat=True)))
        except Exception:
            # Do not dump exception strings, SQL, connection settings, or user content.
            report["database"] = "unavailable_or_unmigrated"
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
