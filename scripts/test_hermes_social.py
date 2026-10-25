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
            if url.endswith('/likes'): return {"success": True}
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
        self.assertEqual(4,len([c for c in self.calls if c[0].endswith('/subscribed_apps')]))
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

    def test_dm_sender_name_lookup_is_cached(self):
        calls=[]
        def provider(url, **kw):
            calls.append((url, kw))
            return {"name": "  Jamie   Rivera "} if "?fields=name" in url else {"id": "x"}
        self.social.transport=provider
        self.ingest()
        self.assertEqual("Jamie Rivera",self.social.claim_message(ORG)["event"]["sender_name"])
        self.now+=181
        self.assertEqual("Jamie Rivera",self.social.claim_message(ORG)["event"]["sender_name"])
        lookups=[c for c in calls if "/999?fields=name" in c[0]]
        self.assertEqual(1,len(lookups))
        self.assertEqual("token-A",lookups[0][1]["token"])

    def test_dm_sender_name_failure_does_not_block_message(self):
        self.ingest()
        self.social.transport=lambda *a, **kw: (_ for _ in ()).throw(SocialError("denied",403,rejected=True))
        event=self.social.claim_message(ORG)["event"]
        self.assertEqual("Hello",event["text"])
        self.assertNotIn("sender_name",event)

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

    def test_typing_actions_use_verified_thread_and_do_not_dedupe(self):
        body = {"org":ORG,"action":"sender_action","platform":"facebook","account_id":"101","recipient_id":"999","sender_action":"typing_on"}
        with self.assertRaises(SocialError): self.social.handle(body)
        self.ingest()
        for action in ("typing_on", "typing_on", "typing_off"):
            self.social.handle({**body,"sender_action":action})
            self.assertEqual({"recipient":{"id":"999"},"sender_action":action},self.calls[-1][1]["data"])
        self.assertEqual(3,len([c for c in self.calls if c[0].endswith('/messages')]))
        with self.assertRaises(SocialError): self.social.handle({**body,"org":OTHER})
        with self.assertRaises(SocialError): self.social.handle({**body,"sender_action":"invalid"})
        self.now += 86401
        with self.assertRaises(SocialError): self.social.handle(body)

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

    def test_explicit_rejection_can_retry_same_delivery(self):
        self.ingest()
        original = self.social.transport
        self.social.transport = lambda *a, **kw: (_ for _ in ()).throw(SocialError("Permission denied", 403, rejected=True))
        with self.assertRaises(SocialError): self.send()
        self.social.transport = original
        self.assertEqual("sent-1", self.send()["message_id"])
        self.assertEqual("sent-1", self.send()["message_id"])

    def test_handled_unknown_delivery_releases_later_messages(self):
        self.ingest()
        first = self.social.claim_message(ORG)["event"]
        self.social.transport = lambda *a, **kw: (_ for _ in ()).throw(SocialError("Outcome unknown", 502))
        with self.assertRaises(SocialError): self.send()
        self.now += 1
        self.ingest(mid="later")
        self.social.handle({"org":OTHER,"action":"inbox_done","id":first["id"]})
        self.assertIsNone(self.social.claim_message(ORG)["event"])
        self.social.handle({"org":ORG,"action":"inbox_done","id":first["id"]})
        self.assertNotEqual(first["id"], self.social.claim_message(ORG)["event"]["id"])
        with self.assertRaisesRegex(SocialError, "outcome unknown"): self.send()

    def test_provider_rejection_classification(self):
        import io
        import urllib.error
        from hermes_social import provider_request
        for status, rejected in [(400,True),(403,True),(429,True),(500,False)]:
            error=urllib.error.HTTPError("https://graph.facebook.com/test",status,"test",{},io.BytesIO(b'{"error":{"message":"Rejected"}}'))
            with patch('hermes_social.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaises(SocialError) as caught: provider_request("https://graph.facebook.com/test",method="POST",data={})
                self.assertEqual(rejected,caught.exception.rejected)

    def test_reels_resume_processing_without_duplicate_upload(self):
        for platform, account in [("facebook","101"),("instagram","303")]:
            calls=[]
            ready=False
            def provider(url, **kw):
                calls.append((url,kw))
                if url.endswith('/video_reels'):
                    return {"video_id":"123"} if kw.get('data',{}).get('upload_phase')=='start' else {"success":True}
                if 'rupload.facebook.com' in url: return {"success":True}
                if url.endswith('/media'): return {"id":"123"}
                if 'fields=status_code' in url: return {"status_code":"FINISHED" if ready else "IN_PROGRESS"}
                if 'fields=status' in url: return {"status":{"processing_phase":{"status":"complete" if ready else "in_progress"},"publishing_phase":{"status":"complete" if ready else "not_started"}}}
                return {"id":"published-123"}
            self.social.transport=provider
            body={"org":ORG,"action":"publish_approved","platform":platform,"account_id":account,"message":"Reviewed Reel","video_url":"https://example.com/reel.mp4","delivery_id":"reel-"+platform}
            self.assertTrue(self.social.handle(body)['processing'])
            self.assertTrue(self.social.handle(body)['processing'])
            ready=True
            result=self.social.handle(body)
            if result.get('processing'): result=self.social.handle(body)
            self.assertTrue(result['post_id'])
            before=len(calls);self.assertEqual(result,self.social.handle(body));self.assertEqual(before,len(calls))
            self.assertEqual(1,len([c for c in calls if c[0].endswith('/media') or c[1].get('data',{}).get('upload_phase')=='start']))

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

    def comment(self, platform="page", account="101", sender="999", cid="101_555", verb="add"):
        value = {"item":"comment","verb":verb,"comment_id":cid,"post_id":"101_888","message":"How much?","from":{"id":sender}} if platform == "page" else {"id":cid,"media":{"id":"888"},"text":"How much?","from":{"id":sender}}
        payload={"object":platform,"entry":[{"id":account,"changes":[{"field":"feed" if platform=="page" else "comments","value":value}]}]}
        raw=json.dumps(payload).encode()
        return self.social.handle({"action":"webhook_ingest","raw":base64.b64encode(raw).decode(),"signature":"sha256="+hmac.new(b"test-secret",raw,hashlib.sha256).hexdigest()})

    def reply_comment(self, **extra):
        return self.social.handle({"org":ORG,"action":"send_comment","platform":"facebook","account_id":"101","comment_id":"101_555","message":"Public answer","like":True,"delivery_id":"comment-reply-1",**extra})

    def test_comment_post_details_and_metadata_fallback(self):
        self.comment()
        body={"org":ORG,"action":"comment_context","platform":"facebook","account_id":"101","comment_id":"101_555"}
        self.social.transport=lambda url,**kw: {"message":"Solar roof", "permalink_url":"https://www.facebook.com/101_888", "full_picture":"https://example.test/solar.jpg"}
        context=self.social.handle(body)
        self.assertEqual("101_888",context["post_id"])
        self.assertEqual("Page A",context["account_name"])
        self.assertEqual("Solar roof",context["post_text"])
        self.assertEqual("https://example.test/solar.jpg",context["image_url"])
        with self.assertRaises(SocialError): self.social.handle({**body,"org":OTHER})
        def fallback(url,**kw):
            if "full_picture" in url: raise SocialError("Unavailable field",400)
            return {"message":"Caption only"}
        self.social.transport=fallback
        self.assertEqual("Caption only",self.social.handle(body)["post_text"])
        self.comment(platform="instagram",account="303",cid="555")
        self.social.transport=lambda url,**kw: {"caption":"Solar reel","permalink":"https://www.instagram.com/p/example/","media_url":"https://example.test/video.mp4","media_type":"VIDEO"}
        context=self.social.handle({**body,"platform":"instagram","account_id":"303","comment_id":"555"})
        self.assertEqual("Solar reel",context["post_text"])
        self.assertFalse(context["image_url"])

    def test_comment_reply_like_and_deduplication(self):
        self.assertEqual(1,self.comment()["accepted"])
        self.assertEqual(0,self.comment()["accepted"])
        self.assertEqual(0,self.comment(cid="101_556",sender="101")["accepted"])
        self.assertEqual(0,self.comment(cid="101_557",verb="remove")["accepted"])
        self.assertEqual("comment",self.social.claim_message(ORG)["event"]["kind"])
        result=self.reply_comment()
        self.assertTrue(result["liked"])
        self.assertEqual(result,self.reply_comment())
        self.assertEqual(1,len([c for c in self.calls if c[0].endswith('/101_555/comments')]))
        self.assertEqual(1,len([c for c in self.calls if c[0].endswith('/101_555/likes')]))
        with self.assertRaises(SocialError): self.reply_comment(org=OTHER)
        with self.assertRaises(SocialError): self.reply_comment(comment_id="101_777")
        with self.assertRaises(SocialError): self.send()

    def test_instagram_public_reply_and_optional_heart(self):
        self.comment(platform="instagram",account="303",cid="555")
        with patch.dict(os.environ,{"META_INSTAGRAM_COMMENT_LIKES":"1"}):
            result=self.reply_comment(platform="instagram",account_id="303",comment_id="555")
        self.assertTrue(result["liked"])
        replies=[c for c in self.calls if c[0].endswith('/555/replies')]
        self.assertEqual('token-B',replies[0][1]['token'])
        self.assertTrue(self.calls[-1][0].endswith('/303/likes'))
        self.assertEqual({"comment_id":"555"},self.calls[-1][1]['data'])

    def test_failed_like_does_not_duplicate_reply(self):
        self.comment()
        provider=self.social.transport
        def fail_like(url,**kw):
            if url.endswith('/likes'): raise SocialError("Permission denied",403)
            return provider(url,**kw)
        self.social.transport=fail_like
        self.assertEqual("needs_attention",self.reply_comment()["reaction_state"])
        self.reply_comment()
        self.assertEqual(1,len([c for c in self.calls if c[0].endswith('/101_555/comments')]))

    def test_private_reply_once_by_comment_id(self):
        self.comment()
        body={"org":ORG,"action":"private_reply","platform":"facebook","account_id":"101","comment_id":"101_555","message":"Thanks for your interest!"}
        result=self.social.handle(body)
        self.assertEqual("sent-1",result["message_id"])
        self.assertEqual(result,self.social.handle(body))
        sent=[c for c in self.calls if c[0].endswith('/101/messages')]
        self.assertEqual(1,len(sent))
        self.assertEqual({"comment_id":"101_555"},sent[0][1]['data']['recipient'])
        with self.assertRaises(SocialError): self.social.handle({**body,"message":"Different text"})
        with self.assertRaises(SocialError): self.social.handle({**body,"org":OTHER})
        with self.assertRaises(SocialError): self.social.handle({**body,"comment_id":"101_777"})
        self.comment(cid="101_600")
        self.now += 7*86400
        with self.assertRaises(SocialError): self.social.handle({**body,"comment_id":"101_600"})

if __name__ == '__main__': unittest.main()
