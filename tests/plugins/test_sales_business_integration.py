"""Real durable outcome cursor contracts for native sales consumers."""
from gateway.business.models import Candidate, ContextEvent, Principal, Scope
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.store import BusinessStore
from gateway.business.store_jobs import outcome_events


def test_outcomes_replay_until_ack_and_rejection_is_not_delivery(tmp_path):
    store = BusinessStore(tmp_path)
    scope = store.bind_contact('default','default','default',['27800000001@s.whatsapp.net'])
    episode = store.record_ingress(scope,ContextEvent('in','message','customer','Hi',None,'inbound'),source_id='in')
    revision = store.append_revision(scope,episode,Candidate('text','Hello',{},(),(),()),expected_revision_id=None,policy_version='v1',lesson_version=0)
    store.save_plan(DeliveryPlan('p',revision.id,scope,episode.context_version,'v1',(DeliveryPart(0,'text',{'text':'Hello'},None),),9999999999999,'sha256:exact'))
    receipt = store.submit_revision(scope,revision.id,request_id='out',now_ms=1)
    principal = Principal('plugin:sales','default',frozenset({'events','ack_events'}),'plugin')
    first = outcome_events(store,principal,'crm')
    assert first['events'][0]['state'] == 'held'
    assert outcome_events(BusinessStore(tmp_path),principal,'crm') == first
    # A CRM transaction failure leaves the server cursor untouched.
    assert outcome_events(store,principal,'crm')['acknowledged_cursor'] == 0
    outcome_events(store,principal,'crm',acknowledge=first['next_cursor'])
    assert outcome_events(store,principal,'crm')['events'] == []
    with store._connect() as db:
        db.execute("UPDATE business_outbox SET state='rejected' WHERE outbox_id=?",(receipt.id,))
    event = outcome_events(store,principal,'crm')['events'][0]
    assert event['state'] == 'rejected' and event['message_ids'] == []
    foreign = Principal('plugin:sales','other',frozenset({'events'}),'plugin')
    assert outcome_events(store,foreign,'crm')['events'] == []
