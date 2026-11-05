"""Owner-authorized image generation for a versioned Luma delivery."""

import base64
import binascii
import hashlib
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from . import credentials, providers
from .models import Approval, Delivery, DeliveryImage, Event, InstagramPublication, SystemState

IMAGE_MODEL = "gpt-image-2"
MAX_IMAGE_BYTES = 1_600_000
IMAGE_ENDPOINT = "https://api.openai.com/v1/images/generations"
ACTIVE_STATES = ("queued", "running")
IMAGE_MODES = ("brand", "openai")
JPEG_SIZE_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_digest(data):
    if not isinstance(data, bytes) or not 10 <= len(data) <= MAX_IMAGE_BYTES:
        raise ValidationError("A imagem está vazia ou excede o limite de tamanho.")
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise ValidationError("A imagem recebida não é um JPEG válido.")
    offset = 2
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        if marker == 0xFF:
            offset += 1
            continue
        if marker in (0xDA, 0xD9):
            break
        size = int.from_bytes(data[offset + 2:offset + 4], "big")
        if size < 2 or offset + 2 + size > len(data):
            break
        if marker in JPEG_SIZE_MARKERS and size >= 8:
            height = int.from_bytes(data[offset + 5:offset + 7], "big")
            width = int.from_bytes(data[offset + 7:offset + 9], "big")
            if 0 < width <= 2048 and 0 < height <= 2048:
                return hashlib.sha256(data).hexdigest()
            break
        offset += 2 + size
    raise ValidationError("O JPEG não apresenta dimensões válidas dentro do limite local.")


def _key(owner_id):
    try:
        key = credentials.load_api_key(owner_id, "openai")
    except credentials.CredentialError:
        key = None
    if not key:
        raise ValidationError("Cadastre uma chave OpenAI API da sua conta antes de gerar a imagem.")
    return key


def _fingerprint(key):
    return salted_hmac("nabio.image-authorization", key, algorithm="sha256").hexdigest()


def _owner_images(owner_id):
    return DeliveryImage.objects.filter(delivery__workspace__owner_id=owner_id)


def _check_quota(owner_id, exclude=None):
    images = _owner_images(owner_id)
    if images.filter(attempted_at__date=timezone.localdate()).exists():
        raise ValidationError("O limite local de uma tentativa de imagem por conta foi atingido hoje.")
    if images.exclude(pk=exclude).filter(mode="openai", status__in=ACTIVE_STATES).exists():
        raise ValidationError("Esta conta já tem uma imagem em andamento.")


def _check_workspace(workspace):
    owner = workspace.owner
    if not owner.is_active or owner.is_superuser:
        raise ValidationError("A geração de imagem exige uma conta operacional ativa.")
    if workspace.paused or SystemState.objects.filter(pk=1, paused=True).exists():
        raise ValidationError("Retome o escritório antes de gerar a imagem.")


def _locked_delivery(delivery_id, version):
    # Acquire SQLite's write lock before inspecting related state.
    if Delivery.objects.filter(pk=delivery_id, version=version).update(version=F("version")) != 1:
        raise ValidationError("A entrega mudou. Revise a versão atual antes de continuar.")
    return Delivery.objects.select_related("workspace__owner", "task__agent").get(pk=delivery_id)


def _pending_caption(delivery):
    from .services import delivery_hash

    if delivery.kind != "content" or not delivery.task.agent or delivery.task.agent.slug != "luma":
        raise ValidationError("A imagem pode ser vinculada a uma entrega de conteúdo da Luma.")
    digest = delivery_hash(delivery, include_image=False)
    if delivery.task.status != "review" or not delivery.approvals.filter(
        status="pending", payload_hash=digest, expires_at__gt=timezone.now(),
    ).exists():
        raise ValidationError("A legenda precisa estar pendente de revisão nesta versão.")
    return digest


@transaction.atomic
def request_generation(delivery, *, expected_version, mode="brand", automatic=False):
    if mode not in IMAGE_MODES:
        raise ValidationError("Escolha um modo válido para a imagem.")
    delivery = _locked_delivery(delivery.pk, expected_version)
    workspace = delivery.workspace
    _check_workspace(workspace)
    if automatic and workspace.policy.get("image_automation") != mode:
        raise ValidationError("A geração automática de imagens está desativada para esta empresa.")
    existing = DeliveryImage.objects.filter(delivery=delivery).defer("data").first()
    if existing and existing.status in ACTIVE_STATES:
        if existing.mode != mode:
            raise ValidationError("Esta entrega já tem uma imagem em andamento em outro modo.")
        return existing
    if existing and existing.status == "ready":
        raise ValidationError("Esta entrega já tem uma imagem pronta.")
    caption_hash = _pending_caption(delivery)
    key_fingerprint = ""
    if mode == "openai":
        _check_quota(workspace.owner_id)
        key_fingerprint = _fingerprint(_key(workspace.owner_id))
    image, _ = DeliveryImage.objects.update_or_create(delivery=delivery, defaults={
        "status": "queued", "mode": mode, "requested_version": delivery.version, "requested_hash": caption_hash,
        "key_fingerprint": key_fingerprint, "automatic": automatic, "sha256": "", "data": None,
        "error": "", "requested_at": timezone.now(), "started_at": None, "finished_at": None,
    })
    Event.objects.create(workspace=workspace, task=delivery.task, kind="image.queued",
                         message="Imagem da Luma registrada para esta versão da legenda.")
    return image


