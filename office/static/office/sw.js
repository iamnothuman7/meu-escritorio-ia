/* Static assets and the generic offline page only. Private HTML and APIs are never cached. */
const CACHE='nabio-public-v9';
const ASSETS=['/offline/','/static/office/claw-theme.css','/static/office/claw-utility.css','/static/office/fonts/bebas-neue-latin-400-normal.woff2','/static/office/fonts/ibm-plex-sans-latin-wght-normal.woff2','/static/office/fonts/ibm-plex-mono-latin-500-normal.woff2','/static/office/brand/icon-192.png','/static/office/brand/icon-512.png','/static/office/brand/icon.svg'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting()));});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('nabio-public-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',event=>{
  const req=event.request, url=new URL(req.url);
  if(req.method!=='GET'||url.origin!==self.location.origin)return;
  if(req.mode==='navigate'){
    event.respondWith(fetch(req).catch(()=>caches.open(CACHE).then(cache=>cache.match('/offline/'))));
    return;
  }
  if(!ASSETS.includes(url.pathname)||url.search)return;
  event.respondWith(caches.open(CACHE).then(async cache=>{
    try { const response=await fetch(req); if(response.ok)await cache.put(req,response.clone()); return response; }
    catch { return cache.match(req); }
  }));
});
