import sys
import os
import glob
import time
import ctypes
import traceback
import configparser
import threading
import subprocess

# 【修正】強制將工作目錄切換到腳本所在資料夾，避免 Errno 2
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
os.chdir(BASE_DIR)

# ==========================================
# 0. 防閃退與模組檢查
# ==========================================
try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QTabWidget, QTextEdit, QMessageBox, QSplitter, QTabBar,
        QComboBox, QLabel, QGroupBox, QGridLayout, QLineEdit, QSpinBox, QDoubleSpinBox,
        QCheckBox, QScrollArea, QFormLayout, QDialog
    )
    from PySide6.QtCore import Qt, Signal, QObject, QThread, QBuffer, QIODevice
    from PySide6.QtGui import QFont, QTextCursor, QPixmap, QGuiApplication
    import win32api
    import win32gui
    import win32con
    import win32clipboard
    
    import pytesseract
    from PIL import Image
    import io
    
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    
    # 載入自動更新模組
    from updater import AutoUpdater
    
except ImportError as e:
    print("❌ 載入模組失敗！請確認是否已安裝所有必要套件。")
    print(f"錯誤訊息: {e}")
    print("請在終端機輸入: pip install PySide6 pywin32 pytesseract pillow requests")
    input("\n按 Enter 鍵結束...")
    sys.exit()

# ==========================================
# 【系統設定區】
# ==========================================
CURRENT_VERSION = "4.0.0"
# 【修改】貼上 GitHub 上 version.json 的 Raw 網址
UPDATE_JSON_URL = "https://raw.githubusercontent.com/fdhbh/Wulin_Auto_Gui/main/version.json"

CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
DEFAULT_CONFIG = os.path.join(CONFIGS_DIR, "config.ini")
CHANGELOG_FILE = os.path.join(BASE_DIR, "更新說明.md")

if not os.path.exists(CONFIGS_DIR):
    os.makedirs(CONFIGS_DIR)

def ensure_base_config():
    config = configparser.ConfigParser()
    if os.path.exists(DEFAULT_CONFIG):
        config.read(DEFAULT_CONFIG, encoding='utf-8')
    if not config.has_section('General'):
        config.add_section('General')
    if not config.has_option('General', 'EnableAdminElevation'):
        config.set('General', 'EnableAdminElevation', '0')
        with open(DEFAULT_CONFIG, 'w', encoding='utf-8') as f:
            config.write(f)

ensure_base_config()

def get_admin_setting():
    config = configparser.ConfigParser()
    config.read(DEFAULT_CONFIG, encoding='utf-8')
    return config.getint('General', 'EnableAdminElevation', fallback=0) == 1

def set_admin_setting(is_enabled):
    config = configparser.ConfigParser()
    config.read(DEFAULT_CONFIG, encoding='utf-8')
    if not config.has_section('General'): config.add_section('General')
    config.set('General', 'EnableAdminElevation', '1' if is_enabled else '0')
    with open(DEFAULT_CONFIG, 'w', encoding='utf-8') as f:
        config.write(f)

KEY_MAPPING = {
    'F1': win32con.VK_F1, 'F2': win32con.VK_F2, 'F3': win32con.VK_F3,
    'F4': win32con.VK_F4, 'F5': win32con.VK_F5, 'F6': win32con.VK_F6,
    'F7': win32con.VK_F7, 'F8': win32con.VK_F8, 'F9': win32con.VK_F9,
    'MOUSE': -1, 'NONE': -2,
}

# ==========================================
# 1. 全域 Log 攔截器
# ==========================================
class LogStream(QObject):
    new_log = Signal(str)
    def write(self, text):
        if text.strip():  
            self.new_log.emit(str(text))
    def flush(self): pass

# ==========================================
# 2. 背景 Worker：視窗鎖定器 (單一)
# ==========================================
class WindowLockerWorker(QThread):
    locked_signal = Signal(int, str, str)  
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        self.log_signal.emit("⏳ [鎖定模式] 請將滑鼠移至遊戲視窗內，並點擊「左鍵」...")
        time.sleep(0.5) 
        while self.running:
            if win32api.GetAsyncKeyState(0x01) < 0:
                x, y = win32api.GetCursorPos()
                hwnd_at_point = win32gui.WindowFromPoint((x, y))
                if hwnd_at_point != 0 and win32gui.IsWindowVisible(hwnd_at_point):
                    root_hwnd = win32gui.GetAncestor(hwnd_at_point, win32con.GA_ROOT)
                    class_name = win32gui.GetClassName(root_hwnd)
                    title = win32gui.GetWindowText(root_hwnd)
                    if class_name in ["Progman", "WorkerW", "Shell_TrayWnd"]:
                        self.log_signal.emit(f"⚠️ 點擊到桌面或工作列 ({class_name})，請重新點擊遊戲視窗。")
                        time.sleep(0.5)
                        continue
                    self.locked_signal.emit(root_hwnd, title, class_name)
                    break
            time.sleep(0.05)

    def stop(self):
        self.running = False

# ==========================================
# 背景 Worker：多視窗鎖定器 (抓 4 個)
# ==========================================
class MultiWindowLockerWorker(QThread):
    log_signal = Signal(str)
    locked_signal = Signal(list) 

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        found_hwnds = []
        for i in range(4):
            if not self.running: break
            self.log_signal.emit(f"⏳ 請點擊第 {i+1}/4 個遊戲視窗...")
            time.sleep(0.5)
            
            while self.running:
                if win32api.GetAsyncKeyState(0x01) < 0:
                    x, y = win32api.GetCursorPos()
                    hwnd = win32gui.WindowFromPoint((x, y))
                    hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
                    if hwnd not in found_hwnds and hwnd != 0:
                        class_name = win32gui.GetClassName(hwnd)
                        if class_name not in ["Progman", "WorkerW", "Shell_TrayWnd"]:
                            found_hwnds.append(hwnd)
                            self.log_signal.emit(f"✅ 已鎖定視窗 {i+1}: {hwnd}")
                            time.sleep(0.5)
                            break
                time.sleep(0.05)
                
        if self.running:
            self.log_signal.emit("🎉 4 個視窗鎖定完畢！")
            self.locked_signal.emit(found_hwnds)

    def stop(self):
        self.running = False

# ==========================================
# 3. 背景 Worker：掛機任務執行器
# ==========================================
class AutoScriptWorker(QThread):
    log_signal = Signal(str)
    stopped_signal = Signal()

    def __init__(self, hwnd, config_path):
        super().__init__()
        self.hwnd = hwnd
        self.config_path = config_path
        self.running = True
        self.threads = []
        self.config = configparser.ConfigParser()

    def background_press(self, key_code):
        win32api.PostMessage(self.hwnd, win32con.WM_KEYDOWN, key_code, 0)
        time.sleep(0.05) 
        win32api.PostMessage(self.hwnd, win32con.WM_KEYUP, key_code, 0)

    def background_click(self, x, y):
        l_param = win32api.MAKELONG(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_param)
        time.sleep(0.05)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, l_param)

    def task_worker(self, section_name):
        try:
            key_str = self.config.get(section_name, 'Key').upper()
            interval = self.config.getfloat(section_name, 'Interval')
            repeat_count = self.config.getint(section_name, 'RepeatCount', fallback=1)
            action_delay = self.config.getfloat(section_name, 'ActionDelay', fallback=0)
            follow_up = self.config.getint(section_name, 'FollowUpClick', fallback=0)
            key_to_click_delay = self.config.getfloat(section_name, 'KeyToClickDelay', fallback=0)
            mx = self.config.getint(section_name, 'MouseX', fallback=0)
            my = self.config.getint(section_name, 'MouseY', fallback=0)
            debug_mode = self.config.getint('General', 'DebugMode', fallback=0)

            self.log_signal.emit(f"🟢 [{section_name}] 已啟動 (按鍵:{key_str}, 間隔:{interval}s)")
            
            while self.running:
                for i in range(repeat_count):
                    if not self.running: break 
                    is_first_action = (i == 0)
                    is_key_action = key_str in KEY_MAPPING and key_str not in ['NONE', 'MOUSE']
                    
                    if is_key_action:
                        vk_code = KEY_MAPPING[key_str]
                        self.background_press(vk_code) 
                        if debug_mode == 1 and is_first_action:
                            self.log_signal.emit(f"  -> [{section_name}] 發送按鍵 {key_str}")
                    
                    if follow_up == 1 or key_str == 'MOUSE':
                        if is_key_action and key_to_click_delay > 0: time.sleep(key_to_click_delay) 
                        self.background_click(mx, my) 
                        if debug_mode == 1 and is_first_action:
                            self.log_signal.emit(f"  -> [{section_name}] 點擊座標 ({mx}, {my})")
                    
                    if action_delay > 0 and i < repeat_count - 1: time.sleep(action_delay)

                wait_time = 0
                while wait_time < interval and self.running:
                    time.sleep(0.1)
                    wait_time += 0.1
        except Exception as e:
            self.log_signal.emit(f"❌ [{section_name}] 發生錯誤: {e}")

    def run(self):
        try:
            self.config.read(self.config_path, encoding='utf-8')
            for task_name in self.config.sections():
                if task_name in ['General', 'WindowArrangement', 'AutoLogin', 'ClearBag']: continue
                if self.config.has_option(task_name, 'Enable') and self.config.getint(task_name, 'Enable') == 1:
                    if not self.config.has_option(task_name, 'Key'): continue
                    t = threading.Thread(target=self.task_worker, args=(task_name,))
                    t.daemon = True 
                    self.threads.append(t)
            
            if not self.threads:
                self.log_signal.emit("⚠️ 找不到任何啟用的任務，掛機腳本已自動停止。")
                self.running = False
                self.stopped_signal.emit()
                return

            self.log_signal.emit(f"🚀 總共啟動 {len(self.threads)} 個任務執行緒。")
            for t in self.threads: t.start()
            while self.running: time.sleep(0.5)
        except Exception as e:
            self.log_signal.emit(f"❌ 掛機腳本發生嚴重錯誤: {e}")
        finally:
            self.stopped_signal.emit()

    def stop(self):
        self.running = False