def queue_automatic_image(delivery):
    mode = delivery.workspace.policy.get("image_automation")
    if delivery.kind != "content" or mode not in IMAGE_MODES:
        return
    if not delivery.task.agent or delivery.task.agent.slug != "luma":
        return
    try:
        request_generation(delivery, expected_version=delivery.version, mode=mode, automatic=True)
    except ValidationError:
        # Keep the caption available if this account lacks image authorization or quota.
        return


def approval_digest(delivery, *, verify=False):
    fields = ("status", "sha256", "data") if verify else ("status", "sha256")
    record = DeliveryImage.objects.filter(delivery=delivery).values_list(*fields).first()
    if not record:
        return ""
    status, digest = record[:2]
    if verify and status in ACTIVE_STATES:
        raise ValidationError("Aguarde a imagem da Luma antes de decidir sobre esta entrega.")
    if status != "ready":
        return ""
    if verify:
        data = bytes(record[2]) if record[2] is not None else b""
        if not constant_time_compare(digest, _jpeg_digest(data)):
            raise ValidationError("A imagem não corresponde à versão aguardando revisão.")
    return digest


@transaction.atomic
def attach_existing_image(delivery, data, *, expected_version, generation_id=None):
    """Attach trusted JPEG bytes and create a new approval covering the image and caption."""
    from .services import delivery_hash

    digest = _jpeg_digest(data)
    delivery = _locked_delivery(delivery.pk, expected_version)
    caption_hash = _pending_caption(delivery)
    image = DeliveryImage.objects.filter(delivery=delivery).defer("data").first()
    if generation_id is not None:
        if not image or image.pk != generation_id or image.status != "running" or image.requested_hash != caption_hash:
            raise ValidationError("A imagem não corresponde mais à versão da legenda solicitada.")
    elif image and image.status != "failed":
        raise ValidationError("A entrega já tem uma imagem pronta ou em processamento.")

    if not image:
        image = DeliveryImage(delivery=delivery, requested_version=expected_version, requested_hash=caption_hash)
    image.status = "ready"
    image.sha256 = digest
    image.data = data
    image.error = ""
    image.finished_at = timezone.now()
    image.save()
    Delivery.objects.filter(pk=delivery.pk).update(version=F("version") + 1, updated_at=timezone.now())
    delivery.refresh_from_db()
    delivery.approvals.filter(status="pending").update(status="expired")
    Approval.objects.create(workspace=delivery.workspace, delivery=delivery,
                            payload_hash=delivery_hash(delivery), expires_at=timezone.now() + timedelta(days=7))
    Event.objects.create(workspace=delivery.workspace, task=delivery.task, kind="image.ready",
                         message="Imagem e legenda prontas para revisão na mesma versão.")
    return image


@transaction.atomic
def refresh_brand_image(delivery, *, expected_version):
    """Recompose a pending brand image without changing its caption or calling a provider."""
    from . import brand_art
    from .services import delivery_hash

    delivery = _locked_delivery(delivery.pk, expected_version)
    delivery.clean()
    _check_workspace(delivery.workspace)
    if delivery.kind != "content" or not delivery.task.agent or delivery.task.agent.slug != "luma":
        raise ValidationError("A revisão de arte se aplica a entregas de conteúdo da Luma.")
    if (delivery.task.status != "review" or delivery.approvals.filter(status="approved").exists()
            or InstagramPublication.objects.filter(delivery=delivery).exists()):
        raise ValidationError("A arte só pode ser atualizada enquanto a entrega estiver pendente e sem publicação.")
    image = DeliveryImage.objects.filter(delivery=delivery, status="ready", mode="brand").defer("data").first()
    if not image:
        raise ValidationError("A entrega precisa ter uma arte da marca pronta para revisar.")
    current_hash = delivery_hash(delivery, verify_image=True)
    pending = delivery.approvals.filter(status="pending", payload_hash=current_hash,
                                       expires_at__gt=timezone.now()).order_by("-pk").first()
    if not pending:
        raise ValidationError("Não há uma aprovação pendente válida para a arte atual.")

    data = brand_art.render(delivery)
    image.sha256 = _jpeg_digest(data)
    image.data = data
    image.requested_version = delivery.version
    image.requested_hash = delivery_hash(delivery, include_image=False)
    image.error = ""
    image.finished_at = timezone.now()
    image.save(update_fields=["sha256", "data", "requested_version", "requested_hash", "error", "finished_at"])
    Delivery.objects.filter(pk=delivery.pk).update(version=F("version") + 1, updated_at=timezone.now())
    delivery.refresh_from_db()
    delivery.approvals.filter(status="pending").update(status="expired")
    Approval.objects.create(workspace=delivery.workspace, delivery=delivery,
                            payload_hash=delivery_hash(delivery, verify_image=True), expires_at=pending.expires_at)
    Event.objects.create(workspace=delivery.workspace, task=delivery.task, kind="image.refreshed",
                         message="Arte da marca atualizada. A nova versão aguarda revisão.")
    return image


