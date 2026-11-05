"""Authenticated image controls. Assets never enter the public static tree."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from . import image_art
from .models import Delivery, DeliveryImage


@login_required
@require_POST
def request_image(request, pk):
    if request.user.is_superuser:
        return redirect("platform_admin")
    from .views import active_workspace

    delivery = get_object_or_404(Delivery, pk=pk, workspace=active_workspace(request))
    try:
        mode = request.POST.get("image_mode", "brand")
        if mode == "openai" and request.POST.get("confirm_image") != "1":
            raise ValidationError("Confirme o uso da sua OpenAI API para gerar esta imagem.")
        try:
            version = int(request.POST.get("version", ""))
        except (ValueError, TypeError):
            raise ValidationError("Revise a versão da entrega antes de solicitar a imagem.") from None
        image_art.request_generation(delivery, expected_version=version, mode=mode)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Imagem colocada na fila da Luma.")
    return redirect("/approvals/")


@never_cache
@login_required
@require_safe
def delivery_image(request, pk):
    if request.user.is_superuser:
        return redirect("platform_admin")
    from .views import active_workspace

    image = get_object_or_404(DeliveryImage, delivery_id=pk, delivery__workspace=active_workspace(request), status="ready")
    data = bytes(image.data) if image.data is not None else b""
    try:
        digest = image_art._jpeg_digest(data)
    except ValidationError:
        raise Http404 from None
    if digest != image.sha256:
        raise Http404
    response = HttpResponse(data, content_type="image/jpeg")
    disposition = "attachment" if request.GET.get("download") == "1" else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="nabi-imagem-{pk}.jpg"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
