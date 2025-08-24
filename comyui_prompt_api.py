from flask import Flask, request, jsonify
import os, json, sys
from threading import Thread
from pathlib import Path
from imagekitio import ImageKit
from imagekitio.models.UploadFileRequestOptions import UploadFileRequestOptions
import traceback
from datetime import datetime
import random

# ——— CONFIG ———
COMFY_API_BASE = os.getenv("COMFY_API_BASE", "http://localhost:8188/api")
COMFY_API_PROMPT = f"{COMFY_API_BASE}/prompt"
COMFY_API_QUEUE = f"{COMFY_API_BASE}/queue"
PROMPT_NODE_ID = "558"
SEED_NODE_ID1="272"
SEED_NODE_ID2="291"
UPLOAD_FOLDER = "../ComfyUI/output/"
HOST = "0.0.0.0"
PORT = 5010

# Accept ImageKit credentials from command line
if len(sys.argv) < 5:
    print("Usage: python script.py <imagekit_private_key> <imagekit_public_key> <imagekit_url_endpoint> <comfyui-token>")
    sys.exit(1)

IMAGEKIT_PRIVATE_KEY = sys.argv[1]
IMAGEKIT_PUBLIC_KEY = sys.argv[2]
IMAGEKIT_URL_ENDPOINT = sys.argv[3]
COMFYUI_TOKEN_AUTH = sys.argv[4]

# Initialize ImageKit
imagekit = ImageKit(
    private_key=IMAGEKIT_PRIVATE_KEY,
    public_key=IMAGEKIT_PUBLIC_KEY,
    url_endpoint=IMAGEKIT_URL_ENDPOINT
)

# Upload status
upload_status = {
    "total": 0,
    "uploaded": 0,
    "files": [],
    "errors": []
}

app = Flask(__name__)

# ——— Utility Functions ———
def read_all_lines(filename):
    with open(filename, encoding="utf-8") as f:
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
        return {"prompt": json.load(f)}

def inject_prompt(payload, prompt_text, node_id=PROMPT_NODE_ID):
    node = payload["prompt"].get(node_id)
    seednode1 = payload["prompt"].get(SEED_NODE_ID1)
    seednode2 = payload["prompt"].get(SEED_NODE_ID2)
    if not node or "inputs" not in node:
        raise ValueError(f"Node ID {node_id} not found or missing inputs in workflow.")
    node["inputs"]["wildcard_string"] = prompt_text
    seednode1["inputs"]["seed"] = random.randint(10**13, 10**14 - 1)
    seednode2["inputs"]["seed"] = random.randint(10**13, 10**14 - 1)
    return payload

def send_to_comfy(payload):
    import requests
    headers = {"Authorization": f"Bearer {COMFYUI_TOKEN_AUTH}"}
    resp = requests.post(COMFY_API_PROMPT, json=payload, timeout=10, headers=headers)
    resp.raise_for_status()
    return resp.json()

# ——— Upload Logic (ImageKit SDK) ———
def upload_to_imagekit():
    global upload_status
    upload_status = {"total": 0, "uploaded": 0, "files": [], "errors": []}
    files = [f for f in Path(UPLOAD_FOLDER).glob("*") if f.is_file()]
    upload_status["total"] = len(files)

    for file_path in files:
        try:
            filename = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            extension = os.path.splitext(file_path)[1]  # e.g. ".jpg" / ".png"
            final_filename = f"{filename}{extension}"

            with open(file_path, "rb") as f:
                upload = imagekit.upload_file(
                    file=f,
                    file_name=final_filename,
                    options=UploadFileRequestOptions(folder="/Influncer/")
                )
                upload_status["uploaded"] += 1
                upload_status["files"].append({
                    "file": file_path.name,
                    "url": upload.url
                })
                print(f"✅ Uploaded: {upload.url}")    
            os.remove(file_path) 
        except Exception as e:
            upload_status["errors"].append({
                "file": file_path.name,
                "error": str(e)
            })
            print(traceback.format_exc())

# ——— API Endpoints ———
@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json(force=True)
    prompts = data.get('prompts')
    count = data.get('count')
    if not prompts or not isinstance(prompts, list):
        return jsonify(status='error', message='Missing or invalid "prompts" field (expected list)'), 400
    if not count or not isinstance(count, int):
        return jsonify(status='error', message='Missing or invalid "count" field (expected int)'), 400

    results = []
    for p in prompts:
        if not isinstance(p, str):
            continue
        try:
            full_prompt = build_prompt(p)
            payload = load_workflow()
            for counter in range(count):
                payload = inject_prompt(payload, full_prompt)
                result = send_to_comfy(payload)
                results.append({"prompt": p, "status": "submitted", "response": result})
        except Exception as e:
            results.append({"prompt": p, "status": "error", "message": str(e)})

    return jsonify(status='success', results=results)

@app.route('/queue', methods=['GET'])
def check_queue():
    import requests
    headers = {"Authorization": f"Bearer {COMFYUI_TOKEN_AUTH}"}
    resp = requests.get(COMFY_API_QUEUE, timeout=5, headers=headers)
    resp.raise_for_status()
    return jsonify(status='success', queue=resp.json())

@app.route('/upload-files', methods=['POST'])
def start_upload():
    thread = Thread(target=upload_to_imagekit)
    thread.start()
    return jsonify(status='started', message='Upload process to ImageKit initiated.')

@app.route('/upload-status', methods=['GET'])
def get_upload_status():
    return jsonify(status='success', upload_status=upload_status)

if __name__ == '__main__':
    app.run(host=HOST, port=PORT)
