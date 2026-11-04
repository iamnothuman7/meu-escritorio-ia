from nabi.wsgi import application
from waitress import serve
import os

if __name__ == '__main__':
    print('Nabi Office: http://127.0.0.1:8787', flush=True)
    options = {'trusted_proxy':'127.0.0.1','trusted_proxy_headers':{'x-forwarded-proto','x-forwarded-for'}} if os.environ.get('NABI_HTTPS') == '1' else {}
    serve(application, host='127.0.0.1', port=int(os.environ.get('NABI_PORT','8787')), threads=4, clear_untrusted_proxy_headers=True, **options)
