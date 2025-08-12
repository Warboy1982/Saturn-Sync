import os
import json
import threading
import hashlib
import time
from queue import Queue, Empty
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw, ImageFont, ImageTk

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from CBD_Api import Printer

CONFIG_FILE = "sync_config.json"
METADATA_FILE = "file_metadata.json"
LOG_UNKNOWN_FILE = "unknown_printer_msgs.log"

# Default config values
DEFAULT_CONFIG = {
    "printer_ip": "192.168.0.230",
    "sync_folder": str(Path.home() / "ElegooSaturnSync"),
    "ping_interval_minutes": 1,
    "send_delay": 0.005,
    "log_unknown_messages": False  # Hidden, must edit config file manually
}

# Icon constants (will be created on the fly)
ICON_SIZE = 64
RES_FOLDER = os.path.join(os.path.dirname(__file__), "res")
BASE_ICON_PATH = os.path.join(RES_FOLDER, "printer_base.png")


def load_base_icon():
    try:
        base_icon = Image.open(BASE_ICON_PATH).convert("RGBA")
        base_icon = base_icon.resize((ICON_SIZE, ICON_SIZE))
        return base_icon
    except Exception as e:
        print(f"Failed to load base icon: {e}")
        # Fallback: create a simple placeholder icon
        img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 122, 204, 255))
        return img
        
def set_window_icon(root, pil_image):
    icon_img = pil_image.resize((ICON_SIZE, ICON_SIZE))
    tk_icon = ImageTk.PhotoImage(icon_img)
    root.iconphoto(True, tk_icon)
    root._icon_image = tk_icon
    
def overlay_icon(base_icon, overlay_type):
    # overlay_type: "synced", "syncing", "offline", "error"
    # bottom right corner small badge icon
    badge_size = 20
    icon = base_icon.copy()
    draw = ImageDraw.Draw(icon)
    # coordinates for badge
    x0 = ICON_SIZE - badge_size - 4
    y0 = ICON_SIZE - badge_size - 4
    x1 = x0 + badge_size
    y1 = y0 + badge_size

    if overlay_type == "synced":
        # Green checkmark circle
        draw.ellipse((x0, y0, x1, y1), fill=(0, 200, 0, 255))
        # check mark
        draw.line([(x0+4,y0+10),(x0+9,y1-5),(x1-4,y0+5)], fill="white", width=2)
    elif overlay_type == "syncing":
        # Two circular arrows (static)
        draw.ellipse((x0, y0, x1, y1), outline=(255, 165, 0, 255), width=3)
        # simplified arrows
        draw.polygon([(x0+7,y0+4), (x0+7,y0+10), (x0+4,y0+7)], fill=(255,165,0,255))
        draw.polygon([(x1-7,y1-4), (x1-7,y1-10), (x1-4,y1-7)], fill=(255,165,0,255))
    elif overlay_type == "offline":
        # Red crossed circle
        draw.ellipse((x0, y0, x1, y1), fill=(200, 0, 0, 255))
        draw.line([(x0+4,y0+4),(x1-4,y1-4)], fill="white", width=3)
        draw.line([(x1-4,y0+4),(x0+4,y1-4)], fill="white", width=3)
    elif overlay_type == "error":
        # Yellow triangle with exclamation
        draw.polygon([(x0+badge_size/2,y0+4), (x0+4,y1-4), (x1-4,y1-4)], fill=(255, 204, 0, 255))
        # exclamation mark
        draw.line([(x0+badge_size/2, y0+8), (x0+badge_size/2, y1-8)], fill="black", width=2)
        # small circle for exclamation dot
        dot_radius = 2
        cx = x0 + badge_size/2
        cy = y1 - 6
        draw.ellipse([cx-dot_radius, cy-dot_radius, cx+dot_radius, cy+dot_radius], fill="black")
    return icon

