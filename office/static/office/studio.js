const byId = (id) => document.getElementById(id);
const root = byId('studio-main');
const form = byId('studio-form');
const selector = byId('studio-agent');
const kind = byId('studio-kind');
const submit = byId('studio-submit');
const result = byId('studio-result');
const roster = byId('studio-roster');
const teamMessage = byId('studio-team-message');
const provider = byId('studio-provider');
const feedback = byId('studio-scene-loading');
const reset = byId('studio-reset');
const workspaceId = Number(root.dataset.workspaceId);

const labels = {idle:'Disponível',queued:'Na fila',running:'Trabalhando',review:'Para revisar',blocked:'Precisa de atenção',disabled:'Desativado'};
const kinds = {general:'Tarefa geral',content:'Post com arte e legenda',research:'Pesquisa em fontes públicas'};
let state;
let scene;
let selection = '';
let sending = false;
let refreshing = false;
let lastRequest = null;
let rosterSignature = '';

function renderAvailability() {
  const validAgent = state?.agents.some((agent) => String(agent.id) === selection && agent.enabled);
  submit.disabled = sending || !validAgent || state?.workspace.paused || state?.provider.connected === false;
}

function selectAgent(id) {
  const agent = state?.agents.find((entry) => String(entry.id) === String(id) && entry.enabled);
  selection = agent ? String(id) : '';
  selector.value = selection;
  for (const card of roster.querySelectorAll('button[data-agent]')) {
    card.setAttribute('aria-pressed', String(card.dataset.agent === selection));
  }
  const availableKinds = (agent?.task_kinds || ['general']).filter((key) => Object.hasOwn(kinds, key));
  const nextKinds = availableKinds.join(',');
  if (kind.dataset.options !== nextKinds) {
    kind.replaceChildren(...availableKinds.map((key) => new Option(kinds[key], key)));
    kind.dataset.options = nextKinds;
  }
  const showKinds = availableKinds.length > 1;
  byId('studio-kind-field').hidden = !showKinds;
  kind.disabled = !showKinds;
  scene?.update(state?.agents || [], agent?.id ?? null);
  renderAvailability();
}

function renderRoster(agents) {
  const signature = JSON.stringify(agents.map(({id,name,role,color,enabled,status}) => [id,name,role,color,enabled,status]));
  if (rosterSignature === signature) return;
  rosterSignature = signature;
  const focused = document.activeElement?.dataset.agent;
  selector.replaceChildren(new Option('Selecione um agente', ''), ...agents.filter((agent) => agent.enabled).map((agent) => new Option(`${agent.name} · ${agent.role}`, agent.id)));
  roster.replaceChildren(...agents.map((agent) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'agent-card';
    button.dataset.agent = agent.id;
    button.disabled = !agent.enabled;
    button.setAttribute('aria-pressed', String(String(agent.id) === selection));
    const badge = document.createElement('span');
    badge.className = 'agent-card__initial';
    badge.setAttribute('aria-hidden', 'true');
    badge.textContent = agent.name.slice(0, 1);
    if (/^#[0-9a-f]{6}$/i.test(agent.color)) badge.style.setProperty('--agent-color', agent.color);
    const name = document.createElement('strong');
    name.textContent = agent.name;
    const role = document.createElement('small');
    role.textContent = agent.role;
    const status = document.createElement('span');
    status.className = 'agent-card__status';
    status.dataset.state = agent.status;
    status.textContent = labels[agent.status] || 'Disponível';
    button.append(badge, name, role, status);
    button.addEventListener('click', () => selectAgent(agent.id));
    return button;
  }));
  if (focused) Array.from(roster.children).find((card) => card.dataset.agent === focused)?.focus({preventScroll:true});
}

