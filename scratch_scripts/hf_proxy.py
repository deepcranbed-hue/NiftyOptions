import http.server
import socketserver
import urllib.request
import urllib.error
import json
import os

PORT = 5005

class HFProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default logger to keep output clean
        return

    def do_POST(self):
        # We only proxy completions
        if not self.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
            
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        # Hugging Face endpoint url mapped from the incoming request path
        # (Using the target DeepSeek-R1-Distill-Qwen-32B model)
        hf_url = "https://api-inference.huggingface.co/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-32B/v1/chat/completions"
        
        # Forward headers, specifically Authorization
        auth_header = self.headers.get('Authorization', '')
        headers = {
            "Content-Type": "application/json",
            "Authorization": auth_header
        }
        
        req = urllib.request.Request(hf_url, data=post_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                response_data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response_data)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

def run_proxy():
    # Allow port reuse
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), HFProxyHandler) as httpd:
        print(f"Hugging Face Local Proxy Tunnel running on port {PORT}...")
        httpd.serve_forever()

if __name__ == "__main__":
    run_proxy()
