"""Publish a single approved JPEG through the Instagram Login Graph API."""
from datetime import timedelta
import ipaddress
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_safe
import requests

from . import credentials, image_art, services
from .chatgpt_plan import PlanError, _protect_directory, _session_lock
from .models import Delivery, DeliveryImage, Event, InstagramPublication, SystemState


GRAPH = "https://graph.instagram.com/v25.0/"
MEDIA_SALT = "office.instagram.publication-media.v1"
MEDIA_TTL = timedelta(minutes=30)
CHECK_INTERVAL = timedelta(minutes=1)
MAX_WAIT = timedelta(minutes=10)
MAX_QUEUE_AGE = timedelta(days=1)
PENDING = ("queued", "uploading", "waiting", "checking")
MEDIA_STATES = ("uploading", "waiting", "checking", "publishing")
CAPTION_LABELS = {"legenda", "legenda do instagram", "legenda para instagram"}
OTHER_LABELS = {
    "titulo", "titulo da arte", "subtitulo", "subtitulo da arte", "cta", "cta da arte",
    "chamada para acao", "briefing", "briefing visual", "conceito", "texto da arte",
    "hashtags", "descricao da imagem", "texto alternativo", "fontes", "observacoes",
}


class InstagramError(ValueError):
    pass


def _public_origin():
    value = os.environ.get("NABI_PUBLIC_ORIGIN", "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".")
        if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.path
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            return ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".internal")):
                return ""
        else:
            if not address.is_global:
                return ""
    except ValueError:
        return ""
    return value


def _storage(workspace):
    if not workspace.pk or not workspace.owner_id:
        raise InstagramError("Selecione uma empresa válida antes de conectar o Instagram.")
    directory = Path(settings.DATA_DIR).resolve() / "instagram-credentials"
    if (directory.is_symlink() or directory.is_relative_to(Path(settings.BASE_DIR).resolve())
            or directory.is_relative_to(Path(settings.MEDIA_ROOT).resolve())):
        raise InstagramError("A pasta protegida do Instagram precisa ficar fora do código e da mídia.")
    return directory, directory / f"{workspace.pk}.enc"


def _load(workspace):
    directory, path = _storage(workspace)
    try:
        encrypted = credentials._read_private(path, credentials.MAX_RECORD_SIZE)
    except FileNotFoundError:
        return None
    except (OSError, credentials.CredentialError):
        raise InstagramError("Não foi possível ler a conexão protegida do Instagram.") from None
    try:
        profile = json.loads(credentials._cipher(directory).decrypt(encrypted))
        if (not isinstance(profile, dict) or profile.get("workspace_id") != workspace.pk
                or profile.get("owner_id") != workspace.owner_id):
            raise ValueError
        if not _identifier(profile.get("account_id")) or not isinstance(profile.get("username"), str):
            raise ValueError
        profile["token"] = credentials._normalize_key(profile.get("token"))
        return profile
    except (OSError, ValueError, InvalidToken):
        raise InstagramError("Não foi possível ler a conexão protegida do Instagram.") from None


def _identifier(value):
    candidate = str(value)
    return candidate if re.fullmatch(r"[0-9]{1,32}", candidate) else ""


def _graph(method, path, token, *, fields=None, data=None):
    try:
        with requests.request(method, GRAPH + path, params={"fields": fields} if fields else None,
                              data=data, headers={"Authorization": f"Bearer {token}"},
                              timeout=(8, 25), allow_redirects=False) as response:
            payload = response.json()
            status = response.status_code
    except (requests.RequestException, ValueError):
        raise InstagramError("A resposta do Instagram não pôde ser confirmada.") from None
    if not isinstance(payload, dict):
        raise InstagramError("O Instagram retornou uma resposta inválida.")
    if not 200 <= status < 300 or "error" in payload:
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if code == 190 or status == 401:
            raise InstagramError("A autorização do Instagram expirou ou é inválida. Reconecte a conta.")
        if code in (10, 200) or status == 403:
            raise InstagramError("O Instagram recusou o acesso. Confira os escopos de publicação e a aprovação do app na Meta.")
        if status == 429:
            raise InstagramError("O Instagram atingiu um limite de publicação ou de chamadas.")
        raise InstagramError("O Instagram recusou a solicitação. Confira a conexão e o conteúdo.")
    return payload


