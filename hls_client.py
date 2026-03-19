import os
import sys
import time
import glob
import subprocess
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
import ssl 

# ================= Configuration =================
GITHUB_TOKEN = "" 

# ⚠️ بسیار مهم: پورت پروکسی خود را چک کنید!
PROXY_URL = "socks5h://127.0.0.1:1088" 
# =================================================

cancel_event = threading.Event()
force_check_event = threading.Event()
is_done_event = threading.Event()

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

os.environ["http_proxy"] = PROXY_URL
os.environ["https_proxy"] = PROXY_URL
os.environ["ALL_PROXY"] = PROXY_URL

def run_cmd(cmd_list, show_output=False, capture=True, timeout_sec=None):
    try:
        if capture and not show_output:
            result = subprocess.run(cmd_list, capture_output=True, text=True, check=True, timeout=timeout_sec)
            return f"{result.stdout}\n{result.stderr}".strip()
        else:
            subprocess.run(cmd_list, check=True, timeout=timeout_sec)
            return "SUCCESS"
    except subprocess.TimeoutExpired as e:
        return f"TIMEOUT_ERROR: {str(e)}"
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if e.stderr else e.stdout
        return f"EXECUTION_ERROR: Command '{' '.join(cmd_list)}' failed.\nDetails: {error_msg}"
    except Exception as e:
        return f"UNKNOWN_ERROR: {str(e)}"

def apply_turbo_git_configs():
    print(">>> Applying Git network configurations & Proxy...")
    run_cmd(["git", "config", "http.proxy", PROXY_URL])
    run_cmd(["git", "config", "https.proxy", PROXY_URL])
    run_cmd(["git", "config", "core.compression", "0"])
    run_cmd(["git", "config", "http.postBuffer", "524288000"])
    run_cmd(["git", "config", "http.sslVerify", "false"])

def cleanup_repository():
    print("\n>>> Initiating server cleanup...")
    run_cmd(["git", "fetch", "--filter=blob:none", "origin", "main"], timeout_sec=30)
    run_cmd(["git", "reset", "--hard", "origin/main"])
    run_cmd(["git", "rm", "-r", "-f", "hls_result/"])
    run_cmd(["git", "rm", "-f", ".server_done"])
    
    with open("hls_status.txt", "w") as f: f.write("CLEANED")
    run_cmd(["git", "add", "hls_status.txt"])
    run_cmd(["git", "commit", "-m", "Client: Auto-cleanup/Reset"])
    run_cmd(["git", "push", "origin", "main"], timeout_sec=60)
    print(">>> Cleanup complete.")

def send_download_request(url):
    print(f"\n>>> Sending request for: {url}")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    subprocess.run(["git", "rm", "-r", "--ignore-unmatch", "-f", ".server_done", "hls_status.txt", "hls_result/"], capture_output=True)
    
    with open("hls_request.txt", "w") as f:
        f.write(f"{url}\n# Requested at {timestamp}")
    
    run_cmd(["git", "add", "hls_request.txt"])
    run_cmd(["git", "commit", "-m", f"Client Request: {timestamp}"])
    
    push_res = run_cmd(["git", "push", "origin", "main"], timeout_sec=90)
    if "ERROR" in push_res:
        print(f"\n[CRITICAL] Failed to push request to GitHub:\n{push_res}")
        sys.exit(1)
        
    print(">>> Request successfully sent! Waking up GitHub Actions...")

