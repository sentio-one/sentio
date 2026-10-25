/** Durable account backstop; policy and scheduling remain in Python. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { requestTransport } from './business_control.js';
import { dispatchAuthorized } from './business_dispatch.js';
import { createAttemptJournal } from './business_journal.js';
import { buildTextSendPayload, buildPollPayload, buildLocationPayload, mediaPayloadForFile } from './bridge_helpers.js';

const PHYSICAL=new Set(['/send','/edit','/send-media','/send-poll','/send-location','/typing']);

export function installBusinessRoutes(app, {sessionDir, connected, send, messageStore, track, authorize}) {
  const latchPath=()=>path.join(sessionDir(),'business-latch.json');
  app.use((req,res,next)=>{
    if(req.method==='POST' && PHYSICAL.has(req.path.toLowerCase().replace(/\/+$/,'')) && fs.existsSync(latchPath())) return res.status(409).json({ok:false,error:'business_authorization_required',submit:'business.request'});
    next();
  });
  app.post('/business/dispatch',async(req,res)=>{
    if(!connected())return res.status(503).json({ok:false,error:'not_connected',submitted:false});
    try {
      const latch=JSON.parse(fs.readFileSync(latchPath(),'utf8'));
      const request=req.body;
      if(!latch.home || request.scope?.transport_profile!==latch.transport_profile || request.scope?.account_id!==latch.account_id) return res.status(409).json({ok:false,error:'account_mismatch',submitted:false});
      const journal=createAttemptJournal(path.join(sessionDir(),'business-attempts.jsonl'));
      const result=await dispatchAuthorized(request,{
        journal,
        authorize:params=>authorize() ? authorize()(params) : requestTransport(latch.home,params),
        sendSocket:async current=>{
          const {content,options}=physicalPayload(current.part,current.recipient || current.scope.contact_id,messageStore);
          const sent=await send(current.recipient || current.scope.contact_id,content,options);
          track(sent);
          return sent;
        },
      });
      return res.status(result.ok ? 200 : 409).json(result);
    } catch(error) {return res.status(409).json({ok:false,error:error.message,submitted:false});}
  });
}

function physicalPayload(part,chatId,messageStore) {
  const p=part.payload;
  if(part.operation==='text') {
    if(p.reply_target && !messageStore.get(p.reply_target))throw new Error('quote_target_unavailable');
    const result=buildTextSendPayload(p.text,{chatId,replyTo:p.reply_target,mentions:p.mentions,messageStore});
    return {content:result.content,options:result.options};
  }
  let content;
  const key={id:p.message_id,remoteJid:chatId,fromMe:true};
  switch(part.operation) {
    case 'edit': content={text:p.text,edit:key};break;
    case 'reaction':content={react:{text:p.emoji,key}};break;
    case 'poll':content=buildPollPayload({question:p.text,options:p.options,selectableCount:p.selectable_count});break;
    case 'location':content=buildLocationPayload(p);break;
    case 'media': {
      const stat=fs.lstatSync(p.path);
      if(!stat.isFile() || stat.isSymbolicLink())throw new Error('invalid_attachment');
      const buffer=fs.readFileSync(p.path);
      if(`sha256:${createHash('sha256').update(buffer).digest('hex')}`!==part.media_hash)throw new Error('media_hash_mismatch');
      content=mediaPayloadForFile({buffer,filePath:p.path,mediaType:p.type,caption:p.caption});
      break;
    }
    default:throw new Error('unsupported_operation');
  }
  if(p.mentions)content.mentions=p.mentions;
  return {content,options:{}};
}
