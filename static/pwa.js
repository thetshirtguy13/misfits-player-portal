(()=>{
  if('serviceWorker' in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('/service-worker.js',{scope:'/'}).catch(()=>{}));
  const button=document.querySelector('[data-install-app]');
  const help=document.querySelector('[data-install-help]');
  if(!button)return;
  if(window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true){button.textContent='App Installed';button.disabled=true;if(help)help.textContent='You are using the installed app.';return;}
  let prompt=null;
  window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();prompt=event;});
  button.addEventListener('click',async()=>{
    if(prompt){prompt.prompt();await prompt.userChoice;prompt=null;return;}
    if(help)help.textContent=/iphone|ipad|ipod/i.test(navigator.userAgent)?'Tap the Share button in Safari, then choose Add to Home Screen.':'Open your browser menu and choose Install App or Add to Home Screen.';
  });
})();

