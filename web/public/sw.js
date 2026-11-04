// CrewPay desktop alerts. Shows what the server pushed ({title, body, url}) and opens that
// page on click. Nothing is cached and nothing else runs here.

/** A path on this origin, or "/": a crafted push cannot open another site. */
function samePage(value) {
  try {
    const url = new URL(typeof value === "string" ? value : "/", self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search + url.hash : "/";
  } catch {
    return "/";
  }
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "CrewPay", {
      body: data.body || "Something needs you on the desk.",
      tag: data.tag || undefined,
      renotify: Boolean(data.tag),
      requireInteraction: true,
      icon: "/icon.svg",
      data: { url: samePage(data.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(samePage(event.notification.data && event.notification.data.url), self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((tabs) => {
      // Never take over the ops-room speaker or the wall board: that would silence them.
      const tab = tabs.find((c) => {
        const at = new URL(c.url);
        return at.origin === self.location.origin && !/^\/(speaker|board)(\/|$)/.test(at.pathname);
      });
      if (tab) return tab.focus().then(() => tab.navigate(url)).catch(() => self.clients.openWindow(url));
      return self.clients.openWindow(url);
    }),
  );
});