def connection_status(workspace):
    available = bool(_public_origin())
    try:
        profile = _load(workspace)
    except InstagramError:
        profile = None
    return {"available": available, "connected": bool(profile),
            "account_id": profile["account_id"] if profile else "",
            "username": profile["username"] if profile else ""}


def connect(workspace, access_token, account_id=""):
    if not _public_origin():
        raise InstagramError("Configure a origem HTTPS pública do escritório antes de conectar o Instagram.")
    token = credentials._normalize_key(access_token)
    result = _graph("GET", "me", token, fields="user_id,username")
    identified = _identifier(result.get("user_id") or result.get("id"))
    username = result.get("username")
    if not identified or not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.]{1,100}", username):
        raise InstagramError("A Meta não confirmou o perfil profissional desta autorização.")
    expected = account_id.strip() if isinstance(account_id, str) else ""
    if expected and identified != expected:
        raise InstagramError("A autorização pertence a outro perfil do Instagram.")
    limit = _graph("GET", f"{identified}/content_publishing_limit", token, fields="config,quota_usage")
    if not isinstance(limit.get("data"), list) or not limit["data"]:
        raise InstagramError("A Meta não confirmou a permissão de publicação deste perfil.")
    directory, path = _storage(workspace)
    profile = {"workspace_id": workspace.pk, "owner_id": workspace.owner_id,
               "account_id": identified, "username": username, "token": token}
    try:
        _protect_directory(directory)
        with _session_lock(directory):
            encrypted = credentials._cipher(directory, create=True).encrypt(json.dumps(profile).encode())
            credentials._write_private(path, encrypted)
    except (OSError, PlanError, ValueError):
        raise InstagramError("Não foi possível guardar a autorização do Instagram com segurança.") from None
    _cancel_pending(workspace, "A conta do Instagram mudou antes da publicação.", keep_account=identified)
    return connection_status(workspace)


def _cancel_pending(workspace, reason, *, keep_account=None):
    items = InstagramPublication.objects.filter(workspace=workspace, status__in=PENDING)
    if keep_account:
        items = items.exclude(account_id=keep_account)
    now = timezone.now()
    items.update(status="cancelled", error=reason, finished_at=now, updated_at=now)


def disconnect(workspace):
    directory, path = _storage(workspace)
    try:
        if directory.is_dir():
            with _session_lock(directory):
                path.unlink(missing_ok=True)
    except OSError:
        raise InstagramError("Não foi possível desconectar o Instagram desta empresa.") from None
    _cancel_pending(workspace, "A conta do Instagram foi desconectada antes da publicação.")


def _caption_label(line):
    prefix, _, text = line.strip().lstrip("# ").partition(":")
    label = unicodedata.normalize("NFKD", prefix.strip(" *_")).encode("ascii", "ignore").decode().casefold()
    if prefix.startswith("**") and not prefix.endswith("**"):
        text = text.removeprefix("**")
    return label, text.strip()


def caption_for(delivery):
    if delivery.kind != "content" or not delivery.body:
        return ""
    lines = []
    started = False
    for line in delivery.body.splitlines():
        label, text = _caption_label(line)
        if started and label in (OTHER_LABELS | CAPTION_LABELS):
            break
        if started:
            lines.append(line)
        elif label in CAPTION_LABELS:
            started = True
            if text:
                lines.append(text)
    caption = "\n".join(lines).strip()
    if not started and delivery.task.objective == "Rascunho editorial cadastrado pelo usuário.":
        caption = delivery.body.strip()
    return caption if len(caption) <= 2200 else ""


def _ready_image(delivery):
    image = DeliveryImage.objects.filter(delivery=delivery, status="ready").first()
    if not image or image.data is None:
        raise InstagramError("Prepare a imagem JPEG antes de aprovar a publicação.")
    data = bytes(image.data)
    if not constant_time_compare(image.sha256, image_art._jpeg_digest(data)):
        raise InstagramError("A imagem mudou. Revise a entrega antes de publicar.")
    return image.sha256, data


