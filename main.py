import json
import re
from languages import LANGUAGES
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import sys
import os
import threading
import time
import subprocess
import numpy as np

# === БЕЗОПАСНЫЙ ИМПОРТ SOUNDDEVICE ===
# Обработка ошибок PortAudio при первом запуске
try:
    import sounddevice as sd
    print("✅ Аудио система инициализирована успешно")
except Exception as e:
    print(f"⚠️ ПРЕДУПРЕЖДЕНИЕ: Ошибка инициализации аудио драйверов")
    print(f"   Детали: {e}")
    print("   Повторная попытка...")
    time.sleep(1)
    try:
        import sounddevice as sd
        print("✅ Аудио система инициализирована (со второй попытки)")
    except Exception as e2:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать аудио")
        print(f"   {e2}")
        print("\n💡 РЕШЕНИЯ:")
        print("   1. Перезагрузите компьютер")
        print("   2. Проверьте что микрофон/динамики подключены")
        print("   3. Обновите аудио драйверы")
        print("   4. Закройте другие аудио программы")
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showerror(
            "Ошибка аудио системы",
            "Не удалось инициализировать аудио драйверы.\n\n"
            "Попробуйте:\n"
            "• Перезагрузить компьютер\n"
            "• Проверить подключение микрофона/динамиков\n"
            "• Закрыть другие аудио программы\n"
            "• Обновить аудио драйверы\n\n"
            f"Техническая информация:\n{str(e2)[:200]}"
        )
        sys.exit(1)

import soundfile as sf
import noisereduce as nr
import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import datetime
import tempfile
import queue
import atexit

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---Фикс окна консоли ffmpeg
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    original_popen = subprocess.Popen

    def patched_popen(*args, **kwargs):
        if "startupinfo" not in kwargs:
            kwargs["startupinfo"] = startupinfo
        return original_popen(*args, **kwargs)

    subprocess.Popen = patched_popen


# --- ИСПРАВЛЕНИЕ ДЛЯ PYTHON 3.14+ ---
try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
        sys.modules["audioop"] = audioop
    except ImportError:
        print("Внимание: библиотека audioop_lts не найдена.")

# --- НАСТРОЙКА FFMPEG ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
os.environ["PATH"] = BIN_DIR + os.pathsep + os.environ["PATH"]
FFMPEG_PATH = os.path.join(BIN_DIR, "ffmpeg.exe")

# === НАСТРОЙКА ПАПКИ ДЛЯ КОНФИГУРАЦИИ ===
# Сохраняем настройки в AppData, чтобы НЕ требовать права администратора
# даже если программа установлена в Program Files
if sys.platform == "win32":
    # Windows: C:\Users\Username\AppData\Roaming\JB Software\JB Audio Recorder
    CONFIG_DIR = os.path.join(os.getenv('APPDATA'), 'JB Software', 'JB Audio Recorder')
    _OLD_CONFIG_DIR = os.path.join(os.getenv('APPDATA'), 'JB Audio Recorder')
else:
    # Linux/Mac: ~/.jb-software/jb-audio-recorder
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), '.jb-software', 'jb-audio-recorder')
    _OLD_CONFIG_DIR = os.path.join(os.path.expanduser("~"), '.jb-audio-recorder')

# Создаем папку если её нет
try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    print(f"📁 Папка настроек: {CONFIG_DIR}")
except Exception as e:
    print(f"⚠️ Ошибка создания папки настроек: {e}")
    # Fallback на папку программы (для совместимости)
    CONFIG_DIR = BASE_DIR
    _OLD_CONFIG_DIR = None

# Миграция настроек из старой папки в новую
_OLD_CONFIG_FILE = os.path.join(_OLD_CONFIG_DIR, "config.json") if _OLD_CONFIG_DIR else None
if _OLD_CONFIG_FILE and os.path.exists(_OLD_CONFIG_FILE):
    _new_config = os.path.join(CONFIG_DIR, "config.json")
    if not os.path.exists(_new_config):
        try:
            import shutil
            shutil.copy2(_OLD_CONFIG_FILE, _new_config)
            print(f"✅ Настройки перенесены из {_OLD_CONFIG_DIR}")
        except Exception as _e:
            print(f"⚠️ Не удалось перенести настройки: {_e}")

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

APP_VERSION  = "1.0"
GITHUB_REPO  = "jeffbennington/JB-Audio-Recorder"


def _version_newer(remote: str, local: str) -> bool:
    """Return True if remote version is strictly newer than local."""
    try:
        r = tuple(int(x) for x in remote.strip().split("."))
        l = tuple(int(x) for x in local.strip().split("."))
        return r > l
    except Exception:
        return False


