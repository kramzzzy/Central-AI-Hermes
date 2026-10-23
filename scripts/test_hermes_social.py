"""Local-only Meta bridge tests: fake provider, temporary encrypted storage."""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from hermes_social import SocialWorkspace, SocialError

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"

class SocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"HERMES_PROFILE_ROOT": self.tmp.name, "FACEBOOK_APP_ID": "123", "FACEBOOK_APP_SECRET": "test-secret", "META_WEBHOOK_VERIFY_TOKEN": "verify-test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = 1700000000
        self.calls = []
        def provider(url, **kw):
            self.calls.append((url, kw))
            if url.endswith('/subscribed_apps'): return {"success": True}
            if url.endswith('/messages'): return {"message_id": "sent-1"}
            return {"id": "real-post-1"}
        self.social = SocialWorkspace(self.tmp.name, transport=provider, clock=lambda:self.now)
        self.pages = [
            {"id":"101","name":"Page A","access_token":"token-A"},
            {"id":"202","name":"Page B","access_token":"token-B","instagram_business_account":{"id":"303","username":"business"}},
        ]
        self.social._save_facebook_and_instagram(ORG,self.pages,self.pages[0],None)

    def ingest(self, platform="page", account="101", sender="999", mid="m1", **message):
        event={"object":platform,"entry":[{"id":account,"messaging":[{"sender":{"id":sender},"recipient":{"id":account},"timestamp":self.now*1000,"message":{"mid":mid,"text":"Hello",**message}}]}]}
        raw=json.dumps(event).encode()
        return self.social.handle({"action":"webhook_ingest","raw":base64.b64encode(raw).decode(),"signature":"sha256="+hmac.new(b"test-secret",raw,hashlib.sha256).hexdigest()})

    def send(self, **extra):
        return self.social.handle({"org":ORG,"action":"send_message","platform":"facebook","account_id":"101","recipient_id":"999","message":"Hello back","delivery_id":"reply-1",**extra})

    def test_signature_required(self):
        with self.assertRaises(SocialError): self.social.handle({"action":"webhook_ingest","raw":"e30=","signature":"sha256=bad"})

    def test_verification(self):
        self.assertTrue(self.social.handle({"action":"webhook_verify","token":"verify-test"})["ok"])
        with self.assertRaises(SocialError): self.social.handle({"action":"webhook_verify","token":"wrong"})

    def test_auto_subscription_and_all_accounts(self):
        self.assertEqual(2,len([c for c in self.calls if c[0].endswith('/subscribed_apps')]))
        self.assertEqual({"101","202","303"},{a["account_id"] for a in self.social.accounts(ORG)})
        self.assertIsNone(self.social.get_connection(OTHER,"facebook"))
        self.assertEqual("token-B",self.social.get_connection(ORG,"instagram")["access_token"])

    def test_dedupe_echo_and_unknown_account(self):
        self.assertEqual(1,self.ingest()["accepted"])
        self.assertEqual(0,self.ingest()["accepted"])
        self.assertEqual(0,self.ingest(mid="echo",is_echo=True)["accepted"])
        self.assertEqual(0,self.ingest(account="666")["accepted"])
        self.assertIsNone(self.social.claim_message(OTHER)["event"])

    def test_thread_order_lease_and_restart(self):
        self.ingest()
        self.now+=1
        self.ingest(mid="m2")
        first=self.social.claim_message(ORG)["event"]
        self.assertIsNone(self.social.claim_message(ORG)["event"])
        self.now+=181
        self.assertEqual(first,self.social.claim_message(ORG)["event"])
        self.social.handle({"action":"inbox_done","org":ORG,"id":first["id"]})
        self.assertNotEqual(first["id"],self.social.claim_message(ORG)["event"]["id"])

    def test_reply_receipt_and_cross_tenant(self):
        self.ingest()
        self.assertEqual(self.send(),self.send())
        self.assertEqual(1,len([c for c in self.calls if c[0].endswith('/messages')]))
        with self.assertRaises(SocialError): self.send(org=OTHER)
        with self.assertRaises(SocialError): self.send(message="changed")

    def test_window_enforced(self):
        self.ingest()
        self.now+=86401
        with self.assertRaises(SocialError): self.send()

    def test_unknown_outcome_not_retried(self):
        self.ingest()
        attempts=[]
        def timeout(*a,**kw):
            attempts.append(1)
            raise SocialError("timeout",502)
        self.social.transport=timeout
        with self.assertRaises(SocialError): self.send()
        with self.assertRaises(SocialError): self.send()
        self.assertEqual(1,len(attempts))

    def test_instagram_page_token_pair(self):
        self.ingest(platform="instagram",account="303")
        self.send(platform="instagram",account_id="303")
        url,kw=self.calls[-1]
        self.assertTrue(url.endswith('/202/messages'))
        self.assertEqual('token-B',kw['token'])

    def test_publish_selected_account_and_receipt(self):
        body={"org":ORG,"action":"publish_approved","platform":"instagram","account_id":"303","message":"Reviewed caption","image_url":"https://example.com/image.jpg","delivery_id":"post:1"}
        result=self.social.handle(body)
        self.assertEqual('real-post-1',result['post_id'])
        self.assertEqual(result,self.social.handle(body))
        media=[c for c in self.calls if c[0].endswith('/media')]
        self.assertEqual(1,len(media))
        self.assertEqual('token-B',media[0][1]['data']['access_token'])
        with self.assertRaises(SocialError): self.social.handle({**body,'org':OTHER})

    def test_duplicate_workspace_link_rejected(self):
        with self.assertRaises(SocialError): self.social._save_facebook_and_instagram(OTHER,self.pages,self.pages[0],None)

    def test_personal_profile_not_accepted(self):
        self.social.transport=lambda url,**kw: {"data":[]} if '/accounts?' in url else {"id":"123","name":"Personal"}
        with self.assertRaises(SocialError): self.social._discover_facebook_pages('user-token')

    def test_failed_subscription_visible(self):
        self.social.transport=lambda *a,**kw: {"success":False}
        self.social._save_facebook_and_instagram(ORG,self.pages,self.pages[0],None)
        self.assertTrue(all(a['messaging_state']=='reconnect_required' for a in self.social.accounts(ORG)))

if __name__ == '__main__': unittest.main()