class SyncAgent:
    def __init__(self):
        self.load_config()
        self.load_metadata()

        self.printer = Printer(self.config["printer_ip"])
        self.sync_folder = Path(self.config["sync_folder"])
        self.ping_interval = self.config["ping_interval_minutes"] * 60
        self.printer.send_delay = self.config["send_delay"] * 1.0

        self.log_unknown = self.config.get("log_unknown_messages", False)

        # Ensure sync folder exists
        self.sync_folder.mkdir(parents=True, exist_ok=True)

        self.metadata_lock = threading.Lock()
        self.sync_lock = threading.Lock()

        self.stop_event = threading.Event()

        self.status = "offline"  # offline, syncing, synced, error
        self.error_files = set()
        self.syncing_files = set()
        self.current_printing_file = None

        self.pending_uploads = Queue()
        self.pending_deletions = Queue()

        self.icon_base = load_base_icon()
        self.icon_images = {
            "offline": overlay_icon(self.icon_base, "offline"),
            "syncing": overlay_icon(self.icon_base, "syncing"),
            "synced": overlay_icon(self.icon_base, "synced"),
            "error": overlay_icon(self.icon_base, "error"),
        }

        # Tray icon related
        self.tray_icon = None

        # File watcher setup
        self.event_handler = FolderChangeHandler(self)
        self.observer = Observer()

        # Internal flags
        self.printing_paused = False
        self.manual_sync_requested = False

        # UI references
        self.ui = None

        # Start threads
        self.start()

    def load_config(self):
        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = DEFAULT_CONFIG.copy()
        else:
            self.config = DEFAULT_CONFIG.copy()
            self.save_config()

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)

    def load_metadata(self):
        if os.path.isfile(METADATA_FILE):
            try:
                with open(METADATA_FILE, "r") as f:
                    self.metadata = json.load(f)
            except Exception:
                self.metadata = {}
        else:
            self.metadata = {}

    def save_metadata(self):
        with self.metadata_lock:
            with open(METADATA_FILE, "w") as f:
                json.dump(self.metadata, f, indent=2)

    def log_unknown_message(self, message_bytes):
        if not self.log_unknown:
            return
        try:
            with open(LOG_UNKNOWN_FILE, "a") as f:
                ts = datetime.now().isoformat()
                # Write as hex for readability
                hexdata = message_bytes.hex()
                f.write(f"{ts} - {hexdata}\n")
        except Exception:
            pass  # Fail silently on logging errors

    def run(self):
        # Main thread for syncing and pinging
        next_ping = 0
        while not self.stop_event.is_set():
            now = time.time()
            if self.manual_sync_requested:
                self.manual_sync_requested = False
                if self.status == "offline":
                    if not self.ping_printer():
                        continue
                self.sync_all()

            if now >= next_ping:
                self.ping_and_sync()
                next_ping = now + self.ping_interval

            time.sleep(1)

    def ping_and_sync(self):
        if not self.ping_printer():
            self.update_status("offline")
            return
        self.update_status("synced")  # Assume synced before syncing

        # Check printing status
        try:
            printing_state = self.printer.printingStatus()
            if printing_state == "Printing":
                self.printing_paused = True
                self.update_status("synced")
                return
            else:
                if self.printing_paused:
                    # Printing just ended
                    self.printing_paused = False
        except Exception:
            # Assume not printing if error
            self.printing_paused = False

        # If printing paused, skip sync for now
        if self.printing_paused:
            return

        self.sync_all()

    def ping_printer(self):
        try:
            ver = self.printer.getVer()
            # If getVer succeeds, printer is online
            return True
        except Exception:
            return False

    def sync_all(self):
        with self.sync_lock:
            self.update_status("syncing")

            # Step 1: Read local files metadata
            local_files = self.scan_local_files()

            # Step 2: Read printer files
            try:
                printer_files = dict(self.printer.getCardFiles())
            except Exception:
                printer_files = {}

            # Step 3: Sync deletions - files on printer but not locally
            to_delete = set(printer_files.keys()) - set(local_files.keys())
            for filename in to_delete:
                try:
                    self.printer.removeCardFile(filename)
                    with self.metadata_lock:
                        self.metadata.pop(filename, None)
                except Exception:
                    self.handle_error(f"Failed to delete '{filename}' on printer")

            # Step 4: Sync additions/modifications
            for filename, meta in local_files.items():
                if not filename.lower().endswith('.ctb'):
                    continue  # Skip non-CTB files
                if filename not in printer_files:
                    # New file - upload
                    self.upload_file(filename)
                else:
                    # File exists - check if modified
                    if self.is_file_modified(filename, meta):
                        self.upload_file(filename)

            # Step 5: Purge metadata entries for deleted local files
            local_set = set(local_files.keys())
            with self.metadata_lock:
                for filename in list(self.metadata.keys()):
                    if filename not in local_set:
                        self.metadata.pop(filename)

            self.save_metadata()
            self.update_status("synced")

    def scan_local_files(self):
        # Return dict: filename -> metadata dict {mtime, size, checksum (optional)}
        files_meta = {}
        for entry in self.sync_folder.glob("*.ctb"):
            try:
                stat = entry.stat()
                mtime = stat.st_mtime
                size = stat.st_size
                key = entry.name

                meta = self.metadata.get(key, {})
                checksum = meta.get("checksum")

                # Check if hash needed
                need_hash = False
                if (not checksum) or meta.get("mtime") != mtime or meta.get("size") != size:
                    need_hash = True

                if need_hash:
                    checksum = self.compute_checksum(entry)
                    with self.metadata_lock:
                        self.metadata[key] = {
                            "mtime": mtime,
                            "size": size,
                            "checksum": checksum,
                        }
                files_meta[key] = {"mtime": mtime, "size": size, "checksum": checksum}
            except Exception:
                # Ignore unreadable files
                pass
        return files_meta

    def compute_checksum(self, filepath):
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def is_file_modified(self, filename, local_meta):
        with self.metadata_lock:
            stored_meta = self.metadata.get(filename)
        if not stored_meta:
            return True  # New file for metadata, consider modified
        if local_meta["checksum"] != stored_meta.get("checksum"):
            return True
        if (Path(self.sync_folder) / filename).stat().st_mtime != stored_meta.get("mtime"):
            return True
        return False

    def upload_file(self, filename):
        path = Path(self.sync_folder) / filename
        try:
            # Check printing status before upload
            if self.printer.printingStatus() == "Printing":
                # Defer upload
                self.printing_paused = True
                return

            self.printing_paused = False

            self.syncing_files.add(filename)
            self.update_status("syncing")
            
            stable_duration = 1.0  # seconds
            start = time.time()
            last_size = -1
            stable_start = None

            while time.time() - start < 60:
                try:
                    size = os.path.getsize(path)
                except (OSError, PermissionError):
                    size = -1

                if size == last_size and size != -1:
                    if stable_start is None:
                        stable_start = time.time()
                    elif time.time() - stable_start >= stable_duration:
                        break  # file size stable long enough, assume done writing
                else:
                    last_size = size
                    stable_start = None

                time.sleep(0.1)
            else:
                self.update_status("error")
                return
                
            result = self.printer.uploadFile(str(path), filename)
            if "Error" in result or "Failed" in result or "No Response" in result:
                self.handle_error(f"Upload error: {result}")
            else:
                # Update metadata on successful upload
                stat = path.stat()
                checksum = self.compute_checksum(path)
                with self.metadata_lock:
                    self.metadata[filename] = {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "checksum": checksum,
                    }
                self.save_metadata()
                if filename in self.error_files:
                    self.error_files.remove(filename)
        except Exception as e:
            self.handle_error(f"Upload exception: {e}")
        finally:
            self.syncing_files.discard(filename)
            self.update_status("synced")
            self.ui.refresh_file_list()

    def handle_error(self, message):
        self.error_files.add(message)
        self.update_status("error")
        self.show_balloon("Elegoo Saturn Sync Agent - Error", message)

    def show_balloon(self, title, msg):
        # Platform specific balloon notification via pystray
        if self.tray_icon:
            self.tray_icon.notify(msg, title)

    def update_status(self, new_status):
        if new_status == self.status:
            return
        self.status = new_status
        self.update_tray_icon(new_status)
        self.update_tray_tooltip()

    def update_tray_icon(self, new_status):
        if self.tray_icon:
            icon_image = self.icon_images.get(self.status, self.icon_images[new_status])
            self.tray_icon.icon = icon_image

    def update_tray_tooltip(self):
        if self.tray_icon:
            tooltips = {
                "offline": f"Elegoo Saturn Sync Agent - Offline\nPrinter IP: {self.config['printer_ip']}",
                "syncing": f"Elegoo Saturn Sync Agent - Syncing\nFiles syncing: {len(self.syncing_files)}",
                "synced": f"Elegoo Saturn Sync Agent - Synced\nPrinter IP: {self.config['printer_ip']}",
                "error": f"Elegoo Saturn Sync Agent - Error\nPending errors: {len(self.error_files)}",
            }
            tooltip = tooltips.get(self.status, "Elegoo Saturn Sync Agent")
            self.tray_icon.title = tooltip

    def start(self):
        # Start folder watcher
        self.observer.schedule(self.event_handler, str(self.sync_folder), recursive=False)
        self.observer.start()

        # Start main sync thread
        self.sync_thread = threading.Thread(target=self.run, daemon=True)
        self.sync_thread.start()

        # Start tray icon
        self.setup_tray_icon()

        # Start Tkinter UI on main thread
        self.setup_ui()

    def stop(self):
        self.stop_event.set()
        self.observer.stop()
        self.observer.join()
        if self.tray_icon:
            self.tray_icon.stop()
        if self.ui:
            self.ui.root.quit()

    def manual_sync(self):
        self.manual_sync_requested = True

    def setup_tray_icon(self):
        menu = (
            item("Open UI", self.show_ui),
            item("Sync Now", lambda _: self.manual_sync()),
            item("Exit", lambda _: self.stop()),
        )
        self.tray_icon = pystray.Icon("ElegooSaturnSync", self.icon_images["offline"], "Elegoo Saturn Sync Agent", menu)
        self.tray_icon.run_detached()
        self.tray_icon.visible = True

    def show_ui(self, _=None):
        if self.ui:
            self.ui.show_window()

    def setup_ui(self):
        self.ui = SyncUI(self)
        self.ui.run()

