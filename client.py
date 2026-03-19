import os
import sys
import time
import subprocess
import webbrowser
import glob
import re
import threading
import logging
from datetime import datetime

# ==========================================
# تنظیمات گیت‌هاب
GITHUB_TOKEN = "" 
PROXY_URL = "socks5h://127.0.0.1:1088"
# ==========================================

# تنظیمات لاگینگ برای جلوگیری از کثیف شدن محیط ترمینال
logging.basicConfig(
    filename='client_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

def run_cmd(cmd, timeout_sec=None, retries=1):
    for attempt in range(retries):
        logging.debug(f"Executing [Attempt {attempt+1}/{retries}]: {cmd}")
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout_sec)
            logging.debug(f"Exit Code: {res.returncode} | Output length: {len(res.stdout)}")
            if res.returncode == 0:
                return res
        except subprocess.TimeoutExpired:
            logging.error(f"Command TIMED OUT after {timeout_sec}s: {cmd}")
        except Exception as e:
            logging.error(f"Error executing command: {str(e)}")
            
        if attempt < retries - 1:
            time.sleep(2)
            
    class DummyResult:
        returncode = 1
        stdout = ""
        stderr = "Failed after retries"
    return DummyResult()

def setup_fast_git():
    logging.info("Setting up Git configurations for fast proxy tunneling...")
    run_cmd(f"git config --global http.proxy {PROXY_URL}")
    run_cmd(f"git config --global https.proxy {PROXY_URL}")
    
    run_cmd("git config --global http.postBuffer 1048576000")
    run_cmd("git config --global http.lowSpeedLimit 1000")
    run_cmd("git config --global http.lowSpeedTime 60")
    run_cmd("git config --global core.compression 0")
    run_cmd("git config --global gc.auto 0")
    
    run_cmd("git config core.sparseCheckout false")
    if os.path.exists(".git/info/sparse-checkout"):
        try:
            os.remove(".git/info/sparse-checkout")
        except:
            pass

def setup_gitignore():
    ignore_content = "result/\nvenv/\n__pycache__/\n*.pyc\nclient_debug.log\n"
    if not os.path.exists(".gitignore"):
        with open(".gitignore", "w") as f:
            f.write(ignore_content)
        run_cmd("git add .gitignore && git commit -m 'Add gitignore'")

def push_with_retry(max_retries=5):
    for attempt in range(1, max_retries + 1):
        sys.stdout.write(f"\r[~] Pushing request to server... (Attempt {attempt}/{max_retries})")
        sys.stdout.flush()
        
        push_cmd = "git push origin main"
        result = run_cmd(push_cmd, timeout_sec=120) 
        
        if result.returncode == 0: 
            print("\n[+] Push successful! Server acknowledged.")
            return True
            
        time.sleep(3)
    print("\n[-] Failed to connect to Git through proxy. Check connection.")
    return False

def get_github_urls():
    remote_url = run_cmd("git config --get remote.origin.url").stdout.strip()
    match = re.search(r'github\.com[:/](.+)/(.+?)(?:\.git)?$', remote_url)
    if match: return match.groups()
    return None, None

def get_total_server_size(commit_hash):
    out = run_cmd(f"git ls-tree -r -l {commit_hash} result/", timeout_sec=10).stdout
    total_bytes = 0
    if out:
        for line in out.splitlines():
            if "video_part_" in line or "response.html" in line or "offline_page.mhtml" in line:
                parts = line.split()
                if len(parts) >= 4 and parts[3].isdigit():
                    total_bytes += int(parts[3])
    return total_bytes

def wait_for_server_and_pull(req_id, req_type, wait_msg):
    print(f"\n[*] {wait_msg}")
    print("[*] You will see live Git download progress (speed/percentage) right here when files are ready.\n")
    
    attempts = 0
    while True:
        attempts += 1
        
        sys.stdout.write("\r" + " "*60 + "\r")
        sys.stdout.flush()

        subprocess.run(["git", "fetch", "origin", "main", "--progress"])
        
        try:
            remote_flag = subprocess.check_output(
                ["git", "show", "origin/main:.server_done"], 
                stderr=subprocess.DEVNULL
            ).decode('utf-8').strip()
        except subprocess.CalledProcessError:
            remote_flag = ""

        if remote_flag == req_id:
            print(f"\n[✅] Server successfully finished processing! (Took ~{attempts * 10} seconds)")
            break
        
        sys.stdout.write(f"\r[~] Polling server... (Elapsed: {attempts * 10}s) ")
        sys.stdout.flush()
        time.sleep(10)

    print("\n[🚀] Extracting files to local 'result' directory...")
    subprocess.run(["git", "reset", "--hard", "origin/main"], stdout=subprocess.DEVNULL)
    
    print("\n[🎉] Download and extraction complete!")
    return True

def get_safe_filename():
    title_path = os.path.join("result", "title.txt")
    if os.path.exists(title_path):
        with open(title_path, "r", encoding="utf-8") as f:
            raw_title = f.read().strip()
            safe_title = "".join(c for c in raw_title if c.isalnum() or c in (' ', '-', '_', '.', '،')).strip()
            if safe_title: return f"{safe_title}_Merged.mp4" 
    return "Downloaded_Video_Merged.mp4"

