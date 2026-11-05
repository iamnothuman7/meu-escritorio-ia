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

Em **Trabalho → Comercial**, a campanha **Prospecção de escritórios de advocacia** consulta sites institucionais públicos no Ceará a cada seis horas. Ela alterna regiões, evita repetir candidatos e pede revisão das fontes. **Aprovar e importar ao CRM** cadastra apenas os novos registros da empresa ativa. A campanha não envia abordagens. Quando a cota de rotinas do ChatGPT acaba, a próxima pesquisa é adiada até a meia-noite de Fortaleza. Em **Trabalho → Automações**, o titular pode pausar ou retomar a campanha.

Para configurar a campanha em uma instalação autorizada, use `python manage.py configure_legal_prospecting --workspace ID --owner ID` com o Radar ativo e o Plano ChatGPT conectado. A opção `--disable` pausa apenas essa campanha.

## Verificar e manter

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py diagnose
.\.venv\Scripts\python.exe manage.py backup_office
```

Veja [operação e restauração](docs/OPERACAO.md) e [requisitos implementados e pendências](docs/REQUISITOS.md).

## Login, escritórios e aplicativo web

O login abre em `/` ou `/login/`. Depois de entrar com e-mail e senha, o usuário escolhe uma das suas empresas em `/escritorios/`. O painel de trabalho está em `/app/`. Contas administrativas continuam com acesso separado em `/administracao/`.

O manifesto e service worker preparam instalação PWA em navegadores compatíveis. Use a opção de instalar/adicionar à tela inicial do navegador. A interface funciona em tela independente, mas tarefas e alterações continuam exigindo conexão com o servidor. Somente arquivos públicos e a página offline genérica são armazenados no cache; páginas privadas e APIs não são cacheadas.

Na instalação sem usuários, a rota `/setup/` cria o primeiro acesso ao escritório. Em hospedagem, essa configuração inicial exige a autorização da instalação. Para habilitar a administração de contas, crie um usuário separado com `python manage.py createsuperuser` e use um e-mail no campo de usuário. Esse login abre `/administracao/`, onde o administrador cadastra contas com e-mail e senha. Cada conta recebe um escritório próprio com os agentes e as rotinas padrão.

## Escritório 3D

A vista `/office-3d/` adapta os avatares e as malhas do [Claw3D](https://github.com/iamlukethedev/Claw3D) à NaBio. A equipe mostra o estado das tarefas reais. O formulário envia para o executor existente da empresa, e as entregas ficam em **Aprovações**. Luma pode receber pedidos de posts com arte e legenda. Radar e Luma podem receber pesquisas em fontes públicas quando habilitadas. O formulário e a lista da equipe funcionam mesmo se o navegador não puder desenhar a cena 3D.

A vista é habilitada por empresa com `python manage.py set_office_studio --workspace ID --owner ID`. Acrescente `--disable` para removê-la do seletor. A configuração do provedor de IA é independente e os tokens ficam no servidor. Veja [código e instruções de compilação da cena](studio3d/README.md).

## Interface dinâmica

A navegação do escritório, os formulários e os filtros usam HTMX servido pelo próprio aplicativo. Tarefas em execução atualizam a entrega a cada 5 segundos; Hoje/Equipe atualizam seus registros a cada 15 segundos. A consulta pausa com a aba oculta, diálogo aberto ou campo em edição, e preserva formulários alterados. Download, backup, saída e autenticação mantêm navegação nativa. O histórico do HTMX não guarda páginas privadas no localStorage. Detalhes e referências oficiais em [HTMX](docs/HTMX.md).

O login e a cena respeitam a preferência por menos movimento. Barras de rolagem ficam ocultas, preservando rolagem por mouse, toque e teclado.

O login, o seletor, o painel e as telas de suporte usam os mesmos tokens do tema escuro do Claw3D, adaptados à NaBio. A navegação interna é compartilhada entre a planta 3D e as telas de trabalho. Bebas Neue, IBM Plex Sans e IBM Plex Mono são servidas localmente, com os avisos de licença em `office/static/office/fonts/`.
