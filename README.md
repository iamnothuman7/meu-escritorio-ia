# Nabi Office

Escritório local de agentes para organizar várias empresas com contextos separados. Construído com Django, SQLite, templates e personagens 2D leves. Não exige Docker, Redis, servidor contratado ou assinatura de plataforma.

Também preparado para o VPS do proprietário em **https://office.nabio.pro**, com HTTPS e serviços exclusivos. Consulte [implantação dedicada](docs/DEPLOY.md). A versão hospedada usa o disco do servidor e seu executor continua enquanto o VPS estiver ligado; a versão local depende do computador ligado.

## Começar no Windows

Abra **Iniciar Nabi Office.cmd**. Na primeira execução o instalador cria `.venv`, instala as dependências fixadas e prepara o banco. O escritório abre em [http://127.0.0.1:8787](http://127.0.0.1:8787). Crie seu usuário e senha na configuração inicial; não existe uma senha padrão.

Alternativa no PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Para encerrar os processos locais:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

Python 3.11 ou posterior compatível com Django 5.2 é necessário. A primeira instalação precisa de internet. Dados, arquivos, logs e backups ficam em `%LOCALAPPDATA%\NabiOffice`, fora da pasta do código e do OneDrive. Não coloque o banco numa pasta sincronizada.

## O que está implementado

- Login local, espaços por empresa, personalização de contexto e identidade.
- Equipe visual, tarefas persistentes, histórico de execuções, entregas e revisão por hash.
- Cadastros de leads, projetos e registros financeiros administrativos com valores em centavos.
- Pausa, fila local, rotinas configuráveis e executor único.
- Exportação e backup consistente com restauração verificada em laboratório.
- Adaptadores de texto OpenAI e Claude protegidos por política de consumo; a conexão depende de chave e configuração explícitas.

O modo sem IA mantém organização e cálculos. Modelos locais de texto são identificados como modelos, não como geração por IA. A interface não inventa atividade, saldo ou resultado de integração.

## Conexões e limites reais

As contas de IA não vêm conectadas. Os adaptadores só podem ser considerados homologados após uma execução autorizada com a conta do usuário. Nenhuma chamada paga é necessária para instalar ou executar os testes.

O fluxo oficial **Entrar com ChatGPT / uso do plano Pro** permanece pendente de implementação e homologação. Não se copiam cookies ou tokens de outro aplicativo. Não existe troca automática do plano para uma API cobrada.

Instagram, e-mail, Telegram, Google, Notion e publicação remota não estão conectados nesta versão. Conteúdo é preparado para revisão e publicação manual. Agentes de desenvolvimento produzem planos e código como entrega; não executam shell, alteram projetos ou implantam em produção.

Rotinas iniciam desativadas. O computador e o executor precisam permanecer ligados para executá-las. Dados públicos, preços e fontes não são descobertos automaticamente sem um conector de pesquisa homologado.

## Verificar e manter

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py diagnose
.\.venv\Scripts\python.exe manage.py backup_office
```

Veja [operação e restauração](docs/OPERACAO.md) e [requisitos implementados e pendências](docs/REQUISITOS.md).

## Entrada pública e aplicativo web

A apresentação pública está em `/`, o login em `/login/` e o escritório protegido em `/app/`. O WhatsApp de atendimento é +55 88 99274-5456, com mensagem pré-preenchida. Logo e ícones vetoriais têm fundo transparente, e Inter/Montserrat são servidas localmente.

O manifesto e service worker preparam instalação PWA em navegadores compatíveis. Use a opção de instalar/adicionar à tela inicial do navegador. A interface funciona em tela independente, mas tarefas e alterações continuam exigindo conexão com o servidor. Somente arquivos públicos e a página offline genérica são armazenados no cache; páginas privadas e APIs não são cacheadas.

Na instalação local sem usuário, abra `/setup/` uma vez para criar o administrador. Na hospedagem, a configuração do primeiro administrador é autorizada por link de ativação da instalação; a página pública e o login nunca redirecionam para esse setup.

## Interface dinâmica

A navegação do escritório, os formulários e os filtros usam HTMX servido pelo próprio aplicativo. Tarefas em execução atualizam a entrega a cada 5 segundos; Hoje/Equipe atualizam seus registros a cada 15 segundos. A consulta pausa com a aba oculta, diálogo aberto ou campo em edição, e preserva formulários alterados. Download, backup, saída e autenticação mantêm navegação nativa. O histórico do HTMX não guarda páginas privadas no localStorage. Detalhes e referências oficiais em [HTMX](docs/HTMX.md).

A apresentação e o login incluem animações da identidade visual, reduzidas quando o sistema solicita menos movimento. O canvas ambiental pausa fora da tela e em abas ocultas. Barras de rolagem ficam ocultas, preservando rolagem por mouse, toque e teclado.
