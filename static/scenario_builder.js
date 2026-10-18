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
  const fieldPlayButton = document.getElementById("field-play-button");
  const stepChips = document.getElementById("step-chips");
  const recordButton = document.getElementById("record-button");
  const progressBar = document.getElementById("play-progress-bar");
  const saved = JSON.parse(app.dataset.scenarios || "[]");
  const colors = { move: "#ff8a2a", throw: "#f4fffb", hit: "#ff3b7c", cover: "#24f2c2", backup: "#b78cff", cutoff: "#d9ff46", tag: "#ff5cc9", draw: "#55d6ff" };
  const names = { move: "Move", throw: "Throw", hit: "Ball flight", cover: "Cover", backup: "Back up", cutoff: "Cutoff", tag: "Tag / run", draw: "Coach drawing" };
  const spots = {
    homePlate:{x:50,y:92.6}, homeCover:{x:47.2,y:89.2}, catcherStart:{x:50,y:94.2}, rightHandedBox:{x:44.2,y:92.2},
    firstBase:{x:69,y:68}, secondBase:{x:50,y:44}, thirdBase:{x:31,y:68}
  };
  const defaults = {
    P:{x:50,y:68}, C:{...spots.catcherStart}, "1B":{x:68,y:61}, "2B":{x:59,y:49}, "3B":{x:32,y:61}, SS:{x:41,y:49}, LF:{x:24,y:22}, CF:{x:50,y:13}, RF:{x:76,y:22},
    R1:{...spots.firstBase,runner:true,active:false}, R2:{...spots.secondBase,runner:true,active:false}, R3:{...spots.thirdBase,runner:true,active:false}, BR:{...spots.rightHandedBox,runner:true,active:false}
  };
  let design = freshDesign();
  let tool = "setup";
  let selected = null;
  let selectedStep = null;
  let actionMode = "append";
  let playing = false;
  let playToken = 0;
  let recording = false;
  let dragState = null;
  let drawState = null;
  let nextDrawingId = 1;

  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function actionPhase(action,index) { return Number.isFinite(action.phase) ? action.phase : index+1; }
  function phaseGroups() {
    const groups=[];
    design.actions.forEach((action,index)=>{
      const phase=actionPhase(action,index);
      let group=groups.find(item=>item.phase===phase);
      if(!group){group={phase,items:[]};groups.push(group);}
      group.items.push({action,index});
    });
    return groups.sort((a,b)=>a.phase-b.phase);
  }
  function freshDesign() { return { version:1, positions:copy(defaults), ball:{...spots.homePlate}, actions:[] }; }
  function point(x,y) { return {x:Math.max(2,Math.min(98,x)),y:Math.max(2,Math.min(98,y))}; }
  function midpoint(from,to) { return point((from.x+to.x)/2,(from.y+to.y)/2); }
  function behindTarget(from,to,ratio=.35) { return point(to.x+(to.x-from.x)*ratio,to.y+(to.y-from.y)*ratio); }
  function catcherCanBackFirst(ballPoint) { return ballPoint.x>=spots.firstBase.x; }
  function catcherFirstBackup(ballPoint) {
    return point(
      spots.firstBase.x+(spots.firstBase.x-ballPoint.x)*.2,
      spots.firstBase.y+(spots.firstBase.y-ballPoint.y)*.2
    );
  }
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
  function beginMarkerDrag(event,id) {
    if(!editable || playing) return;
    event.preventDefault(); event.stopPropagation();
    const canDrag=recording || tool==="setup";
    if(!canDrag){selectMarker(id);return;}
    const origin=id==="ball" ? ballPosition() : activePosition(id);
    dragState={id,origin,marker:event.currentTarget,moved:false};
    event.currentTarget.classList.add("dragging");
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function moveDraggedMarker(event) {
    if(!dragState || playing) return;
    const destination=fieldPoint(event);
    dragState.moved=true;
    setPosition(dragState.marker,destination);
  }
  function finishMarkerDrag(event) {
    if(!dragState) return false;
    const current=dragState; dragState=null; current.marker.classList.remove("dragging");
    if(!current.moved){
      if(!recording) selectMarker(current.id);
      else instruction.textContent="Drag the marker to record its next movement.";
      return true;
    }
    const destination=fieldPoint(event);
    if(recording){
      let action;
      if(current.id==="ball"){
        const hasBallAction=design.actions.some(item=>item.entity==="ball");
        action={type:hasBallAction?"throw":"hit",entity:"ball",actor:hasBallAction?"Defense":"Batter",from:ballPosition(),to:destination};
      }else{
        const movementType=["move","cover","backup","cutoff","tag"].includes(tool)?tool:"move";
        action={type:movementType,entity:current.id,actor:current.id,from:activePosition(current.id),to:destination};
      }
      design.actions.push(action); selectedStep=design.actions.length-1;
      instruction.textContent=`Step ${design.actions.length} recorded. Drag the next player or ball.`;
    }else if(current.id==="ball") design.ball=destination;
    else Object.assign(design.positions[current.id],destination);
    renderAll();
    return true;
  }
  function drawPreview(from,to) {
    let preview=movementLayer.querySelector(".draw-preview");
    if(!preview){preview=document.createElement("div");preview.className="action-path draw draw-preview";movementLayer.appendChild(preview);}
    const dx=to.x-from.x,dy=to.y-from.y;
    preview.style.left=`${from.x}%`;preview.style.top=`${from.y}%`;
    preview.style.width=`${Math.hypot(dx*field.clientWidth/100,dy*field.clientHeight/100)}px`;
    preview.style.transform=`rotate(${Math.atan2(dy*field.clientHeight,dx*field.clientWidth)*180/Math.PI}deg)`;
    preview.style.setProperty("--path-color",colors.draw);
  }
  function beginDrawing(event){
    if(!editable || playing || tool!=="draw" || event.target.closest(".field-marker,.field-ball,.field-control-dock")) return;
    event.preventDefault(); drawState={from:fieldPoint(event),to:fieldPoint(event)}; drawPreview(drawState.from,drawState.to);
  }
  function moveDrawing(event){if(!drawState)return;drawState.to=fieldPoint(event);drawPreview(drawState.from,drawState.to);}
  function finishDrawing(event){
    if(!drawState)return false;
    drawState.to=fieldPoint(event);
    if(Math.hypot(drawState.to.x-drawState.from.x,drawState.to.y-drawState.from.y)>1){
      design.actions.push({type:"draw",entity:`drawing-${nextDrawingId++}`,actor:"Coach",from:drawState.from,to:drawState.to});
      selectedStep=design.actions.length-1; instruction.textContent="Drawing saved. Add another path or choose Move.";
    }
    drawState=null;renderAll();return true;
  }
  function ensureMarkers() {
    markerLayer.innerHTML="";
    Object.entries(design.positions).forEach(([id,position]) => {
      if (position.runner && !position.active) return;
      const marker=document.createElement("button");
      marker.type="button"; marker.className=`field-marker${position.runner ? " runner" : ""}`;
      marker.dataset.id=id; marker.textContent=id; marker.title=position.runner ? `${id} runner` : id;
      marker.addEventListener("pointerdown",event => beginMarkerDrag(event,id));
      markerLayer.appendChild(marker);
    });
    const ball=document.createElement("button");
    ball.type="button"; ball.className="field-ball"; ball.dataset.id="ball"; ball.title="Baseball";
    ball.addEventListener("pointerdown",event => beginMarkerDrag(event,"ball"));
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
    if(action.type==="draw") return "Coach draws an instruction path";
    return `${action.actor} ${names[action.type].toLowerCase()}`;
  }
  function renderTimeline(current=-1) {
    timeline.innerHTML="";
    const currentPhase=current>=0 ? actionPhase(design.actions[current],current) : null;
    design.actions.forEach((action,index) => {
      const item=document.createElement("li"); if(actionPhase(action,index)===currentPhase) item.classList.add("current"); if(index===selectedStep) item.classList.add("selected-step");
      const number=document.createElement("span"); number.className="step-number"; number.textContent=actionPhase(action,index);
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
    const phases=phaseGroups();
    stepCount.textContent=`${phases.length} step${phases.length===1 ? "" : "s"}`;
    emptyTimeline.hidden=design.actions.length>0;
    document.querySelectorAll(".runner-toggle").forEach(button=>button.classList.toggle("active",Boolean(design.positions[button.dataset.runner]?.active)));
    renderStepChips(current);
  }
  function renderStepChips(current=-1){
    if(!stepChips)return;
    stepChips.innerHTML="";
    const start=document.createElement("button");start.type="button";start.textContent="Start";start.className=current<0?"active":"";start.addEventListener("click",()=>previewThrough(0,-1));stepChips.appendChild(start);
    const currentPhase=current>=0 ? actionPhase(design.actions[current],current) : null;
    phaseGroups().forEach(group=>{const last=group.items[group.items.length-1].index;const chip=document.createElement("button");chip.type="button";chip.textContent=String(group.phase);chip.title=group.items.map(item=>actionLabel(item.action)).join(" + ");chip.classList.toggle("active",group.phase===currentPhase);chip.addEventListener("click",()=>{selectedStep=last;previewThrough(last+1,last);});stepChips.appendChild(chip);});
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
  function setTool(next){
    tool=next;selected=null;
    document.querySelectorAll(".tool-button[data-tool]").forEach(item=>item.classList.toggle("active",item.dataset.tool===tool));
    document.getElementById("direct-move-button")?.classList.toggle("active",tool==="move"||tool==="setup");
    document.getElementById("field-draw-button")?.classList.toggle("active",tool==="draw");
  }
  function syncRecordUI(){
    app.classList.toggle("recording",recording);
    if(recordButton){recordButton.classList.toggle("active",recording);const label=recordButton.querySelector("span");if(label)label.textContent=recording?"Stop":"Record";}
  }
  function toggleRecording(){
    if(!editable||playing)return;
    recording=!recording;
    if(recording){setTool("move");instruction.textContent="Recording: drag a player or the baseball. Each drag becomes the next step.";}
    else instruction.textContent="Recording stopped. Replay the sequence or choose a step to fine-tune it.";
    syncRecordUI();
  }
  function undoLast(){if(!design.actions.length)return;design.actions.pop();refreshActionOrigins();selectedStep=null;instruction.textContent="Last recorded step removed.";renderAll();}
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
    if(finishMarkerDrag(event) || finishDrawing(event)) return;
    if(!editable || playing || event.target.closest(".field-marker,.field-ball,.field-control-dock")) return;
    if(tool==="draw") return;
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
    playing=true; selected=null; const token=++playToken; setPlayButtonText("Playing"); fieldPlayButton.disabled=true;
    renderSnapshot(snapshot(0)); progressBar.style.width="0%";
    const groups=phaseGroups();
    for(let groupIndex=0;groupIndex<groups.length;groupIndex+=1){
      if(token!==playToken) break;
      const group=groups[groupIndex],first=group.items[0].index,last=group.items[group.items.length-1].index,before=snapshot(first);
      renderTimeline(first); renderSnapshot(before); instruction.textContent=group.items.map(item=>actionLabel(item.action)).join(" + ");
      const speed=Number(document.getElementById("playback-speed")?.value||1);
      await Promise.all(group.items.map(({action})=>{
        const moving=action.entity==="ball" ? markerLayer.querySelector('[data-id="ball"]') : markerLayer.querySelector(`[data-id="${action.entity}"]`);
        return tween(action.from,action.to,(action.type==="throw"||action.type==="hit" ? 700 : 950)/speed,(position,amount)=>{if(moving)setPosition(moving,position);progressBar.style.width=`${((groupIndex+amount)/groups.length)*100}%`;},token);
      }));
      renderSnapshot(snapshot(last+1));
      await new Promise(resolve=>setTimeout(resolve,180));
    }
    if(token===playToken){playing=false;setPlayButtonText("Replay Scenario");fieldPlayButton.disabled=false;renderSnapshot(snapshot());renderTimeline(design.actions.length-1);progressBar.style.width="100%";instruction.textContent="Scenario complete.";}
  }
  function setPlayButtonText(text){playButton.textContent=text;if(fieldPlayButton){const label=fieldPlayButton.querySelector("span");if(label)label.textContent=text==="Playing"?"Playing":text==="Replay Scenario"?"Replay":"Play";}}
  function stopPlayback() { playing=false; playToken+=1; setPlayButtonText("Play Scenario"); if(fieldPlayButton)fieldPlayButton.disabled=false; }
  function normalizeDesign(candidate) {
    const clean=freshDesign();
    if(candidate && candidate.positions) Object.entries(candidate.positions).forEach(([id,value])=>{if(clean.positions[id]&&Number.isFinite(value.x)&&Number.isFinite(value.y)) Object.assign(clean.positions[id],point(value.x,value.y),{active:value.active!==false});});
    if(candidate && candidate.ball && Number.isFinite(candidate.ball.x)) clean.ball=point(candidate.ball.x,candidate.ball.y);
    if(candidate && Array.isArray(candidate.actions)) clean.actions=candidate.actions.filter(a=>names[a.type]&&a.from&&a.to&&a.entity).slice(0,100).map((a,index)=>({...a,phase:Number.isFinite(a.phase)?a.phase:index+1,from:point(a.from.x,a.from.y),to:point(a.to.x,a.to.y)}));
    return clean;
  }

  const runnerStates = [
    {id:"none",folder:"No runners",label:"No Runners",runners:[]},
    {id:"r1",folder:"Runner on First Base",label:"R1",runners:["R1"]},
    {id:"r2",folder:"Runner on Second Base",label:"R2",runners:["R2"]},
    {id:"r3",folder:"Runner on Third Base",label:"R3",runners:["R3"]},
    {id:"r12",folder:"Runners on First & Second",label:"R1 + R2",runners:["R1","R2"]},
    {id:"r13",folder:"Runners on First & Third",label:"R1 + R3",runners:["R1","R3"]},
    {id:"r23",folder:"Runners on Second & Third",label:"R2 + R3",runners:["R2","R3"]},
    {id:"loaded",folder:"Bases Loaded",label:"Bases Loaded",runners:["R1","R2","R3"]}
  ];
  const outfieldZones = [
    {id:"lf",label:"LF",fielder:"LF",point:{x:22,y:31},support:"CF"},
    {id:"cf-left",label:"CF (Left Side)",fielder:"CF",point:{x:39,y:20},support:"LF"},
    {id:"cf-middle",label:"CF (Middle)",fielder:"CF",point:{x:50,y:16},support:"LF"},
    {id:"cf-right",label:"CF (Right Side)",fielder:"CF",point:{x:61,y:20},support:"RF"},
    {id:"rf",label:"RF",fielder:"RF",point:{x:78,y:31},support:"CF"}
  ];
  const featuredScenarios = [
    {id:"left-single",folder:"Coach Favorites",title:"Single to Left Field",summary:"Every defender rotates"},
    {id:"right-single",folder:"Coach Favorites",title:"Single to Right Field",summary:"Cutoff and backup responsibilities"},
    {id:"fly-r3",folder:"Coach Favorites",title:"Fly Ball + Runner on 3rd",summary:"Tag, relay, and play at home"},
    {id:"left-r2",folder:"Coach Favorites",title:"Left Field Hit + Runner on 2nd",summary:"Relay home with every defender moving"},
    {id:"ground-r1",folder:"Coach Favorites",title:"Ground Ball + Runner on 1st",summary:"Middle-infield double play"},
    {id:"bunt-r1",folder:"Coach Favorites",title:"Bunt Defense + Runner on 1st",summary:"Corner charge and base coverage"},
    {id:"pickoff-third",folder:"Coach Favorites",title:"Pickoff Attempt at 3rd",summary:"Tag and backup coverage"},
    {id:"rundown-third-home",folder:"Coach Favorites",title:"Rundown: 3rd to Home",summary:"Close the lane with one throw"}
  ];
  const scenarioCatalog = [...featuredScenarios];
  runnerStates.forEach(state=>{
    outfieldZones.forEach(zone=>{
      scenarioCatalog.push({id:`fly-${state.id}-${zone.id}`,kind:"outfield",result:"fly",state,zone,folder:state.folder,title:`${state.label}: Fly Ball to ${zone.label}`,summary:"Catch, tag reads, cutoff and complete team rotation"});
      scenarioCatalog.push({id:`hit-${state.id}-${zone.id}`,kind:"outfield",result:"hit",state,zone,folder:state.folder,title:`${state.label}: Base Hit to ${zone.label}`,summary:"Relay target, base coverage and every backup"});
    });
  });
  [runnerStates[0],runnerStates[1],runnerStates[2],runnerStates[3]].forEach(state=>{
    ["first","third"].forEach(side=>scenarioCatalog.push({id:`bunt-${state.id}-${side}`,kind:"bunt",state,side,folder:"Bunt Defense",title:`${state.label}: Bunt Down ${side==="first"?"1st":"3rd"}`,summary:"Corners charge while the defense rotates behind the play"}));
  });
  [
    {id:"first-third-through",title:"1st & 3rd: Throw Through Cut",call:"through"},
    {id:"first-third-third",title:"1st & 3rd: Throw to 3rd",call:"third"},
    {id:"first-third-pitcher",title:"1st & 3rd: Throw to Pitcher",call:"pitcher"}
  ].forEach(item=>scenarioCatalog.push({...item,kind:"firstThird",folder:"1st & 3rd Plays",summary:"Catcher, middle infield and corner responsibilities"}));
  [
    ["lf-line","Left Field Line",{x:13,y:28}], ["lf-gap","Left-Center Gap",{x:34,y:15}], ["cf","Deep Center",{x:50,y:8}],
    ["rf-gap","Right-Center Gap",{x:66,y:15}], ["rf-line","Right Field Line",{x:87,y:28}]
  ].forEach(([id,label,hitPoint])=>["third","home"].forEach(target=>scenarioCatalog.push({id:`double-cut-${id}-${target}`,kind:"doubleCut",folder:"Double-Cuts",title:`Double Cut: ${label} to ${target==="third"?"3rd":"Home"}`,summary:"Two aligned relays, trail runner coverage and deep backup",hitPoint,target})));

  function activateRunners(state,includeBatter=false){
    state.runners.forEach(id=>design.positions[id].active=true);
    if(includeBatter) design.positions.BR.active=true;
  }
  function addAction(actions,type,entity,actor,to,phase,from=null){
    actions.push({type,entity,actor,from:from||copy(entity==="ball"?design.ball:design.positions[entity]),to:copy(to),phase});
  }
  function runnerAdvance(actions,id,to,phase){
    const previous=[...actions].reverse().find(action=>action.entity===id);
    const from=previous?previous.to:id==="R1"?spots.firstBase:id==="R2"?spots.secondBase:id==="R3"?spots.thirdBase:spots.rightHandedBox;
    addAction(actions,"tag",id,id,to,phase,from);
  }
  function targetForState(state,result){
    if(state.runners.includes("R3"))return spots.homeCover;
    if(state.runners.includes("R2"))return result==="fly"?spots.thirdBase:spots.homeCover;
    if(state.runners.includes("R1"))return result==="fly"?spots.secondBase:spots.thirdBase;
    return spots.secondBase;
  }
  function buildOutfieldPreset(entry){
    const {state,zone,result}=entry, actions=[],hitPoint=zone.point,target=targetForState(state,result);
    const isNoRunnerMiddleFly=result==="fly"&&state.id==="none"&&zone.id==="cf-middle";
    activateRunners(state,result==="hit");
    const side=hitPoint.x<45?"left":hitPoint.x>55?"right":"center";
    const cutoffId=side==="right"?"2B":"SS",coverSecond=cutoffId==="SS"?"2B":"SS";
    const cutoffPoint=midpoint(hitPoint,target);
    addAction(actions,"hit","ball","Batter",hitPoint,1,{...spots.homePlate});
    addAction(actions,"move",zone.fielder,zone.fielder,hitPoint,2);
    if(isNoRunnerMiddleFly){
      addAction(actions,"backup","RF","RF",{x:55,y:10},2);
      addAction(actions,"backup","LF","LF",{x:23,y:72},2);
    } else {
      const backupPoint=point(hitPoint.x+(50-hitPoint.x)*.22,Math.max(7,hitPoint.y-7));
      if(zone.support!==zone.fielder)addAction(actions,"backup",zone.support,zone.support,backupPoint,2);
      const remainingOutfielders=["LF","CF","RF"].filter(id=>id!==zone.fielder&&id!==zone.support);
      remainingOutfielders.forEach(id=>addAction(actions,"backup",id,id,behindTarget(hitPoint,target,.32),2));
    }
    addAction(actions,"cutoff",cutoffId,cutoffId,cutoffPoint,2);
    addAction(actions,"cover",coverSecond,coverSecond,{...spots.secondBase},2);
    addAction(actions,"cover","3B","3B",{...spots.thirdBase},2);
    addAction(actions,"cover","1B","1B",{...spots.firstBase},2);
    if(target===spots.homeCover){addAction(actions,"cover","C","C",{...spots.homeCover},2);addAction(actions,"backup","P","P",{x:55,y:96},2);}
    else {
      if(catcherCanBackFirst(hitPoint))addAction(actions,"backup","C","C",catcherFirstBackup(hitPoint),2);
      else addAction(actions,"cover","C","C",{...spots.homeCover},2);
      addAction(actions,"backup","P","P",point((target.x+50)/2,(target.y+68)/2),2);
    }
    if(result==="hit"){
      runnerAdvance(actions,"BR",spots.firstBase,2);
      if(state.runners.includes("R3"))runnerAdvance(actions,"R3",spots.homePlate,4);
      if(state.runners.includes("R2")){runnerAdvance(actions,"R2",spots.thirdBase,2);runnerAdvance(actions,"R2",spots.homePlate,4);}
      if(state.runners.includes("R1"))runnerAdvance(actions,"R1",spots.thirdBase,3);
    } else {
      if(state.runners.includes("R3"))runnerAdvance(actions,"R3",spots.homePlate,4);
      else if(state.runners.includes("R2"))runnerAdvance(actions,"R2",spots.thirdBase,4);
      else if(state.runners.includes("R1"))runnerAdvance(actions,"R1",spots.secondBase,4);
    }
    addAction(actions,"throw","ball",zone.fielder,cutoffPoint,3,hitPoint);
    addAction(actions,"throw","ball",cutoffId,target,4,cutoffPoint);
    design.actions=actions;
    setMeta(entry.title,isNoRunnerMiddleFly
      ?"Center fielder makes the play, right field backs up center, left field backs up third, and catcher stays home because the ball is not on the first-base side"
      :`${result==="fly"?"Catch and tag read":"Base hit read"}; ${cutoffId} aligns halfway to ${target===spots.homeCover?"home":target===spots.thirdBase?"third":"second"}, every base is covered, and outfielders back up the play`);
  }
  function buildBuntPreset(entry){
    const actions=[],downFirst=entry.side==="first",fieldPoint=downFirst?{x:57,y:79}:{x:43,y:79},fielder=downFirst?"1B":"3B";
    activateRunners(entry.state,true);
    addAction(actions,"hit","ball","Batter",fieldPoint,1,{...spots.homePlate});
    addAction(actions,"move","1B","1B",downFirst?fieldPoint:{x:56,y:77},2);
    addAction(actions,"move","3B","3B",downFirst?{x:44,y:77}:fieldPoint,2);
    addAction(actions,"backup","P","P",downFirst?{x:54,y:76}:{x:46,y:76},2);
    addAction(actions,"cover","2B","2B",{...spots.firstBase},2);
    addAction(actions,"cover","SS","SS",entry.state.runners.includes("R2")?spots.thirdBase:spots.secondBase,2);
    addAction(actions,"cover","C","C",{...spots.homeCover},2);
    addAction(actions,"backup","RF","RF",{x:82,y:68},2); addAction(actions,"backup","LF","LF",{x:18,y:70},2); addAction(actions,"backup","CF","CF",{x:50,y:34},2);
    entry.state.runners.forEach(id=>runnerAdvance(actions,id,id==="R3"?spots.homePlate:id==="R2"?spots.thirdBase:spots.secondBase,3));
    runnerAdvance(actions,"BR",spots.firstBase,3);
    addAction(actions,"throw","ball",fielder,spots.firstBase,4,fieldPoint);
    design.actions=actions;
    setMeta(entry.title,`${fielder} fields the bunt, second baseman covers first, shortstop protects the lead base, catcher owns home, and the outfield backs every overthrow lane`);
  }
  function buildFirstThirdPreset(entry){
    design.positions.R1.active=true;design.positions.R3.active=true;design.ball={...spots.homeCover};
    const actions=[];
    runnerAdvance(actions,"R1",spots.secondBase,1);
    addAction(actions,"cover","C","C",{...spots.homeCover},1);
    addAction(actions,"cover","3B","3B",{...spots.thirdBase},1);
    addAction(actions,"backup","P","P",{x:54,y:95},1);
    if(entry.call==="through"){
      addAction(actions,"cutoff","SS","SS",{x:47,y:55},1);addAction(actions,"cover","2B","2B",{...spots.secondBase},1);
      addAction(actions,"throw","ball","C",{x:47,y:55},2,spots.homeCover);runnerAdvance(actions,"R3",spots.homePlate,3);addAction(actions,"throw","ball","SS",spots.homeCover,3,{x:47,y:55});
    } else if(entry.call==="third"){
      addAction(actions,"cover","SS","SS",{x:26,y:70},1);addAction(actions,"cover","2B","2B",{...spots.secondBase},1);addAction(actions,"throw","ball","C",spots.thirdBase,2,spots.homeCover);
    } else {
      addAction(actions,"move","P","P",{x:50,y:72},1);addAction(actions,"cover","SS","SS",{...spots.secondBase},1);addAction(actions,"backup","2B","2B",{x:57,y:50},1);addAction(actions,"throw","ball","C",{x:50,y:72},2,spots.homeCover);
    }
    design.actions=actions;setMeta(entry.title,"Control the first-and-third play with a clear catcher call, protect home first, and keep every base and overthrow lane covered");
  }
  function buildDoubleCutPreset(entry){
    const actions=[],target=entry.target==="home"?spots.homeCover:spots.thirdBase,firstCut=midpoint(entry.hitPoint,target),secondCut=midpoint(firstCut,target);
    design.positions.R2.active=true;design.positions.BR.active=true;
    const fielder=entry.hitPoint.x<40?"LF":entry.hitPoint.x>60?"RF":"CF";
    addAction(actions,"hit","ball","Batter",entry.hitPoint,1,{...spots.homePlate});addAction(actions,"move",fielder,fielder,entry.hitPoint,2);
    addAction(actions,"cutoff","SS","SS",firstCut,2);addAction(actions,"cutoff","2B","2B",secondCut,2);
    ["LF","CF","RF"].filter(id=>id!==fielder).forEach((id,index)=>addAction(actions,"backup",id,id,point(entry.hitPoint.x+(index?10:-10),Math.max(5,entry.hitPoint.y-6)),2));
    addAction(actions,"cover","3B","3B",{...spots.thirdBase},2);addAction(actions,"cover","1B","1B",{...spots.firstBase},2);addAction(actions,"cover","C","C",{...spots.homeCover},2);addAction(actions,"backup","P","P",entry.target==="home"?{x:55,y:96}:{x:23,y:72},2);
    runnerAdvance(actions,"BR",spots.firstBase,2);runnerAdvance(actions,"R2",spots.thirdBase,3);if(entry.target==="home")runnerAdvance(actions,"R2",spots.homePlate,5);
    addAction(actions,"throw","ball",fielder,firstCut,3,entry.hitPoint);addAction(actions,"throw","ball","SS",secondCut,4,firstCut);addAction(actions,"throw","ball","2B",target,5,secondCut);
    design.actions=actions;setMeta(entry.title,"Two relays line up between the ball and the target while the corners cover, pitcher backs the final base, and both remaining outfielders protect the gaps");
  }
  function renderScenarioCatalog(){
    const select=document.getElementById("scenario-preset-select"),container=document.getElementById("built-in-scenario-folders");
    const generated=scenarioCatalog.filter(item=>item.kind);
    const folders=[...new Set(scenarioCatalog.map(item=>item.folder))];
    folders.forEach(folder=>{
      const items=scenarioCatalog.filter(item=>item.folder===folder);
      if(select&&folder!=="Coach Favorites"){
        const group=document.createElement("optgroup");group.label=folder;
        items.forEach(item=>{const option=document.createElement("option");option.value=item.id;option.textContent=item.title;group.appendChild(option);});select.appendChild(group);
      }
      if(container){
        const section=document.createElement("section");section.className="play-folder";const heading=document.createElement("h3");heading.textContent=folder;section.appendChild(heading);
        items.forEach(item=>{const button=document.createElement("button");button.type="button";button.dataset.preset=item.id;const title=document.createElement("span");title.textContent=item.title;const small=document.createElement("small");small.textContent=item.summary;button.append(title,small);section.appendChild(button);});container.appendChild(section);
      }
    });
    return generated.length;
  }
  function applyPreset(name) {
    stopPlayback(); recording=false;syncRecordUI();design=freshDesign(); selected=null; selectedStep=null; actionMode="append"; app.classList.remove("insert-mode","redraw-mode");
    const catalogEntry=scenarioCatalog.find(item=>item.id===name&&item.kind);
    if(catalogEntry){
      if(catalogEntry.kind==="outfield")buildOutfieldPreset(catalogEntry);
      else if(catalogEntry.kind==="bunt")buildBuntPreset(catalogEntry);
      else if(catalogEntry.kind==="firstThird")buildFirstThirdPreset(catalogEntry);
      else if(catalogEntry.kind==="doubleCut")buildDoubleCutPreset(catalogEntry);
    } else if(name==="fly-r3") {
      design.positions.R3.active=true;
      const catchPoint={x:25,y:28};
      const cutoffPoint=midpoint(catchPoint,spots.homeCover);
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{...spots.homePlate},to:catchPoint},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:22},to:catchPoint},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:31,y:23}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:61,y:47}},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:41,y:49},to:cutoffPoint},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{...spots.secondBase}},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{...spots.thirdBase}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{...spots.firstBase}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:55,y:96}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"throw",entity:"ball",actor:"LF",from:catchPoint,to:cutoffPoint},
        {type:"tag",entity:"R3",actor:"R3",from:{...spots.thirdBase},to:{...spots.homePlate}},
        {type:"throw",entity:"ball",actor:"SS",from:cutoffPoint,to:{...spots.homeCover}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,2,2,2,2,2,2,2,2,2,3,4,4][index]);
      setMeta("Fly Ball, Runner on Third","Center backs up left, right backs up second, short lines up halfway to home, the relay beats the tagging runner, and pitcher backs the plate");
    } else if(name==="ground-r1") {
      design.positions.R1.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{...spots.homePlate},to:{x:39,y:54}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"move",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:39,y:54}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"tag",entity:"R1",actor:"R1",from:{...spots.firstBase},to:{...spots.secondBase}},
        {type:"throw",entity:"ball",actor:"SS",from:{x:39,y:54},to:{x:50,y:44}},
        {type:"throw",entity:"ball",actor:"2B",from:{...spots.secondBase},to:{...spots.firstBase}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,2,2,2,2,3,4][index]);
      setMeta("Ground Ball, Runner on First","Middle infield double-play responsibilities");
    } else if(name==="bunt-r1") {
      design.positions.R1.active=true; design.positions.BR.active=true;
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{...spots.homePlate},to:{x:44,y:79}},
        {type:"move",entity:"3B",actor:"3B",from:{x:32,y:61},to:{x:44,y:79}},
        {type:"move",entity:"1B",actor:"1B",from:{x:68,y:61},to:{x:56,y:77}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{...spots.firstBase}},
        {type:"cover",entity:"SS",actor:"SS",from:{x:41,y:49},to:{...spots.thirdBase}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:38,y:76}},
        {type:"backup",entity:"LF",actor:"LF",from:{x:24,y:22},to:{x:20,y:70}},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:50,y:34}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:82,y:68}},
        {type:"tag",entity:"R1",actor:"R1",from:{...spots.firstBase},to:{...spots.secondBase}},
        {type:"tag",entity:"BR",actor:"BR",from:{...spots.rightHandedBox},to:{...spots.firstBase}},
        {type:"throw",entity:"ball",actor:"3B",from:{x:44,y:79},to:{...spots.firstBase}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,2,2,2,2,2,2,2,2,2,2,2,3][index]);
      setMeta("Bunt Defense, Runner on First","Third baseman fields, first baseman charges, second covers first, short covers third, catcher protects home, and all three outfielders back up the rotation");
    } else if(name==="left-single") {
      design.positions.BR.active=true;
      const hitPoint={x:22,y:33};
      const cutoffPoint=midpoint(hitPoint,spots.secondBase);
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{...spots.homePlate},to:hitPoint},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:22},to:hitPoint},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:29,y:27}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:behindTarget(hitPoint,spots.secondBase,.4)},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:41,y:49},to:cutoffPoint},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{...spots.thirdBase}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{...spots.secondBase}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{...spots.firstBase}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:53,y:56}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"tag",entity:"BR",actor:"BR",from:{...spots.rightHandedBox},to:{...spots.firstBase}},
        {type:"throw",entity:"ball",actor:"LF",from:hitPoint,to:cutoffPoint},
        {type:"throw",entity:"ball",actor:"SS",from:cutoffPoint,to:{...spots.secondBase}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,2,2,2,2,2,2,2,2,2,2,3,4][index]);
      setMeta("Single to Left Field","Shortstop lines up halfway from left to second, second covers the bag, right backs up the throw, center backs left, and catcher stays home on the left-side hit");
    } else if(name==="left-r2") {
      design.positions.R2.active=true; design.positions.BR.active=true;
      const hitPoint={x:20,y:31};
      const cutoffPoint=midpoint(hitPoint,spots.homeCover);
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{...spots.homePlate},to:hitPoint},
        {type:"move",entity:"LF",actor:"LF",from:{x:24,y:22},to:hitPoint},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:28,y:25}},
        {type:"backup",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:61,y:47}},
        {type:"cutoff",entity:"SS",actor:"SS",from:{x:41,y:49},to:cutoffPoint},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{...spots.thirdBase}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{...spots.firstBase}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:55,y:96}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"tag",entity:"R2",actor:"R2",from:{...spots.secondBase},to:{...spots.thirdBase}},
        {type:"tag",entity:"BR",actor:"BR",from:{...spots.rightHandedBox},to:{...spots.firstBase}},
        {type:"throw",entity:"ball",actor:"LF",from:hitPoint,to:cutoffPoint},
        {type:"tag",entity:"R2",actor:"R2",from:{...spots.thirdBase},to:{...spots.homePlate}},
        {type:"throw",entity:"ball",actor:"SS",from:cutoffPoint,to:{...spots.homeCover}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,2,2,2,2,2,2,2,2,2,2,2,3,4,4][index]);
      setMeta("Left Field Hit, Runner on Second","Left fields the ball, short lines up the throw home, pitcher backs up the plate, every base is covered, and both runners advance");
    } else if(name==="right-single") {
      design.positions.BR.active=true;
      const hitPoint={x:78,y:33};
      const cutoffPoint=midpoint(hitPoint,spots.secondBase);
      design.actions=[
        {type:"hit",entity:"ball",actor:"Batter",from:{...spots.homePlate},to:hitPoint},
        {type:"move",entity:"RF",actor:"RF",from:{x:76,y:22},to:hitPoint},
        {type:"backup",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:71,y:27}},
        {type:"backup",entity:"LF",actor:"LF",from:{x:24,y:22},to:behindTarget(hitPoint,spots.secondBase,.4)},
        {type:"cutoff",entity:"2B",actor:"2B",from:{x:59,y:49},to:cutoffPoint},
        {type:"cover",entity:"SS",actor:"SS",from:{x:41,y:49},to:{...spots.secondBase}},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{...spots.thirdBase}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{...spots.firstBase}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:47,y:56}},
        {type:"backup",entity:"C",actor:"C",from:{...spots.catcherStart},to:catcherFirstBackup(hitPoint)},
        {type:"tag",entity:"BR",actor:"BR",from:{...spots.rightHandedBox},to:{...spots.firstBase}},
        {type:"throw",entity:"ball",actor:"RF",from:hitPoint,to:cutoffPoint},
        {type:"throw",entity:"ball",actor:"2B",from:cutoffPoint,to:{...spots.secondBase}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,2,2,2,2,2,2,2,2,2,2,3,4][index]);
      setMeta("Single to Right Field","Second baseman lines up halfway from right to second, short covers the bag, left backs up the throw, center backs right, and catcher backs first");
    } else if(name==="pickoff-third") {
      design.positions.R3={x:36,y:74,runner:true,active:true}; design.ball={x:50,y:68};
      design.actions=[
        {type:"move",entity:"P",actor:"P",from:{x:50,y:68},to:{x:47,y:70}},
        {type:"cover",entity:"3B",actor:"3B",from:{x:32,y:61},to:{...spots.thirdBase}},
        {type:"backup",entity:"SS",actor:"SS",from:{x:41,y:49},to:{x:24,y:72}},
        {type:"backup",entity:"LF",actor:"LF",from:{x:24,y:22},to:{x:17,y:73}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{...spots.firstBase}},
        {type:"move",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:39,y:22}},
        {type:"move",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:66,y:25}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"tag",entity:"R3",actor:"R3",from:{x:36,y:74},to:{...spots.thirdBase}},
        {type:"throw",entity:"ball",actor:"P",from:{x:47,y:70},to:{...spots.thirdBase}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,1,1,1,1,1,1,1,1,2,2][index]);
      setMeta("Pickoff Attempt at Third","Pitcher uses a quick step and throw, third applies the tag, short and left back up third, middle infielders protect their bases, and catcher stays home");
    } else if(name==="rundown-third-home") {
      design.positions.R3={x:40,y:78,runner:true,active:true}; design.ball={x:30,y:68};
      design.actions=[
        {type:"move",entity:"3B",actor:"3B",from:{x:32,y:61},to:{x:37,y:76}},
        {type:"cover",entity:"C",actor:"C",from:{...spots.catcherStart},to:{...spots.homeCover}},
        {type:"cover",entity:"SS",actor:"SS",from:{x:41,y:49},to:{...spots.thirdBase}},
        {type:"backup",entity:"P",actor:"P",from:{x:50,y:68},to:{x:55,y:94}},
        {type:"backup",entity:"LF",actor:"LF",from:{x:24,y:22},to:{x:20,y:74}},
        {type:"cover",entity:"2B",actor:"2B",from:{x:59,y:49},to:{x:50,y:44}},
        {type:"cover",entity:"1B",actor:"1B",from:{x:68,y:61},to:{...spots.firstBase}},
        {type:"move",entity:"CF",actor:"CF",from:{x:50,y:13},to:{x:39,y:23}},
        {type:"move",entity:"RF",actor:"RF",from:{x:76,y:22},to:{x:65,y:25}},
        {type:"tag",entity:"R3",actor:"R3",from:{x:40,y:78},to:{x:46,y:85}},
        {type:"throw",entity:"ball",actor:"3B",from:{x:37,y:76},to:{...spots.homeCover}},
        {type:"move",entity:"C",actor:"C",from:{...spots.homeCover},to:{x:39,y:77}},
        {type:"tag",entity:"R3",actor:"R3",from:{x:46,y:85},to:{x:34,y:72}},
        {type:"throw",entity:"ball",actor:"C",from:{x:39,y:77},to:{...spots.thirdBase}},
        {type:"tag",entity:"SS",actor:"SS",from:{...spots.thirdBase},to:{x:34,y:72}}
      ];
      design.actions.forEach((action,index)=>action.phase=[1,1,1,1,1,1,1,1,1,2,2,3,3,4,4][index]);
      setMeta("Rundown: Third to Home","Third and catcher close the lane with one throw, short rotates to third, pitcher backs up home, left backs third, and every remaining defender protects a base or backup lane");
    } else setMeta(""," ");
    const picker=document.getElementById("scenario-preset-select"); if(picker) picker.value=name;
    status.textContent=name==="empty" ? "New Scenario" : document.getElementById("scenario-title").value;
    instruction.textContent="Press Play, choose a step, or press Record and drag a marker."; renderAll(0);progressBar.style.width="0%";
  }
  function setMeta(titleValue,situationValue,teamId=null,id="") {
    const title=document.getElementById("scenario-title"),situation=document.getElementById("scenario-situation");
    if(title) title.value=titleValue;
    if(situation) situation.value=situationValue.trim();
    if(!editable) return;
    document.getElementById("scenario-id").value=id;
    if(teamId) document.getElementById("scenario-team").value=String(teamId);
  }
  function syncForm(){if(editable)document.getElementById("design-json").value=JSON.stringify(design);}
  function loadScenario(row) {
    stopPlayback();recording=false;syncRecordUI();design=normalizeDesign(row.design); selected=null; selectedStep=null; actionMode="append"; app.classList.remove("insert-mode","redraw-mode");
    setMeta(row.title,row.situation,row.team_id,row.id); status.textContent=row.title; instruction.textContent=row.situation||"Press Play Scenario to watch the responsibilities.";
    renderAll(0);progressBar.style.width="0%";
  }

  const libraryDialog=document.getElementById("play-library-dialog");
  const guideDialog=document.getElementById("builder-guide-dialog");
  function openDialog(dialog){if(!dialog)return;if(typeof dialog.showModal==="function")dialog.showModal();else dialog.setAttribute("open","");}
  function closeDialog(dialog){if(!dialog)return;if(typeof dialog.close==="function")dialog.close();else dialog.removeAttribute("open");}

  field.addEventListener("pointerdown",beginDrawing);
  field.addEventListener("pointermove",event=>{moveDraggedMarker(event);moveDrawing(event);});
  field.addEventListener("pointerup",handleField);
  playButton.addEventListener("click",play);
  fieldPlayButton.addEventListener("click",play);
  document.getElementById("restart-button").addEventListener("click",()=>previewThrough(0));
  document.getElementById("field-restart-button")?.addEventListener("click",()=>previewThrough(0));
  document.getElementById("play-library-button")?.addEventListener("click",()=>openDialog(libraryDialog));
  document.getElementById("builder-guide-button")?.addEventListener("click",()=>openDialog(guideDialog));
  document.querySelectorAll(".builder-dialog .dialog-close").forEach(button=>button.addEventListener("click",()=>closeDialog(button.closest("dialog"))));
  document.querySelector(".guide-start")?.addEventListener("click",()=>closeDialog(guideDialog));
  document.querySelectorAll("[data-library-tab]").forEach(button=>button.addEventListener("click",()=>{document.querySelectorAll("[data-library-tab]").forEach(item=>item.classList.toggle("active",item===button));document.querySelectorAll("[data-library-panel]").forEach(panel=>panel.classList.toggle("active",panel.dataset.libraryPanel===button.dataset.libraryTab));}));
  renderScenarioCatalog();
  document.getElementById("scenario-library-search")?.addEventListener("input",event=>{
    const query=event.target.value.trim().toLowerCase();
    const terms=query.split(/\s+/).filter(Boolean);
    document.querySelectorAll("#built-in-scenario-folders .play-folder").forEach(folder=>{
      let visible=0;
      folder.querySelectorAll("button[data-preset]").forEach(button=>{const text=button.textContent.toLowerCase();const match=terms.every(term=>text.includes(term));button.hidden=!match;if(match)visible+=1;});
      folder.hidden=visible===0;
    });
  });
  document.querySelectorAll("[data-preset]").forEach(button=>button.addEventListener("click",()=>{applyPreset(button.dataset.preset);closeDialog(libraryDialog);}));
  document.querySelectorAll("[data-saved-scenario]").forEach(button=>button.addEventListener("click",()=>{const row=saved.find(entry=>String(entry.id)===button.dataset.savedScenario);if(row)loadScenario(row);closeDialog(libraryDialog);}));
  window.addEventListener("resize",renderPaths);
  document.getElementById("scenario-preset-select")?.addEventListener("change",event=>applyPreset(event.target.value));
  if(editable){
    document.querySelectorAll(".tool-button[data-tool]").forEach(button=>button.addEventListener("click",()=>{setTool(button.dataset.tool);instruction.textContent=tool==="hit" ? "Choose where the ball is hit." : tool==="setup" ? "Drag a marker to set its starting position." : recording ? `Drag a player to record ${names[tool].toLowerCase()}.` : `Select a player, then choose where they ${names[tool].toLowerCase()}.`;}));
    document.querySelectorAll(".runner-toggle").forEach(button=>button.addEventListener("click",()=>{const runner=design.positions[button.dataset.runner];runner.active=!runner.active;renderAll();}));
    recordButton?.addEventListener("click",toggleRecording);
    document.getElementById("direct-move-button")?.addEventListener("click",()=>{setTool(recording?"move":"setup");instruction.textContent=recording?"Drag a player or ball to record the next movement.":"Drag any marker to adjust the starting setup.";});
    document.getElementById("field-draw-button")?.addEventListener("click",()=>{setTool("draw");instruction.textContent="Drag anywhere on the field to draw a coaching path.";});
    document.getElementById("field-undo-button")?.addEventListener("click",undoLast);
    document.getElementById("insert-button").addEventListener("click",()=>{if(selectedStep===null){instruction.textContent="Select a timeline step first.";return;}actionMode=actionMode==="insert"?"append":"insert";app.classList.toggle("insert-mode",actionMode==="insert");app.classList.remove("redraw-mode");instruction.textContent=actionMode==="insert"?"The next action will be inserted after the selected step.":"New actions will be added to the end.";});
    document.getElementById("redraw-button").addEventListener("click",()=>{if(selectedStep===null){instruction.textContent="Select a timeline step first.";return;}const action=design.actions[selectedStep];tool=action.type;selected=action.type==="hit"?null:action.actor;actionMode="replace";app.classList.add("redraw-mode");app.classList.remove("insert-mode");document.querySelectorAll(".tool-button[data-tool]").forEach(item=>item.classList.toggle("active",item.dataset.tool===tool));previewThrough(selectedStep);instruction.textContent=`Choose a new destination for step ${selectedStep+1}.`;});
    document.getElementById("undo-button").addEventListener("click",undoLast);
    document.getElementById("clear-button").addEventListener("click",()=>{design.actions=[];selectedStep=null;actionMode="append";app.classList.remove("insert-mode","redraw-mode");renderAll();});
    document.getElementById("scenario-form").addEventListener("submit",syncForm);
  } else app.classList.add("viewer");
  document.querySelectorAll(".library-item").forEach(item=>item.querySelector(".open-scenario").addEventListener("click",()=>{const row=saved.find(entry=>String(entry.id)===item.dataset.scenarioId);if(row)loadScenario(row);}));

  const selectedId=Number(app.dataset.selectedId||0),selectedScenario=saved.find(row=>row.id===selectedId);
  if(selectedScenario) loadScenario(selectedScenario); else if(saved.length&&!editable) loadScenario(saved[0]); else applyPreset("fly-r3");
})();
