// Published image HTTPS, real daemon/setup and fictional engine; no intercepted application routes.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, rmSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const { chromium, expect: baseExpect } = createRequire(new URL("../web/package.json", import.meta.url))("@playwright/test");
const expect=baseExpect.configure({timeout:30000});
const cfg=JSON.parse(readFileSync(process.argv[2],"utf8"));
const result={passed:false,viewports:[],fixtures:"external engine, installation/authentication observations only"};
const shell=(text)=>"'"+text.replaceAll("'", "'\\''")+"'";
const control=(action,value={})=> {
  const args=["python3","input/source/tests/container_browser_probe.py",action,JSON.stringify(value)];
  const output=execFileSync("ssh",[...cfg.ssh,"ubuntu@127.0.0.1",args.map(shell).join(" ")],{encoding:"utf8",timeout:360000});
  return JSON.parse(output.trim().split("\n").at(-1));
};
let prepared=false;
try {
  result.image=control("prepare"); prepared=true;
  for (const [name,viewport] of [["phone",{width:390,height:844}],["desktop",{width:1440,height:900}]]) {
    const instance="browser-"+name;
    const invoke=(action,values={})=>control(action,{instance,...values});
    const opened=invoke("start");
    const home=mkdtempSync(join(tmpdir(),"container-browser-"));
    const evidence=join(cfg.results,name); mkdirSync(evidence);
    const db=join(home,".pki/nssdb"); mkdirSync(db,{recursive:true});
    const ca=join(home,"ca.crt"); writeFileSync(ca,opened.ca);
    execFileSync(cfg.certutil,["-N","--empty-password","-d","sql:"+db]);
    execFileSync(cfg.certutil,["-A","-d","sql:"+db,"-n","Fictional Altitude","-t","C,,","-i",ca]);
    let context;
    const walked=[];
    try {
      context=await chromium.launchPersistentContext(join(home,"profile"),{
        channel:"chromium",chromiumSandbox:true,headless:true,viewport,timeout:30000,
        env:{...process.env,HOME:home,XDG_CONFIG_HOME:join(home,"config"),XDG_CACHE_HOME:join(home,"cache")}});
      const page=await context.newPage();
      page.setDefaultTimeout(30000);
      const errors=[]; page.on("pageerror",e=>errors.push(String(e)));
      const state=async (id,locator)=>{await expect(locator).toBeVisible();await page.screenshot({path:join(evidence,id+".png"),fullPage:true});walked.push(id);};
      const url="https://localhost:"+cfg.port;
      await page.goto(url+"/projects");
      await state("01-pairing",page.getByRole("heading",{name:"Pair this device"}));
      await page.getByLabel("Pairing code").fill(invoke("pair").code);
      await page.getByRole("button",{name:"Pair",exact:true}).click();
      const first=page.getByRole("region",{name:"First run"});
      await state("02-name",first.getByLabel("Your name"));
      await first.getByLabel("Your name").fill("Ada Container");
      await first.getByRole("button",{name:"Continue",exact:true}).click();
      await state("03-container-prerequisites-missing",first.getByText("Run on the host to open the container shell:",{exact:false}));
      invoke("control",{values:{installed:true}});
      await first.getByRole("button",{name:"Check again",exact:true}).click();
      await state("04-container-sign-in-needed",first.getByText("codex login",{exact:true}));
      invoke("control",{values:{signed_in:true}});
      await first.getByRole("button",{name:"Check again",exact:true}).click();
      await state("05-one-engine-ready",first.getByRole("button",{name:"Continue",exact:true}));
      await page.reload(); // URL and saved identity survive interrupted setup.
      await expect(first.getByRole("button",{name:"Continue",exact:true})).toBeVisible();
      await first.getByRole("button",{name:"Continue",exact:true}).click();
      await expect(first.getByRole("radio",{name:"Keep incidents on this computer"})).toBeChecked();
      await state("06-private-incidents",first.getByRole("radio",{name:"Keep incidents on this computer"}));
      await first.getByRole("button",{name:"Continue",exact:true}).click();
      await state("07-host-voice-unavailable",first.getByText(/Voice to text can’t run on this computer/));
      await first.getByRole("button",{name:"Continue",exact:true}).click();
      await state("08-container-folders",first.getByText(/Choose another folder in the container projects volume/));
      await first.getByRole('button',{name:'Choose a folder elsewhere…'}).click();
      await first.getByRole('button',{name:'Type a path instead'}).click();
      await first.getByLabel('A folder in the container projects volume').fill('/tmp');
      await first.getByRole('button',{name:'Add',exact:true}).click();
      await state('08a-outside-volume-denied',first.getByRole('alert').filter({hasText:/inside the container/}));
      await first.getByRole('button',{name:'Cancel',exact:true}).click();
      await first.getByRole("listitem").filter({has:page.getByText('atlas',{exact:true})}).getByRole("button",{name:"Add project",exact:true}).click();
      const panel=page.getByRole("dialog",{name:"Project setup"});
      const coordinator=panel.getByRole("listitem").filter({has:page.getByRole("heading",{name:"Coordinator",exact:true})});
      await state("09-first-conversation-failed",coordinator.getByRole("button",{name:"Retry",exact:true}));
      await expect(coordinator.getByText(/Fixture authentication refused/)).toBeVisible();
      invoke("control",{values:{intro_failure:false}});
      await coordinator.getByRole("button",{name:"Retry",exact:true}).click();
      await state("10-setup-ready",panel.getByRole("status").filter({hasText:/^Ready$/}));
      await expect(panel.getByText("Using AGENTS.md; its contents are unchanged.")).toBeVisible();
      await panel.getByRole("button",{name:"Open conversation"}).click();
      // The real daemon records setup turns as system events; open their actual
      // conversation group instead of replacing history with a fixture response.
      await page.getByRole('button',{name:'Show',exact:true}).click();
      await state("11-coordinator-connected",page.getByText("Fictional container coordinator connected.",{exact:true}).last());
      await page.getByRole('textbox',{name:'Message L3 about atlas'}).fill('Create the fictional browser task.');
      await page.getByRole('button',{name:'Send',exact:true}).click();
      await expect.poll(()=>{try{return invoke('task').inputs.length;}catch{return 0;}},{timeout:45000}).toBe(1);
      await page.goto(url+'/projects/atlas/tasks/browser-fixture-task');
      await state('11a-task-running',page.getByRole('button',{name:/^Stop/}));
      await page.getByRole('button',{name:/^Stop/}).click();
      await state('11b-task-stopped',page.getByRole('button',{name:'Continue',exact:true}));
      const savedTask=invoke('task');
      if (!savedTask.terminated || savedTask.hold!=='Fixture review' || savedTask.inputs.length!==1) throw Error('Task Stop/hold evidence incomplete');
      await page.goto(url+"/settings/name");
      await expect(page.getByLabel("Your name")).toHaveValue("Ada Container");
      await state("12-settings-persist",page.getByLabel("Your name"));
      const restarted=invoke("restart");
      if (!restarted.ready) throw Error("Same-container restart lost prior admission");
      await page.reload();
      await expect(page.getByLabel("Your name")).toHaveValue("Ada Container");
      const replaced=invoke("replace");
      if (replaced.ready) throw Error("Replacement did not pause admission");
      await page.goto(url+"/projects/atlas");
      await state("13-replacement-held",page.getByRole('status',{name:'Container work paused'}));
      await page.goto(url+'/projects/atlas/tasks/browser-fixture-task');
      await page.getByRole('button',{name:'Continue',exact:true}).click();
      await page.getByRole('textbox',{name:'Message the L2'}).fill('Continue the saved fictional draft.');
      await page.getByRole('button',{name:'Send',exact:true}).click();
      await expect(page.getByRole('button',{name:'Resuming…',exact:true})).toBeDisabled();
      if (invoke('task').inputs.length!==1) throw Error('Task launched before host Continue');
      invoke("continue");
      await expect(page.getByRole('status',{name:'Container work paused'})).toBeHidden({timeout:20000});
      await state("14-replacement-task-continued",page.getByRole('button',{name:/^Stop/}));
      await expect.poll(()=>invoke('task').inputs.length,{timeout:30000}).toBe(2);
      const resumed=invoke('task');
      if(resumed.session!==savedTask.session || resumed.draft!==savedTask.draft || resumed.hold!==savedTask.hold ||
         resumed.inputs.length!==2 || resumed.pending!==0 || resumed.inputs[1].prompt.split('Continue the saved fictional draft.').length!==2)
        throw Error('Task continuity or exactly-once queued input evidence differs');
      await page.getByRole('button',{name:/^Stop/}).click();
      await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeVisible();
      await page.goto(url+'/projects/atlas');
      if(name==='phone') await page.getByRole('button',{name:'atlas',exact:true}).click();
      else await page.getByRole('button',{name:'Add a folder',exact:true}).click();
      await page.getByRole('region',{name:'First run'}).getByRole('listitem')
        .filter({has:page.getByText('custom',{exact:true})}).getByRole('button',{name:'Add project',exact:true}).click();
      const guards=panel.getByRole('listitem').filter({has:page.getByRole('heading',{name:'Git guards',exact:true})});
      await state('15-custom-hooks-decision',guards.getByRole('button',{name:'Review integration'}));
      await guards.getByRole('button',{name:'Review integration'}).click();
      await guards.getByRole('button',{name:'Keep current setup'}).click();
      await state('16-custom-hooks-kept',guards.getByRole('button',{name:'Review integration'}));
      await guards.getByRole('button',{name:'Review integration'}).click();
      await guards.getByRole('button',{name:'Use both hook sets'}).click();
      await state('17-custom-hooks-integrated',guards.getByText('Both hook sets are configured and verified.'));
      await panel.getByRole('button',{name:'Open conversation'}).click();
      if(name==='phone') await page.getByRole('button',{name:'custom',exact:true}).click();
      else await page.getByRole('button',{name:'Add a folder',exact:true}).click();
      await page.getByRole('region',{name:'First run'}).getByRole('listitem')
        .filter({has:page.getByText('notes',{exact:true})}).getByRole('button',{name:'Add project',exact:true}).click();
      await state('18-conversation-only-folder',panel.getByRole('status').filter({hasText:/^Conversation ready$/}));
      await expect(panel.getByText('No Git repository. Conversation is available; Git tasks are unavailable.')).toBeVisible();
      await expect(panel.getByText(/No project instructions/)).toBeVisible();
      await panel.getByRole('button',{name:'Open conversation'}).click();
      await expect(page.getByRole('textbox',{name:'Message L3 about notes'})).toBeVisible();
      await page.goto(url+'/settings/incident-reports');
      await state('19-incident-preference-persists',page.getByRole('radio',{name:'Keep incidents on this computer'}));
      await expect(page.getByRole('radio',{name:'Keep incidents on this computer'})).toBeChecked();
      await page.goto(url+'/settings/prerequisites');
      await state('20-container-settings-tools',page.getByText('Run on the host to open the container shell:',{exact:false}));
      await page.getByRole('button',{name:'Check again',exact:true}).click();
      if (errors.length) throw Error(errors.join("\n"));
      result.viewports.push({name,viewport,walked,passed:true});
    } catch(error) {
      try {writeFileSync(join(evidence,'daemon.json'),JSON.stringify(invoke('diagnostics'),null,2));}catch{}
      if(context) {const p=context.pages().at(-1);await p.screenshot({path:join(evidence,"failure.png"),fullPage:true}).catch(()=>{});writeFileSync(join(evidence,"failure.html"),await p.content());}
      throw error;
    } finally {
      if(context) await context.close();
      rmSync(home,{recursive:true,force:true});
      invoke("stop");
    }
  }
  result.passed=true;
} catch(error) {result.error=String(error);}
finally {
  if(prepared) {try {result.cleanup=control("cleanup");} catch(error){result.passed=false;result.cleanup_error=String(error);}}
  writeFileSync(join(cfg.results,"browser.json"),JSON.stringify(result,null,2)+"\n");
}
process.exit(result.passed?0:1);
