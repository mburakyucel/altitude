// Synchronous journey calls use a separate process; its framed socket is independent of logs.
import { createConnection } from "node:net";
const [path, request] = process.argv.slice(2);
const maximum=65536;
const body=Buffer.from(request);
if(body.length>maximum) throw Error("Fixture request exceeds frame bound");
const frame=Buffer.alloc(4);frame.writeUInt32BE(body.length);
const socket=createConnection(path);
let response=Buffer.alloc(0),length;
socket.setTimeout(360000,()=>socket.destroy(Error("Fixture control timed out")));
socket.on("connect",()=>socket.write(Buffer.concat([frame,body])));
socket.on("data",chunk=>{
  response=Buffer.concat([response,chunk]);
  if(response.length>=4 && length===undefined) {
    length=response.readUInt32BE(0);
    if(length>maximum) socket.destroy(Error("Fixture response exceeds frame bound"));
  }
  if(length!==undefined && response.length>=length+4) {
    if(response.length!==length+4) throw Error("Unexpected fixture frame data");
    const value=JSON.parse(response.subarray(4).toString());
    if(value.version!==1 || value.ok!==true) throw Error(value.error || "Fixture response refused");
    process.stdout.write(JSON.stringify(value.result));socket.end();
  }
});
socket.on("error",error=>{console.error(String(error));process.exitCode=1;});
socket.on("end",()=>{if(length===undefined || response.length!==length+4) {console.error("Truncated fixture frame");process.exitCode=1;}});
