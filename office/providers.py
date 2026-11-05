"""Explicit, server-only provider adapters. No retries, tools or automatic fallback."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings

from . import credentials


class ProviderError(Exception):
    def __init__(self, message, uncertain=False, code=""):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


_http = build_opener(_RejectRedirects())


def _api_key(mode, owner_id):
    try:
        key = credentials.load_api_key(owner_id, mode)
    except credentials.CredentialError as exc:
        raise ProviderError(str(exc)) from None
    if key:
        return key
    if not settings.HTTPS_ENABLED and os.environ.get("NABI_LEGACY_PROVIDER_OWNER_ID") == str(owner_id):
        variable = {"openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}[mode]
        return os.environ.get(variable, "").strip()
    return ""


def has_api_key(mode, *, owner_id):
    try:
        return bool(_api_key(mode, owner_id))
    except ProviderError:
        return False


def _post(url, payload, headers):
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with _http.open(request, timeout=90) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ProviderError("Resposta excedeu o limite local; consumo a confirmar.", uncertain=True)
            return json.loads(raw)
    except HTTPError as exc:
        messages = {
            400: "O provedor recusou os parâmetros ou o modelo. Revise a configuração.",
            401: "Credencial inválida ou expirada. Confira a conexão no servidor.",
            403: "Esta conta não autorizou o recurso solicitado.",
            404: "Modelo indisponível. Escolha e valide outro modelo manualmente.",
            429: "Limite de uso, crédito ou taxa atingido. Execução bloqueada; nenhum fallback.",
        }
        raise ProviderError(messages.get(exc.code, f"Provedor indisponível (HTTP {exc.code})."), uncertain=exc.code >= 500) from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ProviderError("Não foi possível confirmar a resposta do provedor. Consumo a confirmar; não haverá repetição automática.", uncertain=True) from None


def generate(mode, model, instructions, prompt, max_output_tokens=1024, *, owner_id):
    if not model:
        raise ProviderError("Nenhum modelo foi escolhido e validado.")
    if mode == "openai":
        key = _api_key(mode, owner_id)
        if not key:
            raise ProviderError("Cadastre a chave OpenAI da sua conta nas configurações.")
        response = _post("https://api.openai.com/v1/responses", {
            "model": model, "instructions": instructions, "input": prompt,
            "max_output_tokens": max_output_tokens, "store": False,
        }, {"Authorization": f"Bearer {key}"})
        if response.get("status") != "completed":
            raise ProviderError("O provedor retornou uma resposta parcial ou recusada. A entrega não foi concluída.", uncertain=True)
        body = "\n".join(part.get("text", "") for item in response.get("output", [])
                         if item.get("type") == "message" for part in item.get("content", [])
                         if part.get("type") == "output_text")
        usage = response.get("usage") or {}
        result = {"body": body, "usage": {
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "request_id": response.get("id", ""),
        }}
    elif mode == "claude":
        key = _api_key(mode, owner_id)
        if not key:
            raise ProviderError("Cadastre a chave Anthropic da sua conta nas configurações.")
        response = _post("https://api.anthropic.com/v1/messages", {
            "model": model, "system": instructions, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
        }, {"Authorization": f"Bearer {key}", "anthropic-version": "2026-10-21"})
        if response.get("stop_reason") != "end_turn":
            raise ProviderError("Claude interrompeu a resposta antes da conclusão. Consumo a confirmar.", uncertain=True)
        body = "\n".join(item.get("text", "") for item in response.get("content", []) if item.get("type") == "text")
        usage = response.get("usage") or {}
        result = {"body": body, "usage": {
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "request_id": response.get("id", ""),
        }}
    elif mode == "chatgpt":
        from . import chatgpt_plan

        try:
            result = chatgpt_plan.generate(model, instructions, prompt, owner_id=owner_id)
        except chatgpt_plan.PlanError as exc:
            raise ProviderError(str(exc), uncertain=exc.uncertain, code=exc.code) from None
    else:
        raise ProviderError("Sem IA conectada. Use uma rotina local ou configure um provedor explicitamente.")
    if not result["body"].strip():
        raise ProviderError("O provedor não retornou uma entrega textual válida.", uncertain=True)
    return result
