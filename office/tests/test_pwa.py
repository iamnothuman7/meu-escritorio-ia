import json
import shutil
import struct
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from office.services import bootstrap_workspace


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PublicPwaTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('private-owner-marker', password='Local-secret-test7!')
        self.workspace = bootstrap_workspace(self.owner, name='Private workspace should never enter public cache')

    def test_manifest_is_public_installable_and_has_same_origin_png_icons(self):
        response = self.client.get('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        manifest = json.loads(response.content)
        self.assertEqual(manifest['start_url'], '/app/')
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['theme_color'], '#F8F7F4')
        self.assertEqual(manifest['background_color'], '#F8F7F4')
        self.assertNotIn('private-owner-marker', response.content.decode())
        by_size = {icon['sizes']: icon for icon in manifest['icons']}
        for expected in (192, 512):
            icon = by_size[f'{expected}x{expected}']
            self.assertTrue(icon['src'].startswith('/static/office/'))
            self.assertEqual(icon['type'], 'image/png')
            self.assertIn('any', icon.get('purpose', 'any').split())
            # Validate the distributable asset without requiring collectstatic in tests.
            # Static delivery itself is verified by the deployment readiness checks.
            png = (settings.BASE_DIR / 'office/static' / icon['src'].removeprefix('/static/')).read_bytes()
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II', png[16:24]), (expected, expected))
            self.assertIn(png[25], (4, 6), 'Ícones devem preservar canal de transparência.')

    def test_landing_public_and_dashboard_protected(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'manifest.webmanifest')
        self.assertNotContains(response, self.workspace.name)
        self.assertNotContains(response, self.owner.username)
        protected = self.client.get('/app/')
        self.assertEqual(protected.status_code, 302)
        self.assertTrue(protected.url.startswith('/login/?next='))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/app/').status_code, 200)

    def test_login_remains_a_form_for_authenticated_and_anonymous_visitors(self):
        for authenticated in (False, True):
            if authenticated:
                self.client.force_login(self.owner)
            response = self.client.get('/login/')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'name="username"')
            self.assertContains(response, 'name="password"')

    def test_offline_fallback_is_identical_public_and_has_no_session_data(self):
        anonymous = self.client.get('/offline/')
        self.assertEqual(anonymous.status_code, 200)
        self.client.force_login(self.owner)
        authenticated = self.client.get('/offline/')
        self.assertEqual(anonymous.content, authenticated.content)
        self.assertNotContains(authenticated, self.workspace.name)
        self.assertNotContains(authenticated, self.owner.username)
        self.assertNotContains(authenticated, 'csrfmiddlewaretoken')
        self.assertNotContains(authenticated, 'name="password"')
        self.assertContains(authenticated, '/app/')

    def test_worker_is_public_javascript_with_root_scope_and_revalidation(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/javascript'))
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertTrue('no-cache' in response['Cache-Control'] or 'no-store' in response['Cache-Control'])
        self.assertNotContains(response, self.workspace.name)
        self.assertNotContains(response, 'csrfmiddlewaretoken')


@skipUnless(shutil.which('node'), 'Node.js é opcional; necessário apenas para a prova isolada do service worker.')
class ServiceWorkerPrivacyTests(SimpleTestCase):
    def test_runtime_never_caches_private_navigations_api_or_mutations(self):
        source = (settings.BASE_DIR / 'office/static/office/sw.js').read_text(encoding='utf-8')
        # Execute the actual worker in an isolated JS context with an in-memory
        # Cache API and no network. The assertions concern observable behavior.
        script = r'''
const vm=require('node:vm'), assert=require('node:assert/strict'), fs=require('node:fs');
const source=fs.readFileSync(0,'utf8');
const origin='https://office.nabio.pro', handlers={}, stores=new Map(), writes=[];
let offline=false;
const address=req=>new URL(typeof req==='string'?req:req.url,origin).href;
const fetch=async req=>{
  if(offline)throw Error('offline');
  const path=new URL(address(req)).pathname;
  return new Response(path==='/offline/'?'PUBLIC OFFLINE':path.startsWith('/static/')?'PUBLIC ASSET':'PRIVATE USER CONTENT');
};
const caches={
  async open(name){
    if(!stores.has(name))stores.set(name,new Map()); const store=stores.get(name);
    return {
      async match(req){const response=store.get(address(req)); return response&&response.clone();},
      async put(req,res){writes.push(address(req));store.set(address(req),res.clone());},
      async addAll(urls){for(const url of urls){const res=await fetch(url);store.set(address(url),res);writes.push(address(url));}}
    };
  },
  async keys(){return [...stores.keys()];},
  async delete(name){return stores.delete(name);}
};
const self={location:{origin},addEventListener:(kind,handler)=>{handlers[kind]=handler;},
  skipWaiting:async()=>{},clients:{claim:async()=>{}}};
vm.runInNewContext(source,{self,caches,fetch,URL,Response,Request,console});
async function lifecycle(kind){let pending;handlers[kind]({waitUntil:p=>{pending=p;}});await pending;}
async function dispatch(path,{method='GET',mode='same-origin'}={}){
  let pending;handlers.fetch({request:{url:new URL(path,origin).href,method,mode},respondWith:p=>{pending=p;}});
  if(!pending)return {handled:false};const response=await pending;
  return {handled:true,body:response&&await response.text()};
}
(async()=>{
  stores.set('another-app-cache',new Map());
  await lifecycle('install');await lifecycle('activate');
  assert(stores.has('another-app-cache'),'Activation may not delete unrelated caches');
  assert(writes.every(url=>new URL(url).pathname==='/offline/'||new URL(url).pathname.startsWith('/static/office/')));
  writes.length=0;
  const privatePaths=['/app/','/api/v1/dashboard','/login/','/setup/?token=DO-NOT-CACHE','/work/','/settings/','/deliveries/1/download/','/backup/','/export/'];
  for(const path of privatePaths){
    const nav=await dispatch(path,{mode:'navigate'});assert.equal(nav.body,'PRIVATE USER CONTENT');
    assert.equal((await dispatch(path)).handled,false);
  }
  assert.equal(writes.length,0,'Private HTML and API responses must not be persisted');
  assert.equal((await dispatch('/work/action/',{method:'POST'})).handled,false,'Mutations must go directly to network');
  assert.equal((await dispatch('https://other.example/private',{mode:'navigate'})).handled,false,'Do not intercept another origin');
  assert.equal((await dispatch('/static/office/app.css?token=DO-NOT-CACHE')).handled,false,'Do not cache query-bearing requests');
  offline=true;
  assert.equal((await dispatch('/app/',{mode:'navigate'})).body,'PUBLIC OFFLINE');
  assert.equal(writes.length,0,'Offline mode must not enqueue writes');
  console.log('Service worker privacy behavior verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which('node'), '-e', script], input=source,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
