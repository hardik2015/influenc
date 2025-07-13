from flask import Flask, request, jsonify
import os, json, requests, sys
from threading import Thread
from pathlib import Path
from requests.auth import HTTPBasicAuth

# ——— CONFIG ———
COMFY_API_BASE = os.getenv("COMFY_API_BASE", "http://localhost:8188/api")
COMFY_API_PROMPT = f"{COMFY_API_BASE}/prompt"
COMFY_API_QUEUE = f"{COMFY_API_BASE}/queue"
PROMPT_NODE_ID = "163"
UPLOAD_FOLDER = "../ComfyUI/output/"
HOST = "0.0.0.0"
PORT = 5010

# Accept WebDAV credentials from command line
if len(sys.argv) < 4:
    print("Usage: python script.py <webdav_url> <username> <password>")
    sys.exit(1)

WEBDAV_URL = sys.argv[1]
WEBDAV_USERNAME = sys.argv[2]
WEBDAV_PASSWORD = sys.argv[3]

# Upload status
upload_status = {
    "total": 0,
    "uploaded": 0,
    "errors": []
}

app = Flask(__name__)

# ——— Utility Functions ———

def read_all_lines(filename):
    path = os.path.join(filename)
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def build_prompt(main_prompt: str):
    prefix_lines = read_all_lines('prefix.txt')
    suffix_lines = read_all_lines('suffix.txt')
    return ', '.join(prefix_lines + [main_prompt] + suffix_lines)

def load_workflow():
    workflow_path = os.path.join(os.getcwd(), "influn.json")
    if not os.path.exists(workflow_path):
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
    with open(workflow_path, encoding="utf-8") as f:
        wf = json.load(f)
    return {"prompt": wf}

def inject_prompt(payload, prompt_text, node_id=PROMPT_NODE_ID):
    if "prompt" not in payload:
        raise ValueError("Payload missing 'prompt' key")
    node = payload["prompt"].get(node_id)
    if not node or "inputs" not in node:
        raise ValueError(f"Node ID {node_id} not found or missing inputs in workflow.")
    print(prompt_text)
    node["inputs"]["wildcard_text"] = prompt_text
    return payload

def send_to_comfy(payload):
    try:
        resp = requests.post(COMFY_API_PROMPT, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data
    except requests.exceptions.RequestException as e:
        print("❌ Error from ComfyUI API:", str(e))
        raise RuntimeError(f"Failed to send to ComfyUI API at {COMFY_API_PROMPT}: {str(e)}")

# ——— Upload Logic ———
def upload_to_nextcloud():
    global upload_status
    upload_status = {"total": 0, "uploaded": 0, "errors": []}
    files = list(Path(UPLOAD_FOLDER).glob("*"))
    upload_status["total"] = len(files)

    for file_path in files:
        try:
            with open(file_path, 'rb') as f:
                dest = f"{WEBDAV_URL.rstrip('/')}/{file_path.name}"
                print("✅ ComfyUI response:", dest)
                resp = requests.put(dest, data=f, auth=HTTPBasicAuth(WEBDAV_USERNAME, WEBDAV_PASSWORD))
                resp.raise_for_status()
                upload_status["uploaded"] += 1
        except Exception as e:
            upload_status["errors"].append({"file": file_path.name, "error": str(e)})

# ——— API Endpoints ———
@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json(force=True)
    prompts = data.get('prompts')
    if not prompts or not isinstance(prompts, list):
        return jsonify(status='error', message='Missing or invalid "prompts" field (expected list)'), 400

    results = []
    for p in prompts:
        if not isinstance(p, str):
            continue
        try:
            full_prompt = build_prompt(p)
            payload = load_workflow()
            payload = inject_prompt(payload, full_prompt)
            result = send_to_comfy(payload)
            results.append({"prompt": p, "status": "submitted", "response": result})
        except Exception as e:
            results.append({"prompt": p, "status": "error", "message": str(e)})

    return jsonify(status='success', results=results)

@app.route('/queue', methods=['GET'])
def check_queue():
    try:
        resp = requests.get(COMFY_API_QUEUE, timeout=5)
        resp.raise_for_status()
        return jsonify(status='success', queue=resp.json())
    except requests.RequestException as e:
        return jsonify(status='error', message=str(e)), 500

@app.route('/upload-files', methods=['POST'])
def start_upload():
    thread = Thread(target=upload_to_nextcloud)
    thread.start()
    return jsonify(status='started', message='Upload process initiated.')

@app.route('/upload-status', methods=['GET'])
def get_upload_status():
    return jsonify(status='success', upload_status=upload_status)

if __name__ == '__main__':
    app.run(host=HOST, port=PORT)
