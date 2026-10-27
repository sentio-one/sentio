"""Opaque producer attachments and exact target metadata reach the rendered plan."""
import pytest
from gateway.business.models import parse_candidate, BusinessProtocolError, DraftRevision, EpisodeRef, Scope
from gateway.business.render import capture_producer_media, render_delivery_plan

@pytest.mark.asyncio
async def test_text_target_metadata_is_strict_and_preserved():
    data={'reply_target':'quoted-id','mentions':['123@s.whatsapp.net']}
    candidate=parse_candidate({'kind':'text','text':'exact','data':data,'fact_ids':[],'completed_action_ids':[],'credential_slots':[]})
    scope=Scope('default','default','default','customer@s.whatsapp.net')
    revision=DraftRevision('revision',scope,EpisodeRef('episode',1),1,candidate,'v1',0,None,None)
    plan=await render_delivery_plan(scope,revision,now_ms=1)
    assert plan.parts[0].payload=={'text':'exact',**data}
    with pytest.raises(BusinessProtocolError):
        parse_candidate({'kind':'text','text':'exact','data':{'reply_target':'/etc/passwd'},'fact_ids':[],'completed_action_ids':[],'credential_slots':[]})

@pytest.mark.asyncio
async def test_captured_media_handle_renders_then_refuses_changed_bytes(tmp_path):
    source=tmp_path/'image.png';source.write_bytes(b'approved image')
    handle=capture_producer_media(str(source),tmp_path,'image')
    candidate=parse_candidate({'kind':'media','text':'caption','data':{'attachments':[handle]},'fact_ids':[],'completed_action_ids':[],'credential_slots':[]})
    scope=Scope('default','default','default','customer@s.whatsapp.net')
    revision=DraftRevision('revision',scope,EpisodeRef('episode',1),1,candidate,'v1',0,None,None)
    directory=tmp_path/'business'/'attachments'
    plan=await render_delivery_plan(scope,revision,now_ms=1,capture_dir=directory)
    assert plan.parts[0].media_hash=='sha256:'+handle['sha256']
    (directory/handle['id']).write_bytes(b'changed')
    with pytest.raises(BusinessProtocolError,match='attachment_hash_mismatch'):
        await render_delivery_plan(scope,revision,now_ms=1,capture_dir=directory)
