import os
import sys
import socket
import ipaddress
import logging
import subprocess
import secrets
import threading
import webbrowser
import uuid
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from flask import Flask, request, jsonify, send_from_directory, render_template_string, send_file, session
from werkzeug.serving import make_server
from werkzeug.utils import safe_join, secure_filename
from functools import wraps
import qrcode
from PIL import ImageTk, Image
import io
import time
import json

APP_NAME = "NEXDROP"
APP_VERSION = "1.0"
DEVELOPER_NAME = "A.I.M.S"

# --- Dynamic Path Resolution (Works with both script and EXE) ---
def get_resource_path(relative_name):
    """Resolve bundled resources for script execution and PyInstaller EXE builds."""
    if getattr(sys, "_MEIPASS", False):
        return os.path.join(sys._MEIPASS, relative_name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_name)

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
APP_DATA_ROOT = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
LEGACY_APP_DATA_DIR = os.path.join(APP_DATA_ROOT, APP_NAME)
APP_DATA_DIR = os.path.join(BASE_DIR, f"{APP_NAME}_Data")
UPLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
LEGACY_UPLOAD_DIRS = {
    os.path.normcase(os.path.normpath(os.path.join(LEGACY_APP_DATA_DIR, "Shared_Folder"))),
    os.path.normcase(os.path.normpath(os.path.join(BASE_DIR, "Shared_Folder"))),
}
LOGO_PATH = get_resource_path("nexdrop logo.png")
ICON_IMAGE_PATH = get_resource_path("NEXDROP TRANSPARANT LOGO.png")
ICON_PATH = get_resource_path("nexdrop_icon.ico")
CONFIG_PATH = os.path.join(APP_DATA_DIR, "nexdrop_config.json")
LEGACY_CONFIG_PATHS = (
    os.path.join(LEGACY_APP_DATA_DIR, "nexdrop_config.json"),
    os.path.join(BASE_DIR, "nexdrop_config.json"),
)
HISTORY_PATH = os.path.join(APP_DATA_DIR, "transfer_history.json")
LEGACY_HISTORY_PATH = os.path.join(LEGACY_APP_DATA_DIR, "transfer_history.json")
UPLOAD_TEMP_DIR = os.path.join(APP_DATA_DIR, "upload_parts")
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024

# Ensure directories exist
os.makedirs(APP_DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)

# --- CONFIGURATION ---
class ServerConfig:
    def __init__(self):
        self.username = "admin"
        self.password = "1234"
        self.secret_key = secrets.token_hex(32)
        self.upload_folder = UPLOAD_DIR
        self.port = 5000
        # Set to practically unlimited for 100GB+ support
        self.max_content_length = 200 * 1024 * 1024 * 1024 
        self.is_running = False
        self.load()

    def load(self):
        """Load configuration from file if it exists."""
        config_path = next(
            (path for path in (CONFIG_PATH, *LEGACY_CONFIG_PATHS) if os.path.exists(path)),
            None,
        )
        if config_path is None:
            self.save()
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.username = data.get("username", self.username)
            saved_password = data.get("password", self.password)
            needs_password_migration = saved_password == "password123"
            self.password = "1234" if needs_password_migration else saved_password
            needs_secret_key = not data.get("secret_key")
            self.secret_key = data.get("secret_key") or self.secret_key
            self.port = int(data.get("port", self.port))
            upload_folder = data.get("upload_folder", self.upload_folder)
            legacy_default = (
                isinstance(upload_folder, str)
                and os.path.normcase(os.path.normpath(upload_folder)) in LEGACY_UPLOAD_DIRS
            )
            if legacy_default:
                upload_folder = UPLOAD_DIR
            try:
                os.makedirs(upload_folder, exist_ok=True)
                self.upload_folder = upload_folder
            except (OSError, TypeError):
                self.upload_folder = UPLOAD_DIR
                os.makedirs(self.upload_folder, exist_ok=True)
            if config_path != CONFIG_PATH or needs_secret_key or legacy_default or needs_password_migration:
                self.save()
        except Exception as e:
            print(f"Error loading config: {e}")
            self.save()

    def save(self):
        """Save configuration to file with error handling."""
        try:
            os.makedirs(APP_DATA_DIR, exist_ok=True)
            os.makedirs(self.upload_folder, exist_ok=True)
            data = {
                "username": self.username,
                "password": self.password,
                "secret_key": self.secret_key,
                "upload_folder": self.upload_folder,
                "port": self.port,
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving config to {CONFIG_PATH}: {e}")
            return False

config = ServerConfig()

# --- FLASK APP SETUP ---
app = Flask(__name__)
app.secret_key = config.secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
app.config['MAX_CONTENT_LENGTH'] = config.max_content_length
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

if not os.path.exists(config.upload_folder):
    os.makedirs(config.upload_folder)

# Global logs and GUI reference
gui_logs = []
app_gui = None
history_lock = threading.RLock()
upload_lock = threading.RLock()
upload_sessions = {}
completed_uploads = {}

def add_log(message):
    if app_gui:
        app_gui.update_log(message)
    gui_logs.insert(0, message)
    if len(gui_logs) > 50:
        gui_logs.pop()


def login_required(route):
    @wraps(route)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"success": False, "error": "Authentication required."}), 401
        return route(*args, **kwargs)
    return wrapped


def resolve_upload_path(relative_path):
    upload_root = os.path.realpath(config.upload_folder)
    resolved_path = safe_join(upload_root, relative_path or "")
    if resolved_path is None:
        return None
    resolved_path = os.path.realpath(resolved_path)
    try:
        if os.path.commonpath([upload_root, resolved_path]) != upload_root:
            return None
    except ValueError:
        return None
    return resolved_path


def load_transfer_history():
    history_path = HISTORY_PATH if os.path.exists(HISTORY_PATH) else LEGACY_HISTORY_PATH
    try:
        with history_lock:
            with open(history_path, "r", encoding="utf-8") as history_file:
                records = json.load(history_file)
        if not isinstance(records, list):
            return []
        if history_path != HISTORY_PATH:
            with history_lock:
                temporary_path = f"{HISTORY_PATH}.tmp"
                with open(temporary_path, "w", encoding="utf-8") as history_file:
                    json.dump(records, history_file, indent=2)
                os.replace(temporary_path, HISTORY_PATH)
        return records
    except (OSError, json.JSONDecodeError):
        return []


