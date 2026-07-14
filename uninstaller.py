import os
import sys
import shutil
import subprocess
import threading
import time
import queue
import tkinter as tk
from tkinter import messagebox
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

# Directory the uninstaller executable itself lives in.
# The uninstaller is expected to be shipped inside the app's install folder,
# so this doubles as "the folder we are going to remove".
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

# Brand palette - kept identical to the installer for a consistent look
HEADER_BG = "#1F3B57"
HEADER_FG = "#FFFFFF"
HEADER_SUBTLE_FG = "#B7C6D6"
BODY_BG = "#FFFFFF"
CARD_BG = "#F5F7FA"


class HisabWinUninstaller(tb.Window):
    def __init__(self):
        super().__init__(
            title="HisabWin Uninstaller",
            themename="flatly",
            size=(640, 480),
            resizable=(False, False)
        )

        self.center_window()

        icon_path = resource_path("logo.ico")
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)

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
        self.install_dir = app_dir()
        self.remove_user_data = tk.BooleanVar(value=False)

        # Thread communication
        self.queue = queue.Queue()
        self.is_uninstalling = False

        # Container for pages
        self.container = tb.Frame(self)
        self.container.pack(fill=BOTH, expand=YES)

        self.pages = {}
        for PageClass in (ConfirmPage, ProgressPage, FinishPage):
            page_name = PageClass.__name__
            page = PageClass(parent=self.container, controller=self)
            self.pages[page_name] = page
            page.grid(row=0, column=0, sticky="nsew")

        self.show_page("ConfirmPage")

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

    def start_uninstallation(self):
        self.show_page("ProgressPage")
        self.is_uninstalling = True

        threading.Thread(target=self.uninstall_thread_func, daemon=True).start()
        self.after(100, self.check_queue)

    def uninstall_thread_func(self):
        try:
            # 1. Remove shortcuts
            self.queue.put(("status", "Menghapus pintasan (shortcut)..."))
            self.remove_shortcut(os.path.join(os.path.expanduser("~"), "Desktop", "HisabWin.lnk"))

            start_menu_dir = os.path.join(
                os.environ.get("APPDATA", ""),
                "Microsoft", "Windows", "Start Menu", "Programs"
            )
            self.remove_shortcut(os.path.join(start_menu_dir, "HisabWin.lnk"))

            # 2. Remove user data / settings, if requested
            if self.remove_user_data.get():
                self.queue.put(("status", "Menghapus data pengguna dan pengaturan..."))
                appdata_dir = os.path.join(os.environ.get("APPDATA", ""), "HisabWin")
                localappdata_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "HisabWin")
                for data_dir in (appdata_dir, localappdata_dir):
                    if data_dir and os.path.exists(data_dir) and os.path.normcase(data_dir) != os.path.normcase(self.install_dir):
                        shutil.rmtree(data_dir, ignore_errors=True)

            # 3. Remove the install directory itself.
            # The uninstaller executable lives inside this same folder and is
            # currently running, so it cannot delete itself or its own folder
            # directly. Instead we schedule the removal via a small detached
            # helper script that waits for this process to exit first.
            self.queue.put(("status", "Menjadwalkan penghapusan folder instalasi..."))
            time.sleep(0.5)  # Micro-delay for smooth transition

            self.queue.put(("success", self.install_dir))

        except Exception as e:
            self.queue.put(("error", str(e)))

    def remove_shortcut(self, lnk_path):
        try:
            if os.path.exists(lnk_path):
                os.remove(lnk_path)
        except Exception as e:
            # Don't let a shortcut removal failure crash the uninstaller
            print(f"Warning: Failed to remove shortcut at {lnk_path}: {e}")

    def check_queue(self):
        try:
            while True:
                msg_type, content = self.queue.get_nowait()
                if msg_type == "status":
                    self.pages["ProgressPage"].status_label.config(text=content)
                elif msg_type == "error":
                    self.is_uninstalling = False
                    messagebox.showerror("Uninstal Gagal", f"Terjadi kesalahan saat menghapus aplikasi:\n{content}")
                    self.show_page("ConfirmPage")
                    return
                elif msg_type == "success":
                    self.is_uninstalling = False
                    self.show_page("FinishPage")
                    return
        except queue.Empty:
            pass

        if self.is_uninstalling:
            self.after(100, self.check_queue)

    def finalize(self):
        """Close the app and clean up the install folder in the background.

        Because the running executable sits inside install_dir, deletion is
        deferred to a small detached script that waits for this process to
        fully exit before removing the folder.
        """
        install_dir = os.path.normpath(self.install_dir)
        try:
            if os.name == "nt":
                self._schedule_windows_cleanup(install_dir)
            else:
                self._schedule_posix_cleanup(install_dir)
        except Exception as e:
            print(f"Warning: Failed to schedule cleanup of {install_dir}: {e}")
        self.destroy()

    def _schedule_windows_cleanup(self, install_dir):
        bat_path = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "hisabwin_cleanup.bat")
        pid = os.getpid()
        bat_content = (
            "@echo off\r\n"
            f":wait\r\n"
            f"tasklist /FI \"PID eq {pid}\" 2>NUL | find \"{pid}\" >NUL\r\n"
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >NUL\r\n"
            "  goto wait\r\n"
            ")\r\n"
            f"rmdir /s /q \"{install_dir}\"\r\n"
            "del \"%~f0\"\r\n"
        )
        with open(bat_path, "w") as f:
            f.write(bat_content)

        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )

    def _schedule_posix_cleanup(self, install_dir):
        pid = os.getpid()
        sh_script = (
            "#!/bin/sh\n"
            f"while kill -0 {pid} 2>/dev/null; do sleep 1; done\n"
            f"rm -rf \"{install_dir}\"\n"
        )
        sh_path = os.path.join("/tmp", "hisabwin_cleanup.sh")
        with open(sh_path, "w") as f:
            f.write(sh_script)
        os.chmod(sh_path, 0o755)
        subprocess.Popen(["/bin/sh", sh_path], start_new_session=True)