def _prompt(delivery):
    from .brand_art import _hex_color

    workspace = delivery.workspace
    art = workspace.policy.get("brand_art") if isinstance(workspace.policy, dict) else None
    colors = []
    if isinstance(art, dict):
        for key, label in (("background", "Cor de fundo"), ("details", "Cor dos detalhes")):
            color = art.get(key)
            if _hex_color(color) is not None:
                colors.append(f"{label}: {color.upper()}\n")
    if not colors and _hex_color(workspace.brand_color) is not None:
        colors.append(f"Cor de referência: {workspace.brand_color.upper()}\n")
    palette = "".join(colors)
    return (
        "Crie uma imagem quadrada para acompanhar uma publicação de marketing. "
        "Use a legenda como referência visual. Evite inserir letras ou números. "
        "A composição deve funcionar também em uma tela pequena.\n"
        "Não invente nem reproduza logotipos. Reserve espaço para aplicar o arquivo oficial da marca.\n"
        f"Empresa: {workspace.name}\nSegmento: {workspace.segment}\n"
        f"{palette}Título: {delivery.title}\n"
        f"Trecho da legenda: {delivery.body[:1800]}"
    )


def _generate_jpeg(key, prompt):
    response = providers._post(IMAGE_ENDPOINT, {
        "model": IMAGE_MODEL, "prompt": prompt, "n": 1, "size": "1024x1024", "quality": "low",
        "output_format": "jpeg", "output_compression": 82, "moderation": "auto",
    }, {"Authorization": f"Bearer {key}"})
    try:
        encoded = response["data"][0]["b64_json"]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError
        data = base64.b64decode(encoded, validate=True)
    except (IndexError, KeyError, TypeError, ValueError, binascii.Error):
        raise providers.ProviderError("A OpenAI não retornou uma imagem válida. Revise o consumo antes de repetir.", uncertain=True) from None
    _jpeg_digest(data)
    return data


@transaction.atomic
def _reserve(image):
    delivery = _locked_delivery(image.delivery_id, image.requested_version)
    workspace = delivery.workspace
    _check_workspace(workspace)
    if image.automatic and workspace.policy.get("image_automation") != image.mode:
        raise ValidationError("A geração automática foi desativada antes da execução.")
    if not constant_time_compare(image.requested_hash, _pending_caption(delivery)):
        raise ValidationError("A legenda mudou antes da geração da imagem.")
    if image.mode == "brand":
        return delivery, None
    if image.mode != "openai":
        raise ValidationError("O modo de imagem registrado é inválido.")
    key = _key(workspace.owner_id)
    if not constant_time_compare(image.key_fingerprint, _fingerprint(key)):
        raise ValidationError("A chave OpenAI mudou. Autorize novamente com a chave atual.")
    _check_quota(workspace.owner_id, exclude=image.pk)
    if DeliveryImage.objects.filter(pk=image.pk, status="running").update(attempted_at=timezone.now()) != 1:
        raise ValidationError("A solicitação de imagem não está mais em execução.")
    return delivery, key


def process_one_image():
    if SystemState.objects.filter(pk=1, paused=True).exists():
        return False
    now = timezone.now()
    DeliveryImage.objects.filter(status="running", started_at__lt=now - timedelta(minutes=5)).update(
        status="failed", finished_at=now, error="Execução interrompida. Confira o consumo antes de solicitar nova imagem.",
    )
    image = DeliveryImage.objects.filter(status="queued", delivery__workspace__paused=False).defer("data").order_by("requested_at").first()
    if not image or DeliveryImage.objects.filter(pk=image.pk, status="queued").update(status="running", started_at=now) != 1:
        return False
    try:
        delivery, key = _reserve(image)
        if image.mode == "brand":
            from . import brand_art

            data = brand_art.render(delivery)
        else:
            data = _generate_jpeg(key, _prompt(delivery))
        attach_existing_image(delivery, data, expected_version=image.requested_version, generation_id=image.pk)
    except ValidationError as error:
        _fail(image, " ".join(error.messages))
    except providers.ProviderError as error:
        _fail(image, str(error))
    except Exception:
        _fail(image, "A imagem não pôde ser concluída. Confira o consumo antes de repetir.")
    return True


def _fail(image, reason):
    DeliveryImage.objects.filter(pk=image.pk, status="running").update(
        status="failed", finished_at=timezone.now(), error=reason[:300],
    )
