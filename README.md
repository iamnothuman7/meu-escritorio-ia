# Nabio Office

Escritório de agentes para organizar várias empresas com contextos separados. Construído com Django, SQLite e HTMX.

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

- Login com e-mail e senha, administração de contas e espaços por empresa.
- Equipe visual, tarefas persistentes, histórico de execuções, entregas e revisão por hash.
- Cadastros de leads, projetos e registros financeiros administrativos com valores em centavos.
- Pausa, fila local, rotinas configuráveis e executor único.
- Exportação e backup consistente com restauração verificada em laboratório.
- Adaptadores de texto OpenAI e Claude protegidos por política de consumo; a conexão depende de chave e configuração explícitas.

O modo sem IA mantém organização e cálculos. Modelos locais de texto são identificados como modelos, não como geração por IA. A interface não inventa atividade, saldo ou resultado de integração.

## Conexões e limites reais

Cada nova conta começa no modo local. Em **Configurações → Conexão de IA**, salve sua chave OpenAI API ou Claude API e defina o modelo e o limite de consumo da empresa. As chaves ficam protegidas por conta, fora do banco e dos backups.

Para a instalação própria do proprietário, `python scripts/connect-chatgpt.py login` abre o consentimento oficial no navegador. No VPS, execute `python scripts/connect-chatgpt.py import --owner ID` como o usuário do serviço e envie o arquivo de sessão protegido pela entrada padrão do SSH. Preserve apenas a sessão ativa no VPS. Em **Configurações → Conexão de IA**, selecione **Plano ChatGPT** e autorize o espaço. A execução usa o catálogo da conta. O limite local diário por proprietário separa 12 chamadas para rotinas e 12 para tarefas avulsas. A cota do ChatGPT se aplica a todas as chamadas. [Guia oficial de instalação em VM própria](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)

Em **Configurações → Identidade**, defina as cores das artes da Luma para cada empresa. **Conexão de IA → Arte da Luma** oferece montagem de JPEG com a marca e, com uma chave e autorização de cobrança, geração pela OpenAI API.

Em **Configurações → Instagram**, conecte o token oficial de um perfil profissional por empresa. **Aprovar e publicar** envia o JPEG e a legenda revisados à fila de publicação no feed. Use as instruções do painel para renovar o token da Meta. E-mail, Telegram, Google e Notion dependem de conectores adicionais. Agentes de desenvolvimento produzem planos e código como entrega.

Em **Trabalho → Rotinas**, ative a equipe para agendar a primeira rodada de oito entregas. A rotina de marketing roda a cada seis horas e mantém até quatro rascunhos na fila. As demais rodam diariamente e aguardam a revisão anterior. O executor funciona enquanto o computador ou servidor da instalação estiver ligado.

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

Na instalação sem usuários, a rota `/setup/` cria o primeiro acesso ao escritório. Em hospedagem, essa configuração inicial exige a autorização da instalação. Para habilitar a administração de contas, crie um usuário separado com `python manage.py createsuperuser` e use um e-mail no campo de usuário. Esse login abre `/administracao/`, onde o administrador cadastra contas com e-mail e senha. Cada conta recebe um escritório próprio com os agentes e as rotinas padrão.

## Interface dinâmica

A navegação do escritório, os formulários e os filtros usam HTMX servido pelo próprio aplicativo. Tarefas em execução atualizam a entrega a cada 5 segundos; Hoje/Equipe atualizam seus registros a cada 15 segundos. A consulta pausa com a aba oculta, diálogo aberto ou campo em edição, e preserva formulários alterados. Download, backup, saída e autenticação mantêm navegação nativa. O histórico do HTMX não guarda páginas privadas no localStorage. Detalhes e referências oficiais em [HTMX](docs/HTMX.md).

A apresentação e o login incluem animações da identidade visual, reduzidas quando o sistema solicita menos movimento. O canvas ambiental pausa fora da tela e em abas ocultas. Barras de rolagem ficam ocultas, preservando rolagem por mouse, toque e teclado.