def record_transfer(direction, file_path):
    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "direction": direction,
        "name": os.path.basename(file_path),
        "path": os.path.abspath(file_path),
    }
    try:
        with history_lock:
            records = load_transfer_history()
            records.insert(0, record)
            temporary_path = f"{HISTORY_PATH}.tmp"
            with open(temporary_path, "w", encoding="utf-8") as history_file:
                json.dump(records[:1000], history_file, indent=2)
            os.replace(temporary_path, HISTORY_PATH)
    except OSError as e:
        add_log(f"Could not save transfer history: {e}")

    if app_gui:
        try:
            app_gui.root.after(0, app_gui.refresh_history)
        except (RuntimeError, tk.TclError):
            pass

def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("192.0.2.1", 80))
            ip = connection.getsockname()[0]
            if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_link_local:
                return ip
    except Exception:
        pass

    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            ip = sockaddr[0]
            address = ipaddress.ip_address(ip)
            if address.is_private and not address.is_loopback and not address.is_link_local:
                return ip
    except Exception:
        pass

    return None


def ensure_app_icon():
    if os.path.isfile(ICON_PATH):
        return ICON_PATH
    return ""


def ensure_lan_firewall_rule(port, parent):
    """Ask before allowing the sharing port on Windows private networks."""
    if os.name != "nt":
        return True

    rule_name = f"NEXDROP-LAN-{port}"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        check = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"if (Get-NetFirewallRule -DisplayName '{rule_name}' -ErrorAction SilentlyContinue) {{ 'present' }}"],
            capture_output=True,
            text=True,
            creationflags=creation_flags,
            timeout=15,
        )
        if check.returncode == 0 and check.stdout.strip().lower() == "present":
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    approved = messagebox.askyesno(
        "Allow phone connections?",
        f"Windows Firewall may block phones from opening NEXDROP. Add an inbound TCP rule for port {port}, limited to devices on the local subnet? Windows may ask for administrator approval.",
        parent=parent,
    )
    if not approved:
        return False

    rule_arguments = f"advfirewall firewall add rule name={rule_name} dir=in action=allow protocol=TCP localport={port} profile=any remoteip=localsubnet"
    elevated_command = (
        f"$rule = Start-Process -FilePath 'netsh.exe' -Verb RunAs -Wait -PassThru "
        f"-ArgumentList '{rule_arguments}'; exit $rule.ExitCode"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", elevated_command],
            capture_output=True,
            text=True,
            creationflags=creation_flags,
            timeout=120,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

# --- EMBEDDED HTML (Optimized for Mobile & Big Data) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>NEXDROP | Secure Transfer</title>
    <link rel="icon" type="image/png" href="/api/icon">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background: #0f172a; color: #f1f5f9; min-height: 100vh; font-family: 'Inter', sans-serif; overflow: hidden; }
        .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.1); }
        .item-card { transition: all 0.2s; border: 1px solid transparent; }
        .item-card:active { transform: scale(0.95); background: rgba(59, 130, 246, 0.2); }
        .upload-progress { height: 4px; transition: width 0.3s ease; }
        /* Custom scrollbar for big file lists */
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }
    </style>