# ==========================================
# 4. 背景 Worker：視窗排列與登入
# ==========================================
class ArrangerWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(list) 

    def __init__(self, mode, config_path, arranged_windows=None):
        super().__init__()
        self.mode = mode 
        self.config_path = config_path
        self.arranged_windows = arranged_windows or []
        self.config = configparser.ConfigParser()

    def run(self):
        try:
            self.config.read(self.config_path, encoding='utf-8')
            target_w = self.config.getint('WindowArrangement', 'TargetWidth', fallback=1030)
            target_h = self.config.getint('WindowArrangement', 'TargetHeight', fallback=797)
            
            monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0,0)))
            w_left, w_top, w_right, w_bottom = monitor_info['Work']
            positions = [
                (w_left, w_top),
                (w_right - target_w, w_top),
                (w_left, w_bottom - target_h),
                (w_right - target_w, w_bottom - target_h)
            ]

            if self.mode == 'manual_arrange':
                self.manual_lock_sequence(positions, target_w, target_h)
            elif self.mode == 'auto_full_process':
                if self.auto_launch_sequence(positions, target_w, target_h):
                    self.execute_login_logic()

        except Exception as e:
            self.log_signal.emit(f"❌ 執行發生錯誤: {e}")
        finally:
            self.finished_signal.emit(self.arranged_windows)

    def manual_lock_sequence(self, positions, target_w, target_h):
        self.log_signal.emit("\n【手動排列模式】請依序點擊 4 個遊戲視窗 (左上->右上->左下->右下)")
        found_hwnds = []
        for i in range(4):
            self.log_signal.emit(f"⏳ 等待點擊第 {i+1} 個視窗...")
            while True:
                if win32api.GetAsyncKeyState(0x01) < 0:
                    x, y = win32api.GetCursorPos()
                    hwnd = win32gui.WindowFromPoint((x, y))
                    hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
                    if hwnd not in found_hwnds and hwnd != 0:
                        class_name = win32gui.GetClassName(hwnd)
                        if class_name not in ["Progman", "WorkerW", "Shell_TrayWnd"]:
                            found_hwnds.append(hwnd)
                            self.log_signal.emit(f"✅ 已鎖定視窗 {i+1}: {hwnd}")
                            time.sleep(0.5)
                            break
                time.sleep(0.05)

        self.arranged_windows = []
        for i, hwnd in enumerate(found_hwnds):
            try:
                win32gui.MoveWindow(hwnd, positions[i][0], positions[i][1], target_w, target_h, 1)
                self.arranged_windows.append({'hwnd': hwnd, 'x': positions[i][0], 'y': positions[i][1], 'index': i+1})
            except Exception as e:
                self.log_signal.emit(f"❌ 移動視窗失敗: {e}")
        self.log_signal.emit("🎉 視窗排列完成！")

    def auto_launch_sequence(self, positions, target_w, target_h):
        path = self.config.get('Launcher', 'Path', fallback=r'A:\WLO\WXOnline.exe')
        delay_load = self.config.getfloat('Launcher', 'DelayLoad', fallback=0.5)
        agree_x = self.config.getint('Launcher', 'AgreeBtnX', fallback=902)
        agree_y = self.config.getint('Launcher', 'AgreeBtnY', fallback=697)
        delay_agree = self.config.getfloat('Launcher', 'DelayAgree', fallback=1.5)
        start_x = self.config.getint('Launcher', 'StartBtnX', fallback=911)
        start_y = self.config.getint('Launcher', 'StartBtnY', fallback=641)
        delay_catch = self.config.getfloat('Launcher', 'DelayCatch', fallback=2.0)

        self.arranged_windows = []
        self.log_signal.emit(f"\n【全自動啟動模式】啟動路徑: {path}")

        def get_all_hwnds():
            hwnds = set()
            win32gui.EnumWindows(lambda h, ctx: hwnds.add(h) if win32gui.IsWindowVisible(h) else None, None)
            return hwnds

        for i in range(4):
            self.log_signal.emit(f"--- 正在啟動第 {i+1}/4 個視窗 ---")
            existing_hwnds = get_all_hwnds()
            try:
                if not os.path.exists(path):
                    self.log_signal.emit(f"❌ 錯誤: 找不到檔案 {path}")
                    return False
                subprocess.Popen(path, cwd=os.path.dirname(path))
            except Exception as e:
                self.log_signal.emit(f"❌ 啟提失敗: {e}")
                return False

            time.sleep(delay_load)
            self.foreground_click(agree_x, agree_y)
            time.sleep(delay_agree)
            self.foreground_click(start_x, start_y)
            
            self.log_signal.emit("⏳ 等待遊戲視窗出現...")
            found_new_hwnd = 0
            for attempt in range(30):
                new_hwnds = get_all_hwnds() - existing_hwnds
                valid_candidates = [h for h in new_hwnds if win32gui.GetWindowText(h)]
                if valid_candidates:
                    found_new_hwnd = valid_candidates[-1]
                    self.log_signal.emit(f"✅ 捕捉到新視窗 HWID: {found_new_hwnd}")
                    break
                time.sleep(0.5)
                
            if found_new_hwnd == 0:
                self.log_signal.emit("❌ 逾時: 無法抓取到新遊戲視窗，流程中斷。")
                return False
                
            time.sleep(delay_catch)
            try:
                win32gui.MoveWindow(found_new_hwnd, positions[i][0], positions[i][1], target_w, target_h, 1)
                self.arranged_windows.append({'hwnd': found_new_hwnd, 'x': positions[i][0], 'y': positions[i][1], 'index': i+1})
            except Exception as e:
                self.log_signal.emit(f"❌ 移動視窗失敗: {e}")

        self.log_signal.emit("🎉 全自動啟動與排列完成！準備進入登入流程...")
        return True

    def execute_login_logic(self):
        if not self.arranged_windows:
            self.log_signal.emit("❌ 錯誤：尚未排列視窗，無法執行自動登入。")
            return

        self.log_signal.emit("\n⚠️ 【警告】自動登入將接管您的滑鼠與鍵盤，請勿移動滑鼠！")
        delay_after = self.config.getfloat('AutoLogin', 'DelayAfterArrange', fallback=1.0)
        time.sleep(delay_after)

        target_w = self.config.getint('WindowArrangement', 'TargetWidth', fallback=1030)
        target_h = self.config.getint('WindowArrangement', 'TargetHeight', fallback=797)
        res_w = self.config.getint('WindowArrangement', 'ResWidth', fallback=target_w)
        res_h = self.config.getint('WindowArrangement', 'ResHeight', fallback=target_h)
        diff_w = target_w - res_w
        diff_h = target_h - res_h

        self.log_signal.emit("=== [階段一] 帳密登入作業 ===")
        for win_data in self.arranged_windows:
            idx = win_data['index']
            acc = self.config.get('AutoLogin', f'LoginAccount{idx}', fallback='')
            pwd = self.config.get('AutoLogin', f'LoginPassword{idx}', fallback='')
            self.perform_login_step(win_data['hwnd'], win_data['x'], win_data['y'], diff_w, diff_h, acc, pwd, idx)

        time.sleep(1)
        self.log_signal.emit("=== [階段二] 選角進入作業 ===")
        for win_data in self.arranged_windows:
            idx = win_data['index']
            char = self.config.getint('AutoLogin', f'LoginCharacter{idx}', fallback=1)
            self.perform_char_select_step(win_data['hwnd'], win_data['x'], win_data['y'], diff_w, diff_h, char, idx)

        self.log_signal.emit("🎉 全自動流程 (啟動 -> 排列 -> 登入) 圓滿結束！")

    def safe_set_foreground(self, hwnd):
        try:
            if win32gui.IsIconic(hwnd): win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except:
            try:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32gui.SetForegroundWindow(hwnd)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            except: pass

    def foreground_click(self, abs_x, abs_y):
        win32api.SetCursorPos((abs_x, abs_y))
        time.sleep(0.1)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, abs_x, abs_y, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, abs_x, abs_y, 0, 0)
        time.sleep(0.1)

    def foreground_key_press(self, key_code):
        win32api.keybd_event(key_code, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(key_code, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)

    def set_clipboard_text(self, text):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except: pass

    def paste_text(self):
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(ord('V'), 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)

    def perform_login_step(self, hwnd, win_x, win_y, diff_w, diff_h, account, password, idx):
        lx = self.config.getint('AutoLogin', 'LoginButtonX', fallback=515)
        ly = self.config.getint('AutoLogin', 'LoginButtonY', fallback=600)
        ax = self.config.getint('AutoLogin', 'AccountInputX', fallback=515)
        ay = self.config.getint('AutoLogin', 'AccountInputY', fallback=235)
        try:
            self.safe_set_foreground(hwnd)
            time.sleep(0.5)
            self.foreground_key_press(win32con.VK_ESCAPE)
            time.sleep(0.5)
            self.foreground_click(win_x + lx + diff_w, win_y + ly + diff_h)
            time.sleep(0.5)
            self.foreground_click(win_x + ax + diff_w, win_y + ay + diff_h)
            time.sleep(0.2)
            
            try:
                hwnd_ime = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
                if hwnd_ime: win32api.SendMessage(hwnd_ime, 0x0283, 0x0006, 0)
            except: pass
            
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            time.sleep(0.02)
            win32api.keybd_event(ord('A'), 0, 0, 0)
            time.sleep(0.02)
            win32api.keybd_event(ord('A'), 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)
            
            self.set_clipboard_text(account)
            self.paste_text()
            time.sleep(0.2)
            self.foreground_key_press(win32con.VK_TAB)
            time.sleep(0.2)
            self.set_clipboard_text(password)
            self.paste_text()
            time.sleep(0.2)
            
            self.foreground_key_press(win32con.VK_TAB)
            time.sleep(0.2)
            self.foreground_key_press(win32con.VK_DOWN)
            time.sleep(0.15)
            self.foreground_key_press(win32con.VK_RETURN)
            time.sleep(0.15) 
            self.foreground_key_press(win32con.VK_RETURN)
            self.log_signal.emit(f"✅ 視窗 {idx} 登入請求已發送。")
        except Exception as e:
            self.log_signal.emit(f"❌ 視窗 {idx} 登入失敗: {e}")

    def perform_char_select_step(self, hwnd, win_x, win_y, diff_w, diff_h, char_idx, idx):
        try:
            self.safe_set_foreground(hwnd)
            time.sleep(0.5)
            cx = self.config.getint('AutoLogin', f'CharSlot{char_idx}X', fallback=0)
            cy = self.config.getint('AutoLogin', f'CharSlot{char_idx}Y', fallback=0)
            if cx != 0 and cy != 0:
                self.foreground_click(win_x + cx + diff_w, win_y + cy + diff_h)
                time.sleep(0.5)
                self.foreground_key_press(win32con.VK_RETURN)
                self.log_signal.emit(f"✅ 視窗 {idx} 已選角進入遊戲。")
            else:
                self.foreground_key_press(win32con.VK_RETURN)
        except Exception as e:
            self.log_signal.emit(f"❌ 視窗 {idx} 選角失敗: {e}")

# ==========================================
# 背景 Worker：清背包古錢執行器 (含 OCR)
# ==========================================
class ClearBagWorker(QThread):
    log_signal = Signal(str)
    image_signal = Signal(int, QPixmap, str) 
    finished_signal = Signal()

    def __init__(self, hwnds, config_path):
        super().__init__()
        self.hwnds = hwnds
        self.config_path = config_path
        self.config = configparser.ConfigParser()

    def safe_set_foreground(self, hwnd):
        try:
            if win32gui.IsIconic(hwnd): win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except:
            try:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32gui.SetForegroundWindow(hwnd)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            except: pass

    def foreground_click(self, abs_x, abs_y):
        win32api.SetCursorPos((abs_x, abs_y))
        time.sleep(0.1)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, abs_x, abs_y, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, abs_x, abs_y, 0, 0)
        time.sleep(0.1)

    def run(self):
        try:
            self.config.read(self.config_path, encoding='utf-8')
            
            mall_x = self.config.getint('ClearBag', 'MallX', fallback=868)
            mall_y = self.config.getint('ClearBag', 'MallY', fallback=103)
            acc_x = self.config.getint('ClearBag', 'AccountX', fallback=258)
            acc_y = self.config.getint('ClearBag', 'AccountY', fallback=438)
            
            coin_x1 = self.config.getint('ClearBag', 'CoinX1', fallback=218)
            coin_x2 = self.config.getint('ClearBag', 'CoinX2', fallback=295)
            coin_y1 = self.config.getint('ClearBag', 'CoinY1', fallback=514)
            coin_y2 = self.config.getint('ClearBag', 'CoinY2', fallback=529)
            
            target_w = self.config.getint('WindowArrangement', 'TargetWidth', fallback=1030)
            res_w = self.config.getint('WindowArrangement', 'ResWidth', fallback=1024)
            target_h = self.config.getint('WindowArrangement', 'TargetHeight', fallback=797)
            res_h = self.config.getint('WindowArrangement', 'ResHeight', fallback=768)
            diff_w = target_w - res_w
            diff_h = target_h - res_h

            crop_w = coin_x2 - coin_x1
            crop_h = coin_y2 - coin_y1

            self.log_signal.emit("\n🚀 開始執行【清背包古錢】測試流程 (含 OCR 辨識)...")

            for idx, hwnd in enumerate(self.hwnds):
                self.log_signal.emit(f"👉 正在處理第 {idx+1} 個視窗 (HWID: {hwnd})...")
                
                self.safe_set_foreground(hwnd)
                time.sleep(0.5)
                
                rect = win32gui.GetWindowRect(hwnd)
                win_x, win_y = rect[0], rect[1]

                self.log_signal.emit(f"   -> 點擊商城 ({mall_x}, {mall_y})")
                self.foreground_click(win_x + mall_x + diff_w, win_y + mall_y + diff_h)
                time.sleep(1.0) 

                self.log_signal.emit(f"   -> 點擊我的帳戶 ({acc_x}, {acc_y})")
                self.foreground_click(win_x + acc_x + diff_w, win_y + acc_y + diff_h)
                time.sleep(1.0) 

                self.log_signal.emit(f"   -> 擷取古錢數量圖片並進行 OCR...")
                screen = QGuiApplication.primaryScreen()
                pixmap = screen.grabWindow(
                    hwnd, 
                    coin_x1 + diff_w, 
                    coin_y1 + diff_h, 
                    crop_w, 
                    crop_h
                )
                
                if not pixmap.isNull():
                    try:
                        buffer = QBuffer()
                        buffer.open(QIODevice.ReadWrite)
                        pixmap.save(buffer, "PNG")
                        
                        pil_img = Image.open(io.BytesIO(buffer.data()))
                        custom_config = r'--psm 7 -c tessedit_char_whitelist=0123456789'
                        ocr_result = pytesseract.image_to_string(pil_img, config=custom_config).strip()
                        
                        if not ocr_result:
                            ocr_result = "無法辨識"
                            
                        self.log_signal.emit(f"   ✅ 截圖成功！OCR 辨識結果: {ocr_result}")
                        self.image_signal.emit(idx, pixmap, ocr_result)
                        
                    except Exception as e:
                        self.log_signal.emit(f"   ❌ OCR 發生錯誤: {e}")
                        self.image_signal.emit(idx, pixmap, "OCR 錯誤")
                else:
                    self.log_signal.emit(f"   ❌ 截圖失敗！")
                
                time.sleep(0.5)

            self.log_signal.emit("🎉 測試流程結束！")

        except Exception as e:
            self.log_signal.emit(f"❌ 執行發生錯誤: {e}")
        finally:
            self.finished_signal.emit()

