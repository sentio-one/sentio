/** The existing OS-private gateway control socket is the bridge's authority. */
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

function privateNode(target, kind) {
  const stat=fs.lstatSync(target);
  if (stat.isSymbolicLink() || (process.getuid && stat.uid!==process.getuid()) || (stat.mode & 0o077) || !stat[kind]()) throw new Error('unsafe_control_endpoint');
}

export function requestTransport(home, params) {
  return new Promise((resolve,reject)=>{
    let endpoint;
    try {
      if(process.platform==='win32') {
        const hash=createHash('sha256').update(path.resolve(home).toLowerCase()).digest('hex').slice(0,16);
        endpoint=`\\\\.\\pipe\\hermes-gateway-${hash}`;
      } else {
        endpoint=path.join(home,'gateway.sock');
        if(!fs.existsSync(endpoint)) {
          const pointer=path.join(home,'gateway.sock.path');
          privateNode(pointer,'isFile');
          endpoint=fs.readFileSync(pointer,'utf8').trim();
          if(!path.isAbsolute(endpoint) || Buffer.byteLength(endpoint)>4096) throw new Error('unsafe_control_endpoint');
          privateNode(path.dirname(endpoint),'isDirectory');
        }
        privateNode(endpoint,'isSocket');
      }
    } catch(error) {reject(error);return;}
    const socket=net.createConnection(endpoint);
    let buffer='';
    socket.setTimeout(5000,()=>socket.destroy(new Error('control_timeout')));
    socket.on('error',reject);
    socket.on('connect',()=>socket.write(JSON.stringify({protocol:1,id:1,verb:'business.transport',params})+'\n'));
    socket.on('data',chunk=>{
      buffer+=chunk.toString('utf8');
      if(buffer.length>524288) {socket.destroy(new Error('control_response_too_large'));return;}
      const newline=buffer.indexOf('\n');
      if(newline<0)return;
      socket.end();
      try {
        const response=JSON.parse(buffer.slice(0,newline));
        resolve(response.ok===true ? response.result : {ok:false,error:response.error || 'control_refused'});
      } catch(error) {reject(error);}
    });
    socket.on('end',()=>{if(!buffer.includes('\n'))reject(new Error('control_disconnected'));});
  });
}
