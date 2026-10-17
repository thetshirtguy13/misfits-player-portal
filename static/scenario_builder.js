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
  const colors = { move: "#ff8a2a", throw: "#f4fffb", hit: "#ff3b7c", cover: "#24f2c2", backup: "#b78cff", cutoff: "#d9ff46", tag: "#ff5cc9" };
  const names = { move: "Move", throw: "Throw", hit: "Ball flight", cover: "Cover", backup: "Back up", cutoff: "Cutoff", tag: "Tag / run" };
  const defaults = {
    P:{x:50,y:68}, C:{x:50,y:91}, "1B":{x:68,y:61}, "2B":{x:59,y:49}, "3B":{x:32,y:61}, SS:{x:41,y:49}, LF:{x:24,y:22}, CF:{x:50,y:13}, RF:{x:76,y:22},
    R1:{x:70,y:68,runner:true,active:false}, R2:{x:50,y:44,runner:true,active:false}, R3:{x:30,y:68,runner:true,active:false}, BR:{x:53,y:88,runner:true,active:false}
  };
  let design = freshDesign();
  let tool = "setup";
  let selected = null;
  let selectedStep = null;
  let actionMode = "append";
  let playing = false;
  let playToken = 0;

  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function freshDesign() { return { version:1, positions:copy(defaults), ball:{x:50,y:89}, actions:[] }; }
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
  function refreshActionOrigins(){
    const positions=copy(design.positions);let ball=copy(design.ball);
    design.actions.forEach(action=>{
      if(action.entity==="ball"){
        action.from=action.type==="throw"&&positions[action.actor] ? copy(positions[action.actor]) : copy(ball);
        ball=copy(action.to);
      }else if(positions[action.entity]){
        action.from=copy(positions[action.entity]);positions[action.entity]=copy(action.to);
      }
    });
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
      const item=document.createElement("li"); if(index===current) item.classList.add("current"); if(index===selectedStep) item.classList.add("selected-step");
      const number=document.createElement("span"); number.className="step-number"; number.textContent=index+1;
      const copyBox=document.createElement("button"); copyBox.type="button"; copyBox.className="step-copy text-button";
      const strong=document.createElement("strong"); strong.textContent=actionLabel(action);
      const small=document.createElement("small"); small.textContent=names[action.type];
      copyBox.append(strong,small); copyBox.addEventListener("click",() => {selectedStep=index;previewThrough(index+1,index);});
      item.append(number,copyBox);
      if(editable){
        const order=document.createElement("span");order.className="timeline-order";
        const earlier=document.createElement("button");earlier.type="button";earlier.textContent="\u2191";earlier.title="Move step earlier";earlier.disabled=index===0;earlier.addEventListener("click",()=>moveStep(index,-1));
        const later=document.createElement("button");later.type="button";later.textContent="\u2193";later.title="Move step later";later.disabled=index===design.actions.length-1;later.addEventListener("click",()=>moveStep(index,1));
        order.append(earlier,later);item.append(order);
        const remove=document.createElement("button");remove.type="button";remove.className="remove-step";remove.textContent="\u00d7";remove.title="Remove step";remove.addEventListener("click",()=>{design.actions.splice(index,1);refreshActionOrigins();selectedStep=null;renderAll();});item.append(remove);
      }
      timeline.appendChild(item);
    });
    stepCount.textContent=`${design.actions.length} step${design.actions.length===1 ? "" : "s"}`;
    emptyTimeline.hidden=design.actions.length>0;
    document.querySelectorAll(".runner-toggle").forEach(button=>button.classList.toggle("active",Boolean(design.positions[button.dataset.runner]?.active)));
  }
  function moveStep(index,direction){const target=index+direction;if(target<0||target>=design.actions.length)return;[design.actions[index],design.actions[target]]=[design.actions[target],design.actions[index]];refreshActionOrigins();selectedStep=target;renderAll();}
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
    const index=actionMode==="replace"&&selectedStep!==null ? selectedStep : actionMode==="insert"&&selectedStep!==null ? selectedStep+1 : design.actions.length;
    let action;
    if(tool==="hit") {
      action={type:"hit",entity:"ball",actor:"Batter",from:ballPosition(index),to:destination};
    } else if(!selected || selected==="ball") {
      instruction.textContent="Select a player or runner first."; return;
    } else if(tool==="throw") {
      action={type:"throw",entity:"ball",actor:selected,from:activePosition(selected,index),to:destination};
    } else {
      action={type:tool,entity:selected,actor:selected,from:activePosition(selected,index),to:destination};
    }
    if(actionMode==="replace") design.actions.splice(index,1,action); else design.actions.splice(index,0,action);
    refreshActionOrigins();
    selectedStep=index; actionMode="append"; app.classList.remove("insert-mode","redraw-mode");
    selected=null; instruction.textContent="Step saved. Select it to insert, redraw, or reorder."; renderAll();
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
      const speed=Number(document.getElementById("playback-speed")?.value||1);
      await tween(action.from,action.to,(action.type==="throw"||action.type==="hit" ? 700 : 950)/speed,(position,amount)=>{if(moving)setPosition(moving,position);progressBar.style.width=`${((index+amount)/design.actions.length)*100}%`;},token);
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
    stopPlayback(); design=freshDesign(); selected=null; selectedStep=null; actionMode="append"; app.classList.remove("insert-mode","redraw-mode");
    if(name==="fly-r3") {
      design.positions.R3.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:89},to:{x:24,y:27}},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:22},to:{x:25,y:28}},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:36,y:57}},
        {type:"tag",entity:"R3",actor:"R3",from:{x:30,y:68},to:{x:50,y:90}},
        {type:"cover",entity:"C",actor:"C",from:{x:50,y:91},to:{x:50,y:89}},
        {type:"throw",entity:"ball",actor:"LF",from:{x:25,y:28},to:{x:36,y:57}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:36,y:57},to:{x:50,y:89}}
      ];
      setMeta("Fly Ball, Runner on Third","Outfield catch, tag-up, cutoff, and play at home");
    } else if(name==="ground-r1") {
      design.positions.R1.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:89},to:{x:39,y:54}},
        {type:"move",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:39,y:54}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"tag",entity:"R1",actor:"R1",from:{x:70,y:68},to:{x:50,y:44}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:39,y:54},to:{x:50,y:44}},
        {type:"throw",entity:"ball",actor:"2B",from:{x:50,y:44},to:{x:70,y:68}}
      ];
      setMeta("Ground Ball, Runner on First","Middle infield double-play responsibilities");
    } else if(name==="bunt-r1") {
      design.positions.R1.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:89},to:{x:45,y:78}},
        {type:"move",entity:"C",actor:"C",from:{x:50,y:91},to:{x:45,y:78}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:70,y:68}},
        {type:"cover",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:50,y:44}},
        {type:"tag",entity:"R1",actor:"R1",from:{x:70,y:68},to:{x:50,y:44}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:82,y:66}},
        {type:"throw",entity:"ball",actor:"C",from:{x:45,y:78},to:{x:70,y:68}}
      ];
      setMeta("Bunt Defense, Runner on First","Field the bunt, cover first and second, back up the throw");
    } else if(name==="left-single") {
      design.positions.BR.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:89},to:{x:22,y:33}},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:22},to:{x:22,y:33}},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:29,y:27}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:58,y:20}},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:34,y:55}},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{x:30,y:68}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{x:70,y:68}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:23,y:74}},
        {type:"cover",entity:"C",actor:"C",from:{x:50,y:91},to:{x:50,y:89}},
        {type:"tag",entity:"BR",actor:"BR",from:{x:53,y:88},to:{x:70,y:68}},
        {type:"throw",entity:"ball",actor:"LF",from:{x:22,y:33},to:{x:34,y:55}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:34,y:55},to:{x:50,y:44}}
      ];
      setMeta("Single to Left Field","Every defender moves: left fields it, center and right back up, short is cutoff, bases are covered, pitcher backs up third, and catcher protects home");
    } else if(name==="left-r2") {
      design.positions.R2.active=true; design.positions.BR.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{x:50,y:89},to:{x:20,y:31}},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:22},to:{x:20,y:31}},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:28,y:25}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:58,y:19}},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:35,y:57}},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{x:30,y:68}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{x:70,y:68}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:50,y:96}},
        {type:"cover",entity:"C",actor:"C",from:{x:50,y:91},to:{x:50,y:89}},
        {type:"tag",entity:"R2",actor:"R2",from:{x:50,y:44},to:{x:50,y:89}},
        {type:"tag",entity:"BR",actor:"BR",from:{x:53,y:88},to:{x:70,y:68}},
        {type:"throw",entity:"ball",actor:"LF",from:{x:20,y:31},to:{x:35,y:57}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:35,y:57},to:{x:50,y:89}}
      ];
      setMeta("Left Field Hit, Runner on Second","Left fields the ball, short lines up the throw home, pitcher backs up the plate, every base is covered, and both runners advance");
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
    stopPlayback(); design=normalizeDesign(row.design); selected=null; selectedStep=null; actionMode="append"; app.classList.remove("insert-mode","redraw-mode");
    setMeta(row.title,row.situation,row.team_id,row.id); status.textContent=row.title; instruction.textContent=row.situation||"Press Play Scenario to watch the responsibilities.";
    document.querySelectorAll(".preset-button").forEach(button=>button.classList.remove("active")); renderAll();
  }

  field.addEventListener("pointerup",handleField);
  playButton.addEventListener("click",play);
  document.getElementById("restart-button").addEventListener("click",()=>previewThrough(0));
  window.addEventListener("resize",renderPaths);
  if(editable){
    document.querySelectorAll(".tool-button[data-tool]").forEach(button=>button.addEventListener("click",()=>{tool=button.dataset.tool;selected=null;document.querySelectorAll(".tool-button[data-tool]").forEach(item=>item.classList.toggle("active",item===button));instruction.textContent=tool==="hit" ? "Choose where the ball is hit." : tool==="setup" ? "Select a marker, then choose its starting position." : `Select a player, then choose where they ${names[tool].toLowerCase()}.`;}));
    document.querySelectorAll(".preset-button[data-preset]").forEach(button=>button.addEventListener("click",()=>applyPreset(button.dataset.preset)));
    document.querySelectorAll(".runner-toggle").forEach(button=>button.addEventListener("click",()=>{const runner=design.positions[button.dataset.runner];runner.active=!runner.active;renderAll();}));
    document.getElementById("insert-button").addEventListener("click",()=>{if(selectedStep===null){instruction.textContent="Select a timeline step first.";return;}actionMode=actionMode==="insert"?"append":"insert";app.classList.toggle("insert-mode",actionMode==="insert");app.classList.remove("redraw-mode");instruction.textContent=actionMode==="insert"?"The next action will be inserted after the selected step.":"New actions will be added to the end.";});
    document.getElementById("redraw-button").addEventListener("click",()=>{if(selectedStep===null){instruction.textContent="Select a timeline step first.";return;}const action=design.actions[selectedStep];tool=action.type;selected=action.type==="hit"?null:action.actor;actionMode="replace";app.classList.add("redraw-mode");app.classList.remove("insert-mode");document.querySelectorAll(".tool-button[data-tool]").forEach(item=>item.classList.toggle("active",item.dataset.tool===tool));previewThrough(selectedStep);instruction.textContent=`Choose a new destination for step ${selectedStep+1}.`;});
    document.getElementById("undo-button").addEventListener("click",()=>{design.actions.pop();refreshActionOrigins();selectedStep=null;renderAll();});
    document.getElementById("clear-button").addEventListener("click",()=>{design.actions=[];selectedStep=null;actionMode="append";app.classList.remove("insert-mode","redraw-mode");renderAll();});
    document.getElementById("scenario-form").addEventListener("submit",syncForm);
  } else app.classList.add("viewer");
  document.querySelectorAll(".library-item").forEach(item=>item.querySelector(".open-scenario").addEventListener("click",()=>{const row=saved.find(entry=>String(entry.id)===item.dataset.scenarioId);if(row)loadScenario(row);}));

  const selectedId=Number(app.dataset.selectedId||0),selectedScenario=saved.find(row=>row.id===selectedId);
  if(selectedScenario) loadScenario(selectedScenario); else if(editable) applyPreset("fly-r3"); else if(saved.length) loadScenario(saved[0]); else renderAll();
})();
