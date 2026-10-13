// Negative network controls run in the same network-none browser configuration.
import { createConnection } from 'node:net';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const cfg=JSON.parse(readFileSync(process.argv[2],'utf8'));
const attempts={};
for(const name of ['gateway','host_listener','internet']) {
  const [host,port]=cfg.network_targets[name];
  attempts[name]=await new Promise(resolve=>{
    const socket=createConnection({host,port});
    socket.setTimeout(3000);
    socket.on('connect',()=>{socket.destroy();resolve({refused:false});});
    socket.on('error',error=>resolve({refused:true,error:error.code}));
    socket.on('timeout',()=>{socket.destroy();resolve({refused:true,error:'timeout'});});
  });
}
const result={passed:Object.values(attempts).every(value=>value.refused),attempts};
writeFileSync(join(cfg.results,'network-controls.json'),JSON.stringify(result,null,2)+'\n');
process.exit(result.passed?0:1);