@transaction.atomic
def approve_and_queue(delivery, expected_hash, expected_account_id):
    # A conditional write serializes double clicks on SQLite before reading state.
    locked = Delivery.objects.filter(pk=delivery.pk, workspace_id=delivery.workspace_id).update(version=F("version"))
    if not locked:
        raise InstagramError("A entrega não está mais disponível nesta empresa.")
    delivery = Delivery.objects.select_related("task", "workspace").get(pk=delivery.pk)
    if services.is_paused(delivery.workspace):
        raise InstagramError("Retome o escritório antes de publicar no Instagram.")
    if not _public_origin():
        raise InstagramError("A origem HTTPS pública do escritório ainda não está configurada.")
    profile = _load(delivery.workspace)
    if not profile or profile["account_id"] != str(expected_account_id):
        raise InstagramError("A conta de destino mudou ou está desconectada. Confira novamente o perfil.")
    if InstagramPublication.objects.filter(delivery=delivery).exists():
        raise InstagramError("Esta entrega já tem uma solicitação de publicação.")
    caption = caption_for(delivery)
    if not caption:
        raise InstagramError("A entrega precisa de uma LEGENDA explícita de até 2.200 caracteres.")
    digest, _ = _ready_image(delivery)
    approval = services.approve_delivery(delivery, "approved", expected_hash)
    publication = InstagramPublication.objects.create(
        workspace=delivery.workspace, delivery=delivery, approval=approval, approved_hash=approval.payload_hash,
        image_sha256=digest, account_id=profile["account_id"], account_username=profile["username"], caption=caption,
    )
    Event.objects.create(workspace=delivery.workspace, task=delivery.task, kind="instagram.queued",
                         message="Publicação no Instagram autorizada e colocada na fila.")
    return publication


def _verified_image(job):
    delivery, approval = job.delivery, job.approval
    if (delivery.workspace_id != job.workspace_id or approval.workspace_id != job.workspace_id
            or approval.delivery_id != delivery.pk or approval.status != "approved" or not approval.decided_at
            or approval.payload_hash != job.approved_hash or caption_for(delivery) != job.caption
            or not constant_time_compare(services.delivery_hash(delivery, verify_image=True), job.approved_hash)):
        raise InstagramError("A versão da entrega ou sua aprovação mudou antes da publicação.")
    digest, data = _ready_image(delivery)
    if not constant_time_compare(job.image_sha256, digest):
        raise InstagramError("A imagem não corresponde à aprovação da publicação.")
    return data


def _signed_fields(job):
    return [job.pk, job.workspace_id, job.approved_hash, job.image_sha256]


def _media_url(job):
    signature = signing.dumps(_signed_fields(job), salt=MEDIA_SALT)
    return f"{_public_origin()}/instagram/media/{job.pk}/{signature}/"


@require_safe
def media(request, pk, signature):
    try:
        if len(signature) > 512:
            raise Http404
        signed = signing.loads(signature, salt=MEDIA_SALT, max_age=MEDIA_TTL)
        if not isinstance(signed, list) or len(signed) != 4 or signed[0] != pk:
            raise Http404
        job = InstagramPublication.objects.select_related("approval", "delivery__task").get(pk=pk, status__in=MEDIA_STATES)
        if signed != _signed_fields(job):
            raise Http404
        data = _verified_image(job)
    except (signing.BadSignature, InstagramPublication.DoesNotExist, InstagramError, ValidationError):
        raise Http404 from None
    response = HttpResponse(data if request.method == "GET" else b"", content_type="image/jpeg")
    response["Content-Length"] = str(len(data))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _profile_for(job):
    profile = _load(job.workspace)
    if not profile or profile["account_id"] != job.account_id:
        raise InstagramError("O perfil aprovado foi alterado ou desconectado.")
    _verified_image(job)
    return profile


def _transition(job, expected, status, **fields):
    return InstagramPublication.objects.filter(pk=job.pk, status=expected).update(
        status=status, updated_at=timezone.now(), **fields,
    )


