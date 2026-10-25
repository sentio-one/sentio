// A real bridge HTTP surface with only the WhatsApp socket replaced.
import { app, configureBridgeTransport } from '../../../scripts/whatsapp-bridge/bridge.js';
import fs from 'node:fs';
const [sessionDir,callsPath]=process.argv.slice(2);
let counter=0;
configureBridgeTransport({sessionDir,state:'connected',socket:{sendMessage:async(chatId,payload)=>{
  const id=`physical-${++counter}`;
  fs.appendFileSync(callsPath,JSON.stringify({chatId,payload,id})+'\n');
  return {key:{id}};
}}});
const server=app.listen(0,'127.0.0.1',()=>process.stdout.write(String(server.address().port)+'\n'));
process.on('SIGTERM',()=>server.close(()=>process.exit(0)));
