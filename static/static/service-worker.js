// Nom du cache
const CACHE_NAME = "compta-smmd-cache-v1";

// Fichiers à mettre en cache au premier chargement
const URLS_TO_CACHE = [
  "/",
  "/login",
  "/static/manifest.json"
];

// Installation du service worker
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(URLS_TO_CACHE);
    })
  );
});

// Activation (nettoyage des anciens caches si besoin)
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Interception des requêtes
self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      // Si trouvé dans le cache -> on renvoie
      if (response) {
        return response;
      }
      // Sinon on fait la requête réseau
      return fetch(event.request);
    })
  );
});
