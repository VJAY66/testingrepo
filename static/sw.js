self.addEventListener('push', function(event) {
  var data = {};
  try { data = event.data.json(); } catch(e) {
    data = { title: 'PickASide', body: event.data ? event.data.text() : '' };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'PickASide', {
      body: data.body || '',
      icon: '/static/icon-192.png',
      badge: '/static/icon-72.png',
      data: { url: data.url || '/' }
    })
  );
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(clients.openWindow(url));
});