# ==========================================
# 5. 背景 Worker：座標拾取器
# ==========================================
class CoordFinderWorker(QThread):
    coord_signal = Signal(dict)
    
    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        time.sleep(0.5) 
        while self.running:
            if win32api.GetAsyncKeyState(0x01) < 0:
                screen_x, screen_y = win32api.GetCursorPos()
                hwnd_at_point = win32gui.WindowFromPoint((screen_x, screen_y))
                
                if hwnd_at_point != 0 and win32gui.IsWindowVisible(hwnd_at_point):
                    target_hwnd = win32gui.GetAncestor(hwnd_at_point, win32con.GA_ROOT)
                    try:
                        class_name = win32gui.GetClassName(target_hwnd)
                        relative_x, relative_y = win32gui.ScreenToClient(target_hwnd, (screen_x, screen_y))
                        window_title = win32gui.GetWindowText(target_hwnd) or "無標題視窗"
                        rect = win32gui.GetWindowRect(target_hwnd)
                        win_w = rect[2] - rect[0]
                        win_h = rect[3] - rect[1]
                        
                        data = {
                            'title': window_title,
                            'class': class_name,
                            'hwnd': target_hwnd,
                            'size': f"{win_w}x{win_h}",
                            'rel_x': relative_x,
                            'rel_y': relative_y,
                            'abs_x': screen_x,
                            'abs_y': screen_y
                        }
                        self.coord_signal.emit(data)
                        break 
                    except Exception:
                        pass
            time.sleep(0.05)

    def stop(self):
        self.running = False

# ==========================================
# 6. 背景 Worker：前景連點器 (F11)
# ==========================================
class ForegroundClickerWorker(QThread):
    state_signal = Signal(bool) 

    def __init__(self):
        super().__init__()
        self.running = True
        self.is_clicking = False
        self.interval = 0.02

    def set_interval(self, val):
        self.interval = val

    def run(self):
        VK_F11 = 0x7A
        while self.running:
            if win32api.GetAsyncKeyState(VK_F11) & 0x8000:
                self.is_clicking = not self.is_clicking
                self.state_signal.emit(self.is_clicking)
                while win32api.GetAsyncKeyState(VK_F11) & 0x8000:
                    time.sleep(0.05)
            
            if self.is_clicking:
                x, y = win32api.GetCursorPos()
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.02)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
                time.sleep(self.interval)
            else:
                time.sleep(0.01)

    def stop(self):
        self.running = False

# ==========================================
# 彈出視窗：更新說明 (Changelog)
# ==========================================
class ChangelogDialog(QDialog):
    def __init__(self, content, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"更新說明 - v{CURRENT_VERSION}")
        self.resize(600, 500)
        self.init_ui(content)

    def init_ui(self, content):
        layout = QVBoxLayout(self)
        
        txt_changelog = QTextEdit()
        txt_changelog.setReadOnly(True)
        txt_changelog.setMarkdown(content) 
        txt_changelog.setStyleSheet("font-size: 14px; background-color: #f9f9f9; color: #333;")
        
        btn_close = QPushButton("我知道了")
        btn_close.setMinimumHeight(40)
        btn_close.setStyleSheet("background-color: #1976d2; color: white; font-weight: bold;")
        btn_close.clicked.connect(self.accept)

        layout.addWidget(txt_changelog)
        layout.addWidget(btn_close)