class FolderChangeHandler(FileSystemEventHandler):
    def __init__(self, agent):
        self.agent = agent

    def on_any_event(self, event):
        if event.is_directory:
            return
        if not event.src_path.lower().endswith(".ctb"):
            return
        # Trigger manual sync due to folder change
        if self.agent.ui:
            self.agent.ui.refresh_file_list()
        self.agent.manual_sync()

    
class SyncUI:        
    def __init__(self, agent):
        self.agent = agent
        self.root = tk.Tk()
        set_window_icon(self.root, load_base_icon())
        self.root.title("Elegoo Saturn Sync Agent")
        self.root.geometry("600x400")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        # File list UI
        self.file_listbox = tk.Listbox(self.root, selectmode=tk.SINGLE)
        self.file_listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Buttons
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        self.btn_refresh = tk.Button(btn_frame, text="Refresh File List", command=self.refresh_file_list)

        self.btn_print = tk.Button(btn_frame, text="Print Selected File", command=self.print_selected_file)

        self.btn_open_folder = tk.Button(btn_frame, text="Open Sync Folder", command=self.open_folder)

        speed_frame = tk.Frame(self.root)

        self.slider_pos = tk.DoubleVar(value=self.agent.config["send_delay"])
        self.speed_slider = tk.Scale(speed_frame, from_=0.0, to=0.2, resolution=0.001, orient=tk.HORIZONTAL, label="Send Delay (sec)", length=200, showvalue=False, variable=self.slider_pos)
        self.speed_slider.pack(side=tk.LEFT)

        self.delay_entry = tk.Entry(speed_frame, width=6)
        self.delay_entry.insert(0, f"{self.agent.config['send_delay']}")
        self.delay_entry.pack(side=tk.LEFT, padx=(5, 0))

        self.btn_sync_now = tk.Button(btn_frame, text="Manual Sync Now", command=self.agent.manual_sync)

        self.btn_refresh.pack(side=tk.LEFT, padx=5)
        self.btn_print.pack(side=tk.LEFT, padx=5)
        self.btn_sync_now.pack(side=tk.RIGHT, padx=5)
        self.btn_open_folder.pack(side=tk.RIGHT, padx=5)
        speed_frame.pack(side=tk.LEFT, padx=5, pady=5)
        def update_from_slider(value):
            value = float(value)
            self.delay_entry.delete(0, tk.END)
            self.delay_entry.insert(0, f"{value:.3f}")
            self.agent.printer.send_delay = value
            self.agent.config["send_delay"] = value
            self.agent.save_config()

        def update_from_entry(event):
            try:
                value = float(self.delay_entry.get())
                value = max(0.0, min(0.2, value))
                self.slider_pos.set(value)
                self.agent.printer.send_delay = value
                self.agent.config["send_delay"] = value
                self.agent.save_config()
            except ValueError:
                pass

        self.speed_slider.config(command=update_from_slider)
        self.delay_entry.bind("<Return>", update_from_entry)
        self.delay_entry.bind("<FocusOut>", update_from_entry)

        # Config menu
        menubar = tk.Menu(self.root)
        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="Change Sync Folder", command=self.change_sync_folder)
        config_menu.add_command(label="Change Printer IP", command=self.change_printer_ip)
        config_menu.add_command(label="Set Ping Interval", command=self.set_ping_interval)
        menubar.add_cascade(label="Config", menu=config_menu)
        self.root.config(menu=menubar)

        self.refresh_file_list()

    def open_folder(self):
        os.startfile(Path(self.agent.sync_folder))
    def run(self):
        self.root.mainloop()

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.refresh_file_list()

    def refresh_file_list(self):
        self.file_listbox.delete(0, tk.END)
        sync_folder = self.agent.sync_folder
        metadata = self.agent.metadata
        compute_checksum = self.agent.compute_checksum

        # Get local .ctb files
        local_files = sorted([f.name for f in sync_folder.glob("*.ctb")])

        for filename in local_files:
            # Determine if file is synced
            synced = False
            meta = metadata.get(filename)
            file_path = sync_folder / filename
            if meta:
                try:
                    stat = file_path.stat()
                    checksum = compute_checksum(file_path)
                    if (
                        stat.st_size == meta.get("size") and
                        abs(stat.st_mtime - meta.get("mtime", 0)) < 1 and
                        checksum == meta.get("checksum") and
                        filename not in self.agent.syncing_files
                    ):
                        synced = True
                except Exception:
                    synced = False

            display_text = filename
            if synced:
                display_text = "✔ " + display_text
            else:
                display_text = "     " + display_text
            self.file_listbox.insert(tk.END, display_text)

    def print_selected_file(self):
        sel = self.file_listbox.curselection()
        if not sel:
            messagebox.showwarning("No selection", "Please select a file to print.")
            return
        filename = self.file_listbox.get(sel[0])
        if not filename.startswith("✔ "):
            return
        filename = filename[2:]
        # Confirm
        if not messagebox.askyesno("Confirm Print", f"Send print command for '{filename}'?"):
            return
        try:
            # Check if printer busy
            status = self.agent.printer.printingStatus()
            if status == "Printing":
                messagebox.showwarning("Printer Busy", "Printer is currently printing. Cannot start new print.")
                return
            result = self.agent.printer.startPrinting(filename)
            if "Error" in result:
                messagebox.showerror("Print Error", f"Failed to start print:\n{result}")
            else:
                messagebox.showinfo("Print Started", f"Print job for '{filename}' started successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send print command:\n{e}")

    def change_sync_folder(self):
        folder = filedialog.askdirectory(initialdir=self.agent.sync_folder)
        if folder:
            self.agent.sync_folder = Path(folder)
            self.agent.config["sync_folder"] = folder
            self.agent.save_config()
            messagebox.showinfo("Sync Folder Changed", f"Sync folder changed to:\n{folder}")

    def change_printer_ip(self):
        ip = tk.simpledialog.askstring("Printer IP", "Enter printer IP address:", initialvalue=self.agent.config["printer_ip"])
        if ip:
            self.agent.printer.ip = ip
            self.agent.config["printer_ip"] = ip
            self.agent.save_config()
            messagebox.showinfo("Printer IP Changed", f"Printer IP changed to: {ip}")

    def set_ping_interval(self):
        val = tk.simpledialog.askinteger("Ping Interval", "Enter ping interval in minutes (0 to disable):",
                                         initialvalue=self.agent.ping_interval // 60, minvalue=0)
        if val is not None:
            self.agent.ping_interval = val * 60
            self.agent.config["ping_interval_minutes"] = val
            self.agent.save_config()
            messagebox.showinfo("Ping Interval Changed", f"Ping interval set to {val} minutes.")

def main():
    agent = SyncAgent()
    try:
        agent.sync_thread.join()
    except KeyboardInterrupt:
        agent.stop()

if __name__ == "__main__":
    main()
