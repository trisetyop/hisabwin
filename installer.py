import os
import sys
import subprocess
import threading
import time
import queue
import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk

# Helper to find resources inside PyInstaller bundle
def resource_path(relative_path):
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Brand palette - used to keep every header/text combo readable and consistent
HEADER_BG = "#1F3B57"       # dark navy, matches the logo's blue tone
HEADER_FG = "#FFFFFF"       # title text on the header
HEADER_SUBTLE_FG = "#B7C6D6"  # secondary text on the header
BODY_BG = "#FFFFFF"
CARD_BG = "#F5F7FA"

class HisabWinInstaller(tb.Window):
    def __init__(self):
        super().__init__(
            title="HisabWin Installer",
            themename="flatly",
            size=(640, 480),
            resizable=(False, False)
        )
        
        # Center the window
        self.center_window()
        
        # Set icon
        icon_path = resource_path("logo.ico")
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)

        # Custom styles for the header bar. Using an explicit hex background/
        # foreground here (instead of the generic "secondary"/"light" bootstyle
        # keywords) avoids the low-contrast/"washed out" title text that those
        # keywords produce when combined on a colored frame.
        self.style.configure("Header.TFrame", background=HEADER_BG)
        self.style.configure(
            "HeaderTitle.TLabel",
            background=HEADER_BG,
            foreground=HEADER_FG,
            font=("Segoe UI", 15, "bold"),
        )
        self.style.configure(
            "HeaderSubtitle.TLabel",
            background=HEADER_BG,
            foreground=HEADER_SUBTLE_FG,
            font=("Segoe UI", 9),
        )

        # Load the logo once and share it across every page's header
        self.logo_img = None
        logo_path = resource_path("logo.png")
        if os.path.exists(logo_path):
            try:
                img = Image.open(logo_path).convert("RGBA")
                img = img.resize((48, 48), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(img)
            except Exception as e:
                print(f"Image load warning: {e}")

        # Variables
        default_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", "C:\\"),
            "Programs",
            "HisabWin"
        )
        self.install_dir = tk.StringVar(value=default_dir)
        self.create_desktop_lnk = tk.BooleanVar(value=True)
        self.create_start_lnk = tk.BooleanVar(value=True)
        self.run_after_install = tk.BooleanVar(value=True)
        
        # Thread communication
        self.queue = queue.Queue()
        self.is_installing = False
        
        # Container for pages
        self.container = tb.Frame(self)
        self.container.pack(fill=BOTH, expand=YES)
        
        # Initialize pages
        self.pages = {}
        for PageClass in (WelcomePage, ProgressPage, FinishPage):
            page_name = PageClass.__name__
            page = PageClass(parent=self.container, controller=self)
            self.pages[page_name] = page
            page.grid(row=0, column=0, sticky="nsew")
            
        # Show first page
        self.show_page("WelcomePage")
        
    def build_header(self, parent, title, subtitle=None):
        """Create a consistent branded header bar (logo + title) for a page."""
        header = tb.Frame(parent, style="Header.TFrame")
        header.pack(fill=X, side=TOP)

        inner = tb.Frame(header, style="Header.TFrame")
        inner.pack(fill=X, padx=25, pady=16)

        if self.logo_img:
            logo_label = tk.Label(inner, image=self.logo_img, bg=HEADER_BG, bd=0)
            logo_label.pack(side=LEFT, padx=(0, 15))

        text_frame = tb.Frame(inner, style="Header.TFrame")
        text_frame.pack(side=LEFT, fill=X, expand=YES)

        tb.Label(text_frame, text=title, style="HeaderTitle.TLabel").pack(anchor="w")
        if subtitle:
            tb.Label(text_frame, text=subtitle, style="HeaderSubtitle.TLabel").pack(anchor="w", pady=(3, 0))

        return header

    def center_window(self):
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"+{x}+{y}")
        
    def show_page(self, page_name):
        page = self.pages[page_name]
        page.tkraise()
        if hasattr(page, "on_show"):
            page.on_show()

    def start_installation(self):
        target = self.install_dir.get().strip()
        if not target:
            messagebox.showerror("Error", "Folder instalasi tidak boleh kosong!")
            return
            
        # Standardize target folder path
        # If it doesn't end with "HisabWin", append it to prevent mess
        if not target.endswith("HisabWin"):
            target = os.path.join(target, "HisabWin")
            self.install_dir.set(target)
            
        self.show_page("ProgressPage")
        self.is_installing = True
        
        # Run installation in background thread
        threading.Thread(target=self.install_thread_func, args=(target,), daemon=True).start()
        
        # Start checking queue for UI updates
        self.after(100, self.check_queue)
        
    def install_thread_func(self, target_dir):
        try:
            # 1. Create directory if not exists
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)
                
            parent_dir = os.path.dirname(target_dir)
            
            # Paths to 7z resources
            exe_7z = resource_path("7z.exe")
            archive_7z = resource_path("HisabWin.7z")
            
            if not os.path.exists(exe_7z) or not os.path.exists(archive_7z):
                raise FileNotFoundError("File installer (7z.exe / HisabWin.7z) tidak ditemukan di dalam paket!")
                
            self.queue.put(("status", "Mengekstrak file aplikasi (ini membutuhkan beberapa detik)..."))
            
            # Run extraction
            # 7z.exe command: x (extract with paths), -y (assume yes to overwrite)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE  # Hide console window
            
            proc = subprocess.Popen(
                [exe_7z, "x", "-y", f"-o{parent_dir}", archive_7z],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                text=True
            )
            
            _, stderr = proc.communicate()
            
            if proc.returncode != 0:
                raise RuntimeError(f"Proses ekstraksi gagal dengan kode {proc.returncode}. Error: {stderr}")
                
            # Extra verification: Make sure HisabWin.exe exists in the target dir
            exe_path = os.path.join(target_dir, "HisabWin.exe")
            if not os.path.exists(exe_path):
                raise FileNotFoundError(f"Hasil ekstraksi tidak lengkap. HisabWin.exe tidak ditemukan di {target_dir}")
                
            time.sleep(0.5)  # Micro-delay for smooth transition
            self.queue.put(("success", target_dir))
            
        except Exception as e:
            self.queue.put(("error", str(e)))

    def check_queue(self):
        try:
            while True:
                msg_type, content = self.queue.get_nowait()
                if msg_type == "status":
                    self.pages["ProgressPage"].status_label.config(text=content)
                elif msg_type == "error":
                    self.is_installing = False
                    messagebox.showerror("Instalasi Gagal", f"Terjadi kesalahan saat menginstal:\n{content}")
                    self.show_page("WelcomePage")
                    return
                elif msg_type == "success":
                    self.is_installing = False
                    self.post_installation(content)
                    self.show_page("FinishPage")
                    return
        except queue.Empty:
            pass
            
        if self.is_installing:
            self.after(100, self.check_queue)
            
    def post_installation(self, target_dir):
        exe_path = os.path.join(target_dir, "HisabWin.exe")
        icon_path = os.path.join(target_dir, "logo.ico")
        if not os.path.exists(icon_path):
            icon_path = None # Fallback if logo.ico is missing
            
        # Create Desktop Shortcut
        if self.create_desktop_lnk.get():
            desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            lnk_path = os.path.join(desktop_dir, "HisabWin.lnk")
            self.create_shortcut(exe_path, lnk_path, icon_path, "Peta Visibilitas Hilal HisabWin")
            
        # Create Start Menu Shortcut
        if self.create_start_lnk.get():
            start_menu_dir = os.path.join(
                os.environ.get("APPDATA", ""),
                "Microsoft", "Windows", "Start Menu", "Programs"
            )
            if os.path.exists(start_menu_dir):
                lnk_path = os.path.join(start_menu_dir, "HisabWin.lnk")
                self.create_shortcut(exe_path, lnk_path, icon_path, "Peta Visibilitas Hilal HisabWin")

    def create_shortcut(self, target, lnk_path, icon=None, desc=""):
        try:
            target = os.path.normpath(target)
            lnk_path = os.path.normpath(lnk_path)
            work_dir = os.path.dirname(target)
            
            ps_script = f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk_path}'); "
            ps_script += f"$s.TargetPath = '{target}'; "
            ps_script += f"$s.WorkingDirectory = '{work_dir}'; "
            if icon:
                icon = os.path.normpath(icon)
                ps_script += f"$s.IconLocation = '{icon}'; "
            if desc:
                ps_script += f"$s.Description = '{desc}'; "
            ps_script += "$s.Save();"
            
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                check=True
            )
        except Exception as e:
            # We don't want shortcut errors to crash the installer, just log it
            print(f"Warning: Failed to create shortcut at {lnk_path}: {e}")

    def finalize(self):
        if self.run_after_install.get():
            target_dir = self.install_dir.get()
            exe_path = os.path.join(target_dir, "HisabWin.exe")
            if os.path.exists(exe_path):
                try:
                    subprocess.Popen([exe_path], cwd=target_dir)
                except Exception as e:
                    messagebox.showwarning("Peringatan", f"Gagal menjalankan aplikasi: {e}")
        self.destroy()

