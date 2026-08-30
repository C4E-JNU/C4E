"""
C4EAI HTTP 服务器 — 纯前端开发用，禁用缓存
用法: python server.py
"""
import http.server
import socketserver
import os
import json
import urllib.request
import urllib.parse

os.chdir(os.path.dirname(os.path.abspath(__file__)))

class C4EAIHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api/proxy/search'):
            self._handle_search()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/proxy/fetch':
            self._handle_fetch()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')

    def _handle_search(self):
        """代理搜索：前端 -> 本服务器 -> DuckDuckGo -> 返回结果"""
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        query = params.get('q', [''])[0]
        limit = int(params.get('limit', ['5'])[0])

        if not query:
            self._send_json({'error': 'query is required'})
            return

        try:
            url = 'https://lite.duckduckgo.com/lite/?q=' + urllib.parse.quote(query)
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10)
            html = resp.read().decode('utf-8', errors='replace')

            # 解析结果
            import re
            results = []
            rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL)
            current = None
            for row in rows:
                link = re.search(r'<a[^>]*href="([^"]*)"[^>]*>([\s\S]*?)</a>', row)
                snippet = re.search(r'class="result-snippet"[^>]*>([\s\S]*?)</td>', row)
                if link and 'result-snippet' not in row:
                    current = {
                        'title': re.sub(r'<[^>]+>', '', link.group(2)).strip(),
                        'url': link.group(1)
                    }
                if snippet and current:
                    results.append({
                        'title': current['title'],
                        'url': current['url'],
                        'snippet': re.sub(r'<[^>]+>', '', snippet.group(1)).strip()
                    })
                    current = None

            self._send_json({
                'source': 'duckduckgo',
                'count': len(results[:limit]),
                'results': results[:limit]
            })
        except Exception as e:
            self._send_json({'error': str(e)})

    def _handle_fetch(self):
        """代理抓取：前端 -> 本服务器 -> 目标网页 -> 返回内容"""
        content_len = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}
        url = body.get('url', '')

        if not url:
            self._send_json({'error': 'url is required'})
            return

        # 安全限制：禁止内网
        blocked = ['localhost', '127.0.0.1', '0.0.0.0', '10.', '192.168.', '172.16.']
        for b in blocked:
            if b in url:
                self._send_json({'error': '不允许访问内网地址'})
                return

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            resp = urllib.request.urlopen(req, timeout=15)
            html = resp.read().decode('utf-8', errors='replace')

            # 提取纯文本
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'&nbsp;', ' ', text)
            text = re.sub(r'&amp;', '&', text)
            text = re.sub(r'&lt;', '<', text)
            text = re.sub(r'&gt;', '>', text)
            text = re.sub(r'\s+', ' ', text).strip()

            title = ''
            m = re.search(r'<title[^>]*>([\s\S]*?)</title>', html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()

            max_len = 15000
            if len(text) > max_len:
                text = text[:max_len] + f'\n\n...（截断至 {max_len} 字符）'

            self._send_json({
                'url': url,
                'title': title,
                'content_length': len(text),
                'content': text
            })
        except Exception as e:
            self._send_json({'error': f'抓取失败: {str(e)}'})

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(('127.0.0.1', 8080), C4EAIHandler)
print('Serving at http://127.0.0.1:8080/ (no-cache, proxy enabled)')
print('  /api/proxy/search?q=...  — 搜索代理')
print('  POST /api/proxy/fetch     — 网页抓取代理')
httpd.serve_forever()
