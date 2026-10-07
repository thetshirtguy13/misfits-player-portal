(() => {
  const app = document.getElementById("scenario-app");
  if (!app) return;

  const editable = app.dataset.editable === "true";
  const field = document.getElementById("baseball-field");
  const markerLayer = document.getElementById("marker-layer");
  const movementLayer = document.getElementById("movement-layer");
  const timeline = document.getElementById("timeline");
  const emptyTimeline = document.getElementById("empty-timeline");
  const stepCount = document.getElementById("step-count");
  const status = document.getElementById("scenario-status");
  const instruction = document.getElementById("instruction-text");
  const playButton = document.getElementById("play-button");
  const progressBar = document.getElementById("play-progress-bar");
  const saved = JSON.parse(app.dataset.scenarios || "[]");
  const colors = { move: "#f97316", throw: "#f8fafc", hit: "#ef4444", cover: "#22d3ee", backup: "#a78bfa", cutoff: "#a3e635", tag: "#fb7185" };
  const names = { move: "Move", throw: "Throw", hit: "Ball flight", cover: "Cover", backup: "Back up", cutoff: "Cutoff", tag: "Tag / run" };
  const defaults = {
    P:{x:50,y:54}, C:{x:50,y:91}, "1B":{x:69,y:52}, "2B":{x:60,y:37}, "3B":{x:31,y:52}, SS:{x:40,y:37}, LF:{x:24,y:19}, CF:{x:50,y:10}, RF:{x:76,y:19},
    R1:{x:73,y:54,runner:true,active:false}, R2:{x:50,y:23,runner:true,active:false}, R3:{x:27,y:54,runner:true,active:false}, BR:{x:53,y:86,runner:true,active:false}
  };
  let design = freshDesign();
  let tool = "setup";
  let selected = null;
  let playing = false;
  let playToken = 0;

  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function freshDesign() { return { version:1, positions:copy(defaults), ball:{x:50,y:87}, actions:[] }; }
  function point(x,y) { return {x:Math.max(2,Math.min(98,x)),y:Math.max(2,Math.min(98,y))}; }
  function fieldPoint(event) {
    const box=field.getBoundingClientRect();
    return point(((event.clientX-box.left)/box.width)*100,((event.clientY-box.top)/box.height)*100);
  }
  function activePosition(id, through=design.actions.length) {
    let position=copy(design.positions[id]);
    for (let i=0;i<through;i+=1) if (design.actions[i].entity===id) position=copy(design.actions[i].to);
    return position;
  }
  function ballPosition(through=design.actions.length) {
    let position=copy(design.ball);
    for (let i=0;i<through;i+=1) if (design.actions[i].entity==="ball") position=copy(design.actions[i].to);
    return position;
  }
  function snapshot(through=design.actions.length) {
    const positions={};
    Object.keys(design.positions).forEach(id => positions[id]=activePosition(id,through));
    return {positions,ball:ballPosition(through)};
  }
  function setPosition(element,position) {
    element.style.left=`${position.x}%`; element.style.top=`${position.y}%`;
  }
  function ensureMarkers() {
    markerLayer.innerHTML="";
    Object.entries(design.positions).forEach(([id,position]) => {
      if (position.runner && !position.active) return;
      const marker=document.createElement("button");
      marker.type="button"; marker.className=`field-marker${position.runner ? " runner" : ""}`;
      marker.dataset.id=id; marker.textContent=id; marker.title=position.runner ? `${id} runner` : id;
      marker.addEventListener("pointerdown",event => { event.stopPropagation(); if(editable && !playing) selectMarker(id); });
      markerLayer.appendChild(marker);
    });
    const ball=document.createElement("button");
    ball.type="button"; ball.className="field-ball"; ball.dataset.id="ball"; ball.title="Baseball";
    ball.addEventListener("pointerdown",event => { event.stopPropagation(); if(editable && !playing && tool==="setup") selectMarker("ball"); });
    markerLayer.appendChild(ball);
  }
  function renderSnapshot(frame,action=null) {
    Object.entries(frame.positions).forEach(([id,position]) => {
      const marker=markerLayer.querySelector(`[data-id="${id}"]`);
      if (!marker) return;
      setPosition(marker,position);
      marker.classList.toggle("selected",id===selected);
      marker.dataset.action=action && action.actor===id ? action.type : "";
    });
    const ball=markerLayer.querySelector('[data-id="ball"]');
    if(ball) { setPosition(ball,frame.ball); ball.classList.toggle("selected",selected==="ball"); }
  }
  function renderPaths() {
    movementLayer.innerHTML="";
    design.actions.forEach((action,index) => {
      const dx=action.to.x-action.from.x,dy=action.to.y-action.from.y;
      const line=document.createElement("div");
      line.className=`action-path ${action.type}`; line.dataset.index=index;
      line.style.left=`${action.from.x}%`; line.style.top=`${action.from.y}%`;
      line.style.width=`${Math.hypot(dx*field.clientWidth/100,dy*field.clientHeight/100)}px`;
      line.style.transform=`rotate(${Math.atan2(dy*field.clientHeight,dx*field.clientWidth)*180/Math.PI}deg)`;
      line.style.setProperty("--path-color",colors[action.type]);
      movementLayer.appendChild(line);
    });
  }
  function actionLabel(action) {
    if(action.type==="hit") return "Ball travels into play";
    if(action.type==="throw") return `${action.actor} throws the ball`;
    return `${action.actor} ${names[action.type].toLowerCase()}`;
  }
  function renderTimeline(current=-1) {
    timeline.innerHTML="";
    design.actions.forEach((action,index) => {
      const item=document.createElement("li"); if(index===current) item.classList.add("current");
      const number=document.createElement("span"); number.className="step-number"; number.textContent=index+1;
      const copyBox=document.createElement("button"); copyBox.type="button"; copyBox.className="step-copy text-button";
      const strong=document.createElement("strong"); strong.textContent=actionLabel(action);
      const small=document.createElement("small"); small.textContent=names[action.type];
      copyBox.append(strong,small); copyBox.addEventListener("click",() => previewThrough(index+1,index));
      item.append(number,copyBox);
      if(editable){const remove=document.createElement("button");remove.type="button";remove.className="remove-step";remove.textContent="\u00d7";remove.title="Remove step";remove.addEventListener("click",()=>{design.actions.splice(index,1);renderAll();});item.append(remove);}
      timeline.appendChild(item);
    });
    stepCount.textContent=`${design.actions.length} step${design.actions.length===1 ? "" : "s"}`;
    emptyTimeline.hidden=design.actions.length>0;
  }
  function renderAll(through=design.actions.length,current=-1) {
    ensureMarkers(); renderSnapshot(snapshot(through)); renderPaths(); renderTimeline(current); syncForm();
  }
  function previewThrough(through,current=-1) { stopPlayback(); renderSnapshot(snapshot(through),design.actions[current]||null); renderTimeline(current); progressBar.style.width=`${design.actions.length ? through/design.actions.length*100 : 0}%`; }
  function selectMarker(id) {
    selected=id; renderSnapshot(snapshot());
    if(tool==="setup") instruction.textContent=`Place ${id} anywhere on the field.`;
    else instruction.textContent=`Choose the destination for ${id}.`;
  }
  function addAction(destination) {
    if(tool==="hit") {
      design.actions.push({type:"hit",entity:"ball",actor:"Batter",from:ballPosition(),to:destination});
    } else if(!selected || selected==="ball") {
      instruction.textContent="Select a player or runner first."; return;
    } else if(tool==="throw") {
      design.actions.push({type:"throw",entity:"ball",actor:selected,from:activePosition(selected),to:destination});
    } else {
      design.actions.push({type:tool,entity:selected,actor:selected,from:activePosition(selected),to:destination});
    }
    selected=null; instruction.textContent="Step added. Add the next responsibility or press Play Scenario."; renderAll();
  }
  function handleField(event) {
    if(!editable || playing || event.target.closest(".field-marker,.field-ball")) return;
    const destination=fieldPoint(event);
    if(tool==="setup") {
      if(!selected){instruction.textContent="Select a marker, then choose its starting position.";return;}
      if(selected==="ball") design.ball=destination;
      else Object.assign(design.positions[selected],destination);
      selected=null; renderAll(); return;
    }
    addAction(destination);
  }
  function tween(from,to,duration,onFrame,token) {
    return new Promise(resolve => {
      const start=performance.now();
      function frame(now){
        if(token!==playToken){resolve();return;}
        const raw=Math.min(1,(now-start)/duration); const eased=raw<.5 ? 2*raw*raw : 1-Math.pow(-2*raw+2,2)/2;
        onFrame({x:from.x+(to.x-from.x)*eased,y:from.y+(to.y-from.y)*eased},raw);
        if(raw<1) requestAnimationFrame(frame); else resolve();
      }
      requestAnimationFrame(frame);
    });
  }
  async function play() {
    if(playing || !design.actions.length) return;
    playing=true; selected=null; const token=++playToken; playButton.textContent="Playing";
    renderSnapshot(snapshot(0)); progressBar.style.width="0%";
    for(let index=0;index<design.actions.length;index+=1){
      if(token!==playToken) break;
      const action=design.actions[index],before=snapshot(index),moving=action.entity==="ball" ? markerLayer.querySelector('[data-id="ball"]') : markerLayer.querySelector(`[data-id="${action.entity}"]`);
      renderTimeline(index); renderSnapshot(before,action); instruction.textContent=actionLabel(action);
      await tween(action.from,action.to,action.type==="throw"||action.type==="hit" ? 700 : 950,(position,amount)=>{if(moving)setPosition(moving,position);progressBar.style.width=`${((index+amount)/design.actions.length)*100}%`;},token);
      await new Promise(resolve=>setTimeout(resolve,180));
    }
    if(token===playToken){playing=false;playButton.textContent="Replay Scenario";renderSnapshot(snapshot());renderTimeline();progressBar.style.width="100%";instruction.textContent="Scenario complete.";}
  }
  function stopPlayback() { playing=false; playToken+=1; playButton.textContent="Play Scenario"; }
  function normalizeDesign(candidate) {
    const clean=freshDesign();
    if(candidate && candidate.positions) Object.entries(candidate.positions).forEach(([id,value])=>{if(clean.positions[id]&&Number.isFinite(value.x)&&Number.isFinite(value.y)) Object.assign(clean.positions[id],point(value.x,value.y),{active:value.active!==false});});
    if(candidate && candidate.ball && Number.isFinite(candidate.ball.x)) clean.ball=point(candidate.ball.x,candidate.ball.y);
    if(candidate && Array.isArray(candidate.actions)) clean.actions=candidate.actions.filter(a=>names[a.type]&&a.from&&a.to&&a.entity).slice(0,100).map(a=>({...a,from:point(a.from.x,a.from.y),to:point(a.to.x,a.to.y)}));
    return clean;
  }
  function applyPreset(name) {
    stopPlayback(); design=freshDesign(); selected=null;
    if(name==="fly-r3") {
      design.positions.R3.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:87},to:{x:24,y:23}},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:19},to:{x:26,y:25}},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:40,y:37},to:{x:37,y:45}},
        {type:"tag",entity:"R3",actor:"R3",from:{x:27,y:54},to:{x:50,y:88}},
        {type:"cover",entity:"C",actor:"C",from:{x:50,y:91},to:{x:50,y:87}},
        {type:"throw",entity:"ball",actor:"LF",from:{x:26,y:25},to:{x:37,y:45}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:37,y:45},to:{x:50,y:87}}
      ];
      setMeta("Fly Ball, Runner on Third","Outfield catch, tag-up, cutoff, and play at home");
    } else if(name==="ground-r1") {
      design.positions.R1.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:87},to:{x:39,y:42}},
        {type:"move",entity:"SS",actor:"SS",from:{x:40,y:37},to:{x:39,y:42}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:60,y:37},to:{x:51,y:25}},
        {type:"tag",entity:"R1",actor:"R1",from:{x:73,y:54},to:{x:51,y:25}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:39,y:42},to:{x:51,y:25}},
        {type:"throw",entity:"ball",actor:"2B",from:{x:51,y:25},to:{x:69,y:52}}
      ];
      setMeta("Ground Ball, Runner on First","Middle infield double-play responsibilities");
    } else if(name==="bunt-r1") {
      design.positions.R1.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:87},to:{x:45,y:73}},
        {type:"move",entity:"C",actor:"C",from:{x:50,y:91},to:{x:45,y:73}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:60,y:37},to:{x:69,y:52}},
        {type:"cover",entity:"SS",actor:"SS",from:{x:40,y:37},to:{x:50,y:23}},
        {type:"tag",entity:"R1",actor:"R1",from:{x:73,y:54},to:{x:50,y:23}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:19},to:{x:82,y:48}},
        {type:"throw",entity:"ball",actor:"C",from:{x:45,y:73},to:{x:69,y:52}}
      ];
      setMeta("Bunt Defense, Runner on First","Field the bunt, cover first and second, back up the throw");
    } else setMeta(""," ");
    document.querySelectorAll(".preset-button").forEach(button=>button.classList.toggle("active",button.dataset.preset===name));
    status.textContent=name==="empty" ? "New Scenario" : document.getElementById("scenario-title").value;
    instruction.textContent="Adjust the setup or add the next action."; renderAll();
  }
  function setMeta(titleValue,situationValue,teamId=null,id="") {
    if(!editable) return;
    document.getElementById("scenario-title").value=titleValue;
    document.getElementById("scenario-situation").value=situationValue.trim();
    document.getElementById("scenario-id").value=id;
    if(teamId) document.getElementById("scenario-team").value=String(teamId);
  }
  function syncForm(){if(editable)document.getElementById("design-json").value=JSON.stringify(design);}
  function loadScenario(row) {
    stopPlayback(); design=normalizeDesign(row.design); selected=null;
    setMeta(row.title,row.situation,row.team_id,row.id); status.textContent=row.title; instruction.textContent=row.situation||"Press Play Scenario to watch the responsibilities.";
    document.querySelectorAll(".preset-button").forEach(button=>button.classList.remove("active")); renderAll();
  }

  field.addEventListener("pointerup",handleField);
  playButton.addEventListener("click",play);
  document.getElementById("restart-button").addEventListener("click",()=>previewThrough(0));
  window.addEventListener("resize",renderPaths);
  if(editable){
    document.querySelectorAll(".tool-button[data-tool]").forEach(button=>button.addEventListener("click",()=>{tool=button.dataset.tool;selected=null;document.querySelectorAll(".tool-button[data-tool]").forEach(item=>item.classList.toggle("active",item===button));instruction.textContent=tool==="hit" ? "Choose where the ball is hit." : tool==="setup" ? "Select a marker, then choose its starting position." : `Select a player, then choose where they ${names[tool].toLowerCase()}.`;}));
    document.querySelectorAll(".preset-button").forEach(button=>button.addEventListener("click",()=>applyPreset(button.dataset.preset)));
    document.getElementById("undo-button").addEventListener("click",()=>{design.actions.pop();renderAll();});
    document.getElementById("clear-button").addEventListener("click",()=>{design.actions=[];renderAll();});
    document.getElementById("scenario-form").addEventListener("submit",syncForm);
  } else app.classList.add("viewer");
  document.querySelectorAll(".library-item").forEach(item=>item.querySelector(".open-scenario").addEventListener("click",()=>{const row=saved.find(entry=>String(entry.id)===item.dataset.scenarioId);if(row)loadScenario(row);}));

  const selectedId=Number(app.dataset.selectedId||0),selectedScenario=saved.find(row=>row.id===selectedId);
  if(selectedScenario) loadScenario(selectedScenario); else if(editable) applyPreset("fly-r3"); else if(saved.length) loadScenario(saved[0]); else renderAll();
})();