def run_live_git_download(is_resume=False):
    print("\n[✔] Initiating ultra-fast Git Native Transfer (via Proxy)...")
    if is_resume:
        print("[!] Note: Resuming partial download. Progress bar might not display fully.")
    print("[⏳] Git Transfer Progress (Live):")
    print("-" * 60)
    
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    
    try:
        cmd = ["git", "fetch", "origin", "main", "--progress"]
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        
        buffer = ""
        while True:
            char = process.stdout.read(1)
            if not char:
                if process.poll() is not None:
                    break
                continue
            
            decoded_char = char.decode('utf-8', errors='ignore')
            buffer += decoded_char
            
            # گیت برای آپدیت زنده از \r استفاده می‌کند
            if decoded_char == '\r' or decoded_char == '\n':
                line = buffer.strip()
                buffer = ""
                
                # استخراج اطلاعات دانلود از خطوط Receiving objects
                if "Receiving objects:" in line:
                    # نمونه خط گیت: Receiving objects: 100% (4/4), 5.00 MiB | 2.50 MiB/s, done.
                    match = re.search(r'(\d+%)\s*\([^)]+\),\s*([^|]+?)\s*\|\s*([^,]+)', line)
                    if match:
                        percent = match.group(1)
                        size = match.group(2).strip()
                        speed = match.group(3).strip()
                        
                        # پاک کردن خط قبلی و چاپ خط جدید
                        sys.stdout.write(f"\r 🚀 Download: [{percent}] | Total Data: {size} | Speed: {speed}      ")
                        sys.stdout.flush()
                # نمایش سایر وضعیت‌های مهم مثل Resolving deltas
                elif "Resolving deltas:" in line:
                    sys.stdout.write(f"\r 🔄 Processing data: {line.split(':', 1)[1].strip()}                  ")
                    sys.stdout.flush()

        process.wait()
        
        print("\n" + "-" * 60)
        print(">>> Extracting files from Git object database...")
        run_cmd(["git", "checkout", "origin/main", "--", "hls_result/"])
        
        if sum(os.path.getsize(f) for f in glob.glob("hls_result/part_*")) > 0:
            print("\n[✔] Native Transfer complete and verified!")
            return True
        else:
            print("\n[!] Transfer failed to locate files.")
            return False
            
    except Exception as e:
        print(f"\n[!] Git transfer error: {e}")
        return False

def get_file_size_http(url, use_api_header=False):
    try:
        req = urllib.request.Request(url, method='HEAD')
        if GITHUB_TOKEN:
            req.add_header("Authorization", f"token {GITHUB_TOKEN}")
        if use_api_header:
            req.add_header("Accept", "application/vnd.github.v3.raw")
            
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
            size = int(response.getheader('Content-Length', 0))
            if size == 0 and use_api_header: return 1 
            return size
    except Exception:
        return 0

def download_manager(part_files=None, is_resume=False):
    print(">>> Finding best high-speed network route (Bypassing Proxies for Private Repo)...", flush=True)
    return run_live_git_download(is_resume=is_resume)

def get_remote_hash():
    """ استخراج هش سرور بدون دانلود هیچ فایلی """
    res = run_cmd(["git", "ls-remote", "origin", "main"], timeout_sec=15)
    if res and "TIMEOUT_ERROR" not in res and "EXECUTION_ERROR" not in res:
        parts = res.split()
        if parts: return parts[0]
    return None

def check_server_status_logic(is_manual, last_hash):
    if is_manual:
        print("\n[⏳] Manual check initiated. Contacting GitHub...", flush=True)
    
    current_hash = get_remote_hash()
    
    if not current_hash:
        if is_manual: print("\n[!] Network Timeout: Could not reach GitHub via Proxy.")
        return False, last_hash
        
    if current_hash != last_hash:
        if is_manual: print("\n[✔] Server update detected!", flush=True)
        return True, current_hash
    else:
        if is_manual: print("\n[!] Server is STILL PROCESSING. Please wait...", flush=True)
        return False, last_hash

def user_input_listener():
    while not cancel_event.is_set() and not is_done_event.is_set():
        try:
            cmd = input()
            if cmd.strip().lower() == 'c': force_check_event.set()
            elif cmd.strip().lower() == 'q': cancel_event.set()
        except EOFError: pass

def wait_and_download():
    print("\n" + "="*50)
    print(">>> Waiting for server to process and extract...")
    print(" ⌨️  Type 'c' + ENTER to MANUALLY CHECK status")
    print(" ⌨️  Type 'q' + ENTER to CANCEL operation")
    print("="*50 + "\n", flush=True)
    
    # ثبت وضعیت گیت قبل از شروع مانیتورینگ
    last_known_hash = run_cmd(["git", "rev-parse", "origin/main"]).strip()
    
    input_thread = threading.Thread(target=user_input_listener, daemon=True)
    input_thread.start()
    
    last_auto_check = time.time()
    
    try:
        while not cancel_event.is_set():
            now = time.time()
            if force_check_event.is_set() or (now - last_auto_check > 20):
                is_manual = force_check_event.is_set()
                force_check_event.clear()
                last_auto_check = now
                
                # بررسی سبک بدون مسدود کردن دانلودهای بعدی
                changed, new_hash = check_server_status_logic(is_manual, last_known_hash)
                
                if changed:
                    last_known_hash = new_hash
                    print("\n>>> Server finished processing! Initiating Native Transfer...", flush=True)
                    
                    if download_manager():
                        is_done_event.set()
                        return True
            time.sleep(1)
            
        if cancel_event.is_set(): return False
            
    except KeyboardInterrupt:
        print("\n\n[!] Operation cancelled by user.")
        cancel_event.set()
        sys.exit(0)

