"""Connect an owner account locally and import its protected session on its own VM."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from office import chatgpt_plan


def main():
    parser = argparse.ArgumentParser(description='Conexão oficial do plano ChatGPT para uma instalação operada pelo dono.')
    actions = parser.add_subparsers(dest='action', required=True)
    login = actions.add_parser('login', help='Autoriza no navegador deste computador via callback local.')
    login.add_argument('--timeout', type=int, default=300)
    actions.add_parser('status', help='Mostra somente o estado e a conta conectada.')
    actions.add_parser('models', help='Lista os modelos disponíveis na conta conectada.')
    actions.add_parser('init-host', help='Prepara um identificador próprio nesta instalação.')
    transfer = actions.add_parser('import', help='Importa na VM o session.json recebido por um canal seguro.')
    transfer.add_argument('--file', type=Path, help='Arquivo protegido. Quando omitido, lê pelo stdin.')
    transfer.add_argument('--owner', type=int, required=True, help='ID do proprietário na instalação de destino.')
    args = parser.parse_args()
    try:
        if args.action == 'login':
            connected = chatgpt_plan.login(timeout=max(10, min(args.timeout, 600)), on_browser_open=lambda: print(
                'Navegador aberto para consentimento na página oficial da OpenAI.', flush=True))
            print(json.dumps(connected, ensure_ascii=False))
            print('Arquivo protegido:', chatgpt_plan.profile_directory() / 'session.json')
        elif args.action == 'status':
            print(json.dumps(chatgpt_plan.status(), ensure_ascii=False))
        elif args.action == 'models':
            print(json.dumps(chatgpt_plan.list_models(), ensure_ascii=False))
        elif args.action == 'init-host':
            chatgpt_plan.init_host()
            print('Identificador desta instalação preparado.')
        elif args.action == 'import':
            if args.file:
                with args.file.open('rb') as stream:
                    raw = stream.read(chatgpt_plan.FILE_LIMIT + 1)
            else:
                raw = sys.stdin.buffer.read(chatgpt_plan.FILE_LIMIT + 1)
            print(json.dumps(chatgpt_plan.import_profile(raw, owner_id=args.owner), ensure_ascii=False))
    except chatgpt_plan.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, KeyboardInterrupt):
        print('Operação interrompida ou arquivo de sessão inacessível.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
