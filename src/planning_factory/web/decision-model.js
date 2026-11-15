/* Decision Center: no external scripts, no browser-side model credentials. */
(function () {
"use strict";
const $ = id => document.getElementById(id);
const $all = selector => [...document.querySelectorAll(selector)];
const modelIds = ["enabled","provider","model","endpoint","threshold","external_approved"];
const ruleIds = ["profile","max_candidates","max_seconds","local_search_budget",
                 "risk_threshold_hours","capacity_threshold_pct","min_historical_samples","use_ai_advice"];
const allPolicies = ["priority","earliest_due","shortest_processing","longest_processing",
                     "min_slack","critical_ratio","atc"];
const fields = ["late_orders","incomplete_orders","unscheduled_units",
                "max_delay_hours","max_resource_utilization_pct"];
let currentReport = null;
const status = text => { $("dc-notice").textContent = text; };
const text = (tag, value, className) => {
  const node = document.createElement(tag);
  node.textContent = String(value == null ? "—" : value);
  if (className) node.className = className;
  return node;
};
const add = (parent, node) => { parent.appendChild(node); return node; };
function select(values, selected) {
  const node = document.createElement("select");
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === selected;
    node.appendChild(option);
  }
  return node;
}
async function request(path, body) {
  const options = body === undefined ? {cache:"no-store"} :
    {method:"POST", headers:{"Content-Type":"application/json"},
     body:JSON.stringify(body), cache:"no-store"};
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error || "HTTP " + response.status);
  return value;
}
async function safe(action) {
  try { status(""); await action(); }
  catch (error) { status("Operazione non riuscita: " + String(error.message || error)); }
}
function activate(name) {
  $all(".dc-tab").forEach(t => t.setAttribute("aria-selected",String(t.dataset.tab === name)));
  $all(".dc-pane").forEach(p => {p.hidden = p.id !== "pane-" + name;});
}
function loadModel(value) {
  for(const id of modelIds) {
    if(id === "enabled" || id === "external_approved") $(id).checked = value[id];
    else $(id).value = value[id];
  }
}
function modelConfig() {
  return {enabled:$("enabled").checked,provider:$("provider").value,
          model:$("model").value,endpoint:$("endpoint").value,
          threshold:Number($("threshold").value),
          external_approved:$("external_approved").checked};
}
function renderPolicies(active) {
  const box = $("policies-list"); box.replaceChildren();
  for(const policy of allPolicies) {
    const label = document.createElement("label"), input = document.createElement("input");
    input.type="checkbox";input.dataset.policy=policy;
    input.checked=active.includes(policy);input.disabled=policy==="priority";
    label.append(input, document.createTextNode(" " + policy)); box.appendChild(label);
  }
}
function ruleRow(value) {
  const row = document.createElement("tr");
  function cell(node){ const td=document.createElement("td");td.appendChild(node);row.appendChild(td);return node; }
  const name = document.createElement("input");name.value=value.name || "";name.maxLength=60;
  name.placeholder="nome_univoco";name.dataset.field="name";cell(name);
  const field = select(fields,value.field || fields[0]);field.dataset.field="field";cell(field);
  const operator = select(["gte","lte","eq"],value.operator || "gte");operator.dataset.field="operator";cell(operator);
  const threshold = document.createElement("input");threshold.type="number";threshold.min="0";threshold.max="100000";
  threshold.step="any";threshold.value=value.value == null ? 1 : value.value;threshold.dataset.field="value";cell(threshold);
  const profile = select(["delivery","balanced","stability"],value.profile || "delivery");
  profile.dataset.field="profile";cell(profile);
  const remove = document.createElement("button");remove.type="button";remove.textContent="×";
  remove.setAttribute("aria-label","Rimuovi regola");
  remove.addEventListener("click",()=>row.remove());cell(remove);
  $("rules-rows").appendChild(row);
}
function loadRules(rules) {
  for (const id of ruleIds) {
    if (id === "use_ai_advice") $(id).checked=rules[id];
    else $(id).value=rules[id];
  }
  renderPolicies(rules.policies);
  $("rules-rows").replaceChildren();
  for(const rule of rules.decision_rules) ruleRow(rule);
}
function rulesPayload() {
  const rows=$all("#rules-rows tr").map(row => {
    const get = name => row.querySelector('[data-field="'+name+'"]').value;
    return {name:get("name"),field:get("field"),operator:get("operator"),
            value:Number(get("value")),profile:get("profile")};
  });
  return {schema_version:1,profile:$("profile").value,
    policies:allPolicies.filter(name => name==="priority" ||
      document.querySelector('[data-policy="'+name+'"]').checked),
    max_candidates:Number($("max_candidates").value),
    max_seconds:Number($("max_seconds").value),
    local_search_budget:Number($("local_search_budget").value),
    risk_threshold_hours:Number($("risk_threshold_hours").value),
    capacity_threshold_pct:Number($("capacity_threshold_pct").value),
    min_historical_samples:Number($("min_historical_samples").value),
    use_ai_advice:$("use_ai_advice").checked,decision_rules:rows};
}
function displayJSON(id,value) { const node=$(id);node.hidden=false;node.textContent=JSON.stringify(value,null,2); }
function renderComparison(report) {
  currentReport = report;
  const root=$("comparison");root.replaceChildren();
  add(root,text("h3","Profilo scelto: "+report.profile));
  const note = add(root,text("p", "Decisione: "+report.decision_id+" · "+report.candidate_count+
    " alternative verificate · vincitore "+report.winner_policy, "dc-muted"));
  if(report.matched_rule) add(root,text("p","Regola applicata: "+report.matched_rule));
  if(report.ai_influenced_profile) add(root,text("p","Profilo suggerito dal modello AI, verificato con i piani candidati"));
  const kpis=add(root,document.createElement("div"));kpis.className="dc-kpis";
  const summary=report.risk.summary;
  for(const [label,value] of [["Commesse in ritardo",summary.late_orders],
    ["Lavorazioni non schedulate",summary.unscheduled_units],
    ["Ritardo massimo",summary.max_delay_hours+" h"],
    ["Utilizzo massimo",summary.max_resource_utilization_pct === null?"n.d.":summary.max_resource_utilization_pct+" %"]]) {
    const item=add(kpis,document.createElement("div"));item.className="dc-kpi";
    add(item,text("strong",value));add(item,text("span",label));
  }
  const hist=report.historical_risk;
  add(root,text("p",hist.status==="provisional"?
    "Rischio storico esplorativo: "+Math.round(hist.probability*100)+"% (n="+hist.sample_count+
    ", Brier="+hist.brier_score+"). Stima non garantita.":
    "Rischio storico: "+hist.status+"; nessuna probabilità utilizzabile.","dc-muted"));
  if(report.risk.signals.length){
    add(root,text("h3","Segnali di rischio"));
    const list=add(root,document.createElement("ul"));
    for(const signal of report.risk.signals) add(list,text("li",signal.code+": "+signal.reason));
  }
  add(root,text("h3","Piani candidati verificati"));
  const scroll=add(root,document.createElement("div"));scroll.className="dc-scroll";
  const table=add(scroll,document.createElement("table"));table.className="dc-table";
  const head=add(table,document.createElement("thead"));const heading=add(head,document.createElement("tr"));
  for(const label of ["Strategia","Operaz. senza piano","Consegne in ritardo","Ritardo (h)","Cambio operaz.","Cambio risorse"]) add(heading,text("th",label));
  const body=add(table,document.createElement("tbody"));
  for(const item of report.candidates) {
    const tr=add(body,document.createElement("tr"));
    const mark=item.policy===report.winner_policy?" ✓":"";
    for(const v of [item.policy+mark,item.unscheduled_operations,item.late_orders,
      item.total_tardiness_hours,item.changed_operations,item.resource_changes]) add(tr,text("td",v));
  }
  if(report.rejected.length) add(root,text("p","Alternative interrotte o non valide: "+JSON.stringify(report.rejected),"dc-muted"));
  add(root,text("p","Risultati consultivi. Nessuna modifica al piano in uso; approvazione richiesta.","dc-warning"));
  const noteField=add(root,document.createElement("textarea"));noteField.id="review-note";
  noteField.rows=2;noteField.maxLength=500;noteField.placeholder="Motivazione della revisione (facoltativa)";
  noteField.style.width="100%";
  const actions=add(root,document.createElement("div"));actions.className="dc-actions";
  for(const [action,label] of [["approve","Approva proposta"],["reject","Rifiuta proposta"]]){
    const btn=add(actions,text("button",label));btn.type="button";
    btn.className="btn "+(action==="approve"?"btn-primary":"btn-muted");
    btn.addEventListener("click",()=>safe(async()=>{
      const result=await request("/api/decision-center/review",
        {decision_id:report.decision_id,expected_digest:report.scenario_digest,
         action,note:noteField.value});
      status("Revisione registrata: "+result.status+" — nessuna applicazione automatica");
      for(const button of actions.querySelectorAll("button")) button.disabled=true;
    }));
  }
}
function renderReviews(rows) {
  const root=$("reviews-result");root.replaceChildren();
  if(!rows.length){add(root,text("p","Nessuna revisione registrata."));return;}
  const table=add(root,document.createElement("table"));table.className="dc-table";
  const head=add(table,document.createElement("tr"));
  for(const title of ["Decisione","Piano proposto","Esito","Revisore","Data"]) add(head,text("th",title));
  for(const row of rows){
    const tr=add(table,document.createElement("tr"));
    for(const val of [row.decision_id,row.winner_policy,row.status,row.actor,row.reviewed_at])
      add(tr,text("td",val));
  }
}
document.addEventListener("DOMContentLoaded",()=>{
  $all(".dc-tab").forEach(t=>t.addEventListener("click",()=>activate(t.dataset.tab)));
  $("decision-form").addEventListener("submit",event=>{event.preventDefault();safe(async()=>{
    await request("/api/decision-model/config",modelConfig());status("Configurazione provider salvata.");
  });});
  $("rules-form").addEventListener("submit",event=>{event.preventDefault();safe(async()=>{
    const value=await request("/api/decision-center/rules",rulesPayload());
    loadRules(value);status("Politiche e regole salvate.");
  });});
  $("add-rule").addEventListener("click",()=>{if($all("#rules-rows tr").length<16)
    ruleRow({name:"nuova_"+($all("#rules-rows tr").length+1)});});
  $("benchmark").addEventListener("click",()=>safe(async()=>{
    displayJSON("benchmark-result",await request("/api/decision-model/benchmark",{approved:true}));
  }));
  $("run-comparison").addEventListener("click",()=>safe(async()=>{
    status("Confronto in esecuzione. Il piano esistente rimane invariato.");
    const report=await request("/api/decision-center/recommend",
      {ai_approved:$("ai_approved").checked});
    renderComparison(report);status("Confronto completato. Tutti i candidati mostrati sono verificati.");
  }));
  $("refresh-reviews").addEventListener("click",()=>safe(async()=>{
    renderReviews((await request("/api/decision-center/reviews")).reviews);
  }));
  safe(async()=>{loadModel(await request("/api/decision-model/config"));});
  safe(async()=>{loadRules(await request("/api/decision-center/rules"));});
});
})();