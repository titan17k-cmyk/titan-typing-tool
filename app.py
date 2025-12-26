"""
TITAN'S TYPING TOOL - Discord Auto Typer + VC Join
"""

from flask import Flask, render_template, request, jsonify
import requests
import threading
import time
import os
import json
import websocket

app = Flask(__name__)

active_jobs = {}
vc_connections = {}

def send_discord_message(token, channel_id, message):
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.post(url, headers=headers, json={"content": message})
    return response.status_code == 200

def vc_heartbeat(ws, interval):
    """Send heartbeat to keep VC connection alive"""
    while True:
        try:
            time.sleep(interval / 1000)
            ws.send(json.dumps({"op": 1, "d": None}))
        except:
            break

def connect_to_vc(token, guild_id, channel_id, connection_id):
    """Connect to voice channel using Discord Gateway"""
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://gateway.discord.gg/?v=9&encoding=json")
        
        # Receive hello
        hello = json.loads(ws.recv())
        heartbeat_interval = hello["d"]["heartbeat_interval"]
        
        # Start heartbeat thread
        hb_thread = threading.Thread(target=vc_heartbeat, args=(ws, heartbeat_interval))
        hb_thread.daemon = True
        hb_thread.start()
        
        # Identify
        identify = {
            "op": 2,
            "d": {
                "token": token,
                "properties": {
                    "$os": "windows",
                    "$browser": "chrome",
                    "$device": "pc"
                },
                "presence": {
                    "status": "online",
                    "afk": False
                }
            }
        }
        ws.send(json.dumps(identify))
        
        # Wait for ready
        while True:
            msg = json.loads(ws.recv())
            if msg.get("t") == "READY":
                break
        
        # Join voice channel
        voice_state = {
            "op": 4,
            "d": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "self_mute": True,
                "self_deaf": True
            }
        }
        ws.send(json.dumps(voice_state))
        
        vc_connections[connection_id] = {
            "status": "connected",
            "ws": ws,
            "guild_id": guild_id,
            "channel_id": channel_id
        }
        
        # Keep connection alive
        while vc_connections.get(connection_id, {}).get("status") == "connected":
            try:
                ws.recv()
            except:
                break
        
        ws.close()
        
    except Exception as e:
        vc_connections[connection_id] = {"status": "error", "error": str(e)}

def typing_worker(job_id, token, channel_id, lines, delay):
    job = active_jobs[job_id]
    job["status"] = "running"
    job["current"] = 0
    job["last_message"] = ""
    
    for i, line in enumerate(lines):
        if job["status"] != "running":
            break
        
        job["current"] = i + 1
        job["last_message"] = line
        
        success = send_discord_message(token, channel_id, line)
        
        if not success:
            job["status"] = "error"
            job["error"] = "Failed to send. Check token/channel."
            return
        
        if i < len(lines) - 1:
            time.sleep(delay)
    
    if job["status"] == "running":
        job["status"] = "completed"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_typing():
    token = request.form.get('token')
    channel_id = request.form.get('channel_id')
    delay = int(request.form.get('delay', 60))
    
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "No file uploaded"}), 400
    
    content = file.read().decode('utf-8')
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    
    if not lines:
        return jsonify({"error": "File is empty"}), 400
    
    job_id = f"job_{int(time.time())}"
    active_jobs[job_id] = {
        "status": "starting",
        "total": len(lines),
        "current": 0,
        "last_message": "",
        "delay": delay
    }
    
    thread = threading.Thread(target=typing_worker, args=(job_id, token, channel_id, lines, delay))
    thread.daemon = True
    thread.start()
    
    return jsonify({"job_id": job_id, "total_lines": len(lines)})

@app.route('/status/<job_id>')
def get_status(job_id):
    if job_id not in active_jobs:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(active_jobs[job_id])

@app.route('/stop/<job_id>', methods=['POST'])
def stop_typing(job_id):
    if job_id in active_jobs:
        active_jobs[job_id]["status"] = "stopped"
        return jsonify({"message": "Stopped"})
    return jsonify({"error": "Job not found"}), 404

@app.route('/vc/connect', methods=['POST'])
def vc_connect():
    data = request.json
    token = data.get('token')
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')
    
    if not all([token, guild_id, channel_id]):
        return jsonify({"error": "Missing token, guild_id or channel_id"}), 400
    
    connection_id = f"vc_{guild_id}_{channel_id}"
    
    # Check if already connected
    if connection_id in vc_connections and vc_connections[connection_id].get("status") == "connected":
        return jsonify({"status": "already_connected", "connection_id": connection_id})
    
    vc_connections[connection_id] = {"status": "connecting"}
    
    thread = threading.Thread(target=connect_to_vc, args=(token, guild_id, channel_id, connection_id))
    thread.daemon = True
    thread.start()
    
    # Wait a bit for connection
    time.sleep(2)
    
    return jsonify({
        "connection_id": connection_id,
        "status": vc_connections.get(connection_id, {}).get("status", "unknown")
    })

@app.route('/vc/disconnect', methods=['POST'])
def vc_disconnect():
    data = request.json
    connection_id = data.get('connection_id')
    
    if connection_id in vc_connections:
        vc_connections[connection_id]["status"] = "disconnected"
        if "ws" in vc_connections[connection_id]:
            try:
                vc_connections[connection_id]["ws"].close()
            except:
                pass
        return jsonify({"message": "Disconnected"})
    
    return jsonify({"error": "Connection not found"}), 404

@app.route('/vc/status/<connection_id>')
def vc_status(connection_id):
    if connection_id not in vc_connections:
        return jsonify({"status": "not_found"})
    return jsonify({"status": vc_connections[connection_id].get("status", "unknown")})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
