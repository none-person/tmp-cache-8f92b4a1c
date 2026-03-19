import sys
import os
import time
import shutil
from urllib.parse import urlparse
from PyQt5.QtWidgets import (QApplication, QMainWindow, QLineEdit, QPushButton, 
                             QVBoxLayout, QHBoxLayout, QWidget, QLabel, QProgressBar)
from PyQt5.QtCore import QUrl, QThread, pyqtSignal, Qt
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

# ایمپورت کردن کلاینت برای استفاده از توابع شبکه و گیت
import client

class WebRequestHandler(QThread):
    """این کلاس مسئولیت ارتباط با گیت‌هاب و دانلود را در پس‌زمینه بر عهده دارد"""
    status_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(str, str) # url, local_path
    error_signal = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.session_dir = "sessions"
        os.makedirs(self.session_dir, exist_ok=True)

    def run(self):
        try:
            domain = urlparse(self.url).netloc.replace('www.', '') or "unknown_domain"
            # تغییر پسوند به mhtml برای پشتیبانی از فرمت جدید سرور
            safe_name = str(int(time.time())) + ".mhtml" 
            domain_dir = os.path.join(self.session_dir, domain)
            os.makedirs(domain_dir, exist_ok=True)
            final_path = os.path.abspath(os.path.join(domain_dir, safe_name))

            self.status_signal.emit("Step 1: Preparing GitHub Request...")
            self.progress_signal.emit(10)
            
            # پاک کردن قفل‌های احتمالی گیت
            lock_file = os.path.join(".git", "index.lock")
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                    print("[*] Cleared residual Git lock file.")
                except Exception:
                    pass

            # نکته مهم: فایل باید request_web.txt باشد تا Workflow فعال شود
            timestamp = str(time.time())
            with open("request_web.txt", "w") as f:
                f.write(f"WEB\n{self.url}\nNONE\n{timestamp}\n")

            client.run_cmd("git add request_web.txt")
            client.run_cmd("git commit --allow-empty -m 'Browser Requested WEB'")

            self.status_signal.emit("Step 2: Sending request to cloud (Please wait)...")
            self.progress_signal.emit(20)
            
            if not client.push_with_retry():
                self.error_signal.emit("Network Error: Failed to push to GitHub after multiple attempts.")
                return

            self.status_signal.emit("Step 3: Server is rendering the page (Bypassing Cloudflare)...")
            self.progress_signal.emit(35)
            
            initial_hash = client.run_cmd("git ls-remote origin main").stdout.strip().split()[0]
            
            # حلقه انتظار برای اتمام کار سرور
            retry_count = 0
            while True:
                current_info = client.run_cmd("git ls-remote origin main").stdout.strip()
                current_hash = current_info.split()[0] if current_info else ""
                
                if current_hash and current_hash != initial_hash:
                    break
                
                retry_count += 1
                
                # پر شدن نرم و روان نوار پیشرفت در حین انتظار (تا 85 درصد)
                progress = min(85, 35 + (retry_count * 2))
                self.progress_signal.emit(progress)

                if retry_count > 40: # حدود 2 دقیقه تایم‌اوت کلی سرور
                    self.error_signal.emit("Server Timeout: GitHub Actions took too long.")
                    return
                    
                time.sleep(3) # چک کردن گیت هر 3 ثانیه

            self.status_signal.emit("Step 4: Server finished! Downloading MHTML via HTTP...")
            self.progress_signal.emit(90)
            
            _, raw_base_url = client.get_github_urls()
            
            # تغییر مسیر به offline_page.mhtml
            mhtml_dest = os.path.join("result", "offline_page.mhtml")
            os.makedirs("result", exist_ok=True)
            
            # اضافه کردن تایم‌استمپ به لینک برای جلوگیری از کش شدن فایل قدیمی در گیت‌هاب
            target_file_url = f"{raw_base_url}/result/offline_page.mhtml?t={int(time.time())}"
            
            success = client.download_file_http(target_file_url, mhtml_dest, "Webpage", is_multi=False)
            
            # اگر دانلود HTTP خطا داد، بک‌آپ: گرفتن با گیت
            if not success or not os.path.exists(mhtml_dest):
                self.status_signal.emit("HTTP Download failed. Pulling via Git...")
                client.run_cmd("git pull --rebase")
                success = os.path.exists(mhtml_dest)

            if success and os.path.exists(mhtml_dest):
                self.status_signal.emit("Step 5: Finalizing...")
                self.progress_signal.emit(95)
                
                # انتقال فایل به پوشه سشن مرورگر
                shutil.move(mhtml_dest, final_path)
                
                self.progress_signal.emit(100)
                self.status_signal.emit("Done!")
                self.finished_signal.emit(self.url, final_path)
            else:
                self.error_signal.emit("Error: File not found or HTTP download failed.")

        except Exception as e:
            self.error_signal.emit(f"Critical Error: {str(e)}")


