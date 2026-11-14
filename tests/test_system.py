"""Offline integration tests. No API credits, credentials, or real CMS are used."""
import asyncio
import json
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from seo_agent.api import create_app
from seo_agent.content import sanitize
from seo_agent.network import PublicHTTP, ServiceError, normalize_url
from seo_agent.wordpress import WordPress


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, start_worker=False)
        self.context = TestClient(self.app, base_url="http://localhost")
        self.client = self.context.__enter__()
        self.client.headers["x-csrf-token"] = self.client.get("/api/bootstrap").json()["csrf_token"]
        self.db, self.worker = self.app.state.db, self.app.state.worker

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def post(self, path, body=None):
        return self.client.post(path, json=body or {})

    def demo(self):
        site = self.post("/api/demo").json()["id"]
        article = self.client.get(f"/api/sites/{site}/articles").json()[0]
        return site, article

    def real_site_from_demo(self):
        site, article = self.demo()
        self.db.execute("UPDATE sites SET demo=0,connection=? WHERE id=?", (json.dumps({"user":"writer","can_publish":True,"categories":[]}),site))
        return site, article

    def execute_job(self, job_id):
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        asyncio.run(self.worker.handle(job))
        self.db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))

    def test_bootstrap_static_and_csrf(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/api/sites").json(), [])
        self.assertEqual(self.client.post("/api/demo", headers={"x-csrf-token":"invalid"}).status_code,403)
        self.assertEqual(self.client.post("/api/demo", headers={"origin":"https://evil.example"}).status_code,403)
        self.assertEqual(self.client.get("/api/sites", headers={"host":"evil.example"}).status_code,400)

    def test_demo_is_explicit_idempotent_and_cannot_publish(self):
        site, a = self.demo()
        self.assertEqual(self.post("/api/demo").json()["id"], site)
        self.assertEqual(len(self.client.get("/api/sites").json()),1)
        self.assertEqual(self.post(f"/api/articles/{a['id']}/sync", {"revision":1,"status":"draft"}).status_code,400)

    def test_credentials_encrypted_and_not_returned(self):
        site, _ = self.real_site_from_demo()
        secret="test application password 12345"
        with patch.object(WordPress,"connect",new=AsyncMock(return_value={"user":"writer","categories":[],"can_publish":True})):
            response=self.post(f"/api/sites/{site}/connect",{"username":"writer","password":secret})
        self.assertEqual(response.status_code,200)
        stored=self.db.one("SELECT credentials FROM sites WHERE id=?",(site,))["credentials"]
        self.assertNotIn(secret,stored)
        self.assertIn(secret,self.app.state.vault.decrypt(stored))
        self.assertNotIn(secret,self.client.get("/api/sites").text)
        self.assertNotIn("credentials",self.client.get("/api/sites").text)
        invalid=self.post(f"/api/sites/{site}/connect",{"username":"writer","password":"abc"})
        self.assertNotIn('"abc"',invalid.text)

    def test_encrypted_model_key_and_settings_roundtrip(self):
        response=self.client.put("/api/settings",json={"model":"gpt-4.1-mini","api_key":"sk-private-test-secret"})
        self.assertEqual(response.status_code,200)
        self.assertNotIn("sk-private-test-secret",self.db.setting("openai_key"))
        self.assertNotIn("sk-private-test-secret",self.client.get("/api/bootstrap").text)

    def test_approval_is_revision_specific_and_html_sanitized(self):
        _,a=self.demo()
        self.assertTrue(a["quality"]["can_publish"])
        self.assertEqual(self.post(f"/api/articles/{a['id']}/approve",{"revision":1,"confirmed":True}).status_code,200)
        edit={key:a[key] for key in ["title","slug","meta_description","excerpt","content_html"]}
        edit.update(revision=1,content_html=a["content_html"]+'<img src=x onerror=alert(1)><script>alert(1)</script><a href="javascript:alert(1)">x</a>')
        response=self.client.put(f"/api/articles/{a['id']}",json=edit)
        self.assertEqual(response.status_code,200)
        updated=response.json()
        self.assertEqual(updated["revision"],2)
        self.assertIsNone(updated["approved_revision"])
        self.assertNotIn("<script",updated["content_html"])
        self.assertNotIn("onerror",updated["content_html"])
        self.assertNotIn("javascript:",updated["content_html"])
        self.assertEqual(self.client.put(f"/api/articles/{a['id']}",json=edit).status_code,409)
        self.assertEqual(self.post(f"/api/articles/{a['id']}/approve",{"revision":1,"confirmed":True}).status_code,400)

    def test_publish_requires_approval_confirmation_and_future_time(self):
        site,a=self.real_site_from_demo()
        url=f"/api/articles/{a['id']}/sync"
        self.assertEqual(self.post(url,{"revision":1,"status":"publish","confirmed":True}).status_code,400)
        self.post(f"/api/articles/{a['id']}/approve",{"revision":1,"confirmed":True})
        self.assertEqual(self.post(url,{"revision":1,"status":"publish","confirmed":False}).status_code,400)
        self.assertEqual(self.post(url,{"revision":1,"status":"future","confirmed":True,"scheduled_at":"2020-01-01T10:00:00Z"}).status_code,400)
        future=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
        result=self.post(url,{"revision":1,"status":"future","confirmed":True,"scheduled_at":future})
        self.assertEqual(result.status_code,200)
        self.assertIn("date_gmt",json.loads(self.db.one("SELECT payload FROM jobs WHERE id=?",(result.json()["id"],))["payload"]))
        self.assertEqual(self.post(url,{"revision":1,"status":"draft"}).status_code,400)
        self.assertEqual(self.client.put(f"/api/articles/{a['id']}",json={**{k:a[k] for k in ["title","slug","meta_description","excerpt","content_html"]},"revision":1}).status_code,400)

    def test_wordpress_timeout_recovery_is_read_only(self):
        site,a=self.real_site_from_demo()
        fake=AsyncMock()
        fake.connect.return_value={"user":"writer","can_publish":True,"categories":[]}
        fake.sync.side_effect=ServiceError("ambiguous timeout")
        with patch.object(self.worker,"wp",return_value=fake):
            job=self.post(f"/api/articles/{a['id']}/sync",{"revision":1,"status":"draft"}).json()["id"]
            with self.assertRaises(ServiceError):self.execute_job(job)
        self.assertEqual(self.client.get(f"/api/articles/{a['id']}").json()["status"],"uncertain")
        self.db.execute("UPDATE jobs SET status='failed' WHERE id=?",(job,))
        fake.find.return_value={"id":45,"status":"draft","link":"https://example.com/?p=45"}
        fake.sync.reset_mock()
        with patch.object(self.worker,"wp",return_value=fake):
            job=self.post(f"/api/articles/{a['id']}/sync",{"revision":1,"status":"draft"}).json()["id"]
            self.execute_job(job)
        self.assertEqual(self.client.get(f"/api/articles/{a['id']}").json()["wp_id"],45)
        fake.sync.assert_not_awaited()

    def test_analysis_to_article_pipeline_offline(self):
        result=self.post("/api/sites",{"name":"Test store","url":"https://store.example","profile":{"business":"A bookstore for students"}})
        self.assertEqual(result.status_code,201)
        site=result.json()["id"]
        analysis={"pages":[{"title":"Home","url":"https://store.example","text":"A bookstore"}],"cms":"WordPress","warnings":[],"sitemap_found":True}
        writer=AsyncMock()
        writer.plan.return_value={"summary":"Bookstore","audience":"Students","topics":[{"title":"How to choose a textbook","keyword":"textbook","intent":"informational","rationale":"Relevant","outline":["Choose","Compare"]}]}
        writer.draft.return_value={"title":"Choosing a textbook for study","slug":"choose-textbook","meta_description":"A practical guide to choosing a textbook for your studies, based on goals and reading needs.","excerpt":"Guide","brief":"Help students", "content_html":"<h2>Choose</h2><p>"+"Useful reading guidance for students. "*100+"</p><h2>Compare</h2><a href=\"https://invented.example/claim\">Unknown</a>","sources":[{"title":"Real","url":"https://store.example"},{"title":"Invented","url":"https://invented.example"}],"review_notes":["Review evidence"]}
        with patch.object(self.worker.crawler,"scan",new=AsyncMock(return_value=analysis)),patch.object(self.worker,"writer",return_value=writer):
            job=self.worker.enqueue(site,"pipeline",{"count":1,"upload_drafts":False})["id"]
            self.execute_job(job)
        articles=self.client.get(f"/api/sites/{site}/articles").json()
        self.assertEqual(len(articles),1)
        self.assertEqual(articles[0]["status"],"draft")
        self.assertEqual(len(articles[0]["sources"]),1)
        self.assertNotIn("https://invented.example",articles[0]["content_html"])
        self.assertIsNone(articles[0]["approved_revision"])
        self.assertEqual(self.client.get(f"/api/articles/{articles[0]['id']}/export").status_code,200)

    def test_cancelled_job_does_not_start(self):
        site,_=self.real_site_from_demo()
        job=self.worker.enqueue(site,"generate",{})["id"]
        self.assertEqual(self.post(f"/api/jobs/{job}/cancel").status_code,200)
        self.assertTrue(self.db.one("SELECT cancel_requested FROM jobs WHERE id=?",(job,))["cancel_requested"])


class NetworkTests(unittest.TestCase):
    def test_private_addresses_and_credentials_blocked(self):
        for url in ["http://example.com","https://127.0.0.1","https://[::1]","https://10.0.0.1","https://169.254.169.254","https://localhost","https://foo.local","https://user:pass@example.com","https://example.com:8443"]:
            with self.subTest(url=url),self.assertRaises(ServiceError):normalize_url(url)

    def test_private_dns_rejected_before_http(self):
        async def check():
            with patch.object(asyncio.get_running_loop(),"getaddrinfo",new=AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))])):
                with self.assertRaises(ServiceError):await PublicHTTP().request("GET","https://external.example")
        asyncio.run(check())

    def test_pin_dns_and_keep_tls_hostname(self):
        observed=[]
        async def responder(request):
            observed.append(request)
            return httpx.Response(200,text="ok")
        original=httpx.AsyncClient
        def client_factory(**kwargs):
            return original(**kwargs,transport=httpx.MockTransport(responder))
        async def check():
            with patch.object(asyncio.get_running_loop(),"getaddrinfo",new=AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))])),patch("seo_agent.network.httpx.AsyncClient",side_effect=client_factory):
                result=await PublicHTTP().request("GET","https://example.com/path")
                self.assertEqual(result.status_code,200)
        asyncio.run(check())
        self.assertEqual(observed[0].url.host,"93.184.216.34")
        self.assertEqual(observed[0].headers["host"],"example.com")
        self.assertEqual(observed[0].extensions["sni_hostname"],"example.com")

    def test_body_limit_and_auth_redirect(self):
        original=httpx.AsyncClient
        async def run_case(response,**kwargs):
            def factory(**opts):return original(**opts,transport=httpx.MockTransport(lambda r:response))
            with patch.object(asyncio.get_running_loop(),"getaddrinfo",new=AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))])),patch("seo_agent.network.httpx.AsyncClient",side_effect=factory):
                with self.assertRaises(ServiceError):await PublicHTTP().request("GET","https://example.com",**kwargs)
        asyncio.run(run_case(httpx.Response(200,content=b"x"*100),limit=10))
        asyncio.run(run_case(httpx.Response(302,headers={"location":"https://evil.example"}),auth=httpx.BasicAuth("u","p")))


if __name__=="__main__":
    unittest.main()
