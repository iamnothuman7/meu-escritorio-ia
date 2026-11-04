from decimal import Decimal
from django import template
from django.utils.safestring import mark_safe
register = template.Library()

@register.filter
def money(value):
    return ('R$ {:,.2f}'.format(Decimal(value or 0) / 100)).replace(',', 'X').replace('.', ',').replace('X', '.')

@register.simple_tag
def icon(name, size=20):
    paths = {
      'home':'M3 10 12 3l9 7v10H3Z M9 20v-7h6v7',
      'team':'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M17 4a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-4',
      'work':'M3 7h18v14H3Z M8 7V3h8v4 M3 12h18 M10 12v3h4v-3',
      'check':'m5 12 4 4L19 6',
      'approval':'M9 3H5v18h14V3h-4 M9 2h6v4H9Z m-1 12 3 3 5-6',
      'settings':'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
      'plus':'M12 5v14 M5 12h14',
      'arrow':'M4 12h15 m-6-6 6 6-6 6',
      'search':'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 m5 12 6 6',
      'pause':'M8 5v14 M16 5v14',
      'play':'m8 4 12 8-12 8Z',
      'clock':'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
      'bolt':'m13 2-9 12h7l-1 8 10-13h-7Z',
      'download':'M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5',
      'close':'m6 6 12 12 M6 18 18 6',
      'logout':'M9 4H3v16h6 M8 12h13 m-5-5 5 5-5 5',
      'leaf':'M20 3C6 2 1 9 6 16s16 2 14-13Z M6 19 17 7',
      'grid':'M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z',
      'shield':'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z m-5 9 4 4 5-6',
      'code':'m8 6-6 6 6 6 M16 6l6 6-6 6 M14 3l-4 18',
      'chat':'M3 3h18v14H8l-5 4Z M7 8h10 M7 12h6',
    }
    return mark_safe(f'<svg width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{paths.get(name,paths["grid"])}"/></svg>')

@register.filter
def label(value):
    return {'draft':'Rascunho','queued':'Na fila','running':'Trabalhando','review':'Em revisão','completed':'Concluída','blocked':'Bloqueada','paused':'Pausada','failed':'Falhou','cancelled':'Cancelada','pending':'Pendente','approved':'Aprovada','rejected':'Rejeitada','expired':'Expirada','income':'A receber','expense':'A pagar','new':'Novo','qualified':'Qualificado','contacted':'Contato registrado','proposal':'Proposta','won':'Ganho','closed':'Encerrado','active':'Em andamento','done':'Concluído','offline':'Sem IA','openai':'OpenAI API','claude':'Claude API','chatgpt':'Plano ChatGPT','normal':'Normal','high':'Alta','low':'Baixa','general':'Geração com IA','content':'Conteúdo com IA','summary':'Resumo local','finance':'Conferência local','template':'Modelo local'}.get(value,value)
