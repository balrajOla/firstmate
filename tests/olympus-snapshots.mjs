// Capture the actual shared terminal renderer against an isolated running demo.
// Usage: node tests/olympus-snapshots.mjs [output-directory]
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { Dashboard } from "../packages/olympus-pi/view.mjs";
import { client } from "../packages/olympus-pi/client.mjs";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const bin=process.env.OLYMPUS_TEST_BIN || resolve(root,"target/debug/olympus");
const output=resolve(process.argv[2] || resolve(root,"docs/assets"));
const home=mkdtempSync(resolve(tmpdir(),"olympus-snapshots-"));
const invoke=(...args)=>JSON.parse(execFileSync(bin,["--home",home,...args],{encoding:"utf8"}));
const action=r=>invoke("request",JSON.stringify(r));
const escape=s=>s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
let daemon,view;
try{
  invoke("demo");
  daemon=spawn(resolve(dirname(bin),"olympusd"),["--home",home],{stdio:"ignore"});
  for(let i=0;i<100;i++) {try{invoke("status");break;}catch{await new Promise(r=>setTimeout(r,50));}}
  const snapshot=invoke("status");const project=snapshot.projects[0].id;
  for(const [i,t] of snapshot.tasks.entries()) if(i<8) action({action:"task.due",task:t.id,due:Math.floor(Date.now()/1000)+(i-2)*3600});
  for(const [title,cadence] of [["Daily project health","daily"],["Weekly delivery review","weekly"],["Weekday acceptance checks","weekdays"]]) action({action:"schedule.create",project,title,requirements:"Inspect current evidence and return separate project outcomes",cadence,time:"09:00",timezone:"Europe/London"});
  const request=client({bin,home});view=new Dashboard(request);
  await view.refresh();mkdirSync(output,{recursive:true});
  const frames={};
  for(const [tab,name] of ["live","projects","crew","queue","memory","health","history","today","week","recurring"].entries()){
    await view.setTab(tab);frames[name]=view.render(160,44);
  }
  await view.setTab(1);view.selected=Math.max(0,view.items().findIndex(p=>p.id===snapshot.responses[0]?.project));await view.key("o");frames.outcomes=view.render(160,44);
  await view.key("\x1b");await view.setTab(0);
  for(let i=0;i<3;i++) frames[`live-frame-${i}`]=view.render(160,44,Date.now()+i*250);
  for(const [name,lines] of Object.entries(frames)){
    const text=lines.map((line,i)=>`<text x="24" y="${36+i*23}" fill="${i===0?"#75c4ff":/blocked|BLOCK|UNKNOWN|overdue|failed/.test(line)?"#ffb66c":/accepted|DONE|verified|WORK/.test(line)?"#8ddbb3":"#d8e2f0"}">${escape(line)}</text>`).join("\n");
    writeFileSync(resolve(output,`olympus-${name}.svg`),`<svg xmlns="http://www.w3.org/2000/svg" width="1648" height="1060" viewBox="0 0 1648 1060"><rect width="1648" height="1060" rx="16" fill="#101827"/><g font-family="DejaVu Sans Mono,monospace" font-size="16" xml:space="preserve">${text}</g></svg>\n`);
  }
  writeFileSync(resolve(output,"olympus-snapshots.json"),JSON.stringify({generated_at:new Date().toISOString(),source:"Running synthetic demo and production Dashboard renderer",columns:160,rows:44,frames},null,2)+"\n");
  process.stdout.write(`Captured ${Object.keys(frames).length} terminal views in ${output}\n`);
}finally{
  view?.dispose();
  if(daemon){try{invoke("stop");}catch{} if(daemon.exitCode===null){await Promise.race([new Promise(r=>daemon.once("exit",r)),new Promise(r=>setTimeout(r,5000))]);if(daemon.exitCode===null)daemon.kill();}}
  rmSync(home,{recursive:true,force:true});
}