function renderState(next) {
  state = next;
  renderRoster(next.agents);
  selectAgent(selection);
  const connected = next.provider.connected === true;
  provider.dataset.connected = String(connected);
  byId('studio-provider-label').textContent = connected ? `${next.provider.label} conectado` : next.provider.label;
  const pending = byId('studio-pending');
  pending.hidden = !next.pending_approvals;
  pending.textContent = next.pending_approvals;
  const notice = next.workspace.paused ? 'Este escritório está pausado. Retome nas configurações para enviar tarefas.' : !connected ? 'Conecte a IA nas configurações para enviar tarefas.' : !next.worker_online ? 'O executor está indisponível no momento. As novas tarefas ficam na fila.' : !next.agents.length ? 'Ainda não há agentes neste escritório.' : '';
  teamMessage.textContent = notice;
  teamMessage.hidden = !notice;
}

async function refresh() {
  if (refreshing || document.hidden) return;
  refreshing = true;
  try {
    const response = await fetch(root.dataset.stateUrl, {headers:{Accept:'application/json'}, cache:'no-store', redirect:'error'});
    if (!response.ok) throw new Error();
    const next = await response.json();
    if (next.workspace.id !== workspaceId) throw new Error();
    renderState(next);
  } catch {
    state = null;
    renderAvailability();
    teamMessage.hidden = false;
    teamMessage.textContent = 'Não foi possível atualizar a equipe. Se você trocou de escritório, reabra a vista 3D.';
  } finally {
    refreshing = false;
  }
}

function showResult(message, error, link) {
  result.hidden = false;
  result.dataset.error = String(error);
  result.textContent = message;
  if (typeof link === 'string' && /^\/tasks\/\d+\/$/.test(link)) {
    const anchor = document.createElement('a');
    anchor.href = link;
    anchor.textContent = 'Acompanhar tarefa';
    result.append(' ', anchor);
  }
}

selector.addEventListener('change', () => selectAgent(selector.value));
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  const fields = new FormData(form);
  const payload = {workspace_id:workspaceId,agent_id:Number(selection),title:fields.get('title'),description:fields.get('description'),kind:fields.get('kind') || 'general'};
  const signature = JSON.stringify(payload);
  if (lastRequest?.signature !== signature) lastRequest = {signature,id:crypto.randomUUID()};
  payload.request_id = lastRequest.id;
  sending = true;
  renderAvailability();
  showResult('Enviando para a fila…', false);
  try {
    const response = await fetch(root.dataset.submitUrl, {method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json','X-CSRFToken':fields.get('csrfmiddlewaretoken')},body:JSON.stringify(payload),redirect:'error'});
    const data = (response.headers.get('Content-Type') || '').includes('application/json') ? await response.json() : {};
    if (!response.ok) {
      showResult(data.error || (response.status === 403 ? 'A sessão expirou. Reabra o escritório e entre novamente.' : 'Não foi possível enviar a tarefa.'), true);
      if (response.status === 409) refresh();
      return;
    }
    showResult('Tarefa enviada para a fila.', false, data.task.url);
    // Keep edits made while the request was in flight.
    if (byId('studio-title-input').value === payload.title) byId('studio-title-input').value = '';
    if (byId('studio-brief').value === payload.description) byId('studio-brief').value = '';
    lastRequest = null;
    refresh();
  } catch {
    showResult('Não recebemos a confirmação. Tente novamente para conferir o envio.', true);
  } finally {
    sending = false;
    renderAvailability();
  }
});

function sceneUnavailable() {
  feedback.hidden = false;
  feedback.textContent = 'A vista 3D não está disponível neste aparelho. Você pode usar a equipe e a delegação abaixo.';
  reset.disabled = true;
}

reset.addEventListener('click', () => scene?.reset());
import('./studio/scene.js').then(({mountScene}) => {
  let unavailable = false;
  scene = mountScene(byId('studio-scene'), {onSelect:selectAgent,onError:() => {
    unavailable = true;
    sceneUnavailable();
  }});
  if (unavailable || !scene) return;
  feedback.hidden = true;
  reset.disabled = false;
  selectAgent(selection);
}).catch(sceneUnavailable);

refresh();
setInterval(refresh, 12000);
document.addEventListener('visibilitychange', refresh);
