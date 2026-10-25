import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { app, configureBridgeTransport } from './bridge.js';

test('physical endpoints refuse raw sends across bridge restart and exact dispatch consumes authorization', async () => {
  const session=mkdtempSync(path.join(os.tmpdir(),'wa-business-'));
  const calls=[];
  let consumed=false;
  let registered=true;
  const configure=()=>configureBridgeTransport({socket:{sendMessage:async (...args)=>{calls.push(args); return {key:{id:'physical-id'}};},onWhatsApp:async jid=>[{jid,exists:registered}]},state:'connected',sessionDir:session,authorize:async request=>{
    assert.equal(request.part.payload.text,'approved  bytes\n');
    if(consumed) return {ok:false,error:'authorization_consumed'};
    consumed=true;
    return {ok:true,attempt_id:'attempt'};
  }});
  configure();
  const server=app.listen(0,'127.0.0.1');
  await new Promise(resolve=>server.once('listening',resolve));
  const url=`http://127.0.0.1:${server.address().port}`;
  const post=async(route,body)=>{const response=await fetch(url+route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); return [response.status,await response.json()];};
  try {
    const raw={chatId:'123@s.whatsapp.net',message:'unapproved',messageId:'m',filePath:'/tmp/m',question:'?',options:['a','b'],latitude:0,longitude:0};
    assert.equal((await post('/send',raw))[0],200);
    calls.length=0;
    writeFileSync(path.join(session,'business-latch.json'),JSON.stringify({transport_profile:'secondary',account_id:'secondary',home:session}));
    for(const route of ['/send','/edit','/send-media','/send-poll','/send-location','/typing']) {
      const [status,body]=await post(route,raw);
      assert.equal(status,409,route);
      assert.equal(body.error,'business_authorization_required');
    }
    for(const route of ['/SEND','/send/','/Send-Media/','/EDIT','/Send-Poll','/send-location//'.replace(/\/+$/,'/')]) {
      assert.equal((await post(route,raw))[0],409,`${route} must not bypass the latch`);
    }
    assert.equal(calls.length,0);
    configure();
    assert.equal((await post('/send',raw))[0],409,'restart retains latch');
    const req={authorization_token:'token',token:'token',attempt_id:'attempt',plan_id:'plan',plan_digest:'digest',payload_digest:'digest',scope:{runtime_profile:'satellite',transport_profile:'secondary',account_id:'secondary',contact_id:'123@s.whatsapp.net'},part:{index:0,operation:'text',payload:{text:'approved  bytes\n'},media_hash:null}};
    const invalid={...req,part:{index:0,operation:'text',payload:{text:'quoted',reply_target:'missing'},media_hash:null}};
    const [invalidStatus,invalidBody]=await post('/business/dispatch',invalid);
    assert.equal(invalidStatus,409);
    assert.equal(invalidBody.submitted,false);
    assert.equal(consumed,false,'local validation must not consume a one-use token');
    assert.deepEqual(await (await post('/business/check-registration',{recipient:'123@s.whatsapp.net'}))[1],{ok:true,registered:true});
    registered=false;
    assert.deepEqual(await (await post('/business/check-registration',{recipient:'123@s.whatsapp.net'}))[1],{ok:true,registered:false});
    registered=true;
    assert.equal((await post('/business/dispatch',req))[0],200);
    assert.equal(calls.length,1);
    assert.equal(calls[0][0],'123@s.whatsapp.net');
    assert.equal(calls[0][1].text,'approved  bytes\n');
    assert.equal((await post('/business/dispatch',req))[0],409);
    assert.equal(calls.length,1);
    const health=await (await fetch(url+'/health')).json();
    assert.equal(health.capabilities.businessDispatch,1);
  } finally {await new Promise(resolve=>server.close(resolve)); rmSync(session,{recursive:true,force:true});}
});
