self.addEventListener('push',event=>{
  let data={title:'Misfits Player Portal',body:'You have a new update.',url:'/chats'};
  try{if(event.data)data={...data,...event.data.json()};}catch(error){}
  event.waitUntil(self.registration.showNotification(data.title,{body:data.body,icon:'/static/misfits-icon.svg',badge:'/static/misfits-icon.svg',tag:data.tag||'misfits-update',data:{url:data.url||'/chats'}}));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=new URL(event.notification.data?.url||'/chats',self.location.origin).href;
  event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{for(const window of windows){if(window.url===target&&'focus' in window)return window.focus();}return clients.openWindow(target);}));
});

