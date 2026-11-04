# Implantação dedicada: office.nabio.pro

O pedido posterior do proprietário autoriza hospedar no VPS existente. O modo local continua disponível. O serviço remoto mantém dados apenas no servidor, separados do repositório e dos demais projetos.

Configuração isolada:

- Código: `/opt/nabi-office/releases/<versão>`, link `current`.
- Ambiente Python exclusivo: `/opt/nabi-office/venv`.
- Usuário sem login: `nabi-office`.
- Dados: `/var/lib/nabi-office/data`, acesso do serviço.
- Ambiente/segredos: `/etc/nabi-office/environment`, root, modo 0600.
- Web e executor: `nabi-office.service` e `nabi-office-worker.service`.
- Upstream: `127.0.0.1:8787`, sem porta pública adicional.
- Virtual host exclusivo: `/etc/nginx/sites-available/office.nabio.pro`.
- Certificado dedicado: `/etc/letsencrypt/live/office.nabio.pro/`.

O nginx sobrescreve cabeçalhos de proxy e o Waitress só confia no proxy loopback. Cookies de sessão/CSRF são Secure e SameSite=Strict. O primeiro cadastro exige um link privado com token aleatório. O token perde utilidade após a criação do primeiro usuário e não concede acesso ao usuário criado. O endereço do cadastro redireciona para o login após essa criação.

Para uma atualização: testar, empacotar apenas fontes, criar release novo, instalar dependências na venv exclusiva se necessário, executar `check`, `migrate`, `collectstatic`, fazer backup, trocar `current` e reiniciar apenas os dois serviços do Nabi Office. Verificar `/health`, login, worker e journal. Manter release e backup anteriores para reversão; avaliar compatibilidade de migrations antes de reverter o código.

O certificado usa ACME webroot exclusivo, sem plugin que reescreve configurações de outros domínios. O hook de renovação testa o nginx e faz reload; não reinicia outros aplicativos. A renovação depende do timer certbot existente. Manter porta 80 acessível para o desafio.

Diagnóstico: `systemctl status nabi-office nabi-office-worker`, `journalctl -u nabi-office -u nabi-office-worker --since today`. Não publicar o ambiente nem tokens. Backups completos incluem todos os espaços e registros; proteger o arquivo como dado privado.

IA ainda requer credencial e homologação com a conta do proprietário. Não foram importadas chaves dos outros projetos. Instagram, Google, Telegram e login de inferência ChatGPT não são apresentados como conectados.
