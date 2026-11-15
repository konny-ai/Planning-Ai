(function () {
"use strict";
const ids = ["enabled","provider","model","endpoint","threshold","external_approved"];
const $ = id => document.getElementById(id);
const show = value => {$("result").textContent = JSON.stringify(value, null, 2);};
async function request(path, body) {
  const options = body === undefined ? {cache:"no-store"} :
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "HTTP " + response.status);
  return result;
}
function config() {
  return {enabled:$("enabled").checked,provider:$("provider").value,
    model:$("model").value,endpoint:$("endpoint").value,
    threshold:Number($("threshold").value),external_approved:$("external_approved").checked};
}
function fill(value) {
  for (const id of ids) {
    if (id === "enabled" || id === "external_approved") $(id).checked = value[id];
    else $(id).value = value[id];
  }
}
async function run(fn) { try {await fn();} catch(error) {show({error:String(error.message || error)});} }
document.addEventListener("DOMContentLoaded", () => {
  run(async()=>fill(await request("/api/decision-model/config")));
  $("decision-form").addEventListener("submit",event=>{event.preventDefault();
    run(async()=>show({saved:await request("/api/decision-model/config",config())}));});
  $("analyze").addEventListener("click",()=>run(async()=>{
    show(await request("/api/decision-model/evaluate",
      {approved:$("approved").checked,question:$("question").value}));}));
  $("benchmark").addEventListener("click",()=>run(async()=>{
    show(await request("/api/decision-model/benchmark",{approved:$("approved").checked}));}));
});
})();