@transaction.atomic
def _finish(job, expected, status, *, error="", media_id=""):
    if not _transition(job, expected, status, error=error[:300], media_id=media_id, finished_at=timezone.now()):
        return
    message = {"published": "Publicação confirmada pelo Instagram.",
               "failed": "A publicação no Instagram não foi concluída.",
               "uncertain": "Resultado não confirmado. Confira o perfil do Instagram antes de reenviar."}[status]
    Event.objects.create(workspace_id=job.workspace_id, task_id=job.delivery.task_id,
                         kind=f"instagram.{status}", message=message)


@transaction.atomic
def _begin_publish(job):
    if not _transition(job, "checking", "publishing"):
        return None
    job.refresh_from_db()
    if services.is_paused(job.workspace):
        _transition(job, "publishing", "waiting", next_check_at=timezone.now() + CHECK_INTERVAL)
        return None
    return _profile_for(job)["token"]


def process_one_publication():
    if not _public_origin() or SystemState.objects.filter(pk=1, paused=True).exists():
        return False
    now = timezone.now()
    job = (InstagramPublication.objects.filter(status__in=("queued", "waiting"), next_check_at__lte=now,
                                                workspace__paused=False)
           .select_related("workspace", "approval", "delivery__task").order_by("next_check_at", "id").first())
    if not job:
        return False
    stage = "uploading" if job.status == "queued" else "checking"
    started = {"started_at": now} if stage == "uploading" else {}
    if not _transition(job, job.status, stage, **started):
        return False
    try:
        if services.is_paused(job.workspace):
            _transition(job, stage, job.status, next_check_at=now + CHECK_INTERVAL)
            return True
        if job.created_at <= now - MAX_QUEUE_AGE or (job.started_at and job.started_at <= now - MAX_WAIT):
            raise InstagramError("A solicitação ficou tempo demais na fila. Revise o conteúdo antes de tentar novamente.")
        profile = _profile_for(job)
        if stage == "uploading":
            response = _graph("POST", f"{job.account_id}/media", profile["token"],
                              data={"image_url": _media_url(job), "caption": job.caption})
            container = _identifier(response.get("id"))
            if not container:
                raise InstagramError("O Instagram não confirmou a preparação da imagem.")
            _transition(job, stage, "waiting", container_id=container, next_check_at=timezone.now() + CHECK_INTERVAL)
            return True

        container = _identifier(job.container_id)
        if not container:
            raise InstagramError("O identificador da mídia preparada está inválido.")
        response = _graph("GET", container, profile["token"], fields="status_code")
        status = response.get("status_code")
        if status == "IN_PROGRESS":
            _transition(job, stage, "waiting", next_check_at=timezone.now() + CHECK_INTERVAL)
        elif status == "PUBLISHED":
            _finish(job, stage, "published")
        elif status == "FINISHED":
            token = _begin_publish(job)
            if token:
                stage = "publishing"
                response = _graph("POST", f"{job.account_id}/media_publish", token, data={"creation_id": container})
                media_id = _identifier(response.get("id"))
                if not media_id:
                    raise InstagramError("O Instagram não confirmou o identificador da publicação.")
                _finish(job, stage, "published", media_id=media_id)
        else:
            raise InstagramError("O Instagram não conseguiu processar a imagem aprovada.")
    except (InstagramError, ValidationError) as exc:
        reason = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        _finish(job, stage, "uncertain" if stage == "publishing" else "failed", error=reason)
    except Exception:
        _finish(job, stage, "uncertain" if stage == "publishing" else "failed",
                error="A execução foi interrompida. Confira o perfil antes de reenviar.")
    return True


def recover_interrupted_publications():
    now = timezone.now()
    InstagramPublication.objects.filter(status="uploading").update(
        status="failed", error="A preparação da imagem foi interrompida.", finished_at=now, updated_at=now,
    )
    InstagramPublication.objects.filter(status="checking").update(status="waiting", next_check_at=now, updated_at=now)
    InstagramPublication.objects.filter(status="publishing").update(
        status="uncertain", error="O executor foi interrompido durante a publicação. Confira o perfil antes de reenviar.",
        finished_at=now, updated_at=now,
    )
