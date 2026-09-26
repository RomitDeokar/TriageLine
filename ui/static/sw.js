// Service worker: network-first app shell so the PWA installs and opens offline.
// API calls and the SSE stream are never cached (live sessions must hit the server).
const C = "triageline-v3";
const SHELL = ["/live.html", "/live.css", "/live.js", "/manifest.json", "/icon.svg", "/sample_port.png"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(C).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== C).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== self.location.origin || u.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && r.type === "basic") { const cp = r.clone(); caches.open(C).then((c) => c.put(e.request, cp)); }
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match("/live.html"))));
});