# ==========================================
# 彈出視窗：掛機任務參數編輯器
# ==========================================
class TaskConfigDialog(QDialog):
    def __init__(self, config_path, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.config.read(config_path, encoding='utf-8')
        self.inputs = {}
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"編輯設定檔 - {os.path.basename(self.config_path)}")
        self.resize(550, 600)
        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        for section in self.config.sections():
            if section in ['General', 'WindowArrangement', 'AutoLogin', 'ClearBag']:
                continue

            group = QGroupBox(f"⚔️ 任務: {section}")
            group.setStyleSheet("QGroupBox { font-weight: bold; color: #1976d2; }")
            form = QFormLayout()
            self.inputs[section] = {}

            txt_name = QLineEdit(section)
            self.inputs[section]['TaskName'] = txt_name
            form.addRow("任務名稱:", txt_name)

            chk_enable = QCheckBox("啟用此任務")
            chk_enable.setChecked(self.config.getint(section, 'Enable', fallback=0) == 1)
            self.inputs[section]['Enable'] = chk_enable
            form.addRow("", chk_enable)

            cmb_key = QComboBox()
            cmb_key.addItems(['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'MOUSE', 'NONE'])
            cmb_key.setCurrentText(self.config.get(section, 'Key', fallback='NONE').upper())
            self.inputs[section]['Key'] = cmb_key
            form.addRow("觸發按鍵 (Key):", cmb_key)

            spn_interval = QDoubleSpinBox()
            spn_interval.setRange(0.1, 9999.0)
            spn_interval.setSingleStep(0.5)
            spn_interval.setValue(self.config.getfloat(section, 'Interval', fallback=1.0))
            self.inputs[section]['Interval'] = spn_interval
            form.addRow("執行間隔(秒) (Interval):", spn_interval)

            spn_repeat = QSpinBox()
            spn_repeat.setRange(1, 999)
            spn_repeat.setValue(self.config.getint(section, 'RepeatCount', fallback=1))
            self.inputs[section]['RepeatCount'] = spn_repeat
            form.addRow("重複次數 (RepeatCount):", spn_repeat)

            spn_delay = QDoubleSpinBox()
            spn_delay.setRange(0.0, 99.0)
            spn_delay.setSingleStep(0.1)
            spn_delay.setValue(self.config.getfloat(section, 'ActionDelay', fallback=0.0))
            self.inputs[section]['ActionDelay'] = spn_delay
            form.addRow("動作間延遲(秒) (ActionDelay):", spn_delay)

            chk_click = QCheckBox("執行後續滑鼠點擊")
            chk_click.setChecked(self.config.getint(section, 'FollowUpClick', fallback=0) == 1)
            self.inputs[section]['FollowUpClick'] = chk_click
            form.addRow("", chk_click)

            box_mouse = QHBoxLayout()
            spn_mx = QSpinBox(); spn_mx.setRange(0, 9999); spn_mx.setValue(self.config.getint(section, 'MouseX', fallback=0))
            spn_my = QSpinBox(); spn_my.setRange(0, 9999); spn_my.setValue(self.config.getint(section, 'MouseY', fallback=0))
            self.inputs[section]['MouseX'] = spn_mx
            self.inputs[section]['MouseY'] = spn_my
            box_mouse.addWidget(QLabel("X:")); box_mouse.addWidget(spn_mx)
            box_mouse.addWidget(QLabel("Y:")); box_mouse.addWidget(spn_my)
            form.addRow("滑鼠點擊座標:", box_mouse)

            group.setLayout(form)
            content_layout.addWidget(group)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        btn_box = QHBoxLayout()
        btn_save = QPushButton("💾 儲存設定")
        btn_save.setMinimumHeight(40)
        btn_save.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.save_data)
        
        btn_cancel = QPushButton("❌ 取消")
        btn_cancel.setMinimumHeight(40)
        btn_cancel.clicked.connect(self.reject)
        
        btn_box.addWidget(btn_save)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def save_data(self):
        changes = {}
        for section, widgets in self.inputs.items():
            new_name = widgets['TaskName'].text().strip()
            if not new_name: new_name = section 
            
            changes[section] = {
                'new_name': new_name,
                'Enable': '1' if widgets['Enable'].isChecked() else '0',
                'Key': widgets['Key'].currentText(),
                'Interval': str(widgets['Interval'].value()),
                'RepeatCount': str(widgets['RepeatCount'].value()),
                'ActionDelay': str(widgets['ActionDelay'].value()),
                'FollowUpClick': '1' if widgets['FollowUpClick'].isChecked() else '0',
                'MouseX': str(widgets['MouseX'].value()),
                'MouseY': str(widgets['MouseY'].value())
            }

        for old_sec, data in changes.items():
            new_sec = data['new_name']
            if new_sec != old_sec:
                if not self.config.has_section(new_sec):
                    self.config.add_section(new_sec)
                self.config.remove_section(old_sec)
            
            self.config.set(new_sec, 'Enable', data['Enable'])
            self.config.set(new_sec, 'Key', data['Key'])
            self.config.set(new_sec, 'Interval', data['Interval'])
            self.config.set(new_sec, 'RepeatCount', data['RepeatCount'])
            self.config.set(new_sec, 'ActionDelay', data['ActionDelay'])
            self.config.set(new_sec, 'FollowUpClick', data['FollowUpClick'])
            self.config.set(new_sec, 'MouseX', data['MouseX'])
            self.config.set(new_sec, 'MouseY', data['MouseY'])

        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)
        self.accept() 

