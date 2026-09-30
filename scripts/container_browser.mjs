// Published image HTTPS, real daemon/setup and fictional engine; no intercepted application routes.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, rmSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const { chromium, expect } = createRequire(new URL("../web/package.json", import.meta.url))("@playwright/test");
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
      await first.getByRole("listitem").filter({hasText:/^atlas\b/}).getByRole("button",{name:"Add project",exact:true}).click();
      const panel=page.getByRole("dialog",{name:"Project setup"});
      const coordinator=panel.getByRole("listitem").filter({has:page.getByRole("heading",{name:"Coordinator",exact:true})});
      await state("09-first-conversation-failed",coordinator.getByRole("button",{name:"Retry",exact:true}));
      invoke("control",{values:{intro_failure:false}});
      await coordinator.getByRole("button",{name:"Retry",exact:true}).click();
      await state("10-setup-ready",panel.getByRole("status").filter({hasText:/^Ready$/}));
      await expect(panel.getByText("Using AGENTS.md; its contents are unchanged.")).toBeVisible();
      await panel.getByRole("button",{name:"Open conversation"}).click();
      await state("11-coordinator-connected",page.getByText("Fictional container coordinator connected.",{exact:true}).last());
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
      invoke("continue");
      await expect(page.getByRole('status',{name:'Container work paused'})).toBeHidden({timeout:20000});
      await state("14-replacement-continued",page.getByRole("textbox",{name:"Message L3 about atlas"}));
      if (errors.length) throw Error(errors.join("\n"));
      result.viewports.push({name,viewport,walked,passed:true});
    } catch(error) {
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
