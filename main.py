import json
import locale
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
import customtkinter as ctk
import noisereduce as nr
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
    # Windows: C:\Users\Username\AppData\Roaming\JB Audio Recorder
    CONFIG_DIR = os.path.join(os.getenv('APPDATA'), 'JB Audio Recorder')
else:
    # Linux/Mac: ~/.jb-audio-recorder
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), '.jb-audio-recorder')

# Создаем папку если её нет
try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    print(f"📁 Папка настроек: {CONFIG_DIR}")
except Exception as e:
    print(f"⚠️ Ошибка создания папки настроек: {e}")
    # Fallback на папку программы (для совместимости)
    CONFIG_DIR = BASE_DIR

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

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

    def _safe_toggle_record(self):
        if not self.is_recording:
            self.start_record()
        else:
            self.stop_record()

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
                saved_lang = data.get("language", "English")
                self.lang_var.set(saved_lang)
                self.tray_enabled_var.set(data.get("tray_enabled", True))
                self.autostart_var.set(data.get("autostart", False))
                self.auto_save_var.set(data.get("auto_save", False))
                self.always_on_top_var.set(data.get("always_on_top", False))
                self.skip_exit_confirm.set(data.get("skip_exit_confirm", False))
                self.hotkeys_enabled.set(data.get("hotkeys_enabled", True))
                self.check_disk_space_var.set(data.get("check_disk_space", True))
                
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
            "language": self.lang_var.get(),
            "tray_enabled": self.tray_enabled_var.get(),
            "autostart": self.autostart_var.get(),
            "auto_save": self.auto_save_var.get(),
            "save_path": self.save_path.get(),
            "always_on_top": self.always_on_top_var.get(),
            "skip_exit_confirm": self.skip_exit_confirm.get(),
            "hotkeys_enabled": self.hotkeys_enabled.get(),
            "check_disk_space": self.check_disk_space_var.get(),
            "device": self.device_var.get()
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
        self.lang_var.set("English")
        self.tray_enabled_var.set(True)
        self.autostart_var.set(False)
        self.auto_save_var.set(False)
        self.always_on_top_var.set(False)
        self.skip_exit_confirm.set(False)
        self.hotkeys_enabled.set(True)
        self.check_disk_space_var.set(True)
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
        
        # Обновляем трей (перезапускаем если нужно)
        # self.update_tray_visibility() удалить это говно надо, смена языка так и так трей перезапустит
        
        self.change_language(self.lang_var.get())
        mb.showinfo(
            txt.get("reset_settings", "Reset Settings"),
            txt.get("reset_settings_success", "Settings reset successfully!")
        )

    def check_disk_space_on_startup(self):
        """Проверка свободного места на диске C при запуске"""
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
    
    def start_tray(self):
        txt = LANGUAGES[self.lang_var.get()]
        image = self.create_tray_icon()
        
        menu = pystray.Menu(
            item(txt["tray_open"], self.show_window, default=True),
            item(txt.get("tray_settings", "Settings"), self.show_settings_window),
            item(txt["tray_exit"], self.quit_app)
        )
        
        self.tray_icon = pystray.Icon("JB Audio Recorder", image, "JB Audio Recorder", menu)
        self.tray_icon.on_click = lambda icon, item: self.show_window()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        print("Запуск tray...")

    def change_language(self, new_lang):

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
        if hasattr(self, 'lbl_auto_save_help'):
            self.lbl_auto_save_help.configure(text=txt.get("auto_save_help", "Recording is saved directly to disk without preview"))
        if hasattr(self, 'lbl_aot_help'):
            self.lbl_aot_help.configure(text=txt.get("always_on_top_help", "App stays on top of all other windows"))

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
        ctk.CTkLabel(header_frame, text=about_txt["about_version"], font=("Roboto", 12), text_color=COLOR_TEXT_DIM).pack(pady=(0, 15))

        desc_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        desc_frame.pack(padx=20, pady=10, fill="x")
        ctk.CTkLabel(
            desc_frame,
            text=about_txt["about_description"],
            font=("Roboto", 15),
            text_color="#e2e8f0",
            wraplength=480,
            justify="left"
        ).pack(padx=20, pady=15)

        links_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        links_frame.pack(padx=20, pady=10, fill="x")
        link_row = ctk.CTkFrame(links_frame, fg_color="transparent")
        link_row.pack(pady=(12, 4))
        ctk.CTkLabel(link_row, text=about_txt["about_links"], font=("Roboto", 13, "bold"), text_color=COLOR_ACCENT).pack(side="left")
        
        github_link = ctk.CTkLabel(
            link_row,
            text="jeffbennington/JB-Audio-Recorder",  # Текст ссылки
            font=("Roboto", 13, "underline"),  # Подчёркнутый
            text_color=COLOR_ACCENT,  # Синий
            cursor="hand2"  # Курсор-рука
        )
        github_link.pack(side="left", padx=(5, 0))
        github_link.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://github.com/jeffbennington/JB-Audio-Recorder"))

        ctk.CTkLabel(tg_row2, text="Telegram:", font=("Roboto", 13, "bold"), text_color=COLOR_ACCENT).pack(side="left")
        _tg2 = ctk.CTkLabel(tg_row2, text="@jbprogramms", font=("Roboto", 13, "underline"), text_color=COLOR_ACCENT, cursor="hand2")
        _tg2.pack(side="left", padx=(5, 0))
        _tg2.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://t.me/jbprogramms"))

        author_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        author_frame.pack(padx=20, pady=(10, 20), fill="x")
        ctk.CTkLabel(author_frame, text=about_txt["about_author"], font=("Roboto", 14, "bold"), text_color="#e2e8f0").pack(pady=(15, 2))
        ctk.CTkLabel(author_frame, text=about_txt["about_signature"], font=("Roboto", 12, "italic"), text_color=COLOR_TEXT_DIM).pack(pady=(0, 15))

    def update_tray_visibility(self):
        # Проверка свободного места на диске при запуске
        self.check_disk_space_on_startup()
        
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
        self.exit_win.title("")
        self.exit_win.geometry("350x200")
        self.exit_win.resizable(False, False)
        self.exit_win.configure(fg_color=COLOR_BG)
        
        self.exit_win.transient(self)
        self.exit_win.grab_set()

        txt = LANGUAGES[self.lang_var.get()]
        self.exit_win.title(txt.get("exit_title", "Exit"))

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

        for widget in self.library_scroll.winfo_children():
            widget.destroy()

        row = 0
        header_font = ("Roboto", 11, "bold")
        lbl_date = ctk.CTkLabel(self.library_scroll, text=txt["list_date"], font=header_font, text_color=COLOR_TEXT_DIM, width=130, anchor="w")
        lbl_date.grid(row=row, column=0, sticky="w", padx=(0, 5))
        lbl_date.bind("<MouseWheel>", self._scroll_library)
        lbl_date.bind("<Button-4>", self._scroll_library)
        lbl_date.bind("<Button-5>", self._scroll_library)
        
        lbl_name = ctk.CTkLabel(self.library_scroll, text=txt["list_name"], font=header_font, text_color=COLOR_TEXT_DIM, width=200, anchor="w")
        lbl_name.grid(row=row, column=1, sticky="w", padx=(0, 5))
        lbl_name.bind("<MouseWheel>", self._scroll_library)
        lbl_name.bind("<Button-4>", self._scroll_library)
        lbl_name.bind("<Button-5>", self._scroll_library)
        
        lbl_format = ctk.CTkLabel(self.library_scroll, text=txt["format"], font=header_font, text_color=COLOR_TEXT_DIM, width=60, anchor="w")
        lbl_format.grid(row=row, column=2, sticky="w")
        lbl_format.bind("<MouseWheel>", self._scroll_library)
        lbl_format.bind("<Button-4>", self._scroll_library)
        lbl_format.bind("<Button-5>", self._scroll_library)
        row += 1

        if self.current_rec_array is not None:
            btn = ctk.CTkButton(
                self.library_scroll,
                text="🔴 " + txt["list_current"],
                anchor="w",
                fg_color=COLOR_HOVER,
                hover_color=COLOR_ACCENT,
                text_color="#e2e8f0",
                command=self._load_temp_rec,
                height=24
            )
            btn.grid(row=row, column=0, columnspan=3, sticky="ew", pady=1)
            btn.bind("<MouseWheel>", self._scroll_library)
            btn.bind("<Button-4>", self._scroll_library)
            btn.bind("<Button-5>", self._scroll_library)
            row += 1

        path = self.save_path.get()
        if os.path.exists(path):
            files = [f for f in os.listdir(path) if f.endswith(('.wav', '.mp3', '.flac', '.ogg'))]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(path, x)), reverse=True)

            for f in files:
                mtime = os.path.getmtime(os.path.join(path, f))
                date_str = time.strftime('%d.%m.%Y %H:%M', time.localtime(mtime))
                ext = f.split('.')[-1].upper()

                cmd = lambda fp=os.path.join(path, f), fn=f: self._load_saved_file(fp, fn)

                frame = ctk.CTkFrame(self.library_scroll, fg_color="transparent")
                frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=1)
                frame.grid_columnconfigure(1, weight=1)

                ctk.CTkLabel(frame, text=date_str, width=130, anchor="w", text_color="#e2e8f0").pack(side="left", padx=(0, 5))
                ctk.CTkLabel(frame, text=f, width=200, anchor="w", text_color="#e2e8f0").pack(side="left", padx=(0, 5))
                ctk.CTkLabel(frame, text=ext, width=60, anchor="w", text_color=COLOR_TEXT_DIM).pack(side="left")

                def make_hover(widget, cmd):
                    def on_enter(e): 
                        widget.configure(fg_color=COLOR_HOVER)
                    def on_leave(e): 
                        widget.configure(fg_color="transparent")
                    def on_click(e): 
                        cmd()
                    widget.bind("<Enter>", on_enter)
                    widget.bind("<Leave>", on_leave)
                    widget.bind("<Button-1>", on_click)
                    widget.configure(cursor="hand2")
                    widget.bind("<MouseWheel>", self._scroll_library)
                    widget.bind("<Button-4>", self._scroll_library)
                    widget.bind("<Button-5>", self._scroll_library)

                make_hover(frame, cmd)
                for child in frame.winfo_children():
                    make_hover(child, cmd)

                row += 1

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
        
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (dialog.winfo_screenheight() // 2) - (180 // 2)
        dialog.geometry(f"400x180+{x}+{y}")
        
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
        AudioSegment.converter = FFMPEG_PATH
        
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
        self.tray_enabled_var = ctk.BooleanVar(value=True)
        self.skip_exit_confirm = ctk.BooleanVar(value=False)
        self.noise_reduce_var = ctk.BooleanVar(value=False)
        self.autostart_var = ctk.BooleanVar(value=False)
        self.check_disk_space_var = ctk.BooleanVar(value=True)
        self.auto_save_var = ctk.BooleanVar(value=False)
        self.always_on_top_var = ctk.BooleanVar(value=False)
        self.skip_exit_confirm = ctk.BooleanVar(value=False)

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
        self.audio_data = []
        self.current_rec_array = None
        self.active_play_array = None
        self.is_playing = False
        self.is_looping = False
        self.current_frame = 0.0
        
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
        
        self.playback_volume = 1.0
        self.volume_mult = 1.0
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
        self.update_file_list()
        
        # Проверка свободного места на диске при запуске
        self.check_disk_space_on_startup()
        
        if self.tray_enabled_var.get():
            self.start_tray()

        self.setup_hotkeys()
    
    def _normalize_bitrate(self, bitrate_str: str) -> str:
        import re
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

    def _scroll_library(self, event):
        if hasattr(self.library_scroll, "_parent_canvas"):
            canvas = self.library_scroll._parent_canvas
            if hasattr(event, 'delta'):
                scroll_amount = -int(event.delta / 120) * 10 
            else:
                scroll_amount = -3 if event.num == 4 else 3
            canvas.yview_scroll(scroll_amount, "units")
        return "break"

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

        self.main_scroll.pack_forget() 
        self.main_scroll.pack(side="bottom", fill="both", expand=True)

        # ---------------------------------------------------------
        # ВКЛАДКА 1: ДИКТОФОН
        # ---------------------------------------------------------
        ctk.CTkLabel(self.page_rec, text="JB AUDIO RECORDER", font=("Roboto", 22, "bold"), text_color=COLOR_ACCENT).pack(pady=10)

        # --- 1. БЛОК ЗАПИСИ ---
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

        # --- 2. БЛОК АУДИОПЛЕЕРА ---
        self.play_frame = ctk.CTkFrame(self.page_rec, fg_color=COLOR_BLOCK, corner_radius=20)
        self.play_frame.pack(padx=20, pady=5, fill="x")
        
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
        
        self.lbl_play_vol = ctk.CTkLabel(vol_box, text=f"{txt['play_volume']}: 100%", font=("Roboto", 10), text_color=COLOR_TEXT_DIM)
        self.lbl_play_vol.pack(side="top")
        
        self.play_vol_slider = ctk.CTkSlider(vol_box, from_=0.0, to=2.0, width=100, height=16, progress_color=COLOR_ACCENT, command=self._update_play_vol_ui)
        self.play_vol_slider.pack(side="bottom")
        self.play_vol_slider.set(1.0)

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

        # --- 3. БЛОК ПАРАМЕТРОВ ЗАПИСИ ---
        self.set_frame = ctk.CTkFrame(self.page_rec, fg_color=COLOR_BLOCK, corner_radius=20)
        self.set_frame.pack(padx=20, pady=5, fill="x")
        
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
        self.lbl_param_noise, _ = add_param_row(4, "noise", "switch", self.noise_reduce_var)

        # --- 4. БЛОК БИБЛИОТЕКИ ---
        self.folder_frame = ctk.CTkFrame(self.page_rec, fg_color=COLOR_BLOCK, corner_radius=20)
        self.folder_frame.pack(padx=20, pady=(2, 20), fill="x")
        
        self.lbl_lib_title = ctk.CTkLabel(self.folder_frame, text=txt["library_title"], font=("Roboto", 10, "bold"), text_color=COLOR_TEXT_DIM)
        self.lbl_lib_title.pack(pady=(10, 2))
        
        self.library_scroll = ctk.CTkScrollableFrame(
            self.folder_frame,
            height=180,
            fg_color=COLOR_BG,
            corner_radius=6
        )
        self.library_scroll.pack(padx=15, pady=8, fill="both", expand=True)

        if hasattr(self.library_scroll, "_parent_canvas"):
            canvas = self.library_scroll._parent_canvas
            canvas.unbind("<MouseWheel>")
            canvas.unbind("<Button-4>")
            canvas.unbind("<Button-5>")
            canvas.bind("<MouseWheel>", self._scroll_library)
            canvas.bind("<Button-4>", self._scroll_library) 
            canvas.bind("<Button-5>", self._scroll_library)
        
        self.library_scroll.bind("<MouseWheel>", self._scroll_library)
        self.library_scroll.bind("<Button-4>", self._scroll_library)
        self.library_scroll.bind("<Button-5>", self._scroll_library)

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

        # ---------------------------------------------------------
        # ВКЛАДКА 2: ПАРАМЕТРЫ
        # ---------------------------------------------------------
        self.lbl_params_title = ctk.CTkLabel(self.page_settings, text=txt["tab_settings"], font=("Roboto", 22, "bold"), text_color=COLOR_ACCENT)
        self.lbl_params_title.pack(pady=20)

        # --- 1. Язык ---
        lang_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
        lang_frame.pack(padx=20, pady=5, fill="x")

        top_row_lang = ctk.CTkFrame(lang_frame, fg_color="transparent")
        top_row_lang.pack(fill="x", padx=20, pady=(15, 5))

        self.lbl_lang_setting = ctk.CTkLabel(top_row_lang, text=txt["language_label"], font=("Roboto", 14))
        self.lbl_lang_setting.pack(side="left")

        self.lang_menu = ctk.CTkOptionMenu(
            top_row_lang,
            values=list(LANGUAGES.keys()),
            variable=self.lang_var,
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

        # --- 2. Устройство записи ---
        dev_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # --- 3. Папка для сохранения ---
        save_path_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # --- 4. Горячие клавиши ---
        hk_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # --- 5. Работать в фоне ---
        tray_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # --- 6. Автозапуск ---
        autostart_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # === Проверка памяти ===
        disk_check_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # --- 7. Автосохранение ---
        auto_save_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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

        # --- 8. Поверх всех окон ---
        aot_frame = ctk.CTkFrame(self.page_settings, fg_color=COLOR_BLOCK, corner_radius=20)
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


        # Кнопка сброса настроек (ссылка self.btn_reset для смены языка)
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
        ).pack(pady=(0, 15))

        desc_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        desc_frame.pack(padx=20, pady=10, fill="x")

        ctk.CTkLabel(
            desc_frame,
            text=txt["about_description"],
            font=("Roboto", 15),
            text_color="#e2e8f0",
            wraplength=480,
            justify="left"
        ).pack(padx=20, pady=15)

        links_frame = ctk.CTkFrame(self.page_about, fg_color=COLOR_BLOCK, corner_radius=20)
        links_frame.pack(padx=20, pady=10, fill="x")

        link_row = ctk.CTkFrame(links_frame, fg_color="transparent")
        link_row.pack(pady=(12, 4))

        ctk.CTkLabel(
            link_row,
            text=txt["about_links"],
            font=("Roboto", 13, "bold"),
            text_color=COLOR_ACCENT
        ).pack(side="left")

        github_link = ctk.CTkLabel(
            link_row,
            text="jeffbennington/JB-Audio-Recorder",  # Текст ссылки
            font=("Roboto", 13, "underline"),  # Подчёркнутый
            text_color=COLOR_ACCENT,  # Синий
            cursor="hand2"  # Курсор-рука
        )
        github_link.pack(side="left", padx=(5, 0))
        github_link.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://github.com/jeffbennington/JB-Audio-Recorder"))

        tg_row = ctk.CTkFrame(links_frame, fg_color="transparent")
        tg_row.pack(pady=(4, 12))
        ctk.CTkLabel(tg_row, text="Telegram:", font=("Roboto", 13, "bold"), text_color=COLOR_ACCENT).pack(side="left")
        _tg = ctk.CTkLabel(tg_row, text="@jbprogramms", font=("Roboto", 13, "underline"), text_color=COLOR_ACCENT, cursor="hand2")
        _tg.pack(side="left", padx=(5, 0))
        _tg.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://t.me/jbprogramms"))

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

    def show_page(self, page_name):
        self.page_rec.pack_forget()
        self.page_settings.pack_forget()
        self.page_about.pack_forget()

        self.btn_nav_rec.configure(fg_color=COLOR_BLOCK)
        self.btn_nav_set.configure(fg_color=COLOR_BLOCK)
        self.btn_nav_about.configure(fg_color=COLOR_BLOCK)

        if page_name == "rec":
            self.page_rec.pack(fill="both", expand=True)
            self.btn_nav_rec.configure(fg_color=COLOR_ACCENT)
        elif page_name == "settings":
            self.page_settings.pack(fill="both", expand=True)
            self.btn_nav_set.configure(fg_color=COLOR_ACCENT)
        elif page_name == "about":
            self.page_about.pack(fill="both", expand=True)
            self.btn_nav_about.configure(fg_color=COLOR_ACCENT)

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
            
            self.btn_rec.configure(text=txt["btn_stop"], fg_color="#4a5568")
            self.set_player_enabled(False)
            
        else:
            self.stop_recording()
            
            self.btn_rec.configure(text=txt["btn_record"], fg_color=COLOR_RED)
            has_content = self.current_rec_array is not None or self.active_play_array is not None
            self.set_player_enabled(has_content)

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
        total_sec = len(self.active_play_array) / int(self.sample_rate_var.get().split()[0])
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
            
            if self.noise_reduce_var.get():
                try:
                    fs = int(self.sample_rate_var.get().split()[0])
                    cleaned = nr.reduce_noise(
                        y=data.flatten(),
                        sr=fs, 
                        stationary=True,
                        prop_decrease=0.8
                    )
                    data = cleaned.reshape(data.shape)
                except Exception as e:
                    print(f"Ошибка шумоподавления: {e}")
                    pass
            
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
        
        if self.writer_stop_event:
            self.writer_stop_event.set()
        
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=2.0)
        
        if self.temp_rec_file and os.path.exists(self.temp_rec_file):
            try:
                data, samplerate = sf.read(self.temp_rec_file, dtype='float32')
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
                    
            except Exception as e:
                print(f"Ошибка загрузки временного файла: {e}")
                self.lbl_status.configure(text=f"ОШИБКА ЗАГРУЗКИ: {e}", text_color=COLOR_RED)
        
        self.vol_bar.set(0)
        self.lbl_timer.configure(text="00:00:00")
        self.update_file_list()
            
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
                # Быстрый путь - прямое сохранение
                sf.write(full_path, self.current_rec_array, fs, subtype='PCM_16')
                print(f"Быстрое сохранение {ext.upper()}: {full_path} @ {fs} Hz")
                
                self.after(0, lambda: self._on_save_complete(True, txt, None))
                return
            
            # === ДЛЯ MP3/OGG: ИСПОЛЬЗУЕМ ВРЕМЕННЫЙ ФАЙЛ КАК ИСТОЧНИК ===
            if not self.temp_rec_file or not os.path.exists(self.temp_rec_file):
                raise Exception("Temporary recording file not found")
            
            clean_bitrate = self._normalize_bitrate(self.bitrate_var.get())
            duration = len(self.current_rec_array) / fs
            
            # ПРАВИЛЬНОЕ РЕШЕНИЕ: Читаем stderr в отдельном потоке!
            process = subprocess.Popen([
                FFMPEG_PATH, '-y',
                '-i', self.temp_rec_file,
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
            
            self.after(0, lambda: self._on_save_complete(True, txt, None))
            
        except Exception as e:
            self.after(0, lambda: self._on_save_complete(False, txt, e))
            
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
            self.btn_pause.configure(text=txt["btn_resume"], fg_color=COLOR_GREEN, hover_color="#48bb78")
            self.lbl_status.configure(text=txt["status_paused"], text_color=COLOR_YELLOW)
        else:
            self.btn_pause.configure(text=txt["btn_pause"], fg_color="#4a5568", hover_color="#718096")
            self.lbl_status.configure(text=txt["status_recording"], text_color=COLOR_RED)

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
            
            # Запускаем запись
            self.after(0, self.start_recording)
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
    app = JBAudioRecorder(); app.mainloop()