def assemble_video():
    print("\n>>> Assembling video parts...")
    parts = sorted(glob.glob("hls_result/part_*"))
    if not parts:
        print("[ERROR] No video parts found locally!")
        return False
        
    output_path = os.path.abspath(os.path.join(os.getcwd(), "..", f"HLS_Video_{int(time.time())}.mp4"))
    try:
        with open(output_path, 'wb') as outfile:
            for part in parts:
                with open(part, 'rb') as infile:
                    while True:
                        chunk = infile.read(1024 * 1024 * 5)
                        if not chunk: break
                        outfile.write(chunk)
                        
        print(f"\n==================================================")
        print(f" [+] SUCCESS! Video saved successfully")
        print(f" [+] Saved at: {output_path}")
        print(f"==================================================")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to assemble video: {e}")
        return False

def check_existing_files_on_server():
    print("\n>>> Checking server for previous unfinished downloads...")
    
    fetch_res = run_cmd(["git", "fetch", "--filter=blob:none", "origin", "main"], timeout_sec=15)
    
    if "TIMEOUT_ERROR" in fetch_res:
        print("\n[!] CRITICAL ERROR: Network Connection Timed Out!")
        print("    1. Is your VPN/Proxy (V2ray/Xray) running?")
        print(f"    2. Is your Proxy Port correct? (Currently set to: {PROXY_URL})")
        sys.exit(1)
    elif "ERROR" in fetch_res:
        print(f"\n[!] GIT ERROR:\n{fetch_res}")
        sys.exit(1)
        
    existing_files = []
    total_size_bytes = 0
    tree_out = run_cmd(["git", "ls-tree", "-r", "-l", "origin/main", "hls_result"])
    
    if tree_out and "ERROR" not in tree_out:
        for line in tree_out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "part_" in parts[-1]:
                try:
                    size = int(parts[3])
                    total_size_bytes += size
                    existing_files.append(parts[-1].split('/')[-1])
                except: pass

    if existing_files:
        mb = total_size_bytes / (1024 * 1024)
        print(f"\n[!] FOUND EXISTING FILES ON SERVER:")
        print(f"    - Parts Found: {len(existing_files)}")
        print(f"    - Total Size: {mb:.2f} MB")
        
        while True:
            ans = input("\n[?] Do you want to download and merge these existing files? (y/n): ").strip().lower()
            if ans == 'y':
                print("\n>>> Proceeding to download existing files...")
                if download_manager(existing_files, is_resume=True):
                    if assemble_video(): 
                        cleanup_repository()
                sys.exit(0)
            elif ans == 'n':
                print("\n>>> Ignoring previous files. Cleaning up server before new request...")
                cleanup_repository()
                break
            else:
                print("Invalid input. Please type 'y' or 'n'.")
    else:
        print("[✔] Server is clean. No leftover files found.")

def main():
    print("="*60)
    print("   HLS Multi-Thread Downloader (Smart Routing Edition)   ")
    print("="*60)
    
    if not os.path.isdir(".git"):
        print("\n[!] CRITICAL ERROR: You are not inside a Git repository folder!")
        sys.exit(1)
    
    try:
        apply_turbo_git_configs()
        
        check_existing_files_on_server()
        
        target_url = input("\nEnter the target URL containing the hidden HLS/m3u8: ").strip()
        if not target_url: sys.exit(1)
            
        send_download_request(target_url)
        
        if wait_and_download():
            # دانلود موفق بود - فایل وضعیت را هم بررسی میکنیم
            run_cmd(["git", "checkout", "origin/main", "--", "hls_status.txt"])
            
            status = ""
            if os.path.exists("hls_status.txt"):
                with open("hls_status.txt", "r") as f: status = f.read().strip()
                    
            if "ERROR" in status:
                print(f"\n[CRITICAL ERROR] Server encountered an error. Status file says: {status}")
            else:
                if assemble_video(): 
                    cleanup_repository()
                
    except KeyboardInterrupt:
        print("\n\n[!] Script force-closed.")
        cancel_event.set()
        os._exit(1)

if __name__ == "__main__":
    main()