# ==========================================
# 7. 動態分頁：視窗排列與登入 UI
# ==========================================
class ArrangerTab(QWidget):
    def __init__(self):
        super().__init__()
        self.config_path = DEFAULT_CONFIG
        self.arranged_windows = []
        self.worker = None
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)

        group_accounts = QGroupBox("📝 帳號密碼快速設定 (對應 4 個視窗)")
        grid = QGridLayout()
        grid.addWidget(QLabel("視窗位置"), 0, 0)
        grid.addWidget(QLabel("登入帳號"), 0, 1)
        grid.addWidget(QLabel("登入密碼"), 0, 2)
        grid.addWidget(QLabel("角色欄位 (1-3)"), 0, 3)

        self.inputs = []
        labels = ["1. 左上角", "2. 右上角", "3. 左下角", "4. 右下角"]
        for i in range(4):
            grid.addWidget(QLabel(labels[i]), i+1, 0)
            acc_input = QLineEdit()
            pwd_input = QLineEdit()
            pwd_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
            char_spin = QSpinBox()
            char_spin.setRange(1, 3)
            
            grid.addWidget(acc_input, i+1, 1)
            grid.addWidget(pwd_input, i+1, 2)
            grid.addWidget(char_spin, i+1, 3)
            self.inputs.append({'acc': acc_input, 'pwd': pwd_input, 'char': char_spin})

        btn_save = QPushButton("💾 儲存設定至 config.ini")
        btn_save.clicked.connect(self.save_config)
        grid.addWidget(btn_save, 5, 0, 1, 4)
        group_accounts.setLayout(grid)

        group_actions = QGroupBox("🚀 執行操作")
        layout_actions = QVBoxLayout()
        
        self.btn_manual = QPushButton("🖱️ 手動鎖定並排列 (依序點擊 4 個視窗)")
        self.btn_manual.setMinimumHeight(45)
        self.btn_manual.clicked.connect(lambda: self.start_worker('manual_arrange'))
        
        self.btn_auto_full = QPushButton("⚡ 全自動流程 (自動啟動 -> 排列 -> 登入)")
        self.btn_auto_full.setMinimumHeight(55)
        self.btn_auto_full.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; font-size: 14px;")
        self.btn_auto_full.clicked.connect(lambda: self.start_worker('auto_full_process'))

        layout_actions.addWidget(self.btn_manual)
        layout_actions.addWidget(self.btn_auto_full)
        group_actions.setLayout(layout_actions)

        layout.addWidget(group_accounts)
        layout.addWidget(group_actions)
        layout.addStretch()
        self.setLayout(layout)

    def load_config(self):
        if not os.path.exists(self.config_path): return
        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        for i in range(4):
            idx = i + 1
            self.inputs[i]['acc'].setText(config.get('AutoLogin', f'LoginAccount{idx}', fallback=''))
            self.inputs[i]['pwd'].setText(config.get('AutoLogin', f'LoginPassword{idx}', fallback=''))
            self.inputs[i]['char'].setValue(config.getint('AutoLogin', f'LoginCharacter{idx}', fallback=1))

    def save_config(self):
        if not os.path.exists(self.config_path): return
        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        if not config.has_section('AutoLogin'): config.add_section('AutoLogin')
        for i in range(4):
            idx = i + 1
            config.set('AutoLogin', f'LoginAccount{idx}', self.inputs[i]['acc'].text())
            config.set('AutoLogin', f'LoginPassword{idx}', self.inputs[i]['pwd'].text())
            config.set('AutoLogin', f'LoginCharacter{idx}', str(self.inputs[i]['char'].value()))
        with open(self.config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        print("✅ 帳密設定已成功儲存至 config.ini！")

    def start_worker(self, mode):
        self.btn_manual.setEnabled(False)
        self.btn_auto_full.setEnabled(False)
        self.worker = ArrangerWorker(mode, self.config_path, self.arranged_windows)
        self.worker.log_signal.connect(print)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

    def on_worker_finished(self, arranged_windows):
        if arranged_windows: self.arranged_windows = arranged_windows
        self.btn_manual.setEnabled(True)
        self.btn_auto_full.setEnabled(True)

    def cleanup(self):
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

# ==========================================
# 8. 動態分頁：掛機控制器 UI
# ==========================================
class AutoScriptTab(QWidget):
    def __init__(self):
        super().__init__()
        self.target_hwnd = 0
        self.locker_thread = None
        self.script_worker = None
        self.crop_x = 100; self.crop_y = 5; self.crop_w = 125; self.crop_h = 15
        self.init_ui()
        self.refresh_configs()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)

        group_config = QGroupBox("📁 設定檔選擇")
        layout_config = QHBoxLayout()
        self.combo_config = QComboBox()
        
        self.btn_edit_cfg = QPushButton("⚙️ 編輯參數")
        self.btn_edit_cfg.clicked.connect(self.open_config_editor)
        
        self.btn_refresh_cfg = QPushButton("🔄 重新整理")
        self.btn_refresh_cfg.clicked.connect(self.refresh_configs)
        
        layout_config.addWidget(self.combo_config, stretch=1)
        layout_config.addWidget(self.btn_edit_cfg)
        layout_config.addWidget(self.btn_refresh_cfg)
        group_config.setLayout(layout_config)

        group_target = QGroupBox("🎯 目標視窗鎖定")
        layout_target = QVBoxLayout()
        self.lbl_target_info = QLabel("目前狀態：尚未鎖定視窗")
        self.lbl_target_info.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        self.btn_lock = QPushButton("🔒 點擊開始鎖定視窗")
        self.btn_lock.setMinimumHeight(40)
        self.btn_lock.clicked.connect(self.start_locking)

        layout_id_img = QHBoxLayout()
        self.lbl_char_img = QLabel("等待截圖...")
        self.lbl_char_img.setFixedSize(self.crop_w, self.crop_h)
        self.lbl_char_img.setAlignment(Qt.AlignCenter)
        self.lbl_char_img.setStyleSheet("border: 1px solid #555; background-color: #222; color: #888;")
        self.btn_recapture = QPushButton("📸 重新截圖")
        self.btn_recapture.setEnabled(False)
        self.btn_recapture.clicked.connect(self.capture_character_id)

        layout_id_img.addWidget(QLabel("角色 ID 預覽："))
        layout_id_img.addWidget(self.lbl_char_img)
        layout_id_img.addWidget(self.btn_recapture)
        layout_id_img.addStretch()

        layout_target.addWidget(self.lbl_target_info)
        layout_target.addWidget(self.btn_lock)
        layout_target.addLayout(layout_id_img)
        group_target.setLayout(layout_target)

        group_tasks = QGroupBox("📋 任務預覽 (讀取自 INI)")
        layout_tasks = QVBoxLayout()
        self.txt_tasks = QTextEdit()
        self.txt_tasks.setReadOnly(True)
        layout_tasks.addWidget(self.txt_tasks)
        group_tasks.setLayout(layout_tasks)

        layout_controls = QHBoxLayout()
        self.btn_start = QPushButton("▶️ 啟動掛機")
        self.btn_start.setMinimumHeight(50)
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; font-size: 14px;")
        self.btn_start.setEnabled(False) 
        self.btn_start.clicked.connect(self.start_script)
        
        self.btn_stop = QPushButton("⏹️ 停止掛機")
        self.btn_stop.setMinimumHeight(50)
        self.btn_stop.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; font-size: 14px;")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_script)

        layout_controls.addWidget(self.btn_start)
        layout_controls.addWidget(self.btn_stop)

        layout.addWidget(group_config)
        layout.addWidget(group_target)
        layout.addWidget(group_tasks, stretch=1)
        layout.addLayout(layout_controls)
        self.setLayout(layout)
        self.combo_config.currentTextChanged.connect(self.preview_config)

    def open_config_editor(self):
        filename = self.combo_config.currentText()
        if not filename or filename == "找不到任何 .ini 設定檔":
            QMessageBox.warning(self, "警告", "請先選擇有效的設定檔！")
            return
        config_path = os.path.join(CONFIGS_DIR, filename)
        dialog = TaskConfigDialog(config_path, self)
        if dialog.exec():
            self.preview_config(filename)
            print(f"✅ 設定檔 {filename} 已更新！")

    def refresh_configs(self):
        self.combo_config.clear()
        search_path = os.path.join(CONFIGS_DIR, "*.ini")
        files = [os.path.basename(f) for f in glob.glob(search_path)]
        if not files: return
        if "config.ini" in files:
            files.remove("config.ini")
            files.sort()
            files.insert(0, "config.ini")
        else: files.sort()
        self.combo_config.addItems(files)

    def preview_config(self, filename):
        if not filename: return
        filepath = os.path.join(CONFIGS_DIR, filename)
        config = configparser.ConfigParser()
        try:
            config.read(filepath, encoding='utf-8')
            preview_text = f"【設定檔: {filename}】\n"
            for section in config.sections():
                if section in ['General', 'WindowArrangement', 'AutoLogin', 'ClearBag']: continue
                enable = config.get(section, 'Enable', fallback='0')
                status = "✅ 啟用" if enable == '1' else "❌ 停用"
                key = config.get(section, 'Key', fallback='無')
                preview_text += f"- [{section}] | 狀態: {status} | 按鍵: {key}\n"
            self.txt_tasks.setText(preview_text)
        except Exception as e: self.txt_tasks.setText(f"讀取設定檔失敗: {e}")

    def start_locking(self):
        self.btn_lock.setEnabled(False)
        self.btn_lock.setText("⏳ 鎖定中... 請點擊遊戲視窗")
        self.locker_thread = WindowLockerWorker()
        self.locker_thread.log_signal.connect(print)
        self.locker_thread.locked_signal.connect(self.on_window_locked)
        self.locker_thread.start()

    def on_window_locked(self, hwnd, title, class_name):
        self.target_hwnd = hwnd
        self.lbl_target_info.setText(f"✅ 已鎖定 | HWID: {hwnd} | 標題: {title}")
        self.lbl_target_info.setStyleSheet("color: #4caf50; font-weight: bold;")
        self.btn_lock.setEnabled(True)
        self.btn_lock.setText("重新鎖定視窗")
        self.btn_start.setEnabled(True) 
        self.btn_recapture.setEnabled(True)
        time.sleep(0.2)
        self.capture_character_id()

    def capture_character_id(self):
        if self.target_hwnd == 0: return
        try:
            screen = QGuiApplication.primaryScreen()
            pixmap = screen.grabWindow(self.target_hwnd, self.crop_x, self.crop_y, self.crop_w, self.crop_h)
            if not pixmap.isNull(): self.lbl_char_img.setPixmap(pixmap)
        except Exception: pass

    def start_script(self):
        filename = self.combo_config.currentText()
        if not filename: return
        config_path = os.path.join(CONFIGS_DIR, filename)
        self.btn_start.setEnabled(False)
        self.btn_start.setText("掛機執行中...")
        self.btn_stop.setEnabled(True)
        self.btn_lock.setEnabled(False)
        self.combo_config.setEnabled(False)
        self.btn_edit_cfg.setEnabled(False)
        self.btn_refresh_cfg.setEnabled(False)
        self.script_worker = AutoScriptWorker(self.target_hwnd, config_path)
        self.script_worker.log_signal.connect(print)
        self.script_worker.stopped_signal.connect(self.on_script_stopped)
        self.script_worker.start()

    def stop_script(self):
        if self.script_worker and self.script_worker.running:
            self.btn_stop.setEnabled(False)
            self.btn_stop.setText("停止中...")
            self.script_worker.stop()

    def on_script_stopped(self):
        self.btn_start.setEnabled(True)
        self.btn_start.setText("▶️ 啟動掛機")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("⏹️ 停止掛機")
        self.btn_lock.setEnabled(True)
        self.combo_config.setEnabled(True)
        self.btn_edit_cfg.setEnabled(True)
        self.btn_refresh_cfg.setEnabled(True)

    def cleanup(self):
        if self.script_worker and self.script_worker.running:
            self.script_worker.stop()
            self.script_worker.wait() 

