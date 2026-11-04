# Rastreabilidade da primeira implementação

A referência de produto é o PDF fornecido pelo usuário, versão de 4 de novembro de 2026. O nome solicitado no pedido mais recente é **Nabi Office**. A arquitetura local foi preservada e ganhou implantação dedicada no VPS a pedido posterior do proprietário. A identidade usa o manual NABIO Office AI fornecido: navy, roxo e ciano, Montserrat e Inter locais, robôs pixel e variações vetoriais da marca.

| Área | Implementação e limite |
| --- | --- |
| Fundação | Django 5.2, SQLite fora do OneDrive, sessão, CSRF, login local e loopback. |
| Multiempresa | Espaço com proprietário e contexto próprio; consultas e relações limitadas ao espaço autorizado. |
| Equipe | Personagens 2D leves, estados derivados de tarefas, instruções versionadas e perfis editáveis. |
| Execução | Fila persistente, executor único, entrega separada da tarefa, eventos e pausa. |
| Aprovações | Revisão da versão e hash exatos da entrega local. Aprovação editorial não publica conteúdo. |
| Rotinas | Configuração persistida, início desativado, ocorrência única e consolidação de atraso. |
| Marketing | Produção de textos e briefings e revisão. Arte final e publicação automática não são presumidas. |
| Comercial | Cadastro de leads, fontes informadas e estágios; sem scraping ou disparo de mensagens. |
| Financeiro | Obrigações, recebimentos/pagamentos parciais e reversões em centavos; sem acesso bancário. |
| Projetos | Cadastros e acompanhamento interno; agentes de desenvolvimento não executam comandos externos. |
| Recuperação | Backup online SQLite, arquivos, manifesto, hashes e restauração em laboratório pausado. |
| IA | Adaptadores OpenAI e Claude; configuração e teste real com a conta ainda necessários para homologação. |

## Pendências explícitas

O fluxo OAuth oficial de uso do plano ChatGPT/Pro, conectores Google/Instagram/Telegram/Notion/GitHub, importação CSV com prévia e deduplicação completa, anexos de evidências com autorização e publicação externa com outbox não estão homologados nesta fundação. Não há promessa de saldo consultado em tempo real ou acesso automático às conversas do ChatGPT. O modo local depende do computador ligado; o modo hospedado executa no VPS enquanto ele e o serviço estiverem ativos.

A cobertura de testes acompanha o código presente; a existência do checklist de aceitação no PDF não equivale a todos os seus cenários passarem nesta primeira versão. O relatório de entrega deve informar a execução efetiva e as limitações observadas.