</head>
<body class="flex flex-col h-screen">
    <!-- Login Screen -->
    <div id="loginScreen" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950 px-6">
        <div class="glass p-8 rounded-3xl w-full max-w-md text-center">
            <div class="w-20 h-20 mx-auto mb-6 flex items-center justify-center">
                <img src="/api/icon" alt="NEXDROP icon" class="w-16 h-16 object-contain" />
            </div>
            <h2 class="text-2xl font-bold mb-2">NEXDROP Secure Access</h2>
            <p class="text-slate-400 text-sm mb-8">Enter credentials to manage files</p>
            <form id="loginForm" class="space-y-4">
                <input type="text" id="username" placeholder="Admin Username" class="w-full bg-slate-800/50 border border-slate-700 rounded-xl p-4 text-white focus:border-blue-500 outline-none">
                <input type="password" id="password" placeholder="Password" class="w-full bg-slate-800/50 border border-slate-700 rounded-xl p-4 text-white focus:border-blue-500 outline-none">
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-4 rounded-xl font-bold transition-colors">Sign In</button>
            </form>
            <p id="loginError" class="text-red-400 text-sm mt-4 hidden">Unauthorized Access Attempt</p>
            <div class="mt-6 text-xs text-slate-500 border-t border-slate-700 pt-4">
                Developer: A.I.M.S
            </div>
        </div>
    </div>

    <!-- Main App -->
    <div id="appInterface" class="hidden flex flex-col h-full">
        <!-- Header -->
        <header class="glass p-4 sticky top-0 z-10 flex justify-between items-center">
            <div class="flex items-center gap-3">
                <img src="/api/icon" alt="NEXDROP icon" class="w-9 h-9 object-contain" />
                <h1 class="font-bold text-lg tracking-tight">NEXDROP</h1>
            </div>
            <div class="flex gap-4">
                <button onclick="logout()" class="text-slate-400 hover:text-red-400"><i class="fas fa-power-off text-lg"></i></button>
            </div>
        </header>

        <!-- Progress Bar (Hidden by default) -->
        <div id="uploadContainer" class="hidden bg-slate-900 border-b border-slate-800 p-3">
            <div class="flex justify-between text-xs mb-1">
                <span id="uploadStatus" class="truncate pr-3">Uploading...</span>
                <span id="uploadPercent">0%</span>
            </div>
            <div class="w-full bg-slate-800 rounded-full overflow-hidden">
                <div id="progressBar" class="upload-progress bg-blue-500 w-0"></div>
            </div>
        </div>

        <!-- Toolbar -->
        <div class="p-4 flex items-center gap-3">
            <button onclick="handleBack()" class="glass h-12 w-12 rounded-xl flex items-center justify-center text-slate-300">
                <i class="fas fa-chevron-left"></i>
            </button>
            <div class="flex-1 glass h-12 rounded-xl flex items-center px-4 overflow-hidden">
                <i class="fas fa-folder-open text-blue-400 mr-3"></i>
                <div id="pathDisplay" class="text-sm font-medium truncate text-slate-300">/root</div>
            </div>
            <label class="bg-blue-600 h-12 w-12 rounded-xl flex items-center justify-center cursor-pointer shadow-lg shadow-blue-900/40 active:scale-90 transition-transform">
                <i class="fas fa-plus text-white"></i>
                <input type="file" id="fileInput" multiple class="hidden" onchange="handleFileUpload(this)">
            </label>
        </div>

        <!-- File List -->
        <main class="flex-1 overflow-y-auto px-4 pb-6" id="fileGrid">
            <!-- Items injected here -->
        </main>
    </div>

    <script>
        let currentPath = "";
        const loginForm = document.getElementById('loginForm');
        
        // --- Navigation Logic for Mobile ---
        window.addEventListener('popstate', (event) => {
            if (event.state && event.state.path !== undefined) {
                currentPath = event.state.path;
                refreshFiles(false);
            }
        });

        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: username.value, password: password.value})
            });
            if ((await res.json()).success) {
                loginScreen.classList.add('hidden');
                appInterface.classList.remove('hidden');
                history.replaceState({path: ""}, "");
                refreshFiles();
            } else {
                loginError.classList.remove('hidden');
            }
        });

        async function refreshFiles(pushHistory = true) {
            const res = await fetch(`/api/files?path=${encodeURIComponent(currentPath)}`);
            const data = await res.json();
            
            pathDisplay.innerText = currentPath === "" ? "/root" : "/" + currentPath;
            
            if (pushHistory && currentPath !== (history.state ? history.state.path : null)) {
                history.pushState({path: currentPath}, "");
            }

            if (data.items.length === 0) {
                fileGrid.innerHTML = `
                    <div class="flex flex-col items-center justify-center h-64 opacity-30">
                        <i class="fas fa-box-open text-5xl mb-4"></i>
                        <p>No files in this directory</p>
                    </div>`;
                return;
            }

            fileGrid.innerHTML = data.items.map(item => `
                <div class="flex items-center glass p-4 rounded-2xl mb-3 item-card" onclick="${item.type==='folder' ? `enterFolder('${item.name}')` : `downloadFile('${item.name}')`}">
                    <div class="w-12 h-12 rounded-xl ${item.type==='folder' ? 'bg-amber-500/10 text-amber-500' : 'bg-blue-500/10 text-blue-500'} flex items-center justify-center text-xl mr-4">
                        <i class="fas ${item.type==='folder' ? 'fa-folder' : 'fa-file-alt'}"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <h3 class="text-sm font-semibold truncate text-slate-100">${item.name}</h3>
                        <p class="text-[10px] text-slate-500 uppercase tracking-widest">${item.type}</p>
                    </div>
                    <i class="fas ${item.type==='folder' ? 'fa-chevron-right text-slate-700' : 'fa-download text-blue-400'} text-xs"></i>
                </div>
            `).join('');
        }

        function handleBack() {
            if (currentPath === "") return;
            window.history.back();
        }

        function enterFolder(n) {
            currentPath = currentPath ? currentPath + '/' + n : n;
            refreshFiles();
        }

        function downloadFile(n) {
            window.location.href = `/api/download?path=${encodeURIComponent(currentPath)}&filename=${encodeURIComponent(n)}`;
        }

        async function logout() {
            try { await fetch('/api/logout', {method: 'POST'}); }
            finally { location.reload(); }
        }

        const uploadChunkBytes = 4 * 1024 * 1024;

        function sendUploadChunk(file, start, end, uploadId, folderPath, onProgress) {
            return new Promise((resolve, reject) => {
                const params = new URLSearchParams({
                    upload_id: uploadId,
                    filename: file.name,
                    path: folderPath,
                    total_size: String(file.size),
                    offset: String(start)
                });
                const xhr = new XMLHttpRequest();
                xhr.open('POST', `/api/upload/chunk?${params.toString()}`, true);
                xhr.timeout = 120000;
                xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                xhr.upload.onprogress = event => {
                    if (event.lengthComputable) onProgress(Math.min(event.loaded, end - start));
                };
                xhr.onload = () => {
                    let result = {};
                    try { result = JSON.parse(xhr.responseText); } catch (_) {}
                    if (xhr.status >= 200 && xhr.status < 300 && result.success) {
                        resolve(result);
                    } else {
                        const error = new Error(result.error || `Upload failed (HTTP ${xhr.status})`);
                        error.retryable = xhr.status >= 500;
                        reject(error);
                    }
                };
                xhr.onerror = () => {
                    const error = new Error('Network interrupted');
                    error.retryable = true;
                    reject(error);
                };
                xhr.ontimeout = () => {
                    const error = new Error('Upload request timed out');
                    error.retryable = true;
                    reject(error);
                };
                xhr.onabort = () => reject(new Error('Upload cancelled'));
                xhr.send(file.slice(start, end));
            });
        }

        async function sendUploadChunkWithRetry(file, start, end, uploadId, folderPath, onProgress) {
            for (let attempt = 0; attempt < 3; attempt++) {
                try {
                    return await sendUploadChunk(file, start, end, uploadId, folderPath, onProgress);
                } catch (error) {
                    if (!error.retryable || attempt === 2) throw error;
                    document.getElementById('uploadStatus').innerText = `Connection interrupted; retrying ${file.name} (${attempt + 1}/2)`;
                    await new Promise(resolve => setTimeout(resolve, 700 * (attempt + 1)));
                }
            }
        }

        // --- Mobile-friendly chunked uploads ---
        async function handleFileUpload(input) {
            if (input.files.length === 0) return;

            const uploadContainer = document.getElementById('uploadContainer');
            const progressBar = document.getElementById('progressBar');
            const uploadPercent = document.getElementById('uploadPercent');
            const uploadStatus = document.getElementById('uploadStatus');
            const files = Array.from(input.files);
            const totalUnits = files.reduce((sum, file) => sum + Math.max(file.size, 1), 0);
            let completedUnits = 0;

            uploadContainer.classList.remove('hidden');
            input.disabled = true;
            try {
                for (let fileIndex = 0; fileIndex < files.length; fileIndex++) {
                    const file = files[fileIndex];
                    const uploadId = Array.from({length: 32}, () => Math.floor(Math.random() * 16).toString(16)).join('');
                    const chunkCount = Math.max(1, Math.ceil(file.size / uploadChunkBytes));

                    for (let chunkIndex = 0; chunkIndex < chunkCount; chunkIndex++) {
                        const start = chunkIndex * uploadChunkBytes;
                        const end = Math.min(start + uploadChunkBytes, file.size);
                        uploadStatus.innerText = `Uploading ${file.name} (${fileIndex + 1}/${files.length})`;
                        const received = await sendUploadChunkWithRetry(file, start, end, uploadId, currentPath, loaded => {
                            const fileProgress = file.size === 0 ? 1 : start + loaded;
                            const percent = Math.floor(((completedUnits + fileProgress) / totalUnits) * 100);
                            progressBar.style.width = percent + '%';
                            uploadPercent.innerText = percent + '%';
                        });
                        if (received.complete) completedUnits += Math.max(file.size, 1);
                    }
                }
                uploadStatus.innerText = 'Upload complete';
                uploadPercent.innerText = '100%';
                progressBar.style.width = '100%';
                await refreshFiles();
                setTimeout(() => uploadContainer.classList.add('hidden'), 1800);
            } catch (error) {
                uploadStatus.innerText = `Upload paused: ${error.message}. Select the file again to retry.`;
            } finally {
                input.disabled = false;
                input.value = '';
            }
        }
    </script>