class CustomWebPage(QWebEnginePage):
    """این کلاس وظیفه رهگیری کلیک روی لینک‌ها را دارد"""
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        # parent در اینجا همان OfflineBrowser است
        self.browser_window = parent

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        # اگر کاربر روی لینکی کلیک کرد
        if _type == QWebEnginePage.NavigationTypeLinkClicked:
            target_url = url.toString()
            # اگر لینک محلی نیست، متوقفش کن و بده به سرور تونل
            if not target_url.startswith("file://") and not target_url.startswith("data:"):
                print(f"[*] Intercepted link click: {target_url}")
                self.browser_window.load_url(target_url)
                return False 
        return super().acceptNavigationRequest(url, _type, isMainFrame)


class OfflineBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tunnel Proxy Browser - Offline Mode (Cloudflare Bypass)")
        self.setGeometry(100, 100, 1200, 800) # سایز بزرگتر برای راحتی

        # ساختار رابط کاربری
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # نوار آدرس و دکمه‌ها
        nav_layout = QHBoxLayout()
        
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self.go_back)
        nav_layout.addWidget(self.btn_back)

        self.btn_forward = QPushButton("Forward")
        self.btn_forward.clicked.connect(self.go_forward)
        nav_layout.addWidget(self.btn_forward)

        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Enter URL here (e.g., https://wikipedia.org) and press Enter...")
        self.url_bar.returnPressed.connect(self.on_url_enter)
        # افزایش سایز فونت نوار آدرس
        font = self.url_bar.font()
        font.setPointSize(11)
        self.url_bar.setFont(font)
        nav_layout.addWidget(self.url_bar)

        self.btn_go = QPushButton("Go (Fetch via Proxy)")
        self.btn_go.clicked.connect(self.on_url_enter)
        nav_layout.addWidget(self.btn_go)

        layout.addLayout(nav_layout)

        # موتور مرورگر
        self.browser = QWebEngineView()
        self.custom_page = CustomWebPage(self.browser.page().profile(), self)
        self.browser.setPage(self.custom_page)
        layout.addWidget(self.browser)

        # نوار وضعیت (Status Bar)
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximumWidth(400)
        self.progress_bar.hide()
        
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.progress_bar)
        layout.addLayout(status_layout)

        # متغیر برای جلوگیری از درخواست‌های همزمان
        self.is_loading = False
        self.worker = None

    def on_url_enter(self):
        url = self.url_bar.text().strip()
        if url:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "https://" + url
            self.load_url(url)

    def load_url(self, url):
        if self.is_loading:
            self.status_label.setText("Please wait, a request is already in progress...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            return

        self.url_bar.setText(url)
        self.is_loading = True
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setStyleSheet("color: blue; font-weight: bold;")
        
        # پاک کردن صفحه مرورگر تا صفحه جدید لود شود
        self.browser.setHtml("<html><body style='background-color: #f0f0f0; display: flex; justify-content: center; align-items: center; height: 100vh; font-family: sans-serif;'><h2>Loading content securely via GitHub Tunnel...</h2></body></html>")
        
        # اجرای عملیات دریافت در پس‌زمینه (Thread)
        self.worker = WebRequestHandler(url)
        self.worker.status_signal.connect(self.update_status)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_page_ready)
        self.worker.error_signal.connect(self.on_error)
        self.worker.start()

    def update_status(self, message):
        self.status_label.setText(message)

    def on_page_ready(self, original_url, local_path):
        self.is_loading = False
        self.progress_bar.hide()
        self.status_label.setText(f"Page loaded successfully! (Saved locally in sessions folder)")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        
        # لود کردن فایل MHTML در مرورگر
        local_url = QUrl.fromLocalFile(local_path)
        self.browser.load(local_url)

    def on_error(self, message):
        self.is_loading = False
        self.progress_bar.hide()
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.browser.setHtml(f"<html><body style='color: red; padding: 20px; font-family: sans-serif;'><h2>Error loading page</h2><p>{message}</p></body></html>")

    def go_back(self):
        self.browser.back()

    def go_forward(self):
        self.browser.forward()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OfflineBrowser()
    window.show()
    sys.exit(app.exec_())