def merge_video_parts():
    parts = sorted(glob.glob(os.path.join("result", "video_part_*")))
    if not parts:
        print("\n[!] Error: No video parts found to merge.")
        return

    final_video_name = get_safe_filename()
    final_path = os.path.join("result", final_video_name)
    
    print(f"\n[*] Merging {len(parts)} segments using Native Python stream...")
    
    try:
        with open(final_path, 'wb') as outfile:
            for i, part in enumerate(parts):
                sys.stdout.write(f"\r[~] Merging part {i+1}/{len(parts)}...")
                sys.stdout.flush()
                with open(part, 'rb') as infile:
                    outfile.write(infile.read())
        
        print(f"\n[+] SUCCESS! Final video saved at: {os.path.abspath(final_path)}")
        
        for part in parts:
            os.remove(part)
    except Exception as e:
        print(f"\n[-] Merge failed: {str(e)}")
        logging.error(f"Merge error: {str(e)}")

def cleanup_local_files():
    for f in glob.glob("result/*"):
        try:
            os.remove(f)
        except Exception as e:
            pass

def check_and_print_errors():
    error_path = os.path.join("result", "error.log")
    if os.path.exists(error_path):
        with open(error_path, "r", encoding="utf-8", errors="ignore") as f:
            errors = f.read().strip()
            if errors:
                print("\n" + "!"*60)
                print("[!] SERVER LOGS / WARNINGS:")
                print(errors)
                print("!"*60 + "\n")

def main():
    open('client_debug.log', 'w').close() 
    os.system('cls' if os.name == 'nt' else 'clear')
    
    setup_fast_git()
    setup_gitignore()
    os.makedirs("result", exist_ok=True)
    
    print("="*60)
    print(" 🚀 GitHub Tunnel V14.0 (Ultra Bypass Edition)")
    print("="*60)
    
    print("\n[1] Fetch Webpage (Bypasses Restrictions)")
    print("[2] Fetch Heavy Video (MP4) via Splitting")
    print("[3] Launch Proxy Browser 🌐")
    
    try: choice = input("\n[?] Select mode (1/2/3): ").strip()
    except KeyboardInterrupt: return
        
    if choice not in ['1', '2', '3']: return

    if choice == '3':
        print("\n[*] Starting isolated browser...")
        subprocess.Popen([sys.executable, "browser.py"])
        return
        
    try: url = input("\n[?] Target URL: ").strip()
    except KeyboardInterrupt: return
        
    if not url: return
    if not url.startswith("http"): url = "https://" + url

    print("\n[*] Syncing local state with remote...")
    run_cmd("git fetch --depth 1 origin main && git reset --hard origin/main", timeout_sec=45)
    cleanup_local_files()

    if choice == '1':
        unique_timestamp = str(time.time())
        # توجه: در اینجا نام فایل به request_web.txt تغییر یافت
        with open("request_web.txt", "w") as f: 
            f.write(f"WEB\n{url}\nNONE\n{unique_timestamp}\n")
            
        run_cmd("git add request_web.txt && git commit -m 'REQ: WEB'")
        if not push_with_retry(): return
            
        if wait_for_server_and_pull(unique_timestamp, 'WEB', "Server is rendering webpage..."):
            check_and_print_errors()
            html_path = os.path.join("result", "offline_page.mhtml")
            #html_path = os.path.join("result", "response.html")
            if os.path.exists(html_path):
                print(f"\n[+] File ready! Opening in default browser...")
                webbrowser.open(f"file://{os.path.abspath(html_path)}")

    elif choice == '2':
        unique_timestamp_info = "INFO_" + str(time.time())
        with open("request.txt", "w") as f: f.write(f"INFO\n{url}\nNONE\n{unique_timestamp_info}\n")
            
        print("\n[*] STAGE 1: Extracting media metadata...")
        run_cmd("git add request.txt && git commit -m 'REQ: VIDEO INFO'")
        if not push_with_retry(): return
        
        if not wait_for_server_and_pull(unique_timestamp_info, 'INFO', "Server probing URLs..."): return
        check_and_print_errors()
        
        info_path = os.path.join("result", "info.txt")
        if not os.path.exists(info_path): 
            print("[-] Metadata not found. Server failed to extract info.")
            return
            
        with open(info_path, "r", encoding="utf-8", errors="ignore") as f:
            print("\n" + "="*60 + "\n" + f.read() + "\n" + "="*60)
            
        format_id = input("\n[?] Enter Format ID (Press Enter for 'best'): ").strip() or "best"

        cleanup_local_files() 
        
        unique_timestamp_video = "VIDEO_" + str(time.time())
        with open("request.txt", "w") as f: f.write(f"VIDEO\n{url}\n{format_id}\n{unique_timestamp_video}\n")
            
        print(f"\n[*] STAGE 2: Queueing format [{format_id}] for remote download...")
        run_cmd("git add request.txt && git commit -m 'REQ: VIDEO DOWNLOAD'")
        if not push_with_retry(): return
            
        if wait_for_server_and_pull(unique_timestamp_video, 'VIDEO', "Server is downloading & compressing (can take a while)..."):
            check_and_print_errors()
            merge_video_parts()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: 
        print("\n\n[!] Exiting...")
        os._exit(1)