class WelcomePage(tb.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        # Header Area with brand colors (shared helper keeps every page consistent)
        controller.build_header(
            self,
            "Instalasi HisabWin",
            subtitle="Wizard pemasangan aplikasi",
        )

        # Main Body
        body = tb.Frame(self, padding=(25, 20))
        body.pack(fill=BOTH, expand=YES)

        desc_text = (
            "Selamat datang di Wizard Instalasi HisabWin. Aplikasi ini adalah peta "
            "visibilitas hilal interaktif yang menggunakan kriteria MABIMS dan KHGT "
            "Muhammadiyah.\n\nSilakan pilih folder tujuan dan opsi pintasan di bawah "
            "ini, lalu klik \"Instal\" untuk melanjutkan."
        )
        desc_label = tb.Label(
            body,
            text=desc_text,
            font=("Segoe UI", 10),
            justify=LEFT,
            wraplength=560,
        )
        desc_label.pack(anchor="w", pady=(0, 18))

        # Path Selection card
        path_card = tb.Labelframe(body, text="Folder Tujuan Instalasi", padding=15, bootstyle="secondary")
        path_card.pack(fill=X, pady=(0, 15))

        path_select_frame = tb.Frame(path_card)
        path_select_frame.pack(fill=X)

        path_entry = tb.Entry(path_select_frame, textvariable=self.controller.install_dir, font=("Segoe UI", 9))
        path_entry.pack(side=LEFT, fill=X, expand=YES, padx=(0, 10), ipady=3)

        browse_btn = tb.Button(path_select_frame, text="Telusuri...", bootstyle="outline-secondary", command=self.browse_folder)
        browse_btn.pack(side=RIGHT)

        # Shortcut Options card
        options_card = tb.Labelframe(body, text="Opsi Pintasan", padding=15, bootstyle="secondary")
        options_card.pack(fill=X, pady=(0, 15))

        chk_desktop = tb.Checkbutton(
            options_card,
            text="Buat Pintasan di Desktop (Desktop Shortcut)",
            variable=self.controller.create_desktop_lnk,
            bootstyle="success-round-toggle",
        )
        chk_desktop.pack(anchor="w", pady=4)

        chk_start = tb.Checkbutton(
            options_card,
            text="Buat Pintasan di Start Menu",
            variable=self.controller.create_start_lnk,
            bootstyle="success-round-toggle",
        )
        chk_start.pack(anchor="w", pady=4)

        # Footer Action Buttons
        footer = tb.Frame(self, padding=(25, 12))
        footer.pack(fill=X, side=BOTTOM)

        # Separator line above footer
        sep = tb.Separator(self, orient=HORIZONTAL)
        sep.pack(fill=X, side=BOTTOM)

        btn_next = tb.Button(footer, text="Instal", bootstyle="success", command=self.controller.start_installation, width=12)
        btn_next.pack(side=RIGHT, padx=(8, 0))

        btn_cancel = tb.Button(footer, text="Batal", bootstyle="secondary-outline", command=self.controller.destroy, width=12)
        btn_cancel.pack(side=RIGHT)
        
    def browse_folder(self):
        folder = filedialog.askdirectory(
            initialdir=self.controller.install_dir.get(),
            title="Pilih Folder Instalasi"
        )
        if folder:
            # Normalize path slashes for Windows
            folder = os.path.normpath(folder)
            self.controller.install_dir.set(folder)

class ProgressPage(tb.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        # Header
        controller.build_header(
            self,
            "Menginstal HisabWin",
            subtitle="Mohon tunggu, proses ini hanya sebentar",
        )

        # Body
        self.body = tb.Frame(self, padding=(25, 30))
        self.body.pack(fill=BOTH, expand=YES)

        self.status_label = tb.Label(
            self.body,
            text="Mempersiapkan proses instalasi...",
            font=("Segoe UI", 10),
            justify=LEFT,
            wraplength=560,
        )
        self.status_label.pack(anchor="w", pady=(0, 12))

        self.progress_bar = tb.Progressbar(
            self.body,
            mode="indeterminate",
            bootstyle="success-striped"
        )
        self.progress_bar.pack(fill=X, pady=(0, 20))

        # Action Buttons (Disabled during installation)
        self.footer = tb.Frame(self, padding=(25, 12))
        self.footer.pack(fill=X, side=BOTTOM)

        sep = tb.Separator(self, orient=HORIZONTAL)
        sep.pack(fill=X, side=BOTTOM)

        self.btn_next = tb.Button(self.footer, text="Lanjut", bootstyle="success", state=DISABLED, width=12)
        self.btn_next.pack(side=RIGHT, padx=(8, 0))

        self.btn_cancel = tb.Button(self.footer, text="Batal", bootstyle="secondary-outline", state=DISABLED, width=12)
        self.btn_cancel.pack(side=RIGHT)
        
    def on_show(self):
        # Start progress bar animation
        self.progress_bar.start(10)

class FinishPage(tb.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        # Header
        controller.build_header(
            self,
            "Instalasi Selesai",
            subtitle="HisabWin siap digunakan",
        )

        # Body
        body = tb.Frame(self, padding=(25, 30))
        body.pack(fill=BOTH, expand=YES)

        desc_text = (
            "Selamat! HisabWin telah berhasil diinstal pada komputer Anda.\n\n"
            "Pintasan telah dibuat pada lokasi yang Anda pilih. Anda dapat menutup "
            "wizard ini dan langsung menggunakan aplikasi."
        )
        desc_label = tb.Label(
            body,
            text=desc_text,
            font=("Segoe UI", 10),
            justify=LEFT,
            wraplength=560,
        )
        desc_label.pack(anchor="w", pady=(0, 20))

        chk_run = tb.Checkbutton(
            body,
            text="Jalankan HisabWin Sekarang",
            variable=self.controller.run_after_install,
            bootstyle="success-round-toggle",
        )
        chk_run.pack(anchor="w", pady=5)

        # Footer Action Buttons
        footer = tb.Frame(self, padding=(25, 12))
        footer.pack(fill=X, side=BOTTOM)

        sep = tb.Separator(self, orient=HORIZONTAL)
        sep.pack(fill=X, side=BOTTOM)

        btn_finish = tb.Button(footer, text="Selesai", bootstyle="success", command=self.controller.finalize, width=12)
        btn_finish.pack(side=RIGHT)

if __name__ == "__main__":
    app = HisabWinInstaller()
    app.mainloop()