# ==========================================
# 9. 動態分頁：輔助工具 UI
# ==========================================
class UtilitiesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.coord_worker = None
        self.coord_history = [] 
        self.clicker_worker = ForegroundClickerWorker()
        self.clicker_worker.state_signal.connect(self.update_clicker_ui)
        self.clicker_worker.start()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)

        group_coord = QGroupBox("📍 座標拾取工具 (用於設定檔 config.ini)")
        layout_coord = QVBoxLayout()
        self.btn_coord = QPushButton("🔍 點擊開始拾取座標")
        self.btn_coord.setMinimumHeight(40)
        self.btn_coord.clicked.connect(self.start_coord_picking)
        self.txt_coord_result = QTextEdit()
        self.txt_coord_result.setReadOnly(True)
        self.txt_coord_result.setMinimumHeight(250) 
        self.txt_coord_result.setStyleSheet("font-weight: bold; font-size: 14px; background-color: #1e1e1e; color: #ffffff;")
        self.txt_coord_result.setPlaceholderText("點擊上方按鈕後，將滑鼠移至目標視窗內並點擊左鍵...\n(最多保留 4 筆歷史紀錄)")
        layout_coord.addWidget(self.btn_coord)
        layout_coord.addWidget(self.txt_coord_result)
        group_coord.setLayout(layout_coord)

        group_clicker = QGroupBox("🖱️ 前景連點工具 (Global Hotkey)")
        layout_clicker = QVBoxLayout()
        lbl_info = QLabel("功能：針對當前滑鼠游標位置進行自動連點。\n控制：按下鍵盤【F11】鍵可隨時 開啟 / 暫停。")
        lbl_info.setStyleSheet("color: #aaaaaa;")
        layout_interval = QHBoxLayout()
        layout_interval.addWidget(QLabel("連點間隔 (秒):"))
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.01, 10.0)
        self.spin_interval.setSingleStep(0.01)
        self.spin_interval.setValue(0.02)
        self.spin_interval.valueChanged.connect(self.clicker_worker.set_interval)
        layout_interval.addWidget(self.spin_interval)
        layout_interval.addStretch()
        self.lbl_clicker_status = QLabel("狀態: 🔴 停止中 (按 F11 開始)")
        self.lbl_clicker_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff5252;")
        self.lbl_clicker_status.setAlignment(Qt.AlignCenter)
        layout_clicker.addWidget(lbl_info)
        layout_clicker.addLayout(layout_interval)
        layout_clicker.addSpacing(10)
        layout_clicker.addWidget(self.lbl_clicker_status)
        group_clicker.setLayout(layout_clicker)

        layout.addWidget(group_coord)
        layout.addWidget(group_clicker)
        layout.addStretch()
        self.setLayout(layout)

    def start_coord_picking(self):
        self.btn_coord.setEnabled(False)
        self.btn_coord.setText("⏳ 拾取中... 請點擊目標視窗")
        self.coord_worker = CoordFinderWorker()
        self.coord_worker.coord_signal.connect(self.on_coord_captured)
        self.coord_worker.start()

    def on_coord_captured(self, data):
        self.btn_coord.setEnabled(True)
        self.btn_coord.setText("🔍 重新拾取座標")
        new_record = (
            f"✅ 【{data['title']}】 (HWND: {data['hwnd']}, 大小: {data['size']})\n"
            f"📌 相對座標: X={data['rel_x']}, Y={data['rel_y']}  |  絕對座標: X={data['abs_x']}, Y={data['abs_y']}\n"
            f"{'-'*50}"
        )
        self.coord_history.insert(0, new_record)
        if len(self.coord_history) > 4:
            self.coord_history.pop() 
        self.txt_coord_result.setText("\n".join(self.coord_history))

    def update_clicker_ui(self, is_clicking):
        if is_clicking:
            self.lbl_clicker_status.setText("狀態: 🟢 連點執行中... (按 F11 暫停)")
            self.lbl_clicker_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #4caf50;")
        else:
            self.lbl_clicker_status.setText("狀態: 🔴 停止中 (按 F11 開始)")
            self.lbl_clicker_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff5252;")

    def cleanup(self):
        if self.coord_worker and self.coord_worker.running:
            self.coord_worker.stop()
            self.coord_worker.wait()
        if self.clicker_worker and self.clicker_worker.running:
            self.clicker_worker.stop()
            self.clicker_worker.wait()