class ConfirmPage(tb.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        controller.build_header(
            self,
            "Uninstal HisabWin",
            subtitle="Wizard penghapusan aplikasi",
        )

        body = tb.Frame(self, padding=(25, 20))
        body.pack(fill=BOTH, expand=YES)

        desc_text = (
            "Anda akan menghapus HisabWin dari komputer ini.\n\n"
            f"Folder instalasi berikut beserta seluruh isinya akan dihapus:\n"
            f"{controller.install_dir}\n\n"
            "Pintasan di Desktop dan Start Menu juga akan dihapus. "
            "Klik \"Uninstal\" untuk melanjutkan."
        )
        desc_label = tb.Label(
            body,
            text=desc_text,
            font=("Segoe UI", 10),
            justify=LEFT,
            wraplength=560,
        )
        desc_label.pack(anchor="w", pady=(0, 18))

        options_card = tb.Labelframe(body, text="Opsi Tambahan", padding=15, bootstyle="secondary")
        options_card.pack(fill=X, pady=(0, 15))

        chk_userdata = tb.Checkbutton(
            options_card,
            text="Hapus juga data pengguna dan pengaturan aplikasi",
            variable=self.controller.remove_user_data,
            bootstyle="danger-round-toggle",
        )
        chk_userdata.pack(anchor="w", pady=4)

        warn_label = tb.Label(
            body,
            text="Tindakan ini tidak dapat dibatalkan setelah proses dimulai.",
            font=("Segoe UI", 9, "italic"),
            bootstyle="danger",
        )
        warn_label.pack(anchor="w", pady=(4, 0))

        footer = tb.Frame(self, padding=(25, 12))
        footer.pack(fill=X, side=BOTTOM)

        sep = tb.Separator(self, orient=HORIZONTAL)
        sep.pack(fill=X, side=BOTTOM)

        btn_next = tb.Button(footer, text="Uninstal", bootstyle="danger", command=self.controller.start_uninstallation, width=12)
        btn_next.pack(side=RIGHT, padx=(8, 0))

        btn_cancel = tb.Button(footer, text="Batal", bootstyle="secondary-outline", command=self.controller.destroy, width=12)
        btn_cancel.pack(side=RIGHT)


class ProgressPage(tb.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        controller.build_header(
            self,
            "Menghapus HisabWin",
            subtitle="Mohon tunggu, proses ini hanya sebentar",
        )

        self.body = tb.Frame(self, padding=(25, 30))
        self.body.pack(fill=BOTH, expand=YES)

        self.status_label = tb.Label(
            self.body,
            text="Mempersiapkan proses uninstal...",
            font=("Segoe UI", 10),
            justify=LEFT,
            wraplength=560,
        )
        self.status_label.pack(anchor="w", pady=(0, 12))

        self.progress_bar = tb.Progressbar(
            self.body,
            mode="indeterminate",
            bootstyle="danger-striped"
        )
        self.progress_bar.pack(fill=X, pady=(0, 20))

        self.footer = tb.Frame(self, padding=(25, 12))
        self.footer.pack(fill=X, side=BOTTOM)

        sep = tb.Separator(self, orient=HORIZONTAL)
        sep.pack(fill=X, side=BOTTOM)

        self.btn_next = tb.Button(self.footer, text="Lanjut", bootstyle="danger", state=DISABLED, width=12)
        self.btn_next.pack(side=RIGHT, padx=(8, 0))

        self.btn_cancel = tb.Button(self.footer, text="Batal", bootstyle="secondary-outline", state=DISABLED, width=12)
        self.btn_cancel.pack(side=RIGHT)

    def on_show(self):
        self.progress_bar.start(10)


class FinishPage(tb.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        controller.build_header(
            self,
            "Uninstal Selesai",
            subtitle="HisabWin telah dihapus",
        )

        body = tb.Frame(self, padding=(25, 30))
        body.pack(fill=BOTH, expand=YES)

        desc_text = (
            "HisabWin telah berhasil dihapus dari komputer Anda.\n\n"
            "Folder instalasi akan dibersihkan sepenuhnya setelah wizard ini "
            "ditutup. Terima kasih telah menggunakan HisabWin."
        )
        desc_label = tb.Label(
            body,
            text=desc_text,
            font=("Segoe UI", 10),
            justify=LEFT,
            wraplength=560,
        )
        desc_label.pack(anchor="w", pady=(0, 20))

        footer = tb.Frame(self, padding=(25, 12))
        footer.pack(fill=X, side=BOTTOM)

        sep = tb.Separator(self, orient=HORIZONTAL)
        sep.pack(fill=X, side=BOTTOM)

        btn_finish = tb.Button(footer, text="Selesai", bootstyle="danger", command=self.controller.finalize, width=12)
        btn_finish.pack(side=RIGHT)


if __name__ == "__main__":
    app = HisabWinUninstaller()
    app.mainloop()
