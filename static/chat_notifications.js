(function(){
  const buttons=[...document.querySelectorAll('[data-enable-notifications]')];
  const badges=[...document.querySelectorAll('[data-notification-count]')];
  const shell=document.querySelector('[data-chat-team]');
  const csrf=buttons[0]?.dataset.csrf||'';

  function applicationKey(value){
    const padding='='.repeat((4-value.length%4)%4);
    const base64=(value+padding).replace(/-/g,'+').replace(/_/g,'/');
    return Uint8Array.from(atob(base64),character=>character.charCodeAt(0));
  }

  async function registration(){
    if(!('serviceWorker' in navigator)) throw new Error('Notifications are not supported on this device.');
    return navigator.serviceWorker.register('/service-worker.js',{scope:'/'});
  }

  async function enableNotifications(button){
    button.disabled=true;
    try{
      if(!('Notification' in window)) throw new Error('Notifications are not supported on this device.');
      const permission=await Notification.requestPermission();
      if(permission!=='granted') throw new Error('Notifications were not enabled.');
      const worker=await registration();
      const keyResponse=await fetch('/push/public-key');
      const keyData=await keyResponse.json();
      let subscription=await worker.pushManager.getSubscription();
      if(!subscription) subscription=await worker.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:applicationKey(keyData.public_key)});
      const response=await fetch('/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(subscription)});
      if(!response.ok) throw new Error('The notification subscription could not be saved.');
      buttons.forEach(item=>{item.textContent='Notifications On';item.disabled=true;});
    }catch(error){
      button.disabled=false;
      button.textContent=error.message||'Enable Notifications';
    }
  }

  buttons.forEach(button=>button.addEventListener('click',()=>enableNotifications(button)));
  if('Notification' in window&&Notification.permission==='granted') registration().then(worker=>worker.pushManager.getSubscription()).then(subscription=>{if(subscription) buttons.forEach(button=>{button.textContent='Notifications On';button.disabled=true;});}).catch(()=>{});

  async function updateUnread(){
    try{
      const response=await fetch('/notifications/unread',{headers:{'Accept':'application/json'}});
      if(!response.ok) return;
      const data=await response.json();
      badges.forEach(badge=>{badge.textContent=data.count;badge.hidden=!data.count;});
    }catch(error){}
  }
  updateUnread(); setInterval(updateUnread,20000);

  if(shell){
    const team=shell.dataset.chatTeam; const latest=Number(shell.dataset.latestMessage||0); const alert=document.querySelector('[data-new-messages]');
    alert?.addEventListener('click',()=>location.reload());
    setInterval(async()=>{
      try{
        const response=await fetch(`/teams/${team}/chat/feed?after=${latest}`,{headers:{'Accept':'application/json'}});
        const data=await response.json();
        if(data.changed){
          const draft=document.querySelector('#chat-body')?.value.trim();
          if(draft){alert.hidden=false;}else{location.reload();}
        }
      }catch(error){}
    },10000);
  }
})();