# ==========================================
# 10. 動態分頁：視窗排列座標參數設定
# ==========================================
class ConfigEditorTab(QWidget):
    def __init__(self):
        super().__init__()
        self.config_path = DEFAULT_CONFIG
        self.inputs = {}
        self.init_ui()
        self.load_config()

    def init_ui(self):
        main_layout = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(15)

        group_launcher = QGroupBox("🚀 啟動器設定 [Launcher]")
        form_launcher = QFormLayout()
        self.inputs['Path'] = QLineEdit()
        self.inputs['DelayLoad'] = QDoubleSpinBox(); self.inputs['DelayLoad'].setSingleStep(0.1)
        self.inputs['AgreeBtnX'] = QSpinBox(); self.inputs['AgreeBtnX'].setRange(0, 9999)
        self.inputs['AgreeBtnY'] = QSpinBox(); self.inputs['AgreeBtnY'].setRange(0, 9999)
        self.inputs['DelayAgree'] = QDoubleSpinBox(); self.inputs['DelayAgree'].setSingleStep(0.1)
        self.inputs['StartBtnX'] = QSpinBox(); self.inputs['StartBtnX'].setRange(0, 9999)
        self.inputs['StartBtnY'] = QSpinBox(); self.inputs['StartBtnY'].setRange(0, 9999)
        self.inputs['DelayCatch'] = QDoubleSpinBox(); self.inputs['DelayCatch'].setSingleStep(0.1)

        form_launcher.addRow("遊戲啟動器路徑 (Path):", self.inputs['Path'])
        form_launcher.addRow("啟動器載入等待時間 (DelayLoad):", self.inputs['DelayLoad'])
        form_launcher.addRow("同意按鈕 X 座標 (AgreeBtnX):", self.inputs['AgreeBtnX'])
        form_launcher.addRow("同意按鈕 Y 座標 (AgreeBtnY):", self.inputs['AgreeBtnY'])
        form_launcher.addRow("同意後等待時間 (DelayAgree):", self.inputs['DelayAgree'])
        form_launcher.addRow("執行遊戲按鈕 X 座標 (StartBtnX):", self.inputs['StartBtnX'])
        form_launcher.addRow("執行遊戲按鈕 Y 座標 (StartBtnY):", self.inputs['StartBtnY'])
        form_launcher.addRow("抓取視窗緩衝時間 (DelayCatch):", self.inputs['DelayCatch'])
        group_launcher.setLayout(form_launcher)

        group_window = QGroupBox("🪟 視窗大小設定 [WindowArrangement]")
        form_window = QFormLayout()
        self.inputs['TargetWidth'] = QSpinBox(); self.inputs['TargetWidth'].setRange(100, 9999)
        self.inputs['TargetHeight'] = QSpinBox(); self.inputs['TargetHeight'].setRange(100, 9999)
        self.inputs['ResWidth'] = QSpinBox(); self.inputs['ResWidth'].setRange(100, 9999)
        self.inputs['ResHeight'] = QSpinBox(); self.inputs['ResHeight'].setRange(100, 9999)

        form_window.addRow("目標視窗寬度 (TargetWidth):", self.inputs['TargetWidth'])
        form_window.addRow("目標視窗高度 (TargetHeight):", self.inputs['TargetHeight'])
        form_window.addRow("遊戲解析度寬度 (ResWidth):", self.inputs['ResWidth'])
        form_window.addRow("遊戲解析度高度 (ResHeight):", self.inputs['ResHeight'])
        group_window.setLayout(form_window)

        group_login = QGroupBox("🔑 登入與選角座標設定 [AutoLogin]")
        form_login = QFormLayout()
        self.inputs['LoginButtonX'] = QSpinBox(); self.inputs['LoginButtonX'].setRange(0, 9999)
        self.inputs['LoginButtonY'] = QSpinBox(); self.inputs['LoginButtonY'].setRange(0, 9999)
        self.inputs['AccountInputX'] = QSpinBox(); self.inputs['AccountInputX'].setRange(0, 9999)
        self.inputs['AccountInputY'] = QSpinBox(); self.inputs['AccountInputY'].setRange(0, 9999)
        self.inputs['CharSlot1X'] = QSpinBox(); self.inputs['CharSlot1X'].setRange(0, 9999)
        self.inputs['CharSlot1Y'] = QSpinBox(); self.inputs['CharSlot1Y'].setRange(0, 9999)
        self.inputs['CharSlot2X'] = QSpinBox(); self.inputs['CharSlot2X'].setRange(0, 9999)
        self.inputs['CharSlot2Y'] = QSpinBox(); self.inputs['CharSlot2Y'].setRange(0, 9999)
        self.inputs['CharSlot3X'] = QSpinBox(); self.inputs['CharSlot3X'].setRange(0, 9999)
        self.inputs['CharSlot3Y'] = QSpinBox(); self.inputs['CharSlot3Y'].setRange(0, 9999)

        form_login.addRow("登入按鈕 X (LoginButtonX):", self.inputs['LoginButtonX'])
        form_login.addRow("登入按鈕 Y (LoginButtonY):", self.inputs['LoginButtonY'])
        form_login.addRow("帳號輸入框 X (AccountInputX):", self.inputs['AccountInputX'])
        form_login.addRow("帳號輸入框 Y (AccountInputY):", self.inputs['AccountInputY'])
        form_login.addRow("角色 1 欄位 X (CharSlot1X):", self.inputs['CharSlot1X'])
        form_login.addRow("角色 1 欄位 Y (CharSlot1Y):", self.inputs['CharSlot1Y'])
        form_login.addRow("角色 2 欄位 X (CharSlot2X):", self.inputs['CharSlot2X'])
        form_login.addRow("角色 2 欄位 Y (CharSlot2Y):", self.inputs['CharSlot2Y'])
        form_login.addRow("角色 3 欄位 X (CharSlot3X):", self.inputs['CharSlot3X'])
        form_login.addRow("角色 3 欄位 Y (CharSlot3Y):", self.inputs['CharSlot3Y'])
        group_login.setLayout(form_login)

        layout.addWidget(group_launcher)
        layout.addWidget(group_window)
        layout.addWidget(group_login)
        scroll.setWidget(content_widget)

        btn_save = QPushButton("💾 儲存所有參數至 config.ini")
        btn_save.setMinimumHeight(50)
        btn_save.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; font-size: 14px;")
        btn_save.clicked.connect(self.save_config)

        main_layout.addWidget(scroll)
        main_layout.addWidget(btn_save)
        self.setLayout(main_layout)

    def load_config(self):
        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        
        self.inputs['Path'].setText(config.get('Launcher', 'Path', fallback=r'A:\WLO\WXOnline.exe'))
        self.inputs['DelayLoad'].setValue(config.getfloat('Launcher', 'DelayLoad', fallback=0.5))
        self.inputs['AgreeBtnX'].setValue(config.getint('Launcher', 'AgreeBtnX', fallback=902))
        self.inputs['AgreeBtnY'].setValue(config.getint('Launcher', 'AgreeBtnY', fallback=697))
        self.inputs['DelayAgree'].setValue(config.getfloat('Launcher', 'DelayAgree', fallback=1.5))
        self.inputs['StartBtnX'].setValue(config.getint('Launcher', 'StartBtnX', fallback=911))
        self.inputs['StartBtnY'].setValue(config.getint('Launcher', 'StartBtnY', fallback=641))
        self.inputs['DelayCatch'].setValue(config.getfloat('Launcher', 'DelayCatch', fallback=2.0))

        self.inputs['TargetWidth'].setValue(config.getint('WindowArrangement', 'TargetWidth', fallback=1030))
        self.inputs['TargetHeight'].setValue(config.getint('WindowArrangement', 'TargetHeight', fallback=797))
        self.inputs['ResWidth'].setValue(config.getint('WindowArrangement', 'ResWidth', fallback=1024))
        self.inputs['ResHeight'].setValue(config.getint('WindowArrangement', 'ResHeight', fallback=768))

        self.inputs['LoginButtonX'].setValue(config.getint('AutoLogin', 'LoginButtonX', fallback=515))
        self.inputs['LoginButtonY'].setValue(config.getint('AutoLogin', 'LoginButtonY', fallback=600))
        self.inputs['AccountInputX'].setValue(config.getint('AutoLogin', 'AccountInputX', fallback=515))
        self.inputs['AccountInputY'].setValue(config.getint('AutoLogin', 'AccountInputY', fallback=235))
        self.inputs['CharSlot1X'].setValue(config.getint('AutoLogin', 'CharSlot1X', fallback=547))
        self.inputs['CharSlot1Y'].setValue(config.getint('AutoLogin', 'CharSlot1Y', fallback=427))
        self.inputs['CharSlot2X'].setValue(config.getint('AutoLogin', 'CharSlot2X', fallback=697))
        self.inputs['CharSlot2Y'].setValue(config.getint('AutoLogin', 'CharSlot2Y', fallback=425))
        self.inputs['CharSlot3X'].setValue(config.getint('AutoLogin', 'CharSlot3X', fallback=913))
        self.inputs['CharSlot3Y'].setValue(config.getint('AutoLogin', 'CharSlot3Y', fallback=414))

    def save_config(self):
        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        
        if not config.has_section('Launcher'): config.add_section('Launcher')
        config.set('Launcher', 'Path', self.inputs['Path'].text())
        config.set('Launcher', 'DelayLoad', str(self.inputs['DelayLoad'].value()))
        config.set('Launcher', 'AgreeBtnX', str(self.inputs['AgreeBtnX'].value()))
        config.set('Launcher', 'AgreeBtnY', str(self.inputs['AgreeBtnY'].value()))
        config.set('Launcher', 'DelayAgree', str(self.inputs['DelayAgree'].value()))
        config.set('Launcher', 'StartBtnX', str(self.inputs['StartBtnX'].value()))
        config.set('Launcher', 'StartBtnY', str(self.inputs['StartBtnY'].value()))
        config.set('Launcher', 'DelayCatch', str(self.inputs['DelayCatch'].value()))

        if not config.has_section('WindowArrangement'): config.add_section('WindowArrangement')
        config.set('WindowArrangement', 'TargetWidth', str(self.inputs['TargetWidth'].value()))
        config.set('WindowArrangement', 'TargetHeight', str(self.inputs['TargetHeight'].value()))
        config.set('WindowArrangement', 'ResWidth', str(self.inputs['ResWidth'].value()))
        config.set('WindowArrangement', 'ResHeight', str(self.inputs['ResHeight'].value()))

        if not config.has_section('AutoLogin'): config.add_section('AutoLogin')
        config.set('AutoLogin', 'LoginButtonX', str(self.inputs['LoginButtonX'].value()))
        config.set('AutoLogin', 'LoginButtonY', str(self.inputs['LoginButtonY'].value()))
        config.set('AutoLogin', 'AccountInputX', str(self.inputs['AccountInputX'].value()))
        config.set('AutoLogin', 'AccountInputY', str(self.inputs['AccountInputY'].value()))
        config.set('AutoLogin', 'CharSlot1X', str(self.inputs['CharSlot1X'].value()))
        config.set('AutoLogin', 'CharSlot1Y', str(self.inputs['CharSlot1Y'].value()))
        config.set('AutoLogin', 'CharSlot2X', str(self.inputs['CharSlot2X'].value()))
        config.set('AutoLogin', 'CharSlot2Y', str(self.inputs['CharSlot2Y'].value()))
        config.set('AutoLogin', 'CharSlot3X', str(self.inputs['CharSlot3X'].value()))
        config.set('AutoLogin', 'CharSlot3Y', str(self.inputs['CharSlot3Y'].value()))

        with open(self.config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        print("✅ 座標與排列參數已成功儲存至 config.ini！")
        QMessageBox.information(self, "儲存成功", "參數已成功儲存！")

# ==========================================
# 11. 動態分頁：清背包古錢測試 UI
# ==========================================
class ClearBagTab(QWidget):
    def __init__(self):
        super().__init__()
        self.config_path = DEFAULT_CONFIG
        self.hwnds = []
        self.locker_worker = None
        self.task_worker = None
        self.inputs = {}
        self.img_labels = []
        self.text_labels = [] 
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)

        group_params = QGroupBox("⚙️ 座標參數設定 (相對座標)")
        form_params = QFormLayout()
        
        self.inputs['MallX'] = QSpinBox(); self.inputs['MallX'].setRange(0, 9999)
        self.inputs['MallY'] = QSpinBox(); self.inputs['MallY'].setRange(0, 9999)
        self.inputs['AccountX'] = QSpinBox(); self.inputs['AccountX'].setRange(0, 9999)
        self.inputs['AccountY'] = QSpinBox(); self.inputs['AccountY'].setRange(0, 9999)
        self.inputs['CoinX1'] = QSpinBox(); self.inputs['CoinX1'].setRange(0, 9999)
        self.inputs['CoinX2'] = QSpinBox(); self.inputs['CoinX2'].setRange(0, 9999)
        self.inputs['CoinY1'] = QSpinBox(); self.inputs['CoinY1'].setRange(0, 9999)
        self.inputs['CoinY2'] = QSpinBox(); self.inputs['CoinY2'].setRange(0, 9999)

        form_params.addRow("商城按鈕 X (MallX):", self.inputs['MallX'])
        form_params.addRow("商城按鈕 Y (MallY):", self.inputs['MallY'])
        form_params.addRow("我的帳戶 X (AccountX):", self.inputs['AccountX'])
        form_params.addRow("我的帳戶 Y (AccountY):", self.inputs['AccountY'])
        
        box_layout = QHBoxLayout()
        box_layout.addWidget(QLabel("X1:")); box_layout.addWidget(self.inputs['CoinX1'])
        box_layout.addWidget(QLabel("X2:")); box_layout.addWidget(self.inputs['CoinX2'])
        box_layout.addWidget(QLabel("Y1:")); box_layout.addWidget(self.inputs['CoinY1'])
        box_layout.addWidget(QLabel("Y2:")); box_layout.addWidget(self.inputs['CoinY2'])
        form_params.addRow("古錢文字範圍 (Coin Box):", box_layout)
        
        btn_save = QPushButton("💾 儲存參數")
        btn_save.clicked.connect(self.save_config)
        form_params.addRow("", btn_save)
        group_params.setLayout(form_params)

        group_action = QGroupBox("🚀 執行測試")
        layout_action = QVBoxLayout()
        
        self.btn_lock = QPushButton("1. 鎖定 4 個遊戲視窗 (依序點擊)")
        self.btn_lock.setMinimumHeight(40)
        self.btn_lock.clicked.connect(self.start_locking)
        
        self.btn_run = QPushButton("2. 執行測試 (點擊並擷取古錢)")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self.start_task)

        layout_action.addWidget(self.btn_lock)
        layout_action.addWidget(self.btn_run)
        
        grid_imgs = QGridLayout()
        for i in range(4):
            lbl_title = QLabel(f"視窗 {i+1} 古錢:")
            
            lbl_img = QLabel("等待測試...")
            lbl_img.setFixedSize(120, 30)
            lbl_img.setAlignment(Qt.AlignCenter)
            lbl_img.setStyleSheet("border: 1px solid #555; background-color: #222; color: #888;")
            
            lbl_text = QLabel("數量: ---")
            lbl_text.setStyleSheet("font-weight: bold; color: #ff9800; font-size: 14px;")
            
            grid_imgs.addWidget(lbl_title, i, 0)
            grid_imgs.addWidget(lbl_img, i, 1)
            grid_imgs.addWidget(lbl_text, i, 2) 
            
            self.img_labels.append(lbl_img)
            self.text_labels.append(lbl_text)
            
        layout_action.addLayout(grid_imgs)
        group_action.setLayout(layout_action)

        layout.addWidget(group_params)
        layout.addWidget(group_action)
        layout.addStretch()
        self.setLayout(layout)

    def load_config(self):
        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        self.inputs['MallX'].setValue(config.getint('ClearBag', 'MallX', fallback=868))
        self.inputs['MallY'].setValue(config.getint('ClearBag', 'MallY', fallback=103))
        self.inputs['AccountX'].setValue(config.getint('ClearBag', 'AccountX', fallback=258))
        self.inputs['AccountY'].setValue(config.getint('ClearBag', 'AccountY', fallback=438))
        self.inputs['CoinX1'].setValue(config.getint('ClearBag', 'CoinX1', fallback=218))
        self.inputs['CoinX2'].setValue(config.getint('ClearBag', 'CoinX2', fallback=295))
        self.inputs['CoinY1'].setValue(config.getint('ClearBag', 'CoinY1', fallback=514))
        self.inputs['CoinY2'].setValue(config.getint('ClearBag', 'CoinY2', fallback=529))

    def save_config(self):
        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        if not config.has_section('ClearBag'): config.add_section('ClearBag')
        config.set('ClearBag', 'MallX', str(self.inputs['MallX'].value()))
        config.set('ClearBag', 'MallY', str(self.inputs['MallY'].value()))
        config.set('ClearBag', 'AccountX', str(self.inputs['AccountX'].value()))
        config.set('ClearBag', 'AccountY', str(self.inputs['AccountY'].value()))
        config.set('ClearBag', 'CoinX1', str(self.inputs['CoinX1'].value()))
        config.set('ClearBag', 'CoinX2', str(self.inputs['CoinX2'].value()))
        config.set('ClearBag', 'CoinY1', str(self.inputs['CoinY1'].value()))
        config.set('ClearBag', 'CoinY2', str(self.inputs['CoinY2'].value()))
        with open(self.config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        print("✅ 清背包參數已儲存！")

    def start_locking(self):
        self.btn_lock.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.locker_worker = MultiWindowLockerWorker()
        self.locker_worker.log_signal.connect(print)
        self.locker_worker.locked_signal.connect(self.on_windows_locked)
        self.locker_worker.start()

    def on_windows_locked(self, hwnds):
        self.hwnds = hwnds
        self.btn_lock.setEnabled(True)
        self.btn_lock.setText("重新鎖定 4 個視窗")
        if len(self.hwnds) > 0:
            self.btn_run.setEnabled(True)

    def start_task(self):
        self.save_config() 
        self.btn_lock.setEnabled(False)
        self.btn_run.setEnabled(False)
        for lbl in self.img_labels: lbl.clear()
        for lbl in self.text_labels: lbl.setText("數量: 辨識中...")
        
        self.task_worker = ClearBagWorker(self.hwnds, self.config_path)
        self.task_worker.log_signal.connect(print)
        self.task_worker.image_signal.connect(self.update_image)
        self.task_worker.finished_signal.connect(self.on_task_finished)
        self.task_worker.start()

    def update_image(self, idx, pixmap, ocr_text):
        if idx < len(self.img_labels):
            self.img_labels[idx].setPixmap(pixmap)
            self.text_labels[idx].setText(f"數量: {ocr_text}")

    def on_task_finished(self):
        self.btn_lock.setEnabled(True)
        self.btn_run.setEnabled(True)

    def cleanup(self):
        if self.locker_worker and self.locker_worker.running:
            self.locker_worker.stop()
            self.locker_worker.wait()
        if self.task_worker and self.task_worker.isRunning():
            self.task_worker.terminate() 
            self.task_worker.wait()

# ==========================================
# 12. 控制台首頁 (Dashboard Tab)
# ==========================================
class DashboardTab(QWidget):
    request_new_tab = Signal(str)  
    show_changelog = Signal() 

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)

        top_layout = QHBoxLayout()
        lbl_title = QLabel(f"武林群俠傳 輔助控制台 (v{CURRENT_VERSION})")
        lbl_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1976d2;")
        
        btn_info = QPushButton("❕ 更新說明")
        btn_info.setFixedSize(100, 35)
        btn_info.setStyleSheet("background-color: #ff9800; color: white; font-weight: bold; border-radius: 5px;")
        btn_info.clicked.connect(lambda: self.show_changelog.emit())
        
        top_layout.addWidget(lbl_title)
        top_layout.addStretch()
        top_layout.addWidget(btn_info)
        layout.addLayout(top_layout)

        self.chk_admin = QCheckBox("🛡️ 強制以管理員身分執行 (下次啟動生效)")
        self.chk_admin.setStyleSheet("font-weight: bold; color: #ff9800;")
        self.chk_admin.setChecked(get_admin_setting())
        self.chk_admin.toggled.connect(self.on_admin_toggled)

        btn_auto_script = QPushButton("➕ 新增掛機控制器 (多開支援)")
        btn_auto_script.setMinimumHeight(50)
        btn_auto_script.clicked.connect(lambda: self.request_new_tab.emit("AutoScript"))

        btn_arranger = QPushButton("🪟 開啟視窗排列與登入工具")
        btn_arranger.setMinimumHeight(50)
        btn_arranger.clicked.connect(lambda: self.request_new_tab.emit("Arranger"))

        btn_config_editor = QPushButton("⚙️ 視窗排列座標參數設定")
        btn_config_editor.setMinimumHeight(50)
        btn_config_editor.clicked.connect(lambda: self.request_new_tab.emit("ConfigEditor"))

        btn_utils = QPushButton("🛠️ 開啟輔助工具 (座標/連點)")
        btn_utils.setMinimumHeight(50)
        btn_utils.clicked.connect(lambda: self.request_new_tab.emit("Utilities"))

        btn_clear_bag = QPushButton("🎒 開啟 [測試] 清背包古錢")
        btn_clear_bag.setMinimumHeight(50)
        btn_clear_bag.setStyleSheet("background-color: #5e35b1; color: white; font-weight: bold;")
        btn_clear_bag.clicked.connect(lambda: self.request_new_tab.emit("ClearBag"))

        layout.addWidget(self.chk_admin)
        layout.addSpacing(10)
        layout.addWidget(btn_auto_script)
        layout.addWidget(btn_arranger)
        layout.addWidget(btn_config_editor)
        layout.addWidget(btn_utils)
        layout.addWidget(btn_clear_bag)
        layout.addStretch()  
        self.setLayout(layout)

    def on_admin_toggled(self, checked):
        set_admin_setting(checked)
        print(f"⚙️ 已將「強制管理員執行」設定為: {'開啟' if checked else '關閉'}")