</body>
</html>
"""

# --- FLASK ROUTES ---
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/api/logo')
def app_logo():
    if os.path.exists(LOGO_PATH):
        return send_file(LOGO_PATH, mimetype='image/png')
    return "", 404

@app.route('/api/icon')
def app_icon_image():
    try:
        with Image.open(ICON_IMAGE_PATH) as source:
            icon_image = source.convert("RGBA")
            icon_bounds = icon_image.getchannel("A").getbbox()
            if icon_bounds:
                icon_image = icon_image.crop(icon_bounds)
            icon_buffer = io.BytesIO()
            icon_image.save(icon_buffer, format="PNG")
            icon_buffer.seek(0)
        return send_file(icon_buffer, mimetype="image/png")
    except (OSError, ValueError):
        return "", 404

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    if data.get('username') == config.username and data.get('password') == config.password:
        session.clear()
        session["authenticated"] = True
        add_log(f"Access Granted: {request.remote_addr}")
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/api/files', methods=['GET'])
@login_required
def list_files():
    subpath = request.args.get('path', '')
    target_dir = resolve_upload_path(subpath)
    if target_dir is None:
        return jsonify({"items": [], "error": "Invalid folder path."}), 400
    if not os.path.exists(target_dir): return jsonify({"items": []})
    
    items = []
    try:
        # Optimized for directories with large number of files
        with os.scandir(target_dir) as entries:
            for entry in entries:
                items.append({"name": entry.name, "type": "folder" if entry.is_dir() else "file"})
    except Exception as e:
        add_log(f"Error scanning dir: {str(e)}")
        
    items.sort(key=lambda x: (x['type'] != 'folder', x['name'].lower()))
    return jsonify({"items": items})

@app.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    files = request.files.getlist('files[]')
    path = request.form.get('path', '')
    save_path = resolve_upload_path(path)
    if save_path is None:
        return jsonify({"success": False, "error": "Invalid destination folder."}), 400
    os.makedirs(save_path, exist_ok=True)
    
    for f in files:
        # Secure filename handling + efficient saving
        filename = secure_filename(f.filename or "")
        if not filename:
            continue
        file_path = os.path.abspath(os.path.join(save_path, filename))
        f.save(file_path)
        record_transfer("Received", file_path)
        
    add_log(f"Processed {len(files)} files into /{path}")
    return jsonify({"success": True})

@app.route('/api/upload/chunk', methods=['POST'])
@login_required
def upload_file_chunk():
    try:
        upload_id = uuid.UUID(request.args.get("upload_id", "")).hex
        total_size = int(request.args.get("total_size", "-1"))
        offset = int(request.args.get("offset", "-1"))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid upload metadata."}), 400

    filename = secure_filename(request.args.get("filename", ""))
    if not filename:
        return jsonify({"success": False, "error": "Invalid file name."}), 400
    if total_size < 0 or total_size > config.max_content_length:
        return jsonify({"success": False, "error": "File size is not allowed."}), 413

    chunk_size = request.content_length
    if chunk_size is None and total_size == 0 and offset == 0:
        chunk_size = 0
    if chunk_size is None or chunk_size > UPLOAD_CHUNK_BYTES or offset < 0 or offset + chunk_size > total_size:
        return jsonify({"success": False, "error": "Invalid upload chunk."}), 400
    if total_size and chunk_size == 0:
        return jsonify({"success": False, "error": "Empty upload chunk."}), 400

    upload_root = os.path.realpath(config.upload_folder)
    relative_path = request.args.get("path", "")
    destination_dir = safe_join(upload_root, relative_path)
    if destination_dir is None:
        return jsonify({"success": False, "error": "Invalid destination folder."}), 400
    destination_dir = os.path.realpath(destination_dir)
    try:
        if os.path.commonpath([upload_root, destination_dir]) != upload_root:
            return jsonify({"success": False, "error": "Invalid destination folder."}), 400
    except ValueError:
        return jsonify({"success": False, "error": "Invalid destination folder."}), 400

    final_path = os.path.join(destination_dir, filename)
    temp_path = os.path.join(UPLOAD_TEMP_DIR, f"{upload_id}.part")
    session_details = (temp_path, final_path, total_size)

    with upload_lock:
        if upload_id in completed_uploads:
            completed_path, completed_size = completed_uploads[upload_id]
            if (completed_path, completed_size) == (final_path, total_size):
                return jsonify({"success": True, "complete": True, "received": total_size})
            return jsonify({"success": False, "error": "Upload identifier was already used."}), 409

        active_session = upload_sessions.get(upload_id)
        if active_session is None:
            if offset != 0:
                return jsonify({"success": False, "error": "Upload must start at byte zero."}), 409
            upload_sessions[upload_id] = session_details
        elif active_session != session_details:
            return jsonify({"success": False, "error": "Upload details changed during transfer."}), 409

        try:
            mode = "r+b" if os.path.exists(temp_path) else "w+b"
            with open(temp_path, mode) as output_file:
                output_file.seek(0, os.SEEK_END)
                current_size = output_file.tell()
                if offset > current_size or (offset < current_size and offset + chunk_size < current_size):
                    return jsonify({"success": False, "error": "Upload chunk is out of sequence."}), 409
                output_file.seek(offset)
                remaining = chunk_size
                while remaining:
                    block = request.stream.read(min(256 * 1024, remaining))
                    if not block:
                        output_file.truncate(offset)
                        return jsonify({"success": False, "error": "Connection ended before this chunk completed."}), 503
                    output_file.write(block)
                    remaining -= len(block)
                output_file.truncate(offset + chunk_size)
                output_file.flush()

            received = offset + chunk_size
            complete = received == total_size
            if complete:
                if os.path.getsize(temp_path) != total_size:
                    return jsonify({"success": False, "error": "Uploaded file size did not match."}), 400
                os.makedirs(destination_dir, exist_ok=True)
                os.replace(temp_path, final_path)
                upload_sessions.pop(upload_id, None)
                completed_uploads[upload_id] = (final_path, total_size)
                if len(completed_uploads) > 512:
                    completed_uploads.pop(next(iter(completed_uploads)))
            else:
                upload_sessions[upload_id] = session_details
        except OSError as e:
            return jsonify({"success": False, "error": f"Could not write upload chunk: {e}"}), 500

    if complete:
        record_transfer("Received", final_path)
        add_log(f"Received {filename} ({total_size} bytes) into {destination_dir}")
    return jsonify({"success": True, "complete": complete, "received": received})

@app.route('/api/download')
@login_required
def download_file():
    path = request.args.get('path', '')
    filename = request.args.get('filename', '')
    # Flask send_from_directory supports Range requests (needed for big files)
    directory = resolve_upload_path(path)
    if directory is None:
        return jsonify({"success": False, "error": "Invalid folder path."}), 400
    file_path = os.path.abspath(os.path.join(directory, filename))
    response = send_from_directory(directory, filename, as_attachment=True)
    record_transfer("Sent", file_path)
    return response

# --- GUI CLASS ---
class ServerGUI:
    def __init__(self, root):
        self.root = root
        self.web_server = None
        self.server_thread = None
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("980x700")
        self.root.configure(bg="#0f172a")

        icon_path = ensure_app_icon()
        if icon_path:
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass
        if os.path.isfile(ICON_IMAGE_PATH):
            try:
                with Image.open(ICON_IMAGE_PATH) as logo:
                    icon_image = logo.convert("RGBA")
                    icon_bounds = icon_image.getchannel("A").getbbox()
                    if icon_bounds:
                        icon_image = icon_image.crop(icon_bounds)
                    icon_image.thumbnail((64, 64), Image.LANCZOS)
                    self.app_icon_photo = ImageTk.PhotoImage(icon_image)
                self.root.iconphoto(True, self.app_icon_photo)
            except Exception:
                self.app_icon_photo = None

        # Main Layout: Sidebar and Content
        self.sidebar = tk.Frame(root, bg="#1e293b", width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content_area = tk.Frame(root, bg="#0f172a")
        self.content_area.pack(side="right", expand=True, fill="both")

        # Sidebar Header
        tk.Label(self.sidebar, text=APP_NAME, font=("Inter", 18, "bold"), bg="#1e293b", fg="#3b82f6").pack(pady=30)

        # Sidebar Buttons
        self.nav_buttons = {}
        pages_info = [("Home", "fa-home"), ("History", "fa-history"), ("Settings", "fa-cog"), ("Live Log", "fa-terminal"), ("About", "fa-info-circle")]
        for text, icon in pages_info:
            btn = tk.Button(self.sidebar, text=f"  {text}", font=("Inter", 11), 
                            bg="#1e293b", fg="#94a3b8", bd=0, activebackground="#334155", 
                            activeforeground="white", anchor="w", padx=25, pady=12,
                            command=lambda t=text: self.show_page(t))
            btn.pack(fill="x")
            self.nav_buttons[text] = btn

        # Status Bar in Sidebar
        self.status_frame = tk.Frame(self.sidebar, bg="#1e293b")
        self.status_frame.pack(side="bottom", fill="x", pady=20)
        self.lbl_status = tk.Label(self.status_frame, text="● SYSTEM OFFLINE", font=("Inter", 9, "bold"), bg="#1e293b", fg="#ef4444")
        self.lbl_status.pack()

        # Initialize Pages
        self.pages = {}
        self.pending_upload_folder = config.upload_folder
        self.init_home_page()
        self.init_history_page()
        self.init_settings_page()
        self.init_log_page()
        self.init_about_page()

        self.show_page("Home")

    def show_page(self, name):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(expand=True, fill="both", padx=40, pady=40)
        for t, btn in self.nav_buttons.items():
            if t == name:
                btn.config(bg="#334155", fg="white")
            else:
                btn.config(bg="#1e293b", fg="#94a3b8")
        if name == "History":
            self.refresh_history()

    def init_home_page(self):
        page = tk.Frame(self.content_area, bg="#0f172a")
        tk.Label(page, text="Network Dashboard", font=("Inter", 24, "bold"), bg="#0f172a", fg="white").pack(anchor="w")

        instructions = tk.Frame(page, bg="#1e293b", padx=20, pady=18)
        instructions.pack(fill="x", pady=(0, 12))
        tk.Label(instructions, text="How to use NEXDROP", font=("Inter", 12, "bold"), bg="#1e293b", fg="white").pack(anchor="w")
        tk.Label(
            instructions,
            text="1. Connect your computer and phone to the same Wi-Fi network. For better speed, use a 5 GHz Wi-Fi band or set your phone hotspot to 5 GHz when available.\n2. Open Settings, configure your login and storage folder, then save changes.\n3. Click INITIALIZE SERVER to start sharing.\n4. Scan the QR code or enter the displayed URL on your phone.\n5. Sign in, then upload or download files between your phone and computer.",
            font=("Inter", 10),
            bg="#1e293b",
            fg="#cbd5e1",
            justify="left",
            anchor="w",
            wraplength=700,
        ).pack(anchor="w", pady=(8, 0))
        
        # Connection Stats
        stats_frame = tk.Frame(page, bg="#1e293b", padx=20, pady=20)
        stats_frame.pack(fill="x", pady=10)
        
        self.qr_label = tk.Label(stats_frame, bg="#1e293b")
        self.qr_label.pack(side="left", padx=(0, 20))
        
        info_col = tk.Frame(stats_frame, bg="#1e293b")
        info_col.pack(side="left", fill="y")
        
        tk.Label(info_col, text="ACCESS URL", font=("Inter", 8, "bold"), bg="#1e293b", fg="#64748b").pack(anchor="w")
        self.lbl_url = tk.Label(info_col, text="Initialize to see URL", font=("Consolas", 12), bg="#1e293b", fg="#3b82f6")
        self.lbl_url.pack(anchor="w", pady=(0, 10))

        tk.Label(info_col, text="PHONE ACCESS", font=("Inter", 8, "bold"), bg="#1e293b", fg="#64748b").pack(anchor="w")
        tk.Label(info_col, text="Use same Wi‑Fi network on both devices", font=("Inter", 10), bg="#1e293b", fg="#94a3b8").pack(anchor="w")

        # Control
        btn_container = tk.Frame(page, bg="#0f172a")
        btn_container.pack(pady=40)
        
        self.btn_toggle = tk.Button(btn_container, text="INITIALIZE SERVER", font=("Inter", 12, "bold"), 
                                   bg="#3b82f6", fg="white", relief="flat", padx=50, pady=15,
                                   command=self.toggle_server)
        self.btn_toggle.pack()
        
        self.pages["Home"] = page

    def init_history_page(self):
        page = tk.Frame(self.content_area, bg="#0f172a")
        tk.Label(page, text="Transfer History", font=("Inter", 24, "bold"), bg="#0f172a", fg="white").pack(anchor="w")
        tk.Label(page, text="Double-click a transfer to open it, or choose an action below.", font=("Inter", 10), bg="#0f172a", fg="#64748b").pack(anchor="w", pady=(5, 16))

        list_frame = tk.Frame(page, bg="#1e293b")
        list_frame.pack(expand=True, fill="both")
        columns = ("time", "direction", "name")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Nexdrop.Treeview",
            background="#1e293b",
            fieldbackground="#1e293b",
            foreground="#e2e8f0",
            borderwidth=0,
            rowheight=30,
        )
        style.map(
            "Nexdrop.Treeview",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure("Nexdrop.Treeview.Heading", background="#334155", foreground="#cbd5e1", relief="flat")
        style.map("Nexdrop.Treeview.Heading", background=[("active", "#475569")])
        self.history_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse", style="Nexdrop.Treeview")
        self.history_tree.heading("time", text="TIME")
        self.history_tree.heading("direction", text="TRANSFER")
        self.history_tree.heading("name", text="FILE")
        self.history_tree.column("time", width=160, stretch=False)
        self.history_tree.column("direction", width=100, stretch=False)
        self.history_tree.column("name", width=420, anchor="w")
        history_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=history_scrollbar.set)
        self.history_tree.pack(side="left", expand=True, fill="both")
        history_scrollbar.pack(side="right", fill="y")
        self.history_tree.bind("<Double-1>", lambda event: self.open_selected_history_file())
        self.history_records = {}

        actions = tk.Frame(page, bg="#0f172a")
        actions.pack(anchor="center", pady=(12, 0))
        tk.Button(actions, text="OPEN FILE", command=self.open_selected_history_file, bg="#3b82f6", fg="white", relief="flat", padx=24, pady=10).pack(side="left", padx=(0, 8))
        tk.Button(actions, text="OPEN FOLDER", command=self.open_selected_history_folder, bg="#334155", fg="white", relief="flat", padx=24, pady=10).pack(side="left")

        self.pages["History"] = page
        self.refresh_history()

    def refresh_history(self):
        if not hasattr(self, "history_tree"):
            return
        for item_id in self.history_tree.get_children():
            self.history_tree.delete(item_id)
        self.history_records.clear()
        for index, record in enumerate(load_transfer_history()):
            if not isinstance(record, dict):
                continue
            item_id = f"transfer-{index}"
            self.history_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(record.get("timestamp", ""), record.get("direction", ""), record.get("name", "")),
            )
            self.history_records[item_id] = record

    def get_selected_history_path(self):
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showinfo("Transfer history", "Select a transfer first.", parent=self.root)
            return ""
        return self.history_records.get(selection[0], {}).get("path", "")

    def open_selected_history_file(self):
        file_path = self.get_selected_history_path()
        if not file_path:
            return
        if not os.path.isfile(file_path):
            messagebox.showwarning("File unavailable", "This transferred file is no longer available at its saved location.", parent=self.root)
            return
        self.open_local_path(file_path)

    def open_selected_history_folder(self):
        file_path = self.get_selected_history_path()
        if not file_path:
            return
        folder_path = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
        if not os.path.isdir(folder_path):
            messagebox.showwarning("Folder unavailable", "The folder containing this transfer is no longer available.", parent=self.root)
            return
        self.open_local_path(folder_path)

    def open_local_path(self, path):
        try:
            start_file = getattr(os, "startfile", None)
            if start_file:
                start_file(path)
            else:
                webbrowser.open(f"file://{os.path.abspath(path)}")
        except OSError as e:
            messagebox.showerror("Could not open item", str(e), parent=self.root)

    def init_settings_page(self):
        page = tk.Frame(self.content_area, bg="#0f172a")
        content = tk.Frame(page, bg="#0f172a")
        content.pack(expand=True, fill="both")
        panel = tk.Frame(
            content,
            bg="#0f172a",
            highlightbackground="#334155",
            highlightcolor="#3b82f6",
            highlightthickness=1,
            padx=24,
            pady=22,
        )
        panel.place(relx=0.5, rely=0.5, anchor="center", width=560)
        
        # Header
        header_frame = tk.Frame(panel, bg="#0f172a")
        header_frame.pack(anchor="w", pady=(0, 10))
        tk.Label(header_frame, text="Server Configuration", font=("Inter", 24, "bold"), bg="#0f172a", fg="white").pack(anchor="w")
        tk.Label(header_frame, text="Changes are saved only when you click Save Changes.", font=("Inter", 10), bg="#0f172a", fg="#64748b").pack(anchor="w")

        # Status message label
        self.settings_status = tk.Label(panel, text="", font=("Inter", 9, "bold"), bg="#0f172a", fg="#10b981", wraplength=520, justify="left")
        self.settings_status.pack(anchor="w", pady=(0, 15))

        fields_frame = tk.Frame(panel, bg="#0f172a", pady=16)
        fields_frame.pack(fill="x")
        fields_frame.grid_columnconfigure(0, weight=1)

        # Style entries
        entry_params = {"bg": "#1e293b", "fg": "white", "insertbackground": "white", "bd": 0, "font": ("Inter", 11)}

        tk.Label(fields_frame, text="Username", bg="#0f172a", fg="#94a3b8", font=("Inter", 9)).grid(row=0, column=0, sticky="w", pady=(10, 2))
        self.ent_user = tk.Entry(fields_frame, **entry_params, width=48)
        self.ent_user.insert(0, config.username)
        self.ent_user.grid(row=1, column=0, sticky="ew", ipadx=8, ipady=7, pady=(0, 14))

        tk.Label(fields_frame, text="Password", bg="#0f172a", fg="#94a3b8", font=("Inter", 9)).grid(row=2, column=0, sticky="w", pady=(10, 2))
        self.ent_pass = tk.Entry(fields_frame, **entry_params, show="*", width=48)
        self.ent_pass.insert(0, config.password)
        self.ent_pass.grid(row=3, column=0, sticky="ew", ipadx=8, ipady=7, pady=(0, 14))
        self.password_visible = tk.BooleanVar(value=False)
        tk.Checkbutton(
            fields_frame,
            text="Show password",
            variable=self.password_visible,
            command=lambda: self.ent_pass.config(show="" if self.password_visible.get() else "*"),
            bg="#0f172a",
            fg="#94a3b8",
            activebackground="#0f172a",
            activeforeground="white",
            selectcolor="#1e293b",
            font=("Inter", 9),
        ).grid(row=3, column=1, sticky="e", padx=(8, 0))

        tk.Label(fields_frame, text="Storage Path", bg="#0f172a", fg="#94a3b8", font=("Inter", 9)).grid(row=4, column=0, sticky="w", pady=(10, 2))
        path_row = tk.Frame(fields_frame, bg="#0f172a")
        path_row.grid(row=5, column=0, sticky="ew")
        
        self.lbl_fold = tk.Label(path_row, text=self.pending_upload_folder, bg="#1e293b", fg="#3b82f6", font=("Consolas", 10), anchor="w", padx=10, height=2)
        self.lbl_fold.pack(side="left", expand=True, fill="x")
        tk.Button(path_row, text="Choose Folder", command=self.change_folder, bg="#334155", fg="white", relief="flat", padx=15, pady=8).pack(side="right", fill="y", padx=(10, 0))

        # Button container
        button_frame = tk.Frame(panel, bg="#0f172a")
        button_frame.pack(fill="x", pady=(18, 4))
        button_frame.grid_columnconfigure(0, weight=1, uniform="settings-actions")
        button_frame.grid_columnconfigure(1, weight=1, uniform="settings-actions")

        tk.Button(button_frame, text="✓ SAVE CHANGES", bg="#10b981", fg="white", relief="flat", font=("Inter", 11, "bold"), pady=14, command=self.manual_save_settings).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tk.Button(button_frame, text="↺ RELOAD", bg="#64748b", fg="white", relief="flat", font=("Inter", 11, "bold"), pady=14, command=self.reload_settings).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        
        self.pages["Settings"] = page

    def init_log_page(self):
        page = tk.Frame(self.content_area, bg="#0f172a")
        tk.Label(page, text="System Log", font=("Inter", 24, "bold"), bg="#0f172a", fg="white").pack(anchor="w", pady=(0, 20))
        self.log_area = scrolledtext.ScrolledText(page, bg="#1e293b", fg="#10b981", font=("Consolas", 10), relief="flat", borderwidth=0)
        self.log_area.pack(expand=True, fill="both")
        self.pages["Live Log"] = page

    def init_about_page(self):
        page = tk.Frame(self.content_area, bg="#0f172a")
        content = tk.Frame(page, bg="#0f172a")
        content.place(relx=0.5, rely=0.5, anchor="center")

        logo_frame = tk.Frame(content, bg="#0f172a")
        logo_frame.pack(pady=(0, 20))

        try:
            if os.path.exists(LOGO_PATH):
                logo_img = Image.open(LOGO_PATH)
                logo_img = logo_img.resize((180, 180), Image.LANCZOS)
                logo_photo = ImageTk.PhotoImage(logo_img)
                self.about_logo = logo_photo
                tk.Label(logo_frame, image=logo_photo, bg="#0f172a").pack()
            else:
                tk.Label(logo_frame, text=APP_NAME, font=("Inter", 22, "bold"), bg="#0f172a", fg="#93c5fd").pack()
        except Exception:
            tk.Label(logo_frame, text=APP_NAME, font=("Inter", 22, "bold"), bg="#0f172a", fg="#93c5fd").pack()

        tk.Label(content, text=f"Version {APP_VERSION}", font=("Inter", 12), bg="#0f172a", fg="#93c5fd").pack()
        tk.Label(content, text=f"Developer: {DEVELOPER_NAME}", font=("Inter", 11), bg="#0f172a", fg="#cbd5e1").pack(pady=(5, 0))
        tk.Label(content, text="NEXDROP is a secure file-sharing platform designed for private cloud access, safe file transfer, and efficient team collaboration.", bg="#0f172a", fg="#64748b", justify="center", wraplength=560, font=("Inter", 11)).pack(pady=(20, 8))
        tk.Label(content, text="Features include secure login, remote file access, large file uploads, and persistent server configuration.", bg="#0f172a", fg="#64748b", justify="center", wraplength=560, font=("Inter", 11)).pack()
        self.pages["About"] = page

    def toggle_server(self):
        if not config.is_running:
            firewall_ready = ensure_lan_firewall_rule(config.port, self.root)
            try:
                self.web_server = make_server("0.0.0.0", config.port, app, threaded=True)
            except OSError as e:
                self.web_server = None
                messagebox.showerror(
                    "Server could not start",
                    f"Port {config.port} is unavailable. Close the other app using this port, then try again.\n\n{e}",
                    parent=self.root,
                )
                add_log(f"Server start failed on port {config.port}: {e}")
                return

            self.server_thread = threading.Thread(target=self.web_server.serve_forever, daemon=True)
            self.server_thread.start()
            config.is_running = True
            self.lbl_status.config(text="● SYSTEM ONLINE", fg="#10b981")
            self.btn_toggle.config(text="SHUTDOWN SERVER", bg="#ef4444")
            self.generate_qr()
            add_log(f"Network server started on port {config.port}.")
            if not firewall_ready:
                messagebox.showwarning(
                    "Firewall access not enabled",
                    "NEXDROP is running, but Windows Firewall permission was not added. Other devices may not be able to open the URL until you allow local-network connections for NEXDROP.",
                    parent=self.root,
                )
            if not get_local_ip():
                messagebox.showwarning(
                    "LAN address unavailable",
                    "The server is running, but no private network address was detected. Connect this computer and phone to the same Wi-Fi network, then restart the server.",
                    parent=self.root,
                )
        else:
            if self.web_server:
                self.web_server.shutdown()
                self.web_server.server_close()
                self.web_server = None
                self.server_thread = None
            config.is_running = False
            self.lbl_status.config(text="● SYSTEM OFFLINE", fg="#ef4444")
            self.btn_toggle.config(text="INITIALIZE SERVER", bg="#3b82f6")
            self.qr_label.config(image='')
            self.lbl_url.config(text="System Halted")
            add_log("Network daemon stopped.")

    def save_settings(self):
        self.manual_save_settings()

    def manual_save_settings(self):
        """Manually save settings and show confirmation."""
        previous_values = (config.username, config.password, config.upload_folder)
        try:
            config.username = self.ent_user.get().strip() or config.username
            config.password = self.ent_pass.get() or config.password
            config.upload_folder = self.pending_upload_folder
            if not config.save():
                config.username, config.password, config.upload_folder = previous_values
                self.settings_status.config(text="Settings could not be saved.", fg="#ef4444")
                return
            self.settings_status.config(text="✓ Settings saved successfully!", fg="#10b981")
            add_log("Server settings saved manually.")
            self.root.after(3000, lambda: self.settings_status.config(text=""))
        except Exception as e:
            self.settings_status.config(text=f"✗ Save failed: {str(e)[:30]}", fg="#ef4444")
            add_log(f"Error saving settings: {e}")
            print(f"Manual save error: {e}")

    def reload_settings(self):
        """Reload saved credentials and choose a storage folder."""
        folder = filedialog.askdirectory(
            title="Choose the storage folder",
            initialdir=self.pending_upload_folder if os.path.isdir(self.pending_upload_folder) else APP_DATA_DIR,
            mustexist=True,
        )
        if not folder:
            return

        try:
            config.load()
            self.ent_user.delete(0, tk.END)
            self.ent_user.insert(0, config.username)
            
            self.ent_pass.delete(0, tk.END)
            self.ent_pass.insert(0, config.password)
            
            self.pending_upload_folder = folder
            self.lbl_fold.config(text=self.pending_upload_folder)
            self.settings_status.config(text="Saved login reloaded; chosen folder is staged. Click Save Changes to keep it.", fg="#93c5fd")
            add_log(f"Saved login reloaded; storage folder selected: {folder}")
            self.root.after(2000, lambda: self.settings_status.config(text=""))
        except Exception as e:
            self.settings_status.config(text=f"✗ Reload failed: {str(e)[:30]}", fg="#ef4444")
            add_log(f"Error reloading settings: {e}")
            print(f"Reload error: {e}")


    def change_folder(self):
        folder = filedialog.askdirectory(
            title="Choose the storage folder",
            initialdir=self.pending_upload_folder if os.path.isdir(self.pending_upload_folder) else APP_DATA_DIR,
            mustexist=True,
        )
        if folder:
            try:
                self.pending_upload_folder = folder
                self.lbl_fold.config(text=self.pending_upload_folder)
                self.settings_status.config(text="Folder selected. Click Save Changes to keep it.", fg="#93c5fd")
            except Exception as e:
                self.settings_status.config(text=f"✗ Error: {str(e)[:30]}", fg="#ef4444")
                print(f"Change folder error: {e}")

    def generate_qr(self):
        ip = get_local_ip()
        if not ip:
            self.lbl_url.config(text="LAN address unavailable; connect to Wi-Fi")
            self.qr_label.config(image="")
            return

        url = f"http://{ip}:{config.port}"
        self.lbl_url.config(text=url)

        qr = qrcode.QRCode(version=1, box_size=5, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="white", back_color="#1e293b")
        img = img.resize((150, 150), Image.NEAREST)
        self.photo = ImageTk.PhotoImage(image=img)
        self.qr_label.config(image=self.photo)

    def update_log(self, msg):
        self.log_area.configure(state='normal')
        self.log_area.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_area.see(tk.END)
        self.log_area.configure(state='disabled')

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    root = tk.Tk()
    root.title(f"{APP_NAME} {APP_VERSION}")
    # Use a modern theme if available
    try:
        root.tk.call('source', 'azure.tcl')
        root.tk.call('set_theme', 'dark')
    except:
        pass
        
    app_gui = ServerGUI(root)
    add_log("IO Subsystem Ready. Waiting for initialization.")
    root.mainloop()