# Operação local

## Pastas e processos

O código pode permanecer no repositório. O banco `office.sqlite3`, os uploads `media/`, os logs e os backups ficam em `%LOCALAPPDATA%\NabiOffice`. `NABI_DATA_DIR` substitui esse diretório para testes ou uma instalação independente. Use somente disco local fora de sincronização automática.

`serve.py` atende exclusivamente o endereço de loopback `127.0.0.1:8787`. `manage.py runworker` processa fila e agendamentos em processo separado. Não exponha essa instalação diretamente à internet. O executor mantém bloqueio de instância e registra atividade real no banco.

O botão de pausa impede novos trabalhos. Uma requisição já aceita pelo provedor pode terminar e consumir franquia. Encerrar o processo pelo script é uma parada do programa; antes de manutenção, pause o escritório pela interface e espere os trabalhos ativos terminarem.

## Backup e restauração

Criar um backup consistente sem parar a interface:

```powershell
.\.venv\Scripts\python.exe manage.py backup_office
.\.venv\Scripts\python.exe manage.py backup_office --output C:\Backups\nabi-office.zip
```

O pacote contém um snapshot obtido pela API de backup do SQLite, arquivos de `media/` e manifesto de hashes SHA-256. Arquivos de ambiente, chaves do provedor e configuração do servidor não são incluídos. O pacote contém dados das empresas e hashes de autenticação local; guarde-o como documento confidencial.

Restaurar **sempre em uma pasta vazia de laboratório**:

```powershell
.\.venv\Scripts\python.exe manage.py backup_office --restore C:\Backups\nabi-office.zip --target C:\NabiLaboratorio
```

A restauração verifica caminhos do ZIP, hashes, integridade e referências do banco. Nenhum arquivo de uma instalação existente é sobrescrito. Os espaços restaurados iniciam pausados e sem IA, as rotinas desativadas, sessões encerradas e tarefas em fila/execução pausadas. As chaves não são recuperadas.

Para inspecionar a cópia, encerre a instalação atual, configure `NABI_DATA_DIR` para o laboratório e inicie novamente:

```powershell
$env:NABI_DATA_DIR = 'C:\NabiLaboratorio'
.\scripts\start.ps1
```

Confira empresas, contagens de registros, entregas e arquivos antes de adotar uma cópia restaurada. Mantenha a instalação e o backup anteriores. Para voltar ao diretório padrão, encerre o laboratório e remova `NABI_DATA_DIR` da sessão. Reconfigure IA e rotinas somente após revisão.

## Diagnóstico e atualização

`manage.py diagnose` informa versões, integridade do banco, espaço disponível e heartbeat. Não realiza chamada de IA nem inclui chaves ou conteúdo comercial. Logs em `logs/` ajudam a identificar falha de inicialização.

Antes de atualizar: pause, conclua trabalhos ativos, crie e valide um backup, encerre os processos, atualize o código e execute `scripts/install.ps1`. A instalação aplica migrações e prepara arquivos estáticos. Depois execute `manage.py check` e os testes. Não apague o último backup válido.

## Problemas frequentes

| Sintoma | Ação |
| --- | --- |
| Navegador não abre | Execute `start.ps1` pelo PowerShell e consulte `logs/server-error.log`. |
| Tarefa não anda | Confira pausa, agente habilitado, modo de IA, política de consumo e heartbeat. |
| Sem IA | Cadastros, cálculos e revisão continuam disponíveis; geração aguarda configuração. |
| Limite ou preço desconhecido | Confira política e capacidade no provedor; não aumente orçamento por suposição. |
| Porta 8787 ocupada | Encerre a instância conhecida com `stop.ps1`; não finalize processos desconhecidos. |
| Segundo executor não inicia | A instância ativa mantém o bloqueio; não inicie vários workers. |
| Backup inválido | Preserve o arquivo original e use outro pacote validado; não force restauração. |

As rotinas locais param quando o computador dorme ou desliga. Na retomada, o agendamento consolida o atraso em vez de gerar uma tarefa para cada horário perdido.
