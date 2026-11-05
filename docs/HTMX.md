# HTMX no escritório

A navegação interna usa HTML renderizado pelo Django. O navegador troca o bloco `#app-shell`, incluindo menus, avisos e diálogos, enquanto mantém os recursos JavaScript carregados. As mesmas URLs continuam atendendo acessos diretos e formulários convencionais.

## Convenções de implementação

- HTMX 2.0.11 é servido pelo próprio projeto, com a licença incluída, sem dependência de CDN no uso normal.
- `hx-target="#app-shell"`, `hx-select="#app-shell"` e `hx-swap="outerHTML"` mantêm um único bloco principal após a navegação.
- Um POST bem-sucedido continua seguindo a regra da view. Para solicitações HTMX, o middleware converte o redirecionamento interno em resposta 200 com `HX-Location`, declarando `path`, `target`, `select` e `swap`.
- A perda de autenticação usa `HX-Redirect` para uma navegação completa ao login. O cabeçalho `HX-Request` não concede acesso nem substitui sessão, CSRF, aprovação ou isolamento entre empresas.
- Downloads, exportação, backup e saída da conta usam navegação nativa com `hx-boost="false"`.
- Eventos da interface usam delegação; a inicialização também ocorre após as trocas de conteúdo.

## Atualização de tarefas

Um elemento de polling dentro do bloco `#task-live` consulta apenas a tarefa aberta enquanto ela estiver na fila ou em execução. A resposta substitui esse mesmo bloco. Os atributos de polling ficam nesse elemento separado para não mudar a seleção dos formulários de tarefa. Ao chegar a revisão, conclusão, bloqueio, erro ou cancelamento, o HTML deixa de solicitar polling.

O polling deve aguardar quando a aba estiver oculta, houver um diálogo aberto ou o usuário estiver editando um campo. Ele consulta o estado persistido; não executa a tarefa nem chama o provedor de IA.

## Histórico e privacidade

O corpo privado usa `hx-history="false"` e a configuração `historyCacheSize: 0`. Isso impede que o histórico do HTMX grave o HTML do escritório no `localStorage`. Respostas privadas mantêm `Cache-Control: no-store` e variam por `HX-Request` quando necessário. A configuração desativa avaliação de expressões e scripts recebidos: `allowEval: false`, `allowScriptTags: false`, `selfRequestsOnly: true` e `historyRestoreAsHxRequest: false`.

O service worker continua guardando somente os recursos públicos permitidos e a página genérica sem conexão. Ele não guarda os fragmentos privados do HTMX nem sincroniza operações de escrita em segundo plano.

## Validação

```powershell
.venv/Scripts/python.exe manage.py test office.tests.test_htmx
```

Os testes verificam autenticação, CSRF, criação idempotente, isolamento por empresa, aprovação do hash exato, destinos internos dos redirecionamentos, configuração de histórico e polling. A validação no navegador também deve cobrir diálogo depois de navegar, troca de empresa, envio de formulário, Voltar/Avançar e edição de texto durante a execução de uma tarefa.

Referências: [documentação oficial](https://htmx.org/docs/), [HX-Location](https://htmx.org/headers/hx-location/) e [opções de configuração](https://htmx.org/reference/#config).
