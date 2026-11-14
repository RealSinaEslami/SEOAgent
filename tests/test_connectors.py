import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from seo_agent.ai import Plan, Writer
from seo_agent.content import sanitize
from seo_agent.crawler import Crawler
from seo_agent.network import ServiceError
from seo_agent.wordpress import WordPress


class ConnectorTests(unittest.TestCase):
    def test_only_grounded_hyperlinks_survive(self):
        content='<a title="test" href="https://invented.example">bad</a><a href="https://known.example?a=1&amp;b=2">good</a>'
        cleaned=sanitize(content,{"https://known.example?a=1&b=2"})
        self.assertNotIn("https://invented.example",cleaned)
        self.assertIn("https://known.example",cleaned)

    def test_wordpress_creates_then_updates_without_duplicate(self):
        http=AsyncMock()
        async def response(method,url,**kwargs):
            data=[] if method=="GET" and "/posts?" in url else {"id":80,"status":"draft","link":"https://example.com/seoagent-abc"}
            return httpx.Response(200,json=data)
        http.request.side_effect=response
        wp=WordPress(http,"https://example.com","author","app-password")
        article={"id":"abc","title":"Test title","content_html":"<p>Article</p>","excerpt":"Summary","wp_id":None}
        result=asyncio.run(wp.sync(article,"draft"))
        self.assertEqual(result["id"],80)
        self.assertEqual(http.request.call_args.kwargs["payload"]["slug"],"seoagent-abc")
        article["wp_id"]=80
        asyncio.run(wp.sync(article,"draft"))
        self.assertTrue(http.request.call_args.args[1].endswith("/posts/80"))
        self.assertNotIn("slug",http.request.call_args.kwargs["payload"])

    def test_wordpress_does_not_unpublish_remote_article(self):
        http=AsyncMock()
        http.request.return_value=httpx.Response(200,json={"id":80,"status":"publish"})
        wp=WordPress(http,"https://example.com","author","app-password")
        with self.assertRaises(ServiceError):
            asyncio.run(wp.sync({"id":"abc","wp_id":80},"draft"))
        self.assertEqual(http.request.await_count,1)

    def test_crawl_robots_sitemap_and_hidden_script_text(self):
        http=AsyncMock()
        async def response(method,url,**kwargs):
            if url.endswith("robots.txt"):
                return httpx.Response(200,text="User-agent: *\nDisallow: /private\n",request=httpx.Request(method,url))
            if url.endswith("sitemap.xml"):
                return httpx.Response(200,text='<urlset><url><loc>https://example.com/public</loc></url><url><loc>https://example.com/private</loc></url></urlset>',request=httpx.Request(method,url))
            return httpx.Response(200,headers={"content-type":"text/html"},text='<html><title>Public page</title><meta name="generator" content="WordPress"><h1>Hello</h1><p>Public content</p><script>secret_instruction()</script></html>',request=httpx.Request(method,url))
        http.request.side_effect=response
        with patch("seo_agent.crawler.asyncio.sleep",new=AsyncMock()):
            analysis=asyncio.run(Crawler(http).scan("https://example.com",AsyncMock()))
        self.assertEqual(len(analysis["pages"]),2)
        self.assertEqual(analysis["cms"],"WordPress")
        self.assertTrue(analysis["sitemap_found"])
        self.assertNotIn("secret_instruction",analysis["pages"][0]["text"])
        self.assertNotIn("https://example.com/private",[c.args[1] for c in http.request.call_args_list])

    def test_writer_uses_structured_output_without_secrets_in_prompt(self):
        client=AsyncMock()
        client.responses.parse.return_value=SimpleNamespace(status="completed",output_parsed=Plan(summary="Test",audience="People",topics=[]))
        manager=AsyncMock()
        manager.__aenter__.return_value=client
        with patch("seo_agent.ai.AsyncOpenAI",return_value=manager):
            result=asyncio.run(Writer("sk-only-client-secret","configured-model").plan({"business":"A store"},{"pages":[]},[]))
        args=client.responses.parse.call_args.kwargs
        self.assertEqual(args["model"],"configured-model")
        self.assertFalse(args["store"])
        self.assertEqual(args["text_format"],Plan)
        self.assertNotIn("sk-only-client-secret",str(args["input"]))
        self.assertEqual(result["summary"],"Test")


if __name__=="__main__":unittest.main()