# === ПРОВЕРКА VISUAL C++ REDISTRIBUTABLE ===
def _is_vcredist_installed():
    """Проверяет наличие VC++ 2015-2022 x64 через реестр и наличие DLL."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
        )
        val, _ = winreg.QueryValueEx(key, "Installed")
        winreg.CloseKey(key)
        if val == 1:
            return True
    except Exception:
        pass
    # Fallback: проверяем DLL
    dll_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "VCRUNTIME140.dll")
    return os.path.isfile(dll_path)


def _save_vcredist_flag():
    """Записывает в config флаг vcredist_ok = True."""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    cfg["vcredist_ok"] = True
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _show_vcredist_dialog(lang="English"):
    """Показывает предупреждение об отсутствии Visual C++ Redistributable."""
    import tkinter as tk
    import webbrowser

    OFFICIAL_URL = "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170"
    DIRECT_URL   = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

    s = LANGUAGES.get(lang) or LANGUAGES["English"]

    root = tk.Tk()
    root.withdraw()

    dlg = tk.Toplevel(root)
    dlg.title(s.get("vcredist_title", s.get("title", "Visual C++ Redistributable Required")))
    dlg.resizable(False, False)
    dlg.configure(bg="#1e1e2e")
    dlg.grab_set()

    dlg.update_idletasks()
    W, H = 560, 350
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    dlg.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")

    try:
        icon_path = os.path.join(BASE_DIR, "icon.ico")
        if os.path.exists(icon_path):
            dlg.iconbitmap(icon_path)
    except Exception:
        pass

    # --- Основной текст ---
    body_frame = tk.Frame(dlg, bg="#1e1e2e", padx=24, pady=20)
    body_frame.pack(fill="both", expand=True)

    tk.Label(body_frame, text=s.get("vcredist_body", s.get("body", "")),
             bg="#1e1e2e", fg="#e0e0e0",
             font=("Segoe UI", 10), justify="left").pack(anchor="w")

    # --- Ссылки ---
    links_frame = tk.Frame(dlg, bg="#1e1e2e", padx=24, pady=4)
    links_frame.pack(fill="x")

    def add_link(parent, label, url):
        lbl = tk.Label(parent, text=label, bg="#1e1e2e", fg="#4da6ff",
                       font=("Segoe UI", 9, "underline"), cursor="hand2")
        lbl.pack(anchor="w")
        lbl.bind("<Button-1>", lambda e: webbrowser.open(url))

    tk.Label(links_frame, text=s.get("vcredist_official", s.get("official", "Official website:")),
             bg="#1e1e2e", fg="#888888", font=("Segoe UI", 9)).pack(anchor="w")
    add_link(links_frame, "→ learn.microsoft.com", OFFICIAL_URL)

    tk.Label(links_frame, text=s.get("vcredist_direct", s.get("direct", "Direct download:")),
             bg="#1e1e2e", fg="#888888", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
    add_link(links_frame, "→ aka.ms/vs/17/release/vc_redist.x64.exe", DIRECT_URL)

    # --- Кнопка ОК ---
    btn_frame = tk.Frame(dlg, bg="#1e1e2e", pady=14)
    btn_frame.pack(fill="x")

    def on_ok():
        dlg.destroy()
        root.destroy()

    tk.Button(
        btn_frame, text=s.get("vcredist_ok", s.get("ok", "OK")), command=on_ok,
        bg="#3a7ebf", fg="white", activebackground="#2d6aa0", activeforeground="white",
        font=("Segoe UI", 10, "bold"), width=12, relief="flat", cursor="hand2"
    ).pack()

    root.mainloop()


def check_vcredist_on_startup():
    """
    Выполняется один раз при запуске.
    После первого подтверждения VC++ — проверка навсегда пропускается.
    """
    if sys.platform != "win32":
        return

    lang = "English"
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    # Уже проверяли и нашли — пропускаем
    if cfg.get("vcredist_ok"):
        return

    saved_lang = cfg.get("language", "System")
    if saved_lang == "System" or saved_lang not in LANGUAGES:
        lang = _detect_system_language()
    else:
        lang = saved_lang

    # Проверяем сейчас
    if _is_vcredist_installed():
        _save_vcredist_flag()          # запоминаем — больше не проверяем
    else:
        _show_vcredist_dialog(lang)    # диалог на языке пользователя


# === ОПРЕДЕЛЕНИЕ СИСТЕМНОГО ЯЗЫКА ===
_LOCALE_TO_LANG = {
    "ru": "Русский (Russian)",
    "be": "Беларуская (Belarusian)",
    "uk": "Українська (Ukrainian)",
    "sr": "Српски (Serbian)",
    "kk": "Қазақша (Kazakh)",
    "de": "Deutsch (German)",
    "fr": "Français (French)",
    "es": "Español (Spanish)",
    "it": "Italiano (Italian)",
    "pt": "Português (Portuguese)",
    "pl": "Polski (Polish)",
    "cs": "Čeština (Czech)",
    "no": "Norsk (Norwegian)",
    "nb": "Norsk (Norwegian)",
    "nn": "Norsk (Norwegian)",
    "sv": "Svenska (Swedish)",
    "fi": "Suomi (Finnish)",
    "tr": "Türkçe (Turkish)",
    "zh": "简体中文 (Chinese)",
    "ja": "日本語 (Japanese)",
    "hi": "हिन्दी (Hindi)",
}

def _detect_system_language():
    """Определяет язык ОС и возвращает соответствующий ключ из LANGUAGES.
    Если язык не поддерживается — возвращает 'English'."""
    lang_code = "en"
    try:
        if sys.platform == "win32":
            # Читаем язык UI из реестра Windows — самый надёжный способ
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Control Panel\International"
            )
            locale_name, _ = winreg.QueryValueEx(key, "LocaleName")  # e.g. "ru-RU"
            winreg.CloseKey(key)
            lang_code = locale_name.split("-")[0].lower()
        else:
            # Linux/macOS: читаем переменную окружения LANG
            import os
            raw = (os.environ.get("LANG") or os.environ.get("LANGUAGE") or "en")
            lang_code = raw.split("_")[0].split(".")[0].lower()
    except Exception:
        pass
    return _LOCALE_TO_LANG.get(lang_code, "English")


from pydub import AudioSegment
AudioSegment.converter = FFMPEG_PATH

# --- ПАТЧ ДЛЯ ЛЁГКОЙ ВЕРСИИ FFPROBE ---
from pydub import utils as pydub_utils
from pydub import audio_segment

original_mediainfo = pydub_utils.mediainfo_json

def patched_mediainfo_json(filepath, read_ahead_limit=-1):
    try:
        result = original_mediainfo(filepath, read_ahead_limit)
        return result
    except Exception:
        return {
            "streams": [{
                "codec_type": "audio",
                "codec_name": "unknown",
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_sample": 16,
                "sample_fmt": "s16"
            }],
            "format": {
                "duration": "0",
                "bit_rate": "320000"
            }
        }

pydub_utils.mediainfo_json = patched_mediainfo_json
audio_segment.mediainfo_json = patched_mediainfo_json

# --- ЦВЕТА ---
COLOR_BG, COLOR_BLOCK, COLOR_ACCENT = "#1a202c", "#2d3748", "#63b3ed"
COLOR_GREEN, COLOR_RED, COLOR_TEXT_DIM, COLOR_HOVER = "#68d391", "#e53e3e", "#718096", "#3e4a5d"
COLOR_YELLOW = "#f6e05e" 

# ===== КОНФИГУРАЦИЯ ОГРАНИЧЕНИЙ ФОРМАТОВ =====
FORMAT_CONSTRAINTS = {
    "ogg": {
        "allowed_sample_rates": [8000, 11025, 16000, 22050, 32000, 44100, 48000, 96000],
        "name": "OGG Vorbis"
    },
    "opus": {
        "allowed_sample_rates": [8000, 12000, 16000, 24000, 48000],
        "name": "Opus"
    },
    "wav": {"allowed_sample_rates": None, "name": "WAV"},
    "mp3": {"allowed_sample_rates": None, "name": "MP3"},
    "flac": {"allowed_sample_rates": None, "name": "FLAC"},
}

def get_available_sample_rates(format_ext):
    format_ext = format_ext.lower().strip('.')
    if format_ext in FORMAT_CONSTRAINTS:
        return FORMAT_CONSTRAINTS[format_ext]["allowed_sample_rates"]
    return None

def is_sample_rate_compatible(format_ext, sample_rate):
    allowed = get_available_sample_rates(format_ext)
    if allowed is None:
        return True
    return sample_rate in allowed

def get_nearest_compatible_sample_rate(format_ext, sample_rate):
    allowed = get_available_sample_rates(format_ext)
    if allowed is None:
        return sample_rate
    return min(allowed, key=lambda x: abs(x - sample_rate))

class JBAudioRecorder(ctk.CTk):

    def show_settings_window(self):
        self.show_window()
        self.show_page("settings")

    def setup_hotkeys(self):
        import keyboard
        keyboard.add_hotkey('ctrl+space', self._hotkey_toggle_record)
        keyboard.add_hotkey('ctrl+shift+space', self._hotkey_toggle_pause)
        keyboard.add_hotkey('ctrl+shift+s', self._hotkey_save)

    def _fix_scroll_region(self, event=None):
        self.file_list_frame._parent_canvas.configure(scrollregion=self.file_list_frame._parent_canvas.bbox("all"))

    def _on_mousewheel(self, event):
        canvas = self.file_list_frame._parent_canvas
        if not canvas:
            return
        try:
            current_pos = float(canvas.yview()[0])
        except:
            current_pos = 0.0

        if current_pos <= 0.0 and event.delta > 0:
            return
        canvas.yview_scroll(-1 * int(event.delta / 120), "units")

    def update_ready_status(self):
        if not self.is_recording and self.current_rec_array is None:
            txt = LANGUAGES[self.lang_var.get()]
            self.lbl_status.configure(text=txt["status_ready"], text_color=COLOR_GREEN)

    def load_settings(self):
        """Загрузка настроек из файла в AppData"""
        # Сначала проверяем новое место (AppData)
        if os.path.exists(CONFIG_FILE):
            settings_path = CONFIG_FILE
            print(f"📂 Загрузка настроек из: {CONFIG_FILE}")
        else:
            # Миграция: проверяем старое место (папка программы)
            old_settings = os.path.join(BASE_DIR, "config.json")
            if os.path.exists(old_settings):
                settings_path = old_settings
                print(f"📂 Загрузка настроек из старого места: {old_settings}")
                print(f"💡 Настройки будут перенесены в: {CONFIG_FILE}")
            else:
                # Настроек нет
                return
        
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                saved_lang = data.get("language", "System")
                self.lang_display_var.set(saved_lang)
                # lang_var всегда содержит реальный ключ из LANGUAGES
                if saved_lang == "System":
                    self.lang_var.set(_detect_system_language())
                elif saved_lang in LANGUAGES:
                    self.lang_var.set(saved_lang)
                else:
                    self.lang_var.set("English")
                self.tray_enabled_var.set(data.get("tray_enabled", True))
                self.autostart_var.set(data.get("autostart", False))
                self.auto_save_var.set(data.get("auto_save", False))
                self.always_on_top_var.set(data.get("always_on_top", False))
                self.skip_exit_confirm.set(data.get("skip_exit_confirm", False))
                self.hotkeys_enabled.set(data.get("hotkeys_enabled", True))
                self.check_disk_space_var.set(data.get("check_disk_space", True))
                self.auto_update_check_var.set(data.get("auto_update_check", True))
                self._last_temp_cleanup = data.get("last_temp_cleanup", "")
                self.notifications_var.set(data.get("notifications", "tray"))
                self.playback_volume = data.get("playback_volume", 0.5)
                self.noise_mode_var.set(data.get("noise_mode", "off"))

                saved_device = data.get("device")
                if saved_device:
                    self.device_var.set(saved_device)

                saved_path = data.get("save_path")
                if saved_path and os.path.exists(saved_path):
                    self.save_path.set(saved_path)
            
            print("✅ Настройки загружены успешно")
        except Exception as e:
            print(f"❌ Ошибка загрузки настроек: {e}")

    def save_settings(self):
        """Сохранение текущих настроек в файл в AppData"""
        data = {
            "language": self.lang_display_var.get(),
            "tray_enabled": self.tray_enabled_var.get(),
            "autostart": self.autostart_var.get(),
            "auto_save": self.auto_save_var.get(),
            "save_path": self.save_path.get(),
            "always_on_top": self.always_on_top_var.get(),
            "skip_exit_confirm": self.skip_exit_confirm.get(),
            "hotkeys_enabled": self.hotkeys_enabled.get(),
            "check_disk_space": self.check_disk_space_var.get(),
            "auto_update_check": self.auto_update_check_var.get(),
            "device": self.device_var.get(),
            "last_temp_cleanup": self._last_temp_cleanup,
            "notifications": self.notifications_var.get(),
            "playback_volume": self.playback_volume,
            "noise_mode": self.noise_mode_var.get(),
        }
        try:
            # Убеждаемся что папка существует
            os.makedirs(CONFIG_DIR, exist_ok=True)
            
            # Сохраняем в AppData
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            
            print(f"💾 Настройки сохранены в: {CONFIG_FILE}")
        except Exception as e:
            print(f"❌ Ошибка сохранения настроек: {e}")
            # Показываем пользователю понятное сообщение
            try:
                import tkinter.messagebox as mb
                mb.showerror(
                    "Ошибка сохранения",
                    f"Не удалось сохранить настройки:\n{e}\n\n"
                    f"Путь: {CONFIG_FILE}"
                )
            except:
                pass

    def reset_settings(self):
        """Сброс всех настроек к значениям по умолчанию"""
        import tkinter.messagebox as mb
        txt = LANGUAGES[self.lang_var.get()]
        if not mb.askyesno(
            txt.get("reset_settings", "Reset Settings"),
            txt.get("reset_settings_confirm", "Reset all settings to defaults?")
        ):
            return
        self.lang_display_var.set("System")
        self.change_language("System")
        self.tray_enabled_var.set(True)
        self.autostart_var.set(False)
        self.auto_save_var.set(False)
        self.always_on_top_var.set(False)
        self.skip_exit_confirm.set(False)
        self.hotkeys_enabled.set(True)
        self.check_disk_space_var.set(True)
        self.notifications_var.set("tray")
        def_path = os.path.join(os.path.expanduser("~"), "Music", "JB Audio Recorder")
        self.save_path.set(def_path)
        try:
            default_input = sd.query_devices(kind='input')
            if default_input:
                self.device_var.set(default_input['name'])
        except Exception:
            pass
        self.apply_always_on_top()
        self.save_settings()
        mb.showinfo(
            txt.get("reset_settings", "Reset Settings"),
            txt.get("reset_settings_success", "Settings reset successfully!")
        )

    def check_disk_space_on_startup(self):
        """Проверка свободного места на диске C — только один раз при запуске."""
        if self._disk_space_checked:
            return
        self._disk_space_checked = True
        if not self.check_disk_space_var.get():
            return
        
        try:
            import ctypes
            import platform
            
            if platform.system() != 'Windows':
                return
            
            # Получаем свободное место на диске C
            free_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p("C:\\"),
                None,
                None,
                ctypes.pointer(free_bytes)
            )
            
            free_gb = free_bytes.value / (1024 ** 3)
            
            # Если меньше 2 ГБ - показываем предупреждение
            if free_gb < 2.0:
                import tkinter.messagebox as mb
                txt = LANGUAGES[self.lang_var.get()]
                mb.showwarning(
                    txt.get("low_disk_space_title", "Low Disk Space"),
                    txt.get("low_disk_space_message", 
                            "Low disk space on C:\\. At least 2GB free space is recommended for long audio recordings.")
                )
        except Exception as e:
            print(f"Ошибка проверки места на диске: {e}")

    
    def update_tray_menu(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        if self.tray_enabled_var.get():
            self.start_tray()

    def create_tray_icon(self):
    # Пробуем загрузить реальную иконку
        try:
            icon_path = os.path.join(BASE_DIR, "icon.ico")
            image = Image.open(icon_path).resize((64, 64))
            return image
        except Exception:
            # Fallback — рисуем кружок если файл не найден
            image = Image.new('RGB', (64, 64), COLOR_BG)
            draw = ImageDraw.Draw(image)
            draw.ellipse((5, 5, 59, 59), fill=COLOR_ACCENT)
            return image
    
    def _tray_rec_label(self, _):
        txt = LANGUAGES[self.lang_var.get()]
        return txt.get("btn_stop","STOP") if self.is_recording else txt.get("tray_rec_start","Start Recording")

    def _tray_pause_label(self, _):
        txt = LANGUAGES[self.lang_var.get()]
        return txt.get("btn_resume","RESUME") if self.is_paused else txt.get("btn_pause","PAUSE")

    def _tray_pause_enabled(self, _):
        return self.is_recording

    def _tray_toggle_record(self, icon, _):
        self.after(0, self._do_tray_toggle_record)

    def _do_tray_toggle_record(self):
        txt = LANGUAGES[self.lang_var.get()]
        if not self.is_recording:
            if self.current_rec_array is not None:
                return
            self.finish_playback()
            self.lbl_status.configure(text=txt["status_recording"], text_color=COLOR_RED)
            self.btn_rec.configure(text=txt["btn_stop"], fg_color=COLOR_RED)
            self.set_player_enabled(False)
            self.start_recording()
            self._send_notification(
                txt.get("notify_rec_start","Recording"),
                txt.get("status_recording","Recording in progress")
            )
        else:
            self.stop_recording()
            self.btn_rec.configure(text=txt["btn_record"], fg_color=COLOR_RED)
        self._refresh_tray_menu()

    def _tray_toggle_pause(self, icon, _):
        if self.is_recording:
            self.after(0, self.toggle_pause)
            self.after(100, self._refresh_tray_menu)

    def _refresh_tray_menu(self):
        if self.tray_icon:
            try:
                self.tray_icon.update_menu()
            except Exception as e:
                print(f"Tray menu refresh error: {e}")

    def start_tray(self):
        image = self.create_tray_icon()
        menu = pystray.Menu(
            item(lambda i: LANGUAGES[self.lang_var.get()]["tray_open"],
                 lambda i, m: self.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            item(self._tray_rec_label, self._tray_toggle_record),
            item(self._tray_pause_label, self._tray_toggle_pause,
                 enabled=self._tray_pause_enabled),
            pystray.Menu.SEPARATOR,
            item(lambda i: LANGUAGES[self.lang_var.get()].get("tray_settings","Settings"),
                 lambda i, m: self.show_settings_window()),
            item(lambda i: LANGUAGES[self.lang_var.get()].get("tray_open_folder","Open Recordings Folder"),
                 lambda i, m: self.after(0, self.open_directory)),
            item(lambda i: LANGUAGES[self.lang_var.get()]["tray_exit"],
                 lambda i, m: self.quit_app()),
        )
        self.tray_icon = pystray.Icon("JB Audio Recorder", image, "JB Audio Recorder", menu)
        self.tray_icon.on_click = lambda icon, item: self.show_window()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        print("Запуск tray...")

    def change_language(self, new_lang):
        # Разрешаем "System" → реальный язык; lang_var всегда содержит реальный ключ
        if new_lang == "System":
            new_lang = _detect_system_language()
        elif new_lang not in LANGUAGES:
            new_lang = "English"
        self.lang_var.set(new_lang)

        self.update_tray_menu()
        txt = LANGUAGES[new_lang]

        # --- НАВИГАЦИЯ ---
        self.btn_nav_rec.configure(text=txt["nav_rec"])
        self.btn_nav_set.configure(text=txt["nav_set"])
        self.btn_nav_about.configure(text=txt["nav_about"])
        
        # --- ВКЛАДКА 1: ДИКТОФОН ---
        if not self.is_recording:
            if self.current_rec_array is not None:
                self.lbl_status.configure(text=txt.get("status_done", "READY"), text_color=COLOR_YELLOW)
            else:
                self.lbl_status.configure(text=txt["status_ready"], text_color=COLOR_GREEN)
            self.btn_rec.configure(text=txt["btn_record"])
        else:
            if self.is_paused:
                self.lbl_status.configure(text=txt["status_paused"])
            else:
                self.lbl_status.configure(text=txt["status_recording"])
            self.btn_rec.configure(text=txt["btn_stop"])

        self.btn_pause.configure(text=txt["btn_resume"] if self.is_paused else txt["btn_pause"])
        self.lbl_vol_input.configure(text=f"{txt['input_volume']}: {int(self.vol_slider.get()*100)}%")
        
        if hasattr(self, 'lbl_play_title'): 
            self.lbl_play_title.configure(text=txt["player_title"])
        
        if "Нет файла" in self.lbl_now_playing.cget("text") or "No file" in self.lbl_now_playing.cget("text"):
            self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {txt['no_file']}")
        else:
            display_name = txt.get("temp_rec_name", "Current recording") if self.current_file_name in ["Текущая запись", "Current recording"] else self.current_file_name
            self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {display_name}")

        if hasattr(self, 'lbl_play_vol'):
            self.lbl_play_vol.configure(text=f"{txt['play_volume']}: {int(self.play_vol_slider.get()*100)}%")
        
        self.btn_save.configure(text=txt["btn_save"])
        
        if self.delete_timer_id is None:
            self.btn_del.configure(text=txt["btn_delete"])

        # --- ПАРАМЕТРЫ ЗАПИСИ (НИЖНИЙ БЛОК) ---
        if hasattr(self, 'lbl_set_title'): self.lbl_set_title.configure(text=txt["nav_set"])
        if hasattr(self, 'lbl_param_format'): self.lbl_param_format.configure(text=txt["format"])
        if hasattr(self, 'lbl_param_bitrate'): self.lbl_param_bitrate.configure(text=txt["bitrate"])
        if hasattr(self, 'lbl_param_freq'): self.lbl_param_freq.configure(text=txt["frequency"])
        if hasattr(self, 'lbl_param_mode'): self.lbl_param_mode.configure(text=txt["mode"])
        if hasattr(self, 'lbl_param_noise'): self.lbl_param_noise.configure(text=txt["noise"])
        if hasattr(self, 'noise_mode_menu'):
            _nv = [txt.get('noise_off','Off'), txt.get('noise_light','Light'),
                   txt.get('noise_medium','Medium'), txt.get('noise_aggressive','Aggressive')]
            self._noise_display_to_key = {
                txt.get('noise_off','Off'): 'off', txt.get('noise_light','Light'): 'light',
                txt.get('noise_medium','Medium'): 'medium', txt.get('noise_aggressive','Aggressive'): 'aggressive',
            }
            self._noise_key_to_display = {v: k for k, v in self._noise_display_to_key.items()}
            self.noise_mode_menu.configure(values=_nv)
            self.noise_mode_menu.set(self._noise_key_to_display.get(self.noise_mode_var.get(), _nv[0]))

        if hasattr(self, 'lbl_lib_title'):
            self.lbl_lib_title.configure(text=txt["library_title"])
        if hasattr(self, 'btn_open_dir'):
            self.btn_open_dir.configure(text=txt["btn_open_explorer"])
        if hasattr(self, 'btn_change_save_path'):
            self.btn_change_save_path.configure(text=txt["btn_choose_folder"])
        if hasattr(self, 'lbl_save_path'):
            self.lbl_save_path.configure(text=txt["save_path_label"])
        
        self.update_file_list()

        # --- ВКЛАДКА 2: ПАРАМЕТРЫ СИСТЕМЫ ---
        self.lbl_params_title.configure(text=txt["tab_settings"])
        if hasattr(self, 'lbl_lang_setting'):
            self.lbl_lang_setting.configure(text=txt["language_label"])
        if hasattr(self, 'lbl_lang_help'):
            self.lbl_lang_help.configure(text=txt.get("lang_help", ""))
        
        if hasattr(self, 'lbl_param_dev'):
            self.lbl_param_dev.configure(text=txt.get("device_setting_label", "Recording Device"))
        if hasattr(self, 'lbl_dev_help'):
            self.lbl_dev_help.configure(text=txt.get("device_help", ""))

        if hasattr(self, 'lbl_tray_setting'):
            self.lbl_tray_setting.configure(text=txt["work_background"])
        if hasattr(self, 'lbl_autostart_setting'):
            self.lbl_autostart_setting.configure(text=txt.get("autostart", "Start with Windows"))
        if hasattr(self, 'lbl_aot_setting'):
            self.lbl_aot_setting.configure(text=txt.get("always_on_top", "Always on top"))
        
        if hasattr(self, 'lbl_hk_title'):
            self.lbl_hk_title.configure(text=txt.get("hotkeys_label", "Hotkeys"))
        if hasattr(self, 'lbl_hk_help'):
            self.lbl_hk_help.configure(text=txt.get("hotkeys_help", "[Ctrl+Space] Rec/Stop | [Ctrl+Shift+Space] Pause | [Ctrl+Shift+S] Save"))
        
        if hasattr(self, 'lbl_tray_help'):
            self.lbl_tray_help.configure(text=txt.get("work_background_help", "Allows recording and playback while app is minimized"))
        if hasattr(self, 'lbl_autostart_help'):
            self.lbl_autostart_help.configure(text=txt.get("autostart_help", "Always available at startup"))
        if hasattr(self, 'lbl_disk_check_setting'):
            self.lbl_disk_check_setting.configure(text=txt.get("check_disk_space", "Check disk space on startup"))
        if hasattr(self, 'lbl_disk_check_help'):
            self.lbl_disk_check_help.configure(text=txt.get("check_disk_space_help", "Warning if less than 2GB free"))
        if hasattr(self, 'lbl_update_check_setting'):
            self.lbl_update_check_setting.configure(text=txt.get("auto_update_check", "Check for updates on startup"))
        if hasattr(self, 'lbl_update_check_help'):
            self.lbl_update_check_help.configure(text=txt.get("auto_update_check_help", "Automatically check for new versions"))
        if hasattr(self, 'lbl_auto_save_help'):
            self.lbl_auto_save_help.configure(text=txt.get("auto_save_help", "Recording is saved directly to disk without preview"))
        if hasattr(self, 'lbl_aot_help'):
            self.lbl_aot_help.configure(text=txt.get("always_on_top_help", "App stays on top of all other windows"))
        if hasattr(self, 'lbl_notif_setting'):
            self.lbl_notif_setting.configure(text=txt.get("notify_setting", "Recording Status Notifications"))
        if hasattr(self, 'lbl_notif_help'):
            self.lbl_notif_help.configure(text=txt.get("notify_help", "Shows notification on recording start, pause and stop"))
        if hasattr(self, 'notif_menu'):
            _nv = [txt.get("notify_off","Disabled"), txt.get("notify_tray","When in tray"), txt.get("notify_always","Always")]
            self._notif_display_to_key = {
                txt.get("notify_off","Disabled"): "off",
                txt.get("notify_tray","When in tray"): "tray",
                txt.get("notify_always","Always"): "always",
            }
            self._notif_key_to_display = {v: k for k, v in self._notif_display_to_key.items()}
            self.notif_menu.configure(values=_nv)
            self.notif_menu.set(self._notif_key_to_display.get(self.notifications_var.get(), _nv[1]))

        if hasattr(self, 'lbl_auto_save_setting'):
            self.lbl_auto_save_setting.configure(text=txt.get("auto_save", "Auto save"))
        if hasattr(self, 'btn_reset'):
            self.btn_reset.configure(text=txt.get("reset_settings", "Reset to Defaults"))
        
        # --- ВКЛАДКА 3: О ПРОГРАММЕ ---
        for widget in self.page_about.winfo_children():
            widget.destroy()

        about_txt = LANGUAGES[new_lang]

        header_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        header_frame.pack(padx=20, pady=(20, 10), fill="x")
        ctk.CTkLabel(header_frame, text=about_txt["about_app_title"], font=("Roboto", 20, "bold"), text_color=COLOR_ACCENT).pack(pady=(15, 5))
        ctk.CTkLabel(header_frame, text=about_txt["about_version"], font=("Roboto", 12), text_color=COLOR_TEXT_DIM).pack(pady=(0, 4))
        self._update_status_label = ctk.CTkLabel(header_frame, text="", font=("Roboto", 11), cursor="")
        self._update_status_label.pack(pady=(0, 15))
        self._refresh_update_label()

        desc_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        desc_frame.pack(padx=20, pady=10, fill="x")
        ctk.CTkLabel(desc_frame, text=about_txt.get("nav_about", "О ПРОГРАММЕ"),
                     font=("Roboto", 11, "bold"), text_color=COLOR_TEXT_DIM).pack(padx=20, pady=(14, 4), anchor="w")
        ctk.CTkLabel(
            desc_frame,
            text=about_txt["about_description"],
            font=("Roboto", 13),
            text_color="#e2e8f0",
            wraplength=480,
            justify="left"
        ).pack(padx=20, pady=(0, 15), anchor="w")

        links_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        links_frame.pack(padx=20, pady=10, fill="x")
        ctk.CTkLabel(links_frame, text=about_txt.get("links_title", "Links"),
                     font=("Roboto", 11, "bold"), text_color=COLOR_TEXT_DIM).pack(pady=(14, 8))
        ctk.CTkButton(links_frame, text="WEBSITE", font=("Roboto", 13, "bold"),
                      fg_color="#7c3aed", hover_color="#6d28d9", corner_radius=10, height=36,
                      command=lambda: __import__("webbrowser").open("https://jbsoftware.ru")
                      ).pack(padx=20, pady=(0, 6), fill="x")
        ctk.CTkButton(links_frame, text="GitHub", font=("Roboto", 13, "bold"),
                      fg_color="#1e293b", hover_color="#0f172a", corner_radius=10, height=36,
                      command=lambda: __import__("webbrowser").open("https://github.com/jeffbennington/JB-Audio-Recorder")
                      ).pack(padx=20, pady=(0, 6), fill="x")
        ctk.CTkButton(links_frame, text="Telegram", font=("Roboto", 13, "bold"),
                      fg_color="#2AABEE", hover_color="#1a8fc8", corner_radius=10, height=36,
                      command=lambda: __import__("webbrowser").open("https://t.me/jbprogramms")
                      ).pack(padx=20, pady=(0, 14), fill="x")

        author_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        author_frame.pack(padx=20, pady=(10, 20), fill="x")
        ctk.CTkLabel(author_frame, text=about_txt["about_author"], font=("Roboto", 14, "bold"), text_color="#e2e8f0").pack(pady=(15, 2))
        ctk.CTkLabel(author_frame, text=about_txt["about_signature"], font=("Roboto", 12, "italic"), text_color=COLOR_TEXT_DIM).pack(pady=(0, 15))

    def update_tray_visibility(self):
        if self.tray_enabled_var.get():
            self.start_tray()
        else:
            if self.tray_icon:
                self.tray_icon.stop()
                self.tray_icon = None
        self.save_settings()
    
    def apply_always_on_top(self):
        if self.always_on_top_var.get():
            self.attributes('-topmost', True)
        else:
            self.attributes('-topmost', False)

    def confirm_exit_dialog(self):
        if self.skip_exit_confirm.get():
            self.real_exit()
            return

        self.exit_win = ctk.CTkToplevel(self)
        self.exit_win.resizable(False, False)
        self.exit_win.configure(fg_color=COLOR_BG)
        self.exit_win.transient(self)
        self.exit_win.grab_set()

        txt = LANGUAGES[self.lang_var.get()]
        self.exit_win.title(txt.get("exit_title", "Exit"))
        self._center_over_self(self.exit_win, 350, 200)

        lbl = ctk.CTkLabel(
            self.exit_win, 
            text=txt.get("exit_confirm", "Confirm Exit"), 
            font=("Roboto", 16, "bold"),
            text_color="#ffffff"
        )
        lbl.pack(pady=(25, 10))

        cb = ctk.CTkCheckBox(
            self.exit_win, 
            text=txt.get("dont_ask", "Don't ask again"), 
            variable=self.skip_exit_confirm,
            font=("Roboto", 12),
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_HOVER
        )
        cb.pack(pady=10)

        btn_frame = ctk.CTkFrame(self.exit_win, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=20, padx=20)

        btn_cancel = ctk.CTkButton(
            btn_frame, 
            text=txt.get("cancel_btn", "Cancel"), 
            width=100,
            fg_color="gray", 
            command=self.exit_win.destroy
        )
        btn_cancel.pack(side="right", padx=5)

        btn_exit = ctk.CTkButton(
            btn_frame, 
            text=txt.get("exit_btn", "Exit"), 
            width=100,
            fg_color="#e74c3c", 
            hover_color="#c0392b",
            command=self.real_exit
        )
        btn_exit.pack(side="left", padx=5)

    def real_exit(self):
        self.save_settings()
        self.destroy()
        os._exit(0)

    def update_file_list(self):
        txt = LANGUAGES[self.lang_var.get()]
        self.lbl_current_path.configure(text=f"{txt['folder_label']}: {self.save_path.get()}")

        # Cancel any pending deletions before rebuild
        for info in list(self._delete_timers.values()):
            if info.get('after_id'):
                self.after_cancel(info['after_id'])
            if info.get('blink_id'):
                self.after_cancel(info['blink_id'])
        self._delete_timers.clear()
        self._lib_tree_items.clear()

        # Update column headings with current language
        if self._lib_tree:
            self._lib_tree.heading("name", text=txt["list_name"].capitalize())
            self._lib_tree.heading("format", text=txt["format"])
            self._lib_tree.heading("size", text=txt.get("list_size", "Size").capitalize())
            self._lib_tree.heading("date", text=txt["list_date"].capitalize())

            # Clear all rows
            self._lib_tree.delete(*self._lib_tree.get_children())

            # Unsaved recording row (shown at top with special style)
            if self.current_rec_array is not None:
                iid = self._lib_tree.insert("", "end",
                    values=("🔴  " + txt["list_current"], "", "", "", ""),
                    tags=("current_rec",))
                self._lib_tree_items[iid] = None  # None = unsaved

            # File rows
            path = self.save_path.get()
            if os.path.exists(path):
                files = [f for f in os.listdir(path)
                         if f.endswith(('.wav', '.mp3', '.flac', '.ogg'))]
                files.sort(key=lambda x: os.path.getmtime(os.path.join(path, x)), reverse=True)

                for f in files:
                    fp = os.path.join(path, f)
                    mtime = os.path.getmtime(fp)
                    date_str = time.strftime('%d.%m.%Y %H:%M', time.localtime(mtime))
                    name_no_ext = os.path.splitext(f)[0]
                    ext = f.split('.')[-1].upper()
                    try:
                        sz = os.path.getsize(fp)
                        if sz < 1024:
                            size_str = f"{sz} B"
                        elif sz < 1024 * 1024:
                            size_str = f"{sz // 1024} KB"
                        else:
                            size_str = f"{sz / (1024*1024):.1f} MB"
                    except Exception:
                        size_str = "—"

                    iid = self._lib_tree.insert("", "end",
                        values=(name_no_ext, ext, size_str, date_str, "🗑️"))
                    self._lib_tree_items[iid] = fp

    # ── Library: Treeview click handler ─────────────────────────────────────

    def _lib_on_click(self, event):
        """Handle click on Treeview: load file OR start/cancel delete countdown."""
        region = self._lib_tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        iid = self._lib_tree.identify_row(event.y)
        if not iid:
            return

        fp = self._lib_tree_items.get(iid)

        # Any click on a countdown row cancels the deletion
        if fp and fp in self._delete_timers:
            self._lib_cancel_delete(fp, iid)
            return

        col = self._lib_tree.identify_column(event.x)
        if col == "#5":  # delete column
            if fp is None:
                return  # cannot delete unsaved recording
            self._lib_start_delete(fp, iid)
        else:
            # Load file / unsaved recording
            if fp is None:
                self._load_temp_rec()
            else:
                self._load_saved_file(fp, os.path.basename(fp))

    # ── Library: hover, scroll ───────────────────────────────────────────────

    def _lib_on_tree_resize(self, _event=None):
        """Подгоняет ширину колонки 'name' под доступное пространство.
        Остальные колонки фиксированы, чтобы корзина не уезжала за скроллбар."""
        if not self._lib_tree:
            return
        tree_w = self._lib_tree.winfo_width()
        if tree_w < 10:
            return
        # Сумма всех фиксированных колонок + 2px запас
        fixed = 48 + 62 + 115 + 48 + 2   # format + size + date + del + border
        name_w = max(60, tree_w - fixed)
        self._lib_tree.column("name", width=name_w)

    def _lib_on_scroll(self, event):
        """Прокручиваем Treeview и блокируем всплытие события наверх."""
        if hasattr(event, 'delta') and event.delta:
            self._lib_tree.yview_scroll(-int(event.delta / 120), "units")
        elif event.num == 4:
            self._lib_tree.yview_scroll(-1, "units")
        elif event.num == 5:
            self._lib_tree.yview_scroll(1, "units")
        return "break"

    def _lib_on_motion(self, event):
        """Подсвечиваем строку под курсором; меняем курсор над колонкой удаления."""
        iid  = self._lib_tree.identify_row(event.y)
        col  = self._lib_tree.identify_column(event.x)
        prev = getattr(self, "_lib_hover_iid", None)

        # Сбрасываем подсветку с предыдущей строки
        if prev and prev != iid:
            try:
                tags = [t for t in self._lib_tree.item(prev, "tags")
                        if t not in ("hover", "del_hover")]
                self._lib_tree.item(prev, tags=tags)
            except Exception:
                pass

        # Применяем подсветку к текущей строке
        if iid:
            try:
                base_tags = [t for t in self._lib_tree.item(iid, "tags")
                             if t not in ("hover", "del_hover")]
                fp = self._lib_tree_items.get(iid)
                in_del = (col == "#5" and fp is not None)
                in_countdown = "countdown" in base_tags
                if in_countdown:
                    self._lib_tree.configure(cursor="hand2")
                else:
                    new_tag = "del_hover" if in_del else "hover"
                    self._lib_tree.item(iid, tags=base_tags + [new_tag])
                    self._lib_tree.configure(cursor="hand2" if in_del else "")
            except Exception:
                pass
        else:
            self._lib_tree.configure(cursor="")

        self._lib_hover_iid = iid or None

    def _lib_on_leave(self, _event):
        """Убираем подсветку когда курсор покидает Treeview."""
        prev = getattr(self, "_lib_hover_iid", None)
        if prev:
            try:
                tags = [t for t in self._lib_tree.item(prev, "tags")
                        if t not in ("hover", "del_hover")]
                self._lib_tree.item(prev, tags=tags)
            except Exception:
                pass
        self._lib_hover_iid = None
        self._lib_tree.configure(cursor="")

    # ── Library: delete with countdown ──────────────────────────────────────

    def _lib_start_delete(self, filepath, iid):
        orig_vals = list(self._lib_tree.item(iid, "values"))
        orig_tags = list(self._lib_tree.item(iid, "tags"))
        self._delete_timers[filepath] = {
            'iid': iid, 'after_id': None, 'blink_id': None,
            'count': 5, 'blink_state': False,
            'orig_vals': orig_vals, 'orig_tags': orig_tags,
        }
        # Deselect AFTER TTK's own class binding fires (which would re-select the row)
        self.after(0, lambda _iid=iid: self._lib_tree.selection_remove(_iid))
        self._lib_apply_countdown_row(filepath, 5)
        after_id = self.after(1000, lambda fp=filepath: self._lib_tick_delete(fp))
        self._delete_timers[filepath]['after_id'] = after_id
        blink_id = self.after(400, lambda fp=filepath: self._lib_blink_delete(fp))
        self._delete_timers[filepath]['blink_id'] = blink_id

    def _lib_apply_countdown_row(self, filepath, count):
        """Update the countdown row text (tag stays as 'countdown')."""
        if filepath not in self._delete_timers:
            return
        info = self._delete_timers[filepath]
        iid = info['iid']
        txt = LANGUAGES[self.lang_var.get()]
        label = txt.get("lib_cancel_delete", "Cancel deletion")
        try:
            self._lib_tree.item(iid,
                values=(f"{label} ({count})", "", "", "", ""),
                tags=("countdown",))
        except Exception:
            pass

    def _lib_blink_delete(self, filepath):
        """Toggle the 'countdown' tag colors directly so TTK is forced to repaint."""
        if filepath not in self._delete_timers:
            return
        info = self._delete_timers[filepath]
        info['blink_state'] = not info['blink_state']
        if info['blink_state']:
            self._lib_tree.tag_configure("countdown",
                background="#d97706", foreground="#1c1917")
        else:
            self._lib_tree.tag_configure("countdown",
                background="#744210", foreground="#fef3c7")
        blink_id = self.after(400, lambda fp=filepath: self._lib_blink_delete(fp))
        info['blink_id'] = blink_id

    def _lib_tick_delete(self, filepath):
        if filepath not in self._delete_timers:
            return
        info = self._delete_timers[filepath]
        info['count'] -= 1
        if info['count'] <= 0:
            if info.get('blink_id'):
                self.after_cancel(info['blink_id'])
            del self._delete_timers[filepath]
            # Restore tag colors when no more countdowns are active
            if not self._delete_timers:
                self._lib_tree.tag_configure("countdown",
                    background="#744210", foreground="#fef3c7")
            try:
                os.remove(filepath)
            except Exception:
                pass
            self.update_file_list()
            return
        self._lib_apply_countdown_row(filepath, info['count'])
        after_id = self.after(1000, lambda fp=filepath: self._lib_tick_delete(fp))
        info['after_id'] = after_id

    def _lib_cancel_delete(self, filepath, iid):
        if filepath not in self._delete_timers:
            return
        info = self._delete_timers[filepath]
        if info.get('after_id'):
            self.after_cancel(info['after_id'])
        if info.get('blink_id'):
            self.after_cancel(info['blink_id'])
        orig_vals = info.get('orig_vals')
        orig_tags = info.get('orig_tags', ())
        del self._delete_timers[filepath]
        # Restore tag colors when no more countdowns are active
        if not self._delete_timers:
            self._lib_tree.tag_configure("countdown",
                background="#744210", foreground="#fef3c7")
        if orig_vals is not None:
            try:
                self._lib_tree.item(iid, values=orig_vals, tags=orig_tags)
            except Exception:
                pass

    def toggle_autostart(self):
        is_enabled = self.autostart_var.get()
        current_reg_status = self.check_autostart_status()
        if is_enabled != current_reg_status:
            self.set_autostart(is_enabled)
        self.save_settings()

    def set_autostart(self, enabled):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "JBAudioRecorder"
        app_path = f'"{os.path.abspath(sys.argv[0])}"' 

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            if enabled:
                try:
                    existing_val, _ = winreg.QueryValueEx(key, app_name)
                    if existing_val == app_path:
                        winreg.CloseKey(key)
                        return 
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print(f"Ошибка реестра: {e}")

    def check_autostart_status(self):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "JBAudioRecorder")
            winreg.CloseKey(key)
            return True
        except:
            return False

    def show_window(self):
        self.after(0, self.deiconify)
        self.after(0, self.focus_force)

    def on_closing(self):
        if self.tray_enabled_var.get():
            self.withdraw()
        else:
            self.confirm_exit_dialog()

    def quit_app(self):
        if self.is_recording or self.current_rec_array is not None:
            self.show_window()
            self.after(100, self._show_unsaved_exit_dialog)
        else:
            self._perform_exit()
    
    def _show_unsaved_exit_dialog(self):
        txt = LANGUAGES[self.lang_var.get()]
        dialog = ctk.CTkToplevel(self)
        dialog.title(txt.get("exit_title", "Exit"))
        dialog.geometry("400x180")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLOR_BG)
        dialog.transient(self)
        dialog.grab_set()
        self._center_over_self(dialog, 400, 180)
        
        warning_text = txt.get("exit_unsaved_warning", "Recording in progress and/or audio is not saved.\nAre you sure you want to exit?")
        
        lbl = ctk.CTkLabel(
            dialog,
            text=warning_text,
            font=("Roboto", 14),
            text_color="#ffffff",
            wraplength=350
        )
        lbl.pack(pady=(30, 20))
        
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=20, padx=20)
        
        btn_cancel = ctk.CTkButton(
            btn_frame,
            text=txt.get("cancel_btn", "Cancel"),
            width=120,
            fg_color="gray",
            command=dialog.destroy
        )
        btn_cancel.pack(side="right", padx=5)
        
        btn_exit = ctk.CTkButton(
            btn_frame,
            text=txt.get("exit_btn", "Exit"),
            width=120,
            fg_color=COLOR_RED,
            hover_color="#c0392b",
            command=lambda: [dialog.destroy(), self._perform_exit()]
        )
        btn_exit.pack(side="left", padx=5)
    
    def _perform_exit(self):
        """Выполняет фактический выход из программы с очисткой"""
        self.is_recording = False
        self._cleanup_temp_file()
        if self.tray_icon:
            self.tray_icon.stop()
        self.quit()
        self.destroy()
    
    def _on_noise_mode_changed(self, display_value):
        """Сохраняет выбранный режим шумоподавления в noise_mode_var."""
        key = getattr(self, "_noise_display_to_key", {}).get(display_value, "off")
        self.noise_mode_var.set(key)
        self.save_settings()

    def _on_notifications_changed(self, display_value):
        key = getattr(self, "_notif_display_to_key", {}).get(display_value, "tray")
        self.notifications_var.set(key)
        self.save_settings()

    def _center_over_self(self, dlg: ctk.CTkToplevel, dw: int, dh: int) -> None:
        """Позиционирует диалог по центру главного окна."""
        self.update_idletasks()
        dlg.update_idletasks()
        wx = self.winfo_x()
        wy = self.winfo_y()
        ww = self.winfo_width()
        wh = self.winfo_height()
        x = wx + (ww - dw) // 2
        y = wy + (wh - dh) // 2
        dlg.geometry(f"{dw}x{dh}+{x}+{y}")

    def _is_in_tray(self):
        """True если окно скрыто в трей (withdraw)."""
        try:
            return self.state() == "withdrawn"
        except Exception:
            return False

    def _send_notification(self, title: str, message: str = "") -> None:
        """Отправляет уведомление согласно настройке notifications_var."""
        mode = self.notifications_var.get()  # "off" / "tray" / "always"
        if mode == "off":
            return
        if mode == "tray" and not self._is_in_tray():
            return
        self.after(0, lambda t=title, m=message: self._show_toast(t, m))

    def _show_toast(self, title: str, message: str = "") -> None:
        """Показывает тематический toast-оверлей в правом нижнем углу."""
        import tkinter as tk

        # Закрываем предыдущий тост если ещё виден
        prev = getattr(self, "_toast_win", None)
        if prev is not None:
            try:
                prev.destroy()
            except Exception:
                pass
        self._toast_win = None

        toast = tk.Toplevel()
        toast.overrideredirect(True)
        toast.wm_attributes("-topmost", True)
        if sys.platform == "win32":
            toast.wm_attributes("-alpha", 0.0)

        W, H = 300, 72 if not message else 88
        sw = toast.winfo_screenwidth()
        sh = toast.winfo_screenheight()
        toast.geometry(f"{W}x{H}+{sw - W - 16}+{sh - H - 52}")
        toast.configure(bg="#3e4a5d")          # рамка-бордер

        inner = tk.Frame(toast, bg="#1e2533", bd=0)
        inner.place(x=1, y=1, width=W - 2, height=H - 2)

        tk.Label(inner, text="JB Audio Recorder",
                 font=("Segoe UI", 9, "bold"), fg="#63b3ed", bg="#1e2533"
                 ).pack(anchor="w", padx=12, pady=(9, 0))
        tk.Label(inner, text=title,
                 font=("Segoe UI", 11, "bold"), fg="#e2e8f0", bg="#1e2533"
                 ).pack(anchor="w", padx=12, pady=(2, 0))
        if message:
            tk.Label(inner, text=message,
                     font=("Segoe UI", 10), fg="#718096", bg="#1e2533",
                     wraplength=270, justify="left"
                     ).pack(anchor="w", padx=12, pady=(1, 0))

        self._toast_win = toast

        def _close():
            try:
                toast.destroy()
            except Exception:
                pass
            if getattr(self, "_toast_win", None) is toast:
                self._toast_win = None

        def _on_click(e=None):
            _close()
            self.show_window()
            self.show_page("rec")

        toast.configure(cursor="hand2")
        inner.configure(cursor="hand2")
        for w in inner.winfo_children():
            w.configure(cursor="hand2")
        toast.bind("<Button-1>", _on_click)
        inner.bind("<Button-1>", _on_click)
        for w in inner.winfo_children():
            w.bind("<Button-1>", _on_click)

        if sys.platform == "win32":
            def _fade_in(a=0.0):
                if not toast.winfo_exists():
                    return
                a = min(a + 0.12, 0.95)
                toast.wm_attributes("-alpha", a)
                if a < 0.95:
                    toast.after(18, lambda: _fade_in(a))

            def _fade_out(a=0.95):
                if not toast.winfo_exists():
                    return
                a = max(a - 0.12, 0.0)
                toast.wm_attributes("-alpha", a)
                if a > 0.0:
                    toast.after(25, lambda: _fade_out(a))
                else:
                    _close()

            _fade_in()
            toast.after(3800, _fade_out)
        else:
            toast.after(4000, _close)

    def _deferred_startup(self):
        """Задачи, которые не нужны до показа окна: трей, диск, очистка."""
        if self.tray_enabled_var.get():
            self.start_tray()
        self.check_disk_space_on_startup()
        self.after(500, self._cleanup_old_temp_files)
        if self.auto_update_check_var.get():
            t = threading.Thread(target=self._check_for_updates, daemon=True)
            t.start()

    def _cleanup_old_temp_files(self):
        """Удаляет jb_recorder_temp_* старше 3 дней. Запускается макс раз в день,
        дата последней проверки хранится в конфиге. Работает тихо — без UI.
        """
        import datetime, tempfile, glob
        today = datetime.date.today().isoformat()  # "YYYY-MM-DD"
        if self._last_temp_cleanup == today:
            return  # уже делали сегодня
        try:
            temp_dir = tempfile.gettempdir()
            pattern = os.path.join(temp_dir, "jb_recorder_temp_*.wav")
            cutoff = datetime.datetime.now() - datetime.timedelta(days=3)
            removed = 0
            for fpath in glob.glob(pattern):
                try:
                    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                    if mtime < cutoff:
                        os.remove(fpath)
                        removed += 1
                except Exception:
                    pass
            self._last_temp_cleanup = today
            self.save_settings()
            if removed:
                print(f"🧹 Удалено старых temp-файлов: {removed}")
        except Exception as e:
            print(f"temp cleanup error: {e}")

    # ------------------------------------------------------------------
    # СИСТЕМА ОБНОВЛЕНИЙ
    # ------------------------------------------------------------------

    def _check_for_updates(self):
        """Фоновый поток: проверяет наличие новой версии на GitHub."""
        import urllib.request
        import json as _json

        self._update_status = "checking"
        self.after(0, self._refresh_update_label)
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "JB-Audio-Recorder"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())

            tag = data.get("tag_name", "").lstrip("v").strip()
            if not tag:
                self._update_status = "error"
                self.after(0, self._refresh_update_label)
                return

            # Выбираем URL скачивания: portable .zip > любой .zip > любой .exe
            assets = data.get("assets", [])
            dl_url = None
            for a in assets:
                if a.get("name", "").lower().endswith(".zip") and "portable" in a.get("name", "").lower():
                    dl_url = a.get("browser_download_url")
                    break
            if dl_url is None:
                for a in assets:
                    if a.get("name", "").lower().endswith(".zip"):
                        dl_url = a.get("browser_download_url")
                        break
            if dl_url is None:
                for a in assets:
                    if a.get("name", "").lower().endswith(".exe"):
                        dl_url = a.get("browser_download_url")
                        break
            if dl_url is None:
                dl_url = data.get("html_url", "")

            self._update_latest_version = tag
            self._update_url = dl_url

            if _version_newer(tag, APP_VERSION):
                self._update_status = "available"
                self.after(0, self._refresh_update_label)
                self.after(0, self._show_update_popup)
            else:
                self._update_status = "uptodate"
                self.after(0, self._refresh_update_label)
        except Exception as e:
            print(f"update check error: {e}")
            self._update_status = "error"
            self.after(0, self._refresh_update_label)

    def _refresh_update_label(self):
        """Обновляет лейбл статуса обновлений на странице 'О программе'."""
        lbl = self._update_status_label
        if lbl is None:
            return
        try:
            lbl.winfo_exists()
        except Exception:
            return

        txt = LANGUAGES[self.lang_var.get()]
        status = self._update_status

        if status == "checking":
            lbl.configure(
                text=txt.get("update_checking", "Checking for updates..."),
                text_color=COLOR_TEXT_DIM,
                cursor="",
            )
            lbl.unbind("<Button-1>")

        elif status == "uptodate":
            lbl.configure(
                text=txt.get("update_uptodate", "You are using the latest version"),
                text_color="#48bb78",   # зелёный
                cursor="",
            )
            lbl.unbind("<Button-1>")

        elif status == "available":
            ver = self._update_latest_version or ""
            lbl.configure(
                text=txt.get("update_available", "Update available: {}").format(ver),
                text_color="#ed8936",   # оранжевый
                cursor="hand2",
            )
            lbl.bind("<Button-1>", lambda e: self._show_update_popup())

        elif status == "error":
            lbl.configure(
                text=txt.get("update_error", "Failed to check for updates"),
                text_color="#fc8181",   # красный
                cursor="hand2",
            )
            lbl.bind("<Button-1>", lambda e: threading.Thread(
                target=self._check_for_updates, daemon=True).start())

        else:  # "idle" — обновления отключены, проверка не запускалась
            lbl.configure(
                text=txt.get("update_check_now", "Check for updates"),
                text_color=COLOR_ACCENT,
                cursor="hand2",
            )
            lbl.bind("<Button-1>", lambda e: threading.Thread(
                target=self._check_for_updates, daemon=True).start())

    def _show_update_popup(self):
        """Показывает popup с предложением обновиться."""
        txt = LANGUAGES[self.lang_var.get()]
        ver = self._update_latest_version or ""

        popup = ctk.CTkToplevel(self)
        popup.title(txt.get("update_popup_title", "Update available"))
        popup.geometry("400x180")
        popup.resizable(False, False)
        popup.configure(fg_color=COLOR_BG)
        popup.transient(self)
        popup.grab_set()
        popup.update_idletasks()
        x = (popup.winfo_screenwidth() // 2) - 200
        y = (popup.winfo_screenheight() // 2) - 90
        popup.geometry(f"400x180+{x}+{y}")

        ctk.CTkLabel(
            popup,
            text=txt.get("update_popup_title", "Update available"),
            font=("Roboto", 16, "bold"),
            text_color=COLOR_ACCENT,
        ).pack(pady=(20, 6))

        ctk.CTkLabel(
            popup,
            text=txt.get("update_popup_body", "Version {} is ready to install").format(ver),
            font=("Roboto", 12),
            text_color=COLOR_TEXT_DIM,
        ).pack()

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(pady=20)

        def do_install():
            popup.destroy()
            self._download_and_replace()

        ctk.CTkButton(
            btn_row,
            text=txt.get("update_install_btn", "Install"),
            fg_color=COLOR_ACCENT,
            command=do_install,
            width=120,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row,
            text=txt.get("update_cancel_btn", "Cancel"),
            fg_color=COLOR_BLOCK,
            command=popup.destroy,
            width=120,
        ).pack(side="left", padx=8)

    def _download_and_replace(self):
        """Скачивает и устанавливает обновление."""
        import urllib.request
        import zipfile
        import tempfile
        import webbrowser

        if not getattr(sys, "frozen", False):
            # В режиме .py скрипта — открываем страницу релиза в браузере
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return

        dl_url = self._update_url
        if not dl_url:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return

        txt = LANGUAGES[self.lang_var.get()]

        # Модальное окно с прогресс-баром
        prog_win = ctk.CTkToplevel(self)
        prog_win.title(txt.get("update_popup_title", "Update available"))
        prog_win.geometry("400x120")
        prog_win.resizable(False, False)
        prog_win.configure(fg_color=COLOR_BG)
        prog_win.transient(self)
        prog_win.grab_set()
        prog_win.protocol("WM_DELETE_WINDOW", lambda: None)   # блокируем закрытие
        prog_win.update_idletasks()
        x = (prog_win.winfo_screenwidth() // 2) - 200
        y = (prog_win.winfo_screenheight() // 2) - 60
        prog_win.geometry(f"400x120+{x}+{y}")

        lbl_prog = ctk.CTkLabel(
            prog_win,
            text=txt.get("update_checking", "Checking for updates..."),
            font=("Roboto", 12),
            text_color=COLOR_TEXT_DIM,
        )
        lbl_prog.pack(pady=(18, 8))

        progress = ctk.CTkProgressBar(prog_win, width=340)
        progress.set(0)
        progress.pack()

        def run_download():
            try:
                tmp_dir = tempfile.mkdtemp()
                is_zip = dl_url.lower().endswith(".zip")
                ext = ".zip" if is_zip else ".exe"
                dl_path = os.path.join(tmp_dir, "update" + ext)

                def reporthook(count, block, total):
                    if total > 0:
                        frac = min(count * block / total, 1.0) * 0.8
                        self.after(0, lambda f=frac: progress.set(f))

                urllib.request.urlretrieve(dl_url, dl_path, reporthook)
                self.after(0, lambda: progress.set(0.85))

                app_exe = sys.executable
                app_dir = os.path.dirname(app_exe)

                if is_zip:
                    ext_dir = os.path.join(tmp_dir, "extracted")
                    os.makedirs(ext_dir, exist_ok=True)
                    with zipfile.ZipFile(dl_path, "r") as zf:
                        zf.extractall(ext_dir)
                    self.after(0, lambda: progress.set(0.95))

                    bat = (
                        "@echo off\n"
                        "timeout /t 2 /nobreak >nul\n"
                        f'xcopy /e /i /y "{ext_dir}\\*" "{app_dir}\\"\n'
                        f'start "" "{app_exe}"\n'
                        f'rmdir /s /q "{ext_dir}"\n'
                        'del "%~f0"\n'
                    )
                else:
                    bat = (
                        "@echo off\n"
                        "timeout /t 2 /nobreak >nul\n"
                        f'copy /y "{dl_path}" "{app_exe}"\n'
                        f'start "" "{app_exe}"\n'
                        'del "%~f0"\n'
                    )

                bat_path = os.path.join(tmp_dir, "update.bat")
                with open(bat_path, "w", encoding="cp1251") as f:
                    f.write(bat)

                self.after(0, lambda: progress.set(1.0))
                subprocess.Popen(
                    ["cmd", "/c", bat_path],
                    creationflags=0x08000000,   # CREATE_NO_WINDOW
                )
                self.after(1200, self._perform_exit)

            except Exception as ex:
                print(f"update download error: {ex}")
                self.after(0, prog_win.destroy)
                import webbrowser as _wb
                self.after(0, lambda: _wb.open(f"https://github.com/{GITHUB_REPO}/releases/latest"))

        threading.Thread(target=run_download, daemon=True).start()

    def _cleanup_temp_file(self):
        """Удаляет временный файл записи если он существует"""
        if self.temp_rec_file and os.path.exists(self.temp_rec_file):
            try:
                os.remove(self.temp_rec_file)
                print(f"Временный файл удален: {self.temp_rec_file}")
            except Exception as e:
                print(f"Ошибка удаления временного файла: {e}")

    def __init__(self):
        super().__init__()
        self.hotkeys_enabled = ctk.BooleanVar(value=True)
        
        self.title("JB Audio Recorder")
        self.geometry("600x820")
        # Иконка окна (заголовок + панель задач)
        try:
            icon_path = os.path.join(BASE_DIR, "icon.ico")
            self.iconbitmap(icon_path)
        except Exception as e:
            print(f"⚠️ Иконка окна не загружена: {e}")
        self.configure(fg_color=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.lang_var = ctk.StringVar(value="English")
        # Хранит выбор пользователя ("System" или конкретный язык) — для dropdown и сохранения
        self.lang_display_var = ctk.StringVar(value="System")
        self.tray_enabled_var = ctk.BooleanVar(value=True)
        self.skip_exit_confirm = ctk.BooleanVar(value=False)
        self.noise_mode_var = ctk.StringVar(value="off")  # off / light / medium / aggressive
        self._last_temp_cleanup = ""  # дата последней очистки temp (YYYY-MM-DD)
        self.autostart_var = ctk.BooleanVar(value=False)
        self.check_disk_space_var = ctk.BooleanVar(value=True)
        self.auto_save_var = ctk.BooleanVar(value=False)
        self.always_on_top_var = ctk.BooleanVar(value=False)
        self.auto_update_check_var = ctk.BooleanVar(value=True)
        # "idle" / "checking" / "uptodate" / "available" / "error"
        self._update_status = "idle"
        self._update_url: str | None = None
        self._update_latest_version: str | None = None
        self._update_status_label: ctk.CTkLabel | None = None
        # "off" / "tray" / "always"
        self.notifications_var = ctk.StringVar(value="tray")

        def_path = os.path.join(os.path.expanduser("~"), "Music", "JB Audio Recorder")
        if not os.path.exists(def_path): 
            os.makedirs(def_path)
        self.save_path = ctk.StringVar(value=def_path)

        # ИСПРАВЛЕНИЕ: load_settings() перенесен ПОСЛЕ создания всех переменных
        # чтобы device_var был уже создан
        # self.load_settings() - перенесено ниже

        self.main_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)

        self.page_rec = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.page_settings = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.page_about = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        
        self.pulse_timer_id = None
        self.delete_timer_id = None
        self.is_stopping = False
        self.is_fading_out = False
        self.is_recording = False
        self.is_paused = False
        self._paused_at = None   # время начала текущей паузы (для корректного таймера)
        self.audio_data = []
        self.current_rec_array = None
        self.active_play_array = None
        self.is_playing = False
        self.is_looping = False
        self.current_frame = 0.0
        
        self._disk_space_checked = False  # проверка выполняется только один раз за сессию

        # === АСИНХРОННОЕ СОХРАНЕНИЕ ===
        self.is_saving = False
        self.save_thread = None
        self.save_progress_window = None
        self.save_progress_bar = None
        self.save_progress_label = None
        
        # === НОВАЯ СИСТЕМА ЗАПИСИ ВО ВРЕМЕННЫЙ ФАЙЛ ===
        self.temp_rec_file = None
        self.temp_rec_samplerate = None
        self.audio_queue = None
        self.writer_thread = None
        self.writer_stop_event = None
        
        self.playback_volume = 0.5
        self.volume_mult = 1.0
        self._lib_tree = None
        self._lib_tree_items = {}   # iid → filepath (None = unsaved recording)
        self._delete_timers = {}
        default_lang = self.lang_var.get()
        if default_lang not in LANGUAGES:
            default_lang = "English"
        self.current_file_name = LANGUAGES[default_lang].get("no_file", "No file")
        
        self.format_var = ctk.StringVar(value="mp3")
        self.sample_rate_var = ctk.StringVar(value="44100 Hz")
        self.bitrate_var = ctk.StringVar(value="320 kb/s")
        self.device_var = ctk.StringVar()
        self.channels_var = ctk.StringVar(value="Stereo")
        
        # ИСПРАВЛЕНИЕ: Загружаем настройки ПОСЛЕ создания всех переменных
        self.load_settings()
        self.apply_always_on_top()
        
        self.tray_icon = None
        
        self.setup_ui()
        self.update_idletasks()
        self.save_settings()

        self.show_page("rec")

        # Всё что не нужно до показа окна — откладываем,
        # чтобы окно появилось как можно быстрее
        self.after(0, self.update_file_list)
        self.after(50, self.setup_hotkeys)
        self.after(100, self._deferred_startup)
    
    def _normalize_bitrate(self, bitrate_str: str) -> str:
        num_match = re.search(r'(\d+\.?\d*)', bitrate_str)
        if not num_match:
            return "320k"
        num = num_match.group(1)
        return f"{num}k"

    def set_player_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        
        if hasattr(self, 'btn_play'):
            self.btn_play.configure(state=state)
        if hasattr(self, 'btn_stop_play'):
            self.btn_stop_play.configure(state=state)
        if hasattr(self, 'btn_loop'):
            self.btn_loop.configure(state=state)
        if hasattr(self, 'seek_slider'):
            self.seek_slider.configure(state=state)

    def on_format_change(self, new_format):
        if new_format.lower() in ["wav", "flac"]:
            self.lbl_param_bitrate.configure(text_color=COLOR_TEXT_DIM)
            self.bitrate_menu.configure(state="disabled")
        else:
            self.lbl_param_bitrate.configure(text_color="#e2e8f0")
            self.bitrate_menu.configure(state="normal")
        
        format_lower = new_format.lower()
        current_sample_rate = int(self.sample_rate_var.get().split()[0])
        
        if format_lower == "ogg":
            available_rates = [8000, 11025, 16000, 22050, 32000, 44100, 48000]
        elif format_lower == "opus":
            available_rates = [8000, 12000, 16000, 24000, 48000]
        else:
            available_rates = [8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000]
        
        rate_strings = [f"{rate} Hz" for rate in available_rates]
        self.sample_rate_menu.configure(values=rate_strings)
        
        if current_sample_rate not in available_rates:
            nearest = min(available_rates, key=lambda x: abs(x - current_sample_rate))
            self.sample_rate_var.set(f"{nearest} Hz")
            
            txt = LANGUAGES[self.lang_var.get()]
            format_name = FORMAT_CONSTRAINTS.get(format_lower, {}).get("name", new_format.upper())
            self.lbl_status.configure(
                text=f"⚠️ {format_name}: {current_sample_rate} Hz → {nearest} Hz",
                text_color=COLOR_YELLOW
            )
            self.after(3000, self.update_ready_status)

    def _load_temp_rec(self):
        self.finish_playback()
        self.active_play_array = self.current_rec_array
        txt = LANGUAGES[self.lang_var.get()]
        self.current_file_name = txt["temp_rec_name"]
        self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {self.current_file_name}")
        self.set_player_enabled(True)

    def _load_saved_file(self, file_path, file_name):
        txt = LANGUAGES[self.lang_var.get()]
        if self.current_rec_array is not None:
            self.lbl_status.configure(
                text=txt.get("warn_save", "Сохраните или удалите текущую запись!"), 
                text_color=COLOR_RED
            )
            self.after(3000, lambda: self.lbl_status.configure(text="") if not self.is_recording else None)
            return
        
        self.finish_playback()
        
        try:
            try:
                data, samplerate = sf.read(file_path, dtype='float32')
                if len(data.shape) == 1:
                    data = data.reshape((-1, 1))
                data = np.clip(data, -1.0, 1.0)
                self.active_play_array = data
                self.temp_rec_samplerate = samplerate  # ← СОХРАНЯЕМ ЧАСТОТУ!
                self.current_file_name = file_name
                txt = LANGUAGES[self.lang_var.get()]
                self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {file_name}")
                self.set_player_enabled(True)
                print(f"📂 Загружен файл: {file_name} @ {samplerate} Hz")
                return
            except Exception:
                pass
            
            from pydub import audio_segment
            original_mediainfo_func = audio_segment.mediainfo_json
            
            def temp_mediainfo(filepath, read_ahead_limit=-1):
                try:
                    return original_mediainfo_func(filepath, read_ahead_limit)
                except:
                    return {
                        "streams": [{
                            "codec_type": "audio",
                            "codec_name": "unknown",
                            "sample_rate": "44100",
                            "channels": 2,
                            "bits_per_sample": 16,
                            "sample_fmt": "s16"
                        }],
                        "format": {
                            "duration": "0",
                            "bit_rate": "320000"
                        }
                    }
            audio_segment.mediainfo_json = temp_mediainfo
            
            try:
                ext = os.path.splitext(file_path)[1].lower().replace('.', '')
                audio = AudioSegment.from_file(file_path, format=ext)
                raw_samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
                
                if audio.sample_width == 1:
                    raw_samples -= 128
                    raw_samples /= 128.0
                elif audio.sample_width == 2:
                    raw_samples /= 32768.0
                elif audio.sample_width == 4:
                    max_val = np.max(np.abs(raw_samples))
                    if max_val > 1.0:
                        raw_samples /= 2147483648.0
                else:
                    max_val = np.max(np.abs(raw_samples)) or 1.0
                    raw_samples /= max_val
                
                raw_samples = np.clip(raw_samples, -1.0, 1.0)
                if audio.channels == 1:
                    data = raw_samples.reshape((-1, 1))
                else:
                    data = raw_samples.reshape((-1, audio.channels))
                
                self.active_play_array = data
                self.current_file_name = file_name
                txt = LANGUAGES[self.lang_var.get()]
                self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {file_name}")
                self.set_player_enabled(True)
            finally:
                audio_segment.mediainfo_json = original_mediainfo_func
            
        except Exception as e:
            txt = LANGUAGES[self.lang_var.get()]
            print(f"Ошибка загрузки файла {file_name}: {e}")
            import traceback
            traceback.print_exc()
            self.lbl_now_playing.configure(text=f"{txt.get('now_playing', 'Playing')}: {txt.get('no_file', 'Error loading file')}")

    def setup_ui(self):
        txt = LANGUAGES[self.lang_var.get()]
        # --- НАВИГАЦИОННАЯ ПАНЕЛЬ ---
        self.nav_frame = ctk.CTkFrame(self, fg_color=COLOR_BLOCK, height=60, corner_radius=0)
        self.nav_frame.pack(fill="x", side="top")

        self.button_container = ctk.CTkFrame(self.nav_frame, fg_color="transparent")
        self.button_container.place(relx=0.5, rely=0.5, anchor="center")

        nav_font = ("Roboto", 13, "bold")

        self.btn_nav_rec = ctk.CTkButton(self.button_container, text=txt["nav_rec"], font=nav_font, 
                                        width=120, command=lambda: self.show_page("rec"))
        self.btn_nav_rec.pack(side="left", padx=5)

        self.btn_nav_set = ctk.CTkButton(self.button_container, text=txt["nav_set"], font=nav_font, 
                                        width=120, command=lambda: self.show_page("settings"))
        self.btn_nav_set.pack(side="left", padx=5)

        self.btn_nav_about = ctk.CTkButton(self.button_container, text=txt["nav_about"], font=nav_font, 
                                          width=120, command=lambda: self.show_page("about"))
        self.btn_nav_about.pack(side="left", padx=5)

        self.main_scroll.pack(side="bottom", fill="both", expand=True)

        # ---------------------------------------------------------
        # ВКЛАДКА 1: ДИКТОФОН
        # ---------------------------------------------------------
        ctk.CTkLabel(self.page_rec, text="JB AUDIO RECORDER", font=("Roboto", 22, "bold"), text_color=COLOR_ACCENT).pack(pady=10)

        # --- 1. БЛОК ЗАПИСИ (всегда сверху, на всю ширину) ---
        self.rec_frame = ctk.CTkFrame(self.page_rec, fg_color=COLOR_BLOCK, corner_radius=20)
        self.rec_frame.pack(padx=20, pady=5, fill="x")

        self.lbl_timer = ctk.CTkLabel(self.rec_frame, text="00:00:00", font=("Roboto", 50, "bold"))
        self.lbl_timer.pack(pady=(10, 0))
        self.lbl_status = ctk.CTkLabel(self.rec_frame, text=txt["status_ready"], font=("Roboto", 12, "bold"), text_color=COLOR_GREEN, height=1)
        self.lbl_status.pack(pady=0)
        self.vol_bar = ctk.CTkProgressBar(self.rec_frame, height=8, progress_color=COLOR_GREEN, fg_color=COLOR_BG)
        self.vol_bar.pack(fill="x", padx=40, pady=(5, 10)); self.vol_bar.set(0)

        self.lbl_vol_input = ctk.CTkLabel(self.rec_frame, text=f"{txt['input_volume']}: 100%", font=("Roboto", 10, "bold"), text_color=COLOR_TEXT_DIM)
        self.lbl_vol_input.pack()
        self.vol_slider = ctk.CTkSlider(self.rec_frame, from_=0.0, to=2.0, height=16, command=self._update_input_vol_ui)
        self.vol_slider.pack(fill="x", padx=60, pady=(0, 10)); self.vol_slider.set(1.0)

        btn_grid = ctk.CTkFrame(self.rec_frame, fg_color="transparent"); btn_grid.pack(pady=(0, 10))
        self.btn_rec = ctk.CTkButton(btn_grid, text=txt["btn_record"], fg_color=COLOR_RED, width=140, font=("Roboto", 12, "bold"), command=self.toggle_record)
        self.btn_rec.grid(row=0, column=0, padx=5)
        self.btn_pause = ctk.CTkButton(btn_grid, text=txt["btn_pause"], state="disabled", width=140, font=("Roboto", 12, "bold"), command=self.toggle_pause, fg_color="#4a5568", hover_color="#718096")
        self.btn_pause.grid(row=0, column=1, padx=5)

        # --- АДАПТИВНЫЙ КОНТЕЙНЕР: плеер+параметры слева, библиотека справа ---
        # Два дочерних фрейма (_left_col, _right_col) размещаются через grid.
        # При ширине >= 750px — 2 колонки, иначе — 1 колонка (стек).
        self._body_frame = ctk.CTkFrame(self.page_rec, fg_color="transparent")
        self._body_frame.pack(padx=20, pady=(5, 20), fill="x")
        self._two_col_active = False
        self._resize_after_id = None  # id debounce-таймера

        self._left_col  = ctk.CTkFrame(self._body_frame, fg_color="transparent")
        self._right_col = ctk.CTkFrame(self._body_frame, fg_color="transparent")

        # Начальный layout — узкий режим: колонки через pack
        self._body_frame.columnconfigure(0, weight=1)
        self._left_col.pack(fill="x")
        self._right_col.pack(fill="x")

        # --- 2. БЛОК АУДИОПЛЕЕРА (родитель: _left_col) ---
        self.play_frame = ctk.CTkFrame(self._left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        self.play_frame.pack(fill="x", pady=(0, 10))

        self.lbl_play_title = ctk.CTkLabel(self.play_frame, text=txt["player_title"], font=("Roboto", 10, "bold"), text_color=COLOR_TEXT_DIM)
        self.lbl_play_title.pack(pady=2)

        self.lbl_now_playing = ctk.CTkLabel(self.play_frame, text=f"{txt['now_playing']}: {txt['no_file']}", font=("Roboto", 11, "italic"), text_color=COLOR_ACCENT)
        self.lbl_now_playing.pack(pady=(2, 0))

        self.seek_slider = ctk.CTkSlider(self.play_frame, from_=0, to=1, height=16, progress_color=COLOR_ACCENT, command=self.manual_seek)
        self.seek_slider.pack(fill="x", padx=15, pady=5)
        self.seek_slider.set(0)

        ctrl_panel = ctk.CTkFrame(self.play_frame, fg_color="transparent")
        ctrl_panel.pack(fill="x", padx=15, pady=5)

        self.lbl_play_time = ctk.CTkLabel(ctrl_panel, text="00:00 / 00:00", font=("Consolas", 12), width=90)
        self.lbl_play_time.pack(side="left")

        vol_box = ctk.CTkFrame(ctrl_panel, fg_color="transparent")
        vol_box.pack(side="right", fill="y")

        self.lbl_play_vol = ctk.CTkLabel(vol_box, text=f"{txt['play_volume']}: {int(self.playback_volume * 100)}%", font=("Roboto", 10), text_color=COLOR_TEXT_DIM)
        self.lbl_play_vol.pack(side="top")

        self.play_vol_slider = ctk.CTkSlider(vol_box, from_=0.0, to=2.0, width=100, height=16, progress_color=COLOR_ACCENT, command=self._update_play_vol_ui)
        self.play_vol_slider.pack(side="bottom")
        self.play_vol_slider.set(self.playback_volume)

        btn_box = ctk.CTkFrame(ctrl_panel, fg_color="transparent")
        btn_box.pack(expand=True)

        self.btn_stop_play = ctk.CTkButton(btn_box, text="⏹", font=("Roboto", 18), width=50, state="disabled", fg_color="#4a5568", command=self.finish_playback)
        self.btn_stop_play.pack(side="left", padx=5)

        self.btn_play = ctk.CTkButton(btn_box, text="▶", font=("Roboto", 18), width=50, state="disabled", fg_color=COLOR_ACCENT, text_color=COLOR_BG, command=self.play_audio)
        self.btn_play.pack(side="left", padx=5)

        self.btn_loop = ctk.CTkButton(btn_box, text="🔄", font=("Roboto", 18), width=50, fg_color="#4a5568", hover_color="#718096", text_color=COLOR_TEXT_DIM, command=self.toggle_loop)
        self.btn_loop.pack(side="left", padx=5)

        save_grid = ctk.CTkFrame(self.play_frame, fg_color="transparent")
        save_grid.pack(pady=10)

        self.btn_save = ctk.CTkButton(save_grid, text=txt["btn_save"], state="disabled", fg_color=COLOR_GREEN, text_color=COLOR_BG, width=140, font=("Roboto", 12, "bold"), command=self.quick_save)
        self.btn_save.grid(row=0, column=0, padx=5)

        self.btn_del = ctk.CTkButton(save_grid, text=txt["btn_delete"], state="disabled", fg_color="#4a5568", hover_color="#718096", width=140, font=("Roboto", 12, "bold"), command=self.delete_rec)
        self.btn_del.grid(row=0, column=1, padx=5)

        # --- 3. БЛОК ПАРАМЕТРОВ ЗАПИСИ (родитель: _left_col) ---
        self.set_frame = ctk.CTkFrame(self._left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        self.set_frame.pack(fill="x", pady=(0, 10))

        self.lbl_set_title = ctk.CTkLabel(self.set_frame, text=txt["nav_settings"], font=("Roboto", 10, "bold"), text_color=COLOR_TEXT_DIM)
        self.lbl_set_title.pack(pady=(10, 5))

        grid_params = ctk.CTkFrame(self.set_frame, fg_color="transparent")
        grid_params.pack(padx=20, pady=(0, 15), fill="x")
        grid_params.columnconfigure(1, weight=1)

        def add_param_row(row, label_key, widget_type, var, values=None):
            lbl = ctk.CTkLabel(grid_params, text=txt[label_key], font=("Roboto", 12), text_color="#e2e8f0")
            lbl.grid(row=row, column=0, sticky="w", pady=5, padx=(0, 20))
            if widget_type == "menu":
                w = ctk.CTkOptionMenu(grid_params, values=values, variable=var, fg_color=COLOR_BG,
                                      button_color=COLOR_BG, button_hover_color=COLOR_HOVER, height=28)
            elif widget_type == "switch":
                w = ctk.CTkSwitch(grid_params, text="", variable=var, progress_color=COLOR_ACCENT, width=40)
            w.grid(row=row, column=1, sticky="e", pady=5)
            return lbl, w

        self.lbl_param_format, _ = add_param_row(0, "format", "menu", self.format_var, ["mp3", "wav", "flac", "ogg"])
        self.format_var.trace_add("write", lambda *args: self.on_format_change(self.format_var.get()))
        self.lbl_param_bitrate, self.bitrate_menu = add_param_row(1, "bitrate", "menu", self.bitrate_var, ["64 kb/s", "128 kb/s", "192 kb/s", "320 kb/s"])
        self.lbl_param_freq, self.sample_rate_menu = add_param_row(2, "frequency", "menu", self.sample_rate_var, ["8000 Hz", "11025 Hz", "16000 Hz", "22050 Hz", "32000 Hz", "44100 Hz", "48000 Hz"])
        self.lbl_param_mode, _ = add_param_row(3, "mode", "menu", self.channels_var, ["Stereo", "Mono"])
        # Шумоподавление: список режимов (значения локализованы при смене языка)
        _noise_label = ctk.CTkLabel(grid_params, text=txt["noise"], font=("Roboto", 12), text_color="#e2e8f0")
        _noise_label.grid(row=4, column=0, sticky="w", pady=5, padx=(0, 20))
        self.lbl_param_noise = _noise_label
        _noise_values = [txt.get("noise_off","Off"), txt.get("noise_light","Light"),
                         txt.get("noise_medium","Medium"), txt.get("noise_aggressive","Aggressive")]
        # Карта значений → внутренний ключ (независимо от языка)
        self._noise_display_to_key = {
            txt.get("noise_off","Off"): "off",
            txt.get("noise_light","Light"): "light",
            txt.get("noise_medium","Medium"): "medium",
            txt.get("noise_aggressive","Aggressive"): "aggressive",
        }
        self._noise_key_to_display = {v: k for k, v in self._noise_display_to_key.items()}
        _cur_display = self._noise_key_to_display.get(self.noise_mode_var.get(), _noise_values[0])
        self.noise_mode_menu = ctk.CTkOptionMenu(
            grid_params, values=_noise_values,
            fg_color=COLOR_BG, button_color=COLOR_BG, button_hover_color=COLOR_HOVER, height=28,
            command=self._on_noise_mode_changed
        )
        self.noise_mode_menu.set(_cur_display)
        self.noise_mode_menu.grid(row=4, column=1, sticky="e", pady=5)

        # --- 4. БЛОК БИБЛИОТЕКИ (родитель: _right_col) ---
        self.folder_frame = ctk.CTkFrame(self._right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        self.folder_frame.pack(fill="x", pady=(0, 10))

        self.lbl_lib_title = ctk.CTkLabel(self.folder_frame, text=txt["library_title"], font=("Roboto", 10, "bold"), text_color=COLOR_TEXT_DIM)
        self.lbl_lib_title.pack(pady=(10, 2))

        # ── Library: ttk.Treeview ────────────────────────────────────────────
        # Dark style for the table
        _style = ttk.Style()
        _style.theme_use("default")
        _style.configure("JBLib.Treeview",
            background="#1a2035",
            foreground="#e2e8f0",
            rowheight=26,
            fieldbackground="#1a2035",
            font=("Segoe UI", 10),
            borderwidth=0,
            relief="flat",
        )
        _style.configure("JBLib.Treeview.Heading",
            background="#252d3d",
            foreground="#718096",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            relief="flat",
            padding=[6, 4],
        )
        _style.map("JBLib.Treeview",
            background=[("selected", "#3e4a5d")],
            foreground=[("selected", "#e2e8f0")],
        )
        _style.map("JBLib.Treeview.Heading",
            background=[("active", "#2d3748")],
            relief=[("active", "flat")],
        )

        lib_container = tk.Frame(self.folder_frame, bg="#1a2035", bd=0)
        lib_container.pack(padx=15, pady=(4, 8), fill="both", expand=True)

        self._lib_tree = ttk.Treeview(
            lib_container,
            columns=("name", "format", "size", "date", "del"),
            show="headings",
            style="JBLib.Treeview",
            selectmode="browse",
        )

        # Column widths and headings
        self._lib_tree.heading("name",   text=txt["list_name"].capitalize(), anchor="w")
        self._lib_tree.heading("format", text=txt["format"],                 anchor="w")
        self._lib_tree.heading("size",   text=txt.get("list_size","Size").capitalize(), anchor="w")
        self._lib_tree.heading("date",   text=txt["list_date"].capitalize(), anchor="w")
        self._lib_tree.heading("del",    text="🗑️",                          anchor="center")

        # stretch=False на всех — ширину name считаем сами в _lib_on_tree_resize
        self._lib_tree.column("name",   stretch=False, minwidth=60,  width=160, anchor="w")
        self._lib_tree.column("format", stretch=False, minwidth=38,  width=48,  anchor="w")
        self._lib_tree.column("size",   stretch=False, minwidth=46,  width=62,  anchor="w")
        self._lib_tree.column("date",   stretch=False, minwidth=90,  width=115, anchor="w")
        self._lib_tree.column("del",    stretch=False, minwidth=44,  width=48,  anchor="center")

        # Tags for special rows
        self._lib_tree.tag_configure("current_rec",
            background="#3e4a5d", foreground="#e2e8f0")
        self._lib_tree.tag_configure("countdown",
            background="#744210", foreground="#fef3c7")
        self._lib_tree.tag_configure("hover",
            background="#2a3350", foreground="#e2e8f0")
        self._lib_tree.tag_configure("del_hover",
            background="#3d1f1f", foreground="#fc8181")

        # Scrollbar
        _vsb = tk.Scrollbar(lib_container, orient="vertical",
                            command=self._lib_tree.yview,
                            bg="#252d3d", troughcolor="#1a2035",
                            activebackground="#3e4a5d", width=12)
        self._lib_tree.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        self._lib_tree.pack(side="left", fill="both", expand=True)

        self._lib_tree.bind("<Button-1>",   self._lib_on_click)
        self._lib_tree.bind("<Motion>",     self._lib_on_motion)
        self._lib_tree.bind("<Leave>",      self._lib_on_leave)
        self._lib_tree.bind("<MouseWheel>", self._lib_on_scroll)
        self._lib_tree.bind("<Button-4>",   self._lib_on_scroll)
        self._lib_tree.bind("<Button-5>",   self._lib_on_scroll)
        # Пересчёт ширины колонки name при любом изменении размера Treeview
        self._lib_tree.bind("<Configure>",  self._lib_on_tree_resize)

        self.lbl_current_path = ctk.CTkLabel(self.folder_frame, text="", font=("Roboto", 9, "italic"), text_color=COLOR_ACCENT, wraplength=500)
        self.lbl_current_path.pack(pady=0)

        path_row = ctk.CTkFrame(self.folder_frame, fg_color="transparent")
        path_row.pack(pady=5)
        self.btn_open_dir = ctk.CTkButton(
            path_row,
            text=txt["btn_open_explorer"],
            width=150,
            height=28,
            font=("Roboto", 12, "bold"),
            command=self.open_directory,
            fg_color=COLOR_ACCENT,
            hover_color="#4a9fd8"
        )
        self.btn_open_dir.pack()

        # Подписываемся на изменение размера body_frame для адаптивного layout
        self._body_frame.bind("<Configure>", self._on_rec_page_resize)

        # ---------------------------------------------------------
        # ВКЛАДКА 2: ПАРАМЕТРЫ
        # ---------------------------------------------------------
        self.lbl_params_title = ctk.CTkLabel(self.page_settings, text=txt["tab_settings"], font=("Roboto", 22, "bold"), text_color=COLOR_ACCENT)
        self.lbl_params_title.pack(pady=20)

        # Адаптивный двухколоночный контейнер (threshold 750px, как на вкладке записи)
        self._settings_body_frame = ctk.CTkFrame(self.page_settings, fg_color="transparent")
        self._settings_body_frame.pack(fill="x")
        self._settings_two_col_active = False
        self._settings_resize_after_id = None

        self._settings_left_col = ctk.CTkFrame(self._settings_body_frame, fg_color="transparent")
        self._settings_right_col = ctk.CTkFrame(self._settings_body_frame, fg_color="transparent")
        self._settings_left_col.pack(fill="x")
        self._settings_right_col.pack(fill="x")

        # --- 1. Язык --- (левая колонка)
        lang_frame = ctk.CTkFrame(self._settings_left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        lang_frame.pack(padx=20, pady=5, fill="x")

        top_row_lang = ctk.CTkFrame(lang_frame, fg_color="transparent")
        top_row_lang.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_lang_setting = ctk.CTkLabel(top_row_lang, text=txt["language_label"], font=("Roboto", 14))
        self.lbl_lang_setting.pack(side="left")

        self.lang_menu = ctk.CTkOptionMenu(
            top_row_lang,
            values=["System"] + list(LANGUAGES.keys()),
            variable=self.lang_display_var,
            command=self.change_language,
            fg_color=COLOR_BG,
            button_color=COLOR_BG,
            width=140
        )
        self.lang_menu.pack(side="right")

        self.lbl_lang_help = ctk.CTkLabel(
            lang_frame,
            text=txt.get("lang_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_lang_help.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 2. Устройство записи --- (левая колонка)
        dev_frame = ctk.CTkFrame(self._settings_left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        dev_frame.pack(padx=20, pady=5, fill="x")

        top_row_dev = ctk.CTkFrame(dev_frame, fg_color="transparent")
        top_row_dev.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_param_dev = ctk.CTkLabel(top_row_dev, text=txt.get("device_setting_label", "Recording Device"), font=("Roboto", 14))
        self.lbl_param_dev.pack(side="left")

        devs = [f"{d['name']}" for d in sd.query_devices() if d['max_input_channels'] > 0]
        
        self.dev_menu = ctk.CTkOptionMenu(
            top_row_dev, values=devs, variable=self.device_var,
            command=lambda x: self.save_settings(), 
            fg_color=COLOR_BG, button_color=COLOR_BG, width=200 
        )
        self.dev_menu.pack(side="right")
        
        if devs:
            current_saved = self.device_var.get()
            if current_saved and current_saved in devs:
                pass 
            else:
                self.device_var.set(devs[0])

        self.lbl_dev_help = ctk.CTkLabel(
            dev_frame,
            text=txt.get("device_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_dev_help.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 3. Папка для сохранения --- (левая колонка)
        save_path_frame = ctk.CTkFrame(self._settings_left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        save_path_frame.pack(padx=20, pady=5, fill="x")

        top_row_path = ctk.CTkFrame(save_path_frame, fg_color="transparent")
        top_row_path.pack(fill="x", padx=20, pady=(15, 10))

        self.lbl_save_path = ctk.CTkLabel(top_row_path, text=txt["save_path_label"], font=("Roboto", 14))
        self.lbl_save_path.pack(side="left")

        self.btn_change_save_path = ctk.CTkButton(
            top_row_path,
            text=txt["btn_choose_folder"],
            width=140,
            height=32,
            font=("Roboto", 12, "bold"),
            command=self.change_directory,
            fg_color=COLOR_ACCENT,
            hover_color="#4a9fd8"
        )
        self.btn_change_save_path.pack(side="right")

        self.lbl_current_save_path = ctk.CTkLabel(
            save_path_frame,
            text=self.save_path.get(),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left",
            wraplength=520
        )
        self.lbl_current_save_path.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 4. Горячие клавиши --- (левая колонка)
        hk_frame = ctk.CTkFrame(self._settings_left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        hk_frame.pack(padx=20, pady=5, fill="x")

        top_row_hk = ctk.CTkFrame(hk_frame, fg_color="transparent")
        top_row_hk.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_hk_title = ctk.CTkLabel(top_row_hk, text=txt.get("hotkeys_label", "Hotkeys"), font=("Roboto", 14))
        self.lbl_hk_title.pack(side="left")

        sw_hk = ctk.CTkSwitch(
            top_row_hk, text="", width=0, variable=self.hotkeys_enabled,
            command=self.save_settings, progress_color=COLOR_ACCENT
        )
        sw_hk.pack(side="right")

        self.lbl_hk_help = ctk.CTkLabel(
            hk_frame,
            text=txt.get("hotkeys_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_hk_help.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 5. Работать в фоне --- (левая колонка)
        tray_frame = ctk.CTkFrame(self._settings_left_col, fg_color=COLOR_BLOCK, corner_radius=20)
        tray_frame.pack(padx=20, pady=5, fill="x")

        top_row_tray = ctk.CTkFrame(tray_frame, fg_color="transparent")
        top_row_tray.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_tray_setting = ctk.CTkLabel(top_row_tray, text=txt["work_background"], font=("Roboto", 14))
        self.lbl_tray_setting.pack(side="left")

        self.switch_tray = ctk.CTkSwitch(
            top_row_tray, text="", width=0, variable=self.tray_enabled_var,
            command=self.update_tray_visibility, progress_color=COLOR_ACCENT
        )
        self.switch_tray.pack(side="right")

        self.lbl_tray_help = ctk.CTkLabel(
            tray_frame,
            text=txt.get("work_background_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_tray_help.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 6. Автозапуск --- (правая колонка)
        autostart_frame = ctk.CTkFrame(self._settings_right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        autostart_frame.pack(padx=20, pady=5, fill="x")

        top_row_autostart = ctk.CTkFrame(autostart_frame, fg_color="transparent")
        top_row_autostart.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_autostart_setting = ctk.CTkLabel(top_row_autostart, text=txt.get("autostart", "Start with Windows"), font=("Roboto", 14))
        self.lbl_autostart_setting.pack(side="left")

        self.switch_auto = ctk.CTkSwitch(
            top_row_autostart, text="", width=0, variable=self.autostart_var,
            command=self.toggle_autostart, progress_color=COLOR_ACCENT
        )
        self.switch_auto.pack(side="right")

        self.lbl_autostart_help = ctk.CTkLabel(
            autostart_frame,
            text=txt.get("autostart_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_autostart_help.pack(padx=20, pady=(0, 15), anchor="w")

        # === Проверка памяти === (правая колонка)
        disk_check_frame = ctk.CTkFrame(self._settings_right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        disk_check_frame.pack(padx=20, pady=5, fill="x")

        top_row_disk = ctk.CTkFrame(disk_check_frame, fg_color="transparent")
        top_row_disk.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_disk_check_setting = ctk.CTkLabel(top_row_disk, text=txt.get("check_disk_space", "Check disk space on startup"), font=("Roboto", 14))
        self.lbl_disk_check_setting.pack(side="left")

        self.switch_disk_check = ctk.CTkSwitch(
            top_row_disk, text="", width=0, variable=self.check_disk_space_var,
            command=self.save_settings,
            progress_color=COLOR_ACCENT
        )
        self.switch_disk_check.pack(side="right")

        self.lbl_disk_check_help = ctk.CTkLabel(
            disk_check_frame,
            text=txt.get("check_disk_space_help", "Warning if less than 2GB free on system drive"),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_disk_check_help.pack(padx=20, pady=(0, 15), anchor="w")

        # === Проверка обновлений === (правая колонка)
        update_check_frame = ctk.CTkFrame(self._settings_right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        update_check_frame.pack(padx=20, pady=5, fill="x")

        top_row_update = ctk.CTkFrame(update_check_frame, fg_color="transparent")
        top_row_update.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_update_check_setting = ctk.CTkLabel(
            top_row_update,
            text=txt.get("auto_update_check", "Check for updates on startup"),
            font=("Roboto", 14)
        )
        self.lbl_update_check_setting.pack(side="left")

        self.switch_update_check = ctk.CTkSwitch(
            top_row_update, text="", width=0,
            variable=self.auto_update_check_var,
            command=self.save_settings,
            progress_color=COLOR_ACCENT
        )
        self.switch_update_check.pack(side="right")

        self.lbl_update_check_help = ctk.CTkLabel(
            update_check_frame,
            text=txt.get("auto_update_check_help", "Automatically check for new versions"),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_update_check_help.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 7. Автосохранение --- (правая колонка)
        auto_save_frame = ctk.CTkFrame(self._settings_right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        auto_save_frame.pack(padx=20, pady=5, fill="x")

        top_row_auto_save = ctk.CTkFrame(auto_save_frame, fg_color="transparent")
        top_row_auto_save.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_auto_save_setting = ctk.CTkLabel(top_row_auto_save, text=txt.get("auto_save", "Auto save"), font=("Roboto", 14))
        self.lbl_auto_save_setting.pack(side="left")

        self.switch_auto_save = ctk.CTkSwitch(
            top_row_auto_save, text="", width=0, variable=self.auto_save_var,
            command=self.save_settings, progress_color=COLOR_ACCENT
        )
        self.switch_auto_save.pack(side="right")

        self.lbl_auto_save_help = ctk.CTkLabel(
            auto_save_frame,
            text=txt.get("auto_save_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_auto_save_help.pack(padx=20, pady=(0, 15), anchor="w")

        # --- 8. Поверх всех окон --- (правая колонка)
        aot_frame = ctk.CTkFrame(self._settings_right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        aot_frame.pack(padx=20, pady=5, fill="x")

        top_row_aot = ctk.CTkFrame(aot_frame, fg_color="transparent")
        top_row_aot.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_aot_setting = ctk.CTkLabel(top_row_aot, text=txt.get("always_on_top", "Always on top"), font=("Roboto", 14))
        self.lbl_aot_setting.pack(side="left")

        self.switch_aot = ctk.CTkSwitch(
            top_row_aot, text="", width=0, variable=self.always_on_top_var,
            command=lambda: [self.apply_always_on_top(), self.save_settings()],
            progress_color=COLOR_ACCENT
        )
        self.switch_aot.pack(side="right")

        self.lbl_aot_help = ctk.CTkLabel(
            aot_frame,
            text=txt.get("always_on_top_help", ""),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_aot_help.pack(padx=20, pady=(0, 15), anchor="w")


        # --- 9. Уведомления --- (правая колонка)
        notif_frame = ctk.CTkFrame(self._settings_right_col, fg_color=COLOR_BLOCK, corner_radius=20)
        notif_frame.pack(padx=20, pady=5, fill="x")

        top_row_notif = ctk.CTkFrame(notif_frame, fg_color="transparent")
        top_row_notif.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_notif_setting = ctk.CTkLabel(
            top_row_notif, text=txt.get("notify_setting", "Recording Status Notifications"),
            font=("Roboto", 14)
        )
        self.lbl_notif_setting.pack(side="left")

        _notif_values = [
            txt.get("notify_off", "Disabled"),
            txt.get("notify_tray", "When in tray"),
            txt.get("notify_always", "Always"),
        ]
        self._notif_display_to_key = {
            txt.get("notify_off", "Disabled"): "off",
            txt.get("notify_tray", "When in tray"): "tray",
            txt.get("notify_always", "Always"): "always",
        }
        self._notif_key_to_display = {v: k for k, v in self._notif_display_to_key.items()}
        self.notif_menu = ctk.CTkOptionMenu(
            top_row_notif,
            values=_notif_values,
            fg_color=COLOR_BG, button_color=COLOR_BG, button_hover_color=COLOR_HOVER, height=28,
            command=self._on_notifications_changed
        )
        self.notif_menu.set(
            self._notif_key_to_display.get(self.notifications_var.get(),
                                           txt.get("notify_tray", "When in tray"))
        )
        self.notif_menu.pack(side="right")

        self.lbl_notif_help = ctk.CTkLabel(
            notif_frame,
            text=txt.get("notify_help", "Shows notification on recording start, pause and stop"),
            font=("Roboto", 11),
            text_color=COLOR_TEXT_DIM,
            justify="left"
        )
        self.lbl_notif_help.pack(padx=20, pady=(0, 15), anchor="w")

        # Привязка адаптивного layout настроек
        self._settings_body_frame.bind("<Configure>", self._on_settings_page_resize)

        # Кнопка сброса настроек — вне колонок, внизу по центру
        self.btn_reset = ctk.CTkButton(
            self.page_settings,
            text=txt.get("reset_settings", "Reset to Defaults"),
            width=300, height=40,
            font=("Roboto", 13, "bold"),
            fg_color=COLOR_YELLOW, hover_color="#d69e2e", text_color=COLOR_BG,
            command=self.reset_settings
        )
        self.btn_reset.pack(pady=20)

        # ---------------------------------------------------------
        # ВКЛАДКА 3: О ПРОГРАММЕ
        # ---------------------------------------------------------
        for widget in self.page_about.winfo_children():
            widget.destroy()

        txt = LANGUAGES[self.lang_var.get()]

        header_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        header_frame.pack(padx=20, pady=(20, 10), fill="x")

        ctk.CTkLabel(
            header_frame,
            text=txt["about_app_title"],
            font=("Roboto", 20, "bold"),
            text_color=COLOR_ACCENT
        ).pack(pady=(15, 5))

        ctk.CTkLabel(
            header_frame,
            text=txt["about_version"],
            font=("Roboto", 12),
            text_color=COLOR_TEXT_DIM
        ).pack(pady=(0, 4))

        self._update_status_label = ctk.CTkLabel(header_frame, text="", font=("Roboto", 11), cursor="")
        self._update_status_label.pack(pady=(0, 15))
        self._refresh_update_label()

        desc_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        desc_frame.pack(padx=20, pady=10, fill="x")
        ctk.CTkLabel(desc_frame, text=txt.get("nav_about", "О ПРОГРАММЕ"),
                     font=("Roboto", 11, "bold"), text_color=COLOR_TEXT_DIM).pack(pady=(14, 4))
        ctk.CTkLabel(
            desc_frame,
            text=txt["about_description"],
            font=("Roboto", 13),
            text_color="#e2e8f0",
            wraplength=480,
            justify="left"
        ).pack(padx=20, pady=(0, 15), anchor="w")

        links_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        links_frame.pack(padx=20, pady=10, fill="x")
        ctk.CTkLabel(links_frame, text=txt.get("links_title", "Links"),
                     font=("Roboto", 11, "bold"), text_color=COLOR_TEXT_DIM).pack(pady=(14, 8))
        ctk.CTkButton(links_frame, text="WEBSITE", font=("Roboto", 13, "bold"),
                      fg_color="#7c3aed", hover_color="#6d28d9", corner_radius=10, height=36,
                      command=lambda: __import__("webbrowser").open("https://jbsoftware.ru")
                      ).pack(padx=20, pady=(0, 6), fill="x")
        ctk.CTkButton(links_frame, text="GitHub", font=("Roboto", 13, "bold"),
                      fg_color="#1e293b", hover_color="#0f172a", corner_radius=10, height=36,
                      command=lambda: __import__("webbrowser").open("https://github.com/jeffbennington/JB-Audio-Recorder")
                      ).pack(padx=20, pady=(0, 6), fill="x")
        ctk.CTkButton(links_frame, text="Telegram", font=("Roboto", 13, "bold"),
                      fg_color="#2AABEE", hover_color="#1a8fc8", corner_radius=10, height=36,
                      command=lambda: __import__("webbrowser").open("https://t.me/jbprogramms")
                      ).pack(padx=20, pady=(0, 14), fill="x")

        author_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        author_frame.pack(padx=20, pady=(10, 20), fill="x")

        ctk.CTkLabel(
            author_frame,
            text=txt["about_author"],
            font=("Roboto", 14, "bold"),
            text_color="#e2e8f0"
        ).pack(pady=(15, 2))

        ctk.CTkLabel(
            author_frame,
            text=txt["about_signature"],
            font=("Roboto", 12, "italic"),
            text_color=COLOR_TEXT_DIM
        ).pack(pady=(0, 15))

        self.on_format_change(self.format_var.get())

    def _on_rec_page_resize(self, event):
        """Debounce: откладываем пересчёт layout на 80ms после последнего события.
        Без debounce <Configure> стреляет сотни раз в секунду при перетаскивании окна,
        что даёт ~8-9% загрузку CPU. С debounce — менее 0.5%.
        """
        if self._resize_after_id:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(80, lambda w=event.width: self._apply_rec_layout(w))

    def _freeze_redraw(self, freeze: bool):
        """Заморозить/разморозить перерисовку окна на время перекладки виджетов.
        Используется WM_SETREDRAW (Windows API) — окно обновляется одним кадром
        вместо поочерёдной отрисовки каждого виджета.
        На не-Windows платформах — no-op.
        """
        if sys.platform != "win32":
            return
        try:
            hwnd = self.winfo_id()
            if freeze:
                ctypes.windll.user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW FALSE
            else:
                ctypes.windll.user32.SendMessageW(hwnd, 0x000B, 1, 0)  # WM_SETREDRAW TRUE
                # RDW_ERASE | RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW
                ctypes.windll.user32.RedrawWindow(hwnd, None, None, 0x0185)
        except Exception:
            pass  # если WinAPI недоступен — просто работаем как раньше

    def _apply_rec_layout(self, width):
        """
        Узкий (<750px): pack-стек, всё естественной высоты.
          main_scroll прокручивает если не влезает в окно.
        Широкий (>=750px): grid, 2 колонки рядом.
          _right_col тянется до высоты _left_col через sticky=nsew + rowconfigure.
        """
        self._resize_after_id = None
        wide = width >= 750
        if wide == self._two_col_active:
            return
        self._two_col_active = wide

        self._freeze_redraw(True)
        try:
            if wide:
                self._left_col.pack_forget()
                self._right_col.pack_forget()
                self._body_frame.columnconfigure(0, weight=1)
                self._body_frame.columnconfigure(1, weight=1)
                self._body_frame.rowconfigure(0, weight=1)
                self._left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
                self._right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
                self.folder_frame.pack_forget()
                self.folder_frame.pack(fill="both", expand=True)
            else:
                self._left_col.grid_forget()
                self._right_col.grid_forget()
                self._body_frame.columnconfigure(1, weight=0)
                self._body_frame.rowconfigure(0, weight=0)
                self._left_col.pack(fill="x")
                self._right_col.pack(fill="x")
                self.folder_frame.pack_forget()
                self.folder_frame.pack(fill="x")
        finally:
            self._freeze_redraw(False)

    def _on_settings_page_resize(self, event):
        if self._settings_resize_after_id:
            self.after_cancel(self._settings_resize_after_id)
        self._settings_resize_after_id = self.after(80, lambda w=event.width: self._apply_settings_layout(w))

    def _apply_settings_layout(self, width):
        self._settings_resize_after_id = None
        wide = width >= 750
        if wide == self._settings_two_col_active:
            return
        self._settings_two_col_active = wide

        self._freeze_redraw(True)
        try:
            if wide:
                self._settings_left_col.pack_forget()
                self._settings_right_col.pack_forget()
                self._settings_body_frame.columnconfigure(0, weight=1)
                self._settings_body_frame.columnconfigure(1, weight=1)
                self._settings_body_frame.rowconfigure(0, weight=1)
                self._settings_left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
                self._settings_right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
            else:
                self._settings_left_col.grid_forget()
                self._settings_right_col.grid_forget()
                self._settings_body_frame.columnconfigure(1, weight=0)
                self._settings_body_frame.rowconfigure(0, weight=0)
                self._settings_left_col.pack(fill="x")
                self._settings_right_col.pack(fill="x")
        finally:
            self._freeze_redraw(False)

    def show_page(self, page_name):
        self._freeze_redraw(True)
        try:
            # main_scroll всегда виден; скрываем только страницы внутри него
            self.page_rec.pack_forget()
            self.page_settings.pack_forget()
            self.page_about.pack_forget()

            self.btn_nav_rec.configure(fg_color=COLOR_BLOCK)
            self.btn_nav_set.configure(fg_color=COLOR_BLOCK)
            self.btn_nav_about.configure(fg_color=COLOR_BLOCK)

            if page_name == "rec":
                # fill="x" — ширина окна, высота определяется содержимым → main_scroll скроллит
                self.page_rec.pack(fill="x")
                self.btn_nav_rec.configure(fg_color=COLOR_ACCENT)
            elif page_name == "settings":
                self.page_settings.pack(fill="x")
                self.btn_nav_set.configure(fg_color=COLOR_ACCENT)
            elif page_name == "about":
                self.page_about.pack(fill="x")
                self.btn_nav_about.configure(fg_color=COLOR_ACCENT)
        finally:
            self._freeze_redraw(False)

    def toggle_record(self):
        txt = LANGUAGES[self.lang_var.get()]
        
        if not self.is_recording:
            if self.current_rec_array is not None:
                self.lbl_status.configure(
                    text=txt.get("warn_save", "Save or delete previous recording first!"),
                    text_color=COLOR_RED
                )
                return
            
            self.finish_playback()
            
            self.lbl_status.configure(text=txt["status_recording"], text_color=COLOR_RED)
            self.start_recording()
            self._send_notification(
                txt.get("notify_rec_start", "Recording"),
                txt.get("status_recording", "Recording in progress")
            )
            
            self.btn_rec.configure(text=txt["btn_stop"], fg_color=COLOR_RED)
            self.set_player_enabled(False)
            
        else:
            self.stop_recording()
            
            self.btn_rec.configure(text=txt["btn_record"], fg_color=COLOR_RED)
            has_content = self.current_rec_array is not None or self.active_play_array is not None
            self.set_player_enabled(has_content)
        self._refresh_tray_menu()

    def _update_input_vol_ui(self, v):
        self.volume_mult = float(v)
        txt = LANGUAGES[self.lang_var.get()]
        self.lbl_vol_input.configure(text=f"{txt['input_volume']}: {int(self.volume_mult * 100)}%")
        self.vol_slider.configure(progress_color=COLOR_RED if self.volume_mult > 1.0 else COLOR_ACCENT)

    def _update_play_vol_ui(self, val):
        self.playback_volume = float(val)
        txt = LANGUAGES[self.lang_var.get()]
        self.lbl_play_vol.configure(text=f"{txt['play_volume']}: {int(self.playback_volume * 100)}%")

    def toggle_loop(self):
        self.is_looping = not self.is_looping
        if self.is_looping:
            self.btn_loop.configure(fg_color=COLOR_GREEN, hover_color="#48bb78", text_color=COLOR_BG)
        else:
            self.btn_loop.configure(fg_color="#4a5568", hover_color="#718096", text_color=COLOR_TEXT_DIM)

    def play_audio(self, from_seek=False):
        if self.active_play_array is None: return
        
        current_time = time.time()
        if hasattr(self, '_last_play_click'):
            if current_time - self._last_play_click < 0.3:
                return
        self._last_play_click = current_time
        
        if self.is_playing and not from_seek:
            self.is_fading_out = True 
            self.btn_play.configure(text="▶", fg_color=COLOR_ACCENT)
            return
        if self.is_playing and from_seek: return

        self.is_fading_out = False
        self.is_playing = True
        self.btn_play.configure(text="⏸", fg_color=COLOR_GREEN)
        
        if not from_seek and self.current_frame >= len(self.active_play_array): 
            self.current_frame = 0.0

        alive_threads = [t.name for t in threading.enumerate()]
        if "PlaybackThread" not in alive_threads:
            t = threading.Thread(target=self._playback_thread, daemon=True, name="PlaybackThread")
            t.start()

    def _playback_thread(self):
        # === ВАЖНО: Используем РЕАЛЬНУЮ частоту записи! ===
        # НЕ из UI, а из самого аудио файла
        try:
            if hasattr(self, 'temp_rec_samplerate') and self.temp_rec_samplerate:
                # Используем частоту из загруженного файла
                fs = int(self.temp_rec_samplerate)
                print(f"🔊 Воспроизведение на частоте: {fs} Hz (из файла)")
            else:
                # Fallback на UI (для обратной совместимости)
                raw_sr = self.sample_rate_var.get()
                fs = int(raw_sr.split()[0])
                print(f"🔊 Воспроизведение на частоте: {fs} Hz (из UI)")
        except (ValueError, IndexError, AttributeError) as e:
            fs = 44100
            print(f"⚠️ Ошибка определения частоты: {e}, используем 44100 Hz")
            
        data = self.active_play_array
        fade_step = 0.25  
        self.fade_multiplier = 1.0

        def callback(outdata, frames, time_info, status):
            if not self.is_playing: 
                raise sd.CallbackStop
            
            start = int(self.current_frame)
            end = start + frames
            
            if self.is_fading_out:
                fades = np.linspace(self.fade_multiplier, self.fade_multiplier - fade_step, frames)
                fades = np.maximum(fades, 0) 
                self.fade_multiplier = fades[-1]
                current_fade = fades.reshape(-1, 1)
                if self.fade_multiplier <= 0:
                    self.is_playing = False 
                    raise sd.CallbackStop
            else:
                current_fade = np.ones((frames, 1), dtype=np.float32)

            if start >= len(data):
                if self.is_looping: 
                    self.current_frame = 0
                    start, end = 0, frames
                else: 
                    raise sd.CallbackStop

            if end > len(data):
                chunk = data[start:]
                outdata[:len(chunk)] = chunk * self.playback_volume * current_fade[:len(chunk)]
                if self.is_looping:
                    rem = frames - len(chunk)
                    outdata[len(chunk):] = data[:rem] * self.playback_volume * current_fade[len(chunk):]
                    self.current_frame = rem
                else:
                    outdata[len(chunk):] = 0
                    self.current_frame = len(data)
                    raise sd.CallbackStop
            else:
                outdata[:] = data[start:end] * self.playback_volume * current_fade
                self.current_frame += frames

        try:
            with sd.OutputStream(samplerate=fs, channels=data.shape[1], callback=callback):
                while self.is_playing:
                    if not self.is_looping and self.current_frame >= len(data): 
                        break
                    self.after(0, self._update_ui_playback, self.current_frame / len(data))
                    time.sleep(0.05)
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
        
        is_end = not self.is_looping and self.current_frame >= len(data)
        if is_end or self.is_stopping:
            self.after(0, self.finish_playback)
        else: 
            sd.stop()

    def _update_ui_playback(self, prog):
        if not self.is_playing: return
        self.seek_slider.set(prog)
        fs = int(self.temp_rec_samplerate) if (hasattr(self, 'temp_rec_samplerate') and self.temp_rec_samplerate) else int(self.sample_rate_var.get().split()[0])
        total_sec = len(self.active_play_array) / fs
        self.lbl_play_time.configure(text=f"{self.format_time(prog * total_sec)} / {self.format_time(total_sec)}")

    def finish_playback(self):
        self.is_playing = False
        self.is_stopping = False
        self.is_fading_out = False
        self.current_frame = 0.0
        sd.stop() 
        self.btn_play.configure(text="▶", fg_color=COLOR_ACCENT)
        self.seek_slider.set(0)
        
        if self.active_play_array is not None:
            try:
                fs = int(self.sample_rate_var.get().split()[0])
                total_sec = len(self.active_play_array) / fs
                self.lbl_play_time.configure(text=f"00:00 / {self.format_time(total_sec)}")
            except Exception as e:
                print(f"Ошибка сброса таймера: {e}")
                self.lbl_play_time.configure(text="00:00 / 00:00")

    def manual_seek(self, v):
        if self.active_play_array is not None:
            self.current_frame = float(v) * len(self.active_play_array)
            try:
                raw_sr = self.sample_rate_var.get()
                fs = int(raw_sr.split()[0])
                total_sec = len(self.active_play_array) / fs
                current_sec = float(v) * total_sec
                self.lbl_play_time.configure(
                    text=f"{self.format_time(current_sec)} / {self.format_time(total_sec)}"
                )
            except Exception as e:
                print(f"Ошибка при перемотке: {e}")

    def audio_callback(self, indata, frames, time_info, status):
        """Callback для обработки входящего аудио"""
        if self.is_recording and not self.is_paused:
            data = indata.copy() * self.volume_mult
            
            try:
                self.audio_queue.put_nowait(data)
            except queue.Full:
                print("Предупреждение: очередь аудио переполнена, пропуск чанка")
            
            rms = np.sqrt(np.mean(data**2))
            self.after(0, lambda: self.vol_bar.set(min(rms * 25, 1.0)))
        else:
            self.after(0, lambda: self.vol_bar.set(0))

    def start_recording(self):
        """Начинает запись во временный файл на диске"""
        self.is_recording, self.is_paused = True, False
        self.start_time = time.time()
        
        txt = LANGUAGES[self.lang_var.get()]
        self.btn_pause.configure(state="normal", text=txt["btn_pause"], fg_color="#4a5568", hover_color="#718096")
        
        self._create_temp_recording_file()
        
        self.audio_queue = queue.Queue(maxsize=100)
        self.writer_stop_event = threading.Event()
        self.writer_thread = threading.Thread(target=self._audio_writer_thread, daemon=True)
        self.writer_thread.start()
        
        dev_id = next((i for i, d in enumerate(sd.query_devices()) if d['name'] == self.device_var.get()), None)
        threading.Thread(target=self.record_thread, args=(dev_id,), daemon=True).start()
        self.update_timer()
    
    def _create_temp_recording_file(self):
        """Создает временный WAV файл для записи"""
        self._cleanup_temp_file()
        
        temp_dir = tempfile.gettempdir()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.temp_rec_file = os.path.join(temp_dir, f"jb_recorder_temp_{timestamp}.wav")
        print(f"Создан временный файл: {self.temp_rec_file}")
    
    def _audio_writer_thread(self):
        """Поток для записи аудио из очереди в файл"""
        fs = int(self.sample_rate_var.get().split()[0])
        current_mode = self.channels_var.get()
        ch_count = 1 if current_mode in ["Mono", "Моно"] else 2
        
        print(f"Writer thread: {fs}Hz, Каналов: {ch_count}")
        
        try:
            with sf.SoundFile(self.temp_rec_file, mode='w', samplerate=fs, 
                            channels=ch_count, subtype='PCM_16') as file:
                while not self.writer_stop_event.is_set() or not self.audio_queue.empty():
                    try:
                        data = self.audio_queue.get(timeout=0.1)
                        if data is not None:
                            file.write(data)
                    except queue.Empty:
                        continue
                    except Exception as e:
                        print(f"Ошибка записи в файл: {e}")
                        break
            
            print(f"Запись в файл завершена: {self.temp_rec_file}")
        except Exception as e:
            print(f"Ошибка создания файла: {e}")

    def record_thread(self, dev_id):
        try:
            fs = int(self.sample_rate_var.get().split()[0])
            current_mode = self.channels_var.get()
            if current_mode in ["Mono", "Моно"]:
                ch_count = 1
            else:
                ch_count = 2
            
            print(f"Запуск потока: {fs}Hz, Каналов: {ch_count} ({current_mode})")

            with sd.InputStream(samplerate=fs, 
                                channels=ch_count, 
                                device=dev_id, 
                                callback=self.audio_callback):
                while self.is_recording:
                    sd.sleep(100)
        except Exception as e:
            print(f"Ошибка записи: {e}")
            self.after(0, self.stop_recording)
        
    def stop_recording(self):
        """Останавливает запись и загружает данные из временного файла"""
        self.is_recording = False
        self.is_paused = False
        
        txt = LANGUAGES[self.lang_var.get()]
        
        self.lbl_status.configure(text="")
        self.btn_rec.configure(text=txt["btn_record"], fg_color=COLOR_RED)
        self.btn_pause.configure(state="disabled", text=txt["btn_pause"], fg_color="#4a5568")
        self._send_notification(
            txt.get("notify_rec_stop", "Stop"),
            txt.get("status_done", "Recording ready")
        )
        
        if self.writer_stop_event:
            self.writer_stop_event.set()
        
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=2.0)
        
        if self.temp_rec_file and os.path.exists(self.temp_rec_file):
            try:
                data, samplerate = sf.read(self.temp_rec_file, dtype='float32')
                _noise_mode = self.noise_mode_var.get()
                if _noise_mode != "off":
                    # Шумоподавление — в отдельном потоке, чтобы UI не зависал
                    _prop = {"light": 0.4, "medium": 0.75, "aggressive": 1.0}.get(_noise_mode, 0.75)
                    self._apply_noise_async(data, samplerate, _prop, txt)
                    # vol_bar/timer/update_file_list are handled in _on_noise_complete
                    return
                else:
                    self._finish_stop_recording(data, samplerate, txt)
            except Exception as e:
                print(f"Ошибка загрузки временного файла: {e}")
                self.lbl_status.configure(text=f"ОШИБКА ЗАГРУЗКИ: {e}", text_color=COLOR_RED)

        self.vol_bar.set(0)
        self.lbl_timer.configure(text="00:00:00")
        self.update_file_list()
            
    def _apply_noise_async(self, data, samplerate, prop, txt):
        """Запускает шумоподавление в фоновом потоке с окном прогресса."""
        self._show_noise_progress_window(txt)
        self._noise_progress_timer = None
        result_holder = [None]
        noise_error_holder = [None]

        def worker():
            try:
                if len(data.shape) == 1:
                    # Моно: один вызов, прогресс анимируется таймером
                    result_holder[0] = nr.reduce_noise(
                        y=data, sr=samplerate, stationary=True, prop_decrease=prop)
                else:
                    # Стерео/мульти: прогресс по каналам
                    total = data.shape[1]
                    channels = []
                    for idx in range(total):
                        ch = nr.reduce_noise(
                            y=data[:, idx], sr=samplerate, stationary=True, prop_decrease=prop)
                        channels.append(ch)
                        pct = int((idx + 1) / total * 99)
                        self.after(0, lambda p=pct: self._update_noise_progress(p))
                    result_holder[0] = np.column_stack(channels)
            except Exception as e:
                print(f"Ошибка шумоподавления: {e}")
                noise_error_holder[0] = str(e)
                result_holder[0] = data  # fallback: оригинал без шумоподавления
            finally:
                self.after(0, lambda: self._on_noise_complete(
                    result_holder[0], samplerate, txt, noise_error_holder[0]))

        threading.Thread(target=worker, daemon=True).start()

        # Для моно: анимируем прогресс от 0 до 90% пока поток работает
        if len(data.shape) == 1:
            self._noise_fake_progress = 0
            self._animate_noise_progress()

    def _animate_noise_progress(self):
        """Плавно анимирует прогресс-бар (для моно канала)."""
        if not hasattr(self, "_noise_progress_window") or self._noise_progress_window is None:
            return
        if self._noise_fake_progress < 90:
            self._noise_fake_progress += 1
            self._update_noise_progress(self._noise_fake_progress)
            self._noise_progress_timer = self.after(150, self._animate_noise_progress)

    def _show_noise_progress_window(self, txt):
        """Создаёт модальное окно с прогресс-баром шумоподавления."""
        self._noise_progress_window = ctk.CTkToplevel(self)
        self._noise_progress_window.title(txt.get("noise_progress_title", "Noise Reduction"))
        self._noise_progress_window.geometry("400x180")
        self._noise_progress_window.resizable(False, False)
        self._noise_progress_window.configure(fg_color=COLOR_BG)
        self._noise_progress_window.transient(self)
        self._noise_progress_window.grab_set()
        self._noise_progress_window.protocol("WM_DELETE_WINDOW", lambda: None)
        self._noise_progress_window.update_idletasks()
        x = (self._noise_progress_window.winfo_screenwidth() // 2) - 200
        y = (self._noise_progress_window.winfo_screenheight() // 2) - 90
        self._noise_progress_window.geometry(f"400x180+{x}+{y}")

        ctk.CTkLabel(
            self._noise_progress_window,
            text=txt.get("noise_progress_msg", "Applying noise reduction...\nPlease wait..."),
            font=("Roboto", 14), text_color="#ffffff"
        ).pack(pady=(30, 5))

        self._noise_pct_label = ctk.CTkLabel(
            self._noise_progress_window, text="0%",
            font=("Roboto", 12), text_color=COLOR_TEXT_DIM
        )
        self._noise_pct_label.pack(pady=5)

        self._noise_progress_bar = ctk.CTkProgressBar(
            self._noise_progress_window, width=350, mode="determinate"
        )
        self._noise_progress_bar.pack(pady=10)
        self._noise_progress_bar.set(0)

    def _update_noise_progress(self, percent):
        """Обновляет прогресс-бар шумоподавления."""
        if hasattr(self, "_noise_progress_window") and self._noise_progress_window:
            try:
                self._noise_progress_bar.set(percent / 100)
                self._noise_pct_label.configure(text=f"{int(percent)}%")
            except Exception:
                pass

    def _on_noise_complete(self, data, samplerate, txt, error=None):
        """Вызывается после завершения шумоподавления."""
        # Отменяем таймер анимации если был
        if self._noise_progress_timer:
            try:
                self.after_cancel(self._noise_progress_timer)
            except Exception:
                pass
            self._noise_progress_timer = None
        # Закрываем окно прогресса
        self._update_noise_progress(100)
        self.after(200, self._close_noise_window)
        # Сбрасываем таймер и полосу громкости (они не сброшены в stop_recording при шумоподавлении)
        self.vol_bar.set(0)
        self.lbl_timer.configure(text="00:00:00")
        # Если шумоподавление завершилось с ошибкой — показываем пользователю
        if error:
            self.lbl_status.configure(
                text=f"⚠️ Noise reduction failed: {error[:60]}", text_color=COLOR_YELLOW)
            self.after(4000, self.update_ready_status)
        # Передаём данные дальше через небольшую задержку чтоб 100% успело показаться
        self.after(250, lambda: self._finish_stop_recording(data, samplerate, txt))
        self.after(300, self.update_file_list)

    def _close_noise_window(self):
        if hasattr(self, "_noise_progress_window") and self._noise_progress_window:
            try:
                self._noise_progress_window.grab_release()
                self._noise_progress_window.destroy()
            except Exception:
                pass
            self._noise_progress_window = None

    def _finish_stop_recording(self, data, samplerate, txt):
        """Финальная часть stop_recording — записывает данные и обновляет UI."""
        self.current_rec_array = data
        self.active_play_array = self.current_rec_array
        self.temp_rec_samplerate = samplerate
        self.current_file_name = txt["temp_rec_name"]
        print(f"Загружено из файла: {len(data)} samples, {samplerate} Hz")
        if self.auto_save_var.get():
            self.quick_save()
            self.active_play_array = None
            self.current_file_name = ""
            self.finish_playback()
            self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {txt['no_file']}")
            self.set_player_enabled(False)
        else:
            self.lbl_status.configure(text=txt["status_done"], text_color=COLOR_YELLOW)
            self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {self.current_file_name}")
            self.set_player_enabled(True)
            self.btn_save.configure(state="normal")
            self.btn_del.configure(state="normal")

    def quick_save(self):
        """Сохраняет запись асинхронно с индикатором прогресса"""
        if self.current_rec_array is None or self.is_saving:
            return
        
        if self.is_playing:
            self.finish_playback()
        
        self.btn_save.configure(state="disabled")
        self.btn_del.configure(state="disabled")
        
        duration_seconds = len(self.current_rec_array) / (self.temp_rec_samplerate or 44100)
        ext = self.format_var.get().lower()
        show_progress = True
        
        if duration_seconds < 5:
            show_progress = False
        elif ext in ["wav", "flac"] and duration_seconds < 30:
            show_progress = False
        
        if show_progress:
            self._show_save_progress_window()
        
        self.is_saving = True
        self.save_thread = threading.Thread(target=self._save_thread_worker, daemon=True)
        self.save_thread.start()
    
    def _show_save_progress_window(self):
        """Создает окно с индикатором прогресса сохранения"""
        txt = LANGUAGES[self.lang_var.get()]
        
        self.save_progress_window = ctk.CTkToplevel(self)
        self.save_progress_window.title(txt.get("saving_title", "Saving..."))
        self.save_progress_window.geometry("400x180")
        self.save_progress_window.resizable(False, False)
        self.save_progress_window.configure(fg_color=COLOR_BG)
        
        self.save_progress_window.transient(self)
        self.save_progress_window.grab_set()
        self.save_progress_window.protocol("WM_DELETE_WINDOW", lambda: None)
        
        self.save_progress_window.update_idletasks()
        x = (self.save_progress_window.winfo_screenwidth() // 2) - (400 // 2)
        y = (self.save_progress_window.winfo_screenheight() // 2) - (180 // 2)
        self.save_progress_window.geometry(f"400x180+{x}+{y}")
        
        lbl = ctk.CTkLabel(
            self.save_progress_window,
            text=txt.get("saving_message", "Saving audio file...\nPlease wait..."),
            font=("Roboto", 14),
            text_color="#ffffff"
        )
        lbl.pack(pady=(30, 5))
        
        self.save_progress_label = ctk.CTkLabel(
            self.save_progress_window,
            text="0%",
            font=("Roboto", 12),
            text_color=COLOR_TEXT_DIM
        )
        self.save_progress_label.pack(pady=5)
        
        self.save_progress_bar = ctk.CTkProgressBar(
            self.save_progress_window,
            width=350,
            mode="determinate"
        )
        self.save_progress_bar.pack(pady=10)
        self.save_progress_bar.set(0)
    
    def _update_save_progress(self, percent):
        """Обновляет прогресс-бар при сохранении"""
        if self.save_progress_window and hasattr(self, 'save_progress_bar'):
            self.save_progress_bar.set(percent / 100)
            if hasattr(self, 'save_progress_label'):
                self.save_progress_label.configure(text=f"{int(percent)}%")
    
    def _save_thread_worker(self):
        """ОПТИМИЗИРОВАННЫЙ worker для сохранения больших файлов через ffmpeg"""
        txt = LANGUAGES[self.lang_var.get()]
        
        try:
            ext = self.format_var.get().lower()
            if ext == "aac":
                ext = "m4a"
            
            # ИСПОЛЬЗУЕМ РЕАЛЬНУЮ ЧАСТОТУ ЗАПИСИ!
            if self.temp_rec_samplerate:
                fs = int(self.temp_rec_samplerate)
            else:
                fs = int(self.sample_rate_var.get().split()[0])
            
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"rec_{timestamp}.{ext}"
            full_path = os.path.join(self.save_path.get(), filename)
            
            # === ОПТИМИЗАЦИЯ: ПРЯМОЕ СОХРАНЕНИЕ ДЛЯ WAV/FLAC ===
            if ext in ["wav", "flac"]:
                # Запись чанками для отображения прогресса
                arr = self.current_rec_array
                total_frames = len(arr)
                channels = 1 if arr.ndim == 1 else arr.shape[1]
                chunk = max(fs, total_frames // 100)  # ~1 сек или 1% — что больше
                subtype = "PCM_16"
                with sf.SoundFile(full_path, "w", samplerate=fs,
                                  channels=channels, subtype=subtype) as f:
                    written = 0
                    while written < total_frames:
                        end = min(written + chunk, total_frames)
                        f.write(arr[written:end])
                        written = end
                        pct = written / total_frames * 100
                        self.after(0, lambda p=pct: self._update_save_progress(p))
                print(f"Сохранение {ext.upper()}: {full_path} @ {fs} Hz")
                self.after(0, lambda: self._update_save_progress(100))
                self.after(0, lambda: self._on_save_complete(True, txt, None))
                return
            
            # === ДЛЯ MP3/OGG: КОДИРУЕМ ЧЕРЕЗ FFMPEG ===
            # Пишем current_rec_array (уже с шумоподавлением!) во временный WAV,
            # а не temp_rec_file (сырая запись без обработки)
            import tempfile as _tm
            _encode_wav = os.path.join(
                _tm.gettempdir(),
                f"jb_enc_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            )
            sf.write(_encode_wav, self.current_rec_array, fs, subtype='PCM_16')
            
            clean_bitrate = self._normalize_bitrate(self.bitrate_var.get())
            duration = len(self.current_rec_array) / fs
            
            # Читаем stderr в отдельном потоке (избегаем deadlock)
            process = subprocess.Popen([
                FFMPEG_PATH, '-y',
                '-i', _encode_wav,
                '-b:a', clean_bitrate,
                '-progress', 'pipe:2',  # Прогресс в stderr
                full_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               universal_newlines=True,
               startupinfo=startupinfo if sys.platform == "win32" else None)
            
            # Функция для чтения stderr в отдельном потоке (избегаем deadlock!)
            def read_stderr():
                try:
                    for line in process.stderr:
                        # Парсим прогресс из stderr
                        if 'out_time_ms=' in line:
                            try:
                                time_ms = int(line.split('=')[1].strip())
                                time_sec = time_ms / 1_000_000.0
                                progress = min((time_sec / duration) * 100, 99)
                                # Обновляем UI из главного потока
                                self.after(0, lambda p=progress: self._update_save_progress(p))
                            except:
                                pass
                except:
                    pass
            
            # Запускаем чтение stderr в отдельном потоке
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()
            
            # Читаем stdout (может быть пустым, но это нормально)
            for line in process.stdout:
                pass  # Просто читаем чтобы не переполнялся буфер
            
            # Ждем завершения процесса
            process.wait()
            
            # Проверяем код возврата
            if process.returncode != 0:
                raise Exception(f"ffmpeg returned error code: {process.returncode}")
            
            # Ставим 100% перед завершением
            self.after(0, lambda: self._update_save_progress(100))
            
            print(f"Конвертировано в {ext.upper()} через ffmpeg: {full_path} @ {fs} Hz")
            try:
                os.remove(_encode_wav)
            except Exception:
                pass
            
            self.after(0, lambda: self._on_save_complete(True, txt, None))
            
        except Exception as e:
            self.after(0, lambda: self._on_save_complete(False, txt, e))
    
    def _on_save_complete(self, success, txt, error):
        """Вызывается после завершения сохранения"""
        if self.save_progress_window:
            self.save_progress_window.grab_release()
            self.save_progress_window.destroy()
            self.save_progress_window = None
        
        self.is_saving = False
        
        if success:
            self.lbl_status.configure(text=txt["status_save_success"], text_color=COLOR_GREEN)
            self._send_notification(
                txt.get("status_save_success", "File saved"),
            )

            self.current_rec_array = None
            self.active_play_array = None
            self.current_file_name = ""
            self.finish_playback()
            self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {txt['no_file']}")
            self.set_player_enabled(False)
            self.btn_save.configure(state="disabled")
            self.btn_del.configure(state="disabled")

            self._cleanup_temp_file()

            self.after(2000, self._restore_ready_status_if_applicable)
            self.update_file_list()
        else:
            error_prefix = txt.get("status_save_error", "SAVE ERROR")
            self.lbl_status.configure(text=f"{error_prefix}: {error}", text_color=COLOR_RED)
            self._send_notification(
                txt.get("status_save_error", "Save error"),
                str(error)[:120] if error else "",
            )
            print(f"Error during save: {error}")

            self.btn_save.configure(state="normal")
            self.btn_del.configure(state="normal")
            
    def change_directory(self):
        p = ctk.filedialog.askdirectory()
        if p: 
            self.save_path.set(p)
            self.update_file_list()
            if hasattr(self, 'lbl_current_save_path'):
                self.lbl_current_save_path.configure(text=p)
            self.save_settings()

    def open_directory(self):
        path = self.save_path.get()
        if os.path.exists(path):
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

    def delete_rec(self):
        if self.delete_timer_id is not None:
            self._cancel_deletion()
        else:
            self._start_deletion_countdown(5)

    def _start_deletion_countdown(self, seconds):
        txt = LANGUAGES[self.lang_var.get()]
        if seconds > 0:
            self.btn_save.configure(state="disabled")
            
            cancel_text = txt.get("btn_cancel", "CANCEL")
            sec_text = txt.get("unit_sec", "sec")
            self.btn_del.configure(text=f"{cancel_text} ({seconds} {sec_text})")
            
            self.btn_del.configure(fg_color=COLOR_RED, hover_color="#c53030")
            if not self.pulse_timer_id:
                self._pulse_button(True)
                
            self.delete_timer_id = self.after(1000, lambda: self._start_deletion_countdown(seconds - 1))
        else:
            self._confirm_deletion()

    def _pulse_button(self, toggle):
        next_color = COLOR_YELLOW if toggle else "#d69e2e" 
        self.btn_del.configure(fg_color=next_color, text_color=COLOR_BG)
        self.pulse_timer_id = self.after(500, lambda: self._pulse_button(not toggle))

    def _stop_pulse(self):
        if self.pulse_timer_id:
            self.after_cancel(self.pulse_timer_id)
            self.pulse_timer_id = None

    def _cancel_deletion(self):
        txt = LANGUAGES[self.lang_var.get()]
        self._stop_pulse()
        if self.delete_timer_id:
            self.after_cancel(self.delete_timer_id)
            self.delete_timer_id = None
        
        self.btn_save.configure(state="normal")
        self.btn_del.configure(text=txt["btn_delete"], fg_color="#4a5568", hover_color="#718096", text_color="#ffffff")
        
        self.lbl_status.configure(text=txt["status_del_cancel"], text_color=COLOR_ACCENT)
        self.after(2000, lambda: self.lbl_status.configure(text="") if not self.is_recording else None)

        self.after(2000, lambda: self.lbl_status.configure(
            text=txt["status_done"], 
            text_color=COLOR_ACCENT
        ) if self.current_rec_array is not None and not self.is_recording else None)

    def _restore_ready_status_if_applicable(self):
        if not self.is_recording and self.current_rec_array is None:
            txt = LANGUAGES[self.lang_var.get()]
            self.lbl_status.configure(text=txt["status_ready"], text_color=COLOR_GREEN)

    def _confirm_deletion(self):
        """Подтверждает удаление записи и очищает временный файл"""
        self._stop_pulse()
        txt = LANGUAGES[self.lang_var.get()]

        if self.delete_timer_id:
            self.after_cancel(self.delete_timer_id)
            self.delete_timer_id = None

        self.finish_playback()
        self.current_rec_array = None
        self.active_play_array = None
        self.current_file_name = ""
        
        self._cleanup_temp_file()
        
        self.lbl_now_playing.configure(text=f"{txt['now_playing']}: {txt['no_file']}")

        self.lbl_status.configure(text=txt["status_deleted"], text_color=COLOR_RED)

        self.set_player_enabled(False)
        self.btn_save.configure(state="disabled")
        self.btn_del.configure(state="disabled", text=txt["btn_delete"], fg_color="#4a5568", hover_color="#718096", text_color="#ffffff")

        self.after(2000, self._restore_ready_status_if_applicable)
        self.update_file_list()

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        txt = LANGUAGES[self.lang_var.get()]

        if self.is_paused:
            # Запоминаем момент начала паузы
            self._paused_at = time.time()
            self.btn_pause.configure(text=txt["btn_resume"], fg_color=COLOR_GREEN, hover_color="#48bb78")
            self.lbl_status.configure(text=txt["status_paused"], text_color=COLOR_YELLOW)
            self._send_notification(
                txt.get("notify_rec_pause", "Pause"),
                txt.get("status_paused", "Recording paused")
            )
        else:
            # Сдвигаем start_time вперёд на длительность паузы,
            # чтобы формула (time.time() - start_time) давала чистое время записи
            if self._paused_at is not None:
                self.start_time += time.time() - self._paused_at
                self._paused_at = None
            self.btn_pause.configure(text=txt["btn_pause"], fg_color="#4a5568", hover_color="#718096")
            self.lbl_status.configure(text=txt["status_recording"], text_color=COLOR_RED)
            self._send_notification(
                txt.get("notify_rec_resume", "Resume"),
                txt.get("status_recording", "Recording in progress")
            )
        self._refresh_tray_menu()

    def update_timer(self):
        if self.is_recording:
            if not self.is_paused:
                el = int(time.time() - self.start_time)
                self.lbl_timer.configure(text=time.strftime('%H:%M:%S', time.gmtime(el)))
            self.after(500, self.update_timer)

    def format_time(self, seconds):
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

    def _hotkey_toggle_record(self):
        if not self.hotkeys_enabled.get():
            return
        
        txt = LANGUAGES[self.lang_var.get()]
        
        if not self.is_recording:
            # Проверка что нет несохраненной записи
            if self.current_rec_array is not None:
                self.after(0, lambda: self.lbl_status.configure(
                    text=txt.get("warn_save", "Save or delete previous recording first!"),
                    text_color=COLOR_RED
                ))
                return
            
            # Останавливаем воспроизведение если идет
            self.after(0, self.finish_playback)
            
            # ИСПРАВЛЕНИЕ: Обновляем статус при начале записи через горячие клавиши
            self.after(0, lambda: self.lbl_status.configure(
                text=txt["status_recording"], 
                text_color=COLOR_RED
            ))
            
            # Обновляем кнопку
            self.after(0, lambda: self.btn_rec.configure(
                text=txt["btn_stop"], 
                fg_color="#4a5568"
            ))
            
            # Отключаем плеер
            self.after(0, lambda: self.set_player_enabled(False))
            
            # Запускаем запись + уведомление
            self.after(0, self.start_recording)
            self.after(50, lambda: self._send_notification(
                txt.get("notify_rec_start", "Recording"),
                txt.get("status_recording", "Recording in progress")
            ))
        else:
            # Останавливаем запись
            self.after(0, self.stop_recording)
            
            # Обновляем кнопку
            self.after(0, lambda: self.btn_rec.configure(
                text=txt["btn_record"], 
                fg_color=COLOR_RED
            ))
            
            # Включаем плеер если есть контент
            has_content = self.current_rec_array is not None or self.active_play_array is not None
            self.after(0, lambda: self.set_player_enabled(has_content))

    def _hotkey_toggle_pause(self):
        if self.hotkeys_enabled.get() and self.is_recording:
            self.after(0, self.toggle_pause)

    def _hotkey_save(self):
        if self.hotkeys_enabled.get() and not self.is_recording and self.audio_data:
            self.after(0, self.quick_save)

if __name__ == "__main__":
    check_vcredist_on_startup()
    app = JBAudioRecorder(); app.mainloop()