# ==========================================
# 13. 主視窗 (Main Window)
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"武林群俠傳 - 現代化自動輔助系統 v{CURRENT_VERSION}")
        self.resize(900, 700)
        
        self.init_ui()
        self.setup_logger()
        self.check_admin()
        self.process_changelog_and_update()

    def process_changelog_and_update(self):
        file_version = "0.0.0"
        content = "歡迎使用武林群俠傳輔助系統！\n\n(目前尚無更新說明)"

        if os.path.exists(CHANGELOG_FILE):
            try:
                with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if lines and lines[0].startswith("Version:"):
                        file_version = lines[0].replace("Version:", "").strip()
                        content = "".join(lines[1:]).strip()
                    else:
                        content = "".join(lines).strip()
            except Exception as e:
                print(f"讀取更新說明失敗: {e}")

        def parse_v(v): return tuple(map(int, v.split(".")))
        
        if parse_v(CURRENT_VERSION) > parse_v(file_version):
            self.display_changelog(content)
            try:
                with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
                    f.write(f"Version: {CURRENT_VERSION}\n")
                    f.write(content)
            except Exception as e:
                print(f"寫入更新說明失敗: {e}")

        self.updater = AutoUpdater(self, CURRENT_VERSION, UPDATE_JSON_URL)
        self.updater.check_for_updates()

    def display_changelog(self, content=None):
        if content is None:
            if os.path.exists(CHANGELOG_FILE):
                try:
                    with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        content = "".join(lines[1:]).strip() if lines and lines[0].startswith("Version:") else "".join(lines)
                except:
                    content = "讀取更新說明失敗。"
            else:
                content = "找不到更新說明檔案。"
                
        dialog = ChangelogDialog(content, self)
        dialog.exec()

    def check_admin(self):
        try: is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except: is_admin = False
            
        if is_admin:
            print("✅ 權限狀態：已取得「系統管理員」權限。")
            return

        if get_admin_setting():
            print("⚠️ 偵測到無管理員權限，正在請求自動提權...")
            try:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
                sys.exit()  
            except Exception as e:
                print(f"❌ 自動提權失敗: {e}")
        else:
            print("⚠️ 權限狀態：目前以「一般權限」執行中。")

    def init_ui(self):
        splitter = QSplitter(Qt.Vertical)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        
        self.dashboard = DashboardTab()
        self.dashboard.request_new_tab.connect(self.add_dynamic_tab)
        self.dashboard.show_changelog.connect(self.display_changelog)
        
        self.tabs.addTab(self.dashboard, "🏠 控制台首頁")
        self.tabs.tabBar().setTabButton(0, QTabBar.RightSide, None)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFont(QFont("Consolas", 10))
        self.log_console.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")

        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_console)
        splitter.setSizes([500, 200])  

        self.setCentralWidget(splitter)

    def setup_logger(self):
        self.log_stream = LogStream()
        self.log_stream.new_log.connect(self.append_log)
        sys.stdout = self.log_stream  
        sys.stderr = self.log_stream  
        print("✅ 系統初始化完成，等待指令...")

    def append_log(self, text):
        self.log_console.moveCursor(QTextCursor.End)
        self.log_console.insertPlainText(text + "\n")
        self.log_console.moveCursor(QTextCursor.End)

    def add_dynamic_tab(self, tab_type):
        try:
            if tab_type == "AutoScript":
                new_tab = AutoScriptTab()
                title = f"掛機控制器 ({self.tabs.count()})"
            elif tab_type == "Arranger":
                new_tab = ArrangerTab()
                title = "視窗排列與登入"
            elif tab_type == "ConfigEditor":
                new_tab = ConfigEditorTab()
                title = "排列座標參數設定"
            elif tab_type == "Utilities":
                new_tab = UtilitiesTab()
                title = "輔助工具"
            elif tab_type == "ClearBag":
                new_tab = ClearBagTab()
                title = "測試: 清背包古錢"
            else:
                return

            self.tabs.addTab(new_tab, title)
            print(f"👉 已在背景開啟新分頁: {title}")
            
        except Exception as e:
            print(f"❌ 新增分頁時發生錯誤: {e}")

    def close_tab(self, index):
        if index == 0: return  
        widget = self.tabs.widget(index)
        if hasattr(widget, 'cleanup'):
            widget.cleanup()
        tab_title = self.tabs.tabText(index)
        self.tabs.removeTab(index)
        print(f"🗑️ 已關閉分頁: {tab_title}")

# ==========================================
# 程式進入點
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft JhengHei", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())