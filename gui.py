"""
Video Highlight Extractor - GUI Launcher
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import subprocess
import sys
import os
from pathlib import Path
import queue
import locale


class VideoClipperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Highlight Extractor")
        self.root.geometry("800x600")
        self.root.resizable(True, True)
        
        self.process = None
        self.output_queue = queue.Queue()
        
        self._setup_encoding()
        self._create_widgets()
        self._load_config()
        self._check_environment()
    
    def _setup_encoding(self):
        self.system_encoding = locale.getpreferredencoding(False)
        if sys.platform == 'win32':
            self.system_encoding = 'gbk'
        
    def _create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        config_frame = ttk.LabelFrame(main_frame, text="Config", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(config_frame, text="Target Duration (min):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.duration_var = tk.StringVar(value="30")
        ttk.Entry(config_frame, textvariable=self.duration_var, width=10).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(config_frame, text="Max Solutions:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.solutions_var = tk.StringVar(value="3")
        ttk.Entry(config_frame, textvariable=self.solutions_var, width=10).grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(config_frame, text="Input Dir:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.input_var = tk.StringVar(value="data/input")
        ttk.Entry(config_frame, textvariable=self.input_var, width=30).grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=5, pady=5)
        ttk.Button(config_frame, text="Browse...", command=self._browse_input).grid(row=1, column=3, padx=5, pady=5)
        
        ttk.Label(config_frame, text="Output Dir:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.output_var = tk.StringVar(value="data/output")
        ttk.Entry(config_frame, textvariable=self.output_var, width=30).grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=5, pady=5)
        ttk.Button(config_frame, text="Browse...", command=self._browse_output).grid(row=2, column=3, padx=5, pady=5)
        
        status_frame = ttk.LabelFrame(main_frame, text="Environment Status", padding="10")
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.python_status = ttk.Label(status_frame, text="Python: Checking...")
        self.python_status.pack(anchor=tk.W)
        
        self.ffmpeg_status = ttk.Label(status_frame, text="FFmpeg: Checking...")
        self.ffmpeg_status.pack(anchor=tk.W)
        
        self.deps_status = ttk.Label(status_frame, text="Dependencies: Checking...")
        self.deps_status.pack(anchor=tk.W)
        
        log_frame = ttk.LabelFrame(main_frame, text="Log", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)
        
        self.start_btn = ttk.Button(btn_frame, text="Start", command=self._start_process)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop_process, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="Open Output", command=self._open_output).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Install Deps", command=self._install_deps).pack(side=tk.LEFT, padx=5)
        
        self.progress = ttk.Progressbar(btn_frame, mode='indeterminate')
        self.progress.pack(side=tk.RIGHT, padx=5, fill=tk.X, expand=True)
        
    def _load_config(self):
        config_path = Path("user_config.txt")
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if '=' in line and not line.startswith('#'):
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            
                            if key == 'target_duration_minutes':
                                self.duration_var.set(value)
                            elif key == 'max_solutions':
                                self.solutions_var.set(value)
                            elif key == 'input_dir':
                                self.input_var.set(value)
                            elif key == 'output_dir':
                                self.output_var.set(value)
            except Exception:
                pass
    
    def _save_config(self):
        config_content = f"""# User config
target_duration_minutes = {self.duration_var.get()}
max_solutions = {self.solutions_var.get()}
input_dir = {self.input_var.get()}
output_dir = {self.output_var.get()}
"""
        with open("user_config.txt", 'w', encoding='utf-8') as f:
            f.write(config_content)
    
    def _check_environment(self):
        def check():
            python_ok = False
            try:
                result = subprocess.run([sys.executable, '--version'], capture_output=True, text=True)
                python_ok = True
                self.root.after(0, lambda: self.python_status.config(text=f"Python: {result.stdout.strip()}"))
            except Exception:
                self.root.after(0, lambda: self.python_status.config(text="Python: Not found"))
            
            ffmpeg_ok = False
            try:
                result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
                ffmpeg_ok = True
                self.root.after(0, lambda: self.ffmpeg_status.config(text="FFmpeg: OK"))
            except Exception:
                self.root.after(0, lambda: self.ffmpeg_status.config(text="FFmpeg: Not found (video merge disabled)"))
            
            deps_ok = False
            try:
                result = subprocess.run([sys.executable, '-c', 'import cv2, numpy, librosa'], capture_output=True, text=True)
                if result.returncode == 0:
                    deps_ok = True
                    self.root.after(0, lambda: self.deps_status.config(text="Dependencies: OK"))
                else:
                    self.root.after(0, lambda: self.deps_status.config(text="Dependencies: Missing (click 'Install Deps')"))
            except Exception:
                self.root.after(0, lambda: self.deps_status.config(text="Dependencies: Not installed"))
            
            return python_ok, ffmpeg_ok, deps_ok
        
        threading.Thread(target=check, daemon=True).start()
    
    def _browse_input(self):
        path = filedialog.askdirectory(title="Select Input Directory")
        if path:
            self.input_var.set(path)
    
    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.output_var.set(path)
    
    def _open_output(self):
        output_path = Path(self.output_var.get())
        if output_path.exists():
            if sys.platform == 'win32':
                os.startfile(output_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', output_path])
            else:
                subprocess.run(['xdg-open', output_path])
        else:
            messagebox.showinfo("Info", "Output directory does not exist yet.")
    
    def _install_deps(self):
        self._log("Installing dependencies...")
        
        def install():
            try:
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    self.root.after(0, lambda: self._log("Dependencies installed!"))
                    self.root.after(0, self._check_environment)
                else:
                    self.root.after(0, lambda: self._log(f"Install failed:\n{result.stderr}"))
            except Exception as e:
                self.root.after(0, lambda: self._log(f"Install error: {e}"))
        
        threading.Thread(target=install, daemon=True).start()
    
    def _log(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
    
    def _start_process(self):
        self._save_config()
        
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress.start()
        
        self._log("=" * 50)
        self._log("Starting video processing...")
        self._log(f"Target duration: {self.duration_var.get()} min")
        self._log(f"Max solutions: {self.solutions_var.get()}")
        self._log("=" * 50)
        
        def run():
            try:
                self.process = subprocess.Popen(
                    [sys.executable, 'main.py'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding=self.system_encoding,
                    errors='replace'
                )
                
                for line in self.process.stdout:
                    self.root.after(0, lambda l=line: self._log(l.rstrip()))
                
                self.process.wait()
                
                if self.process.returncode == 0:
                    self.root.after(0, lambda: self._log("Processing complete!"))
                else:
                    self.root.after(0, lambda: self._log(f"Process ended with code: {self.process.returncode}"))
                    
            except Exception as e:
                self.root.after(0, lambda: self._log(f"Error: {e}"))
            finally:
                self.root.after(0, self._process_finished)
        
        threading.Thread(target=run, daemon=True).start()
    
    def _stop_process(self):
        if self.process:
            self.process.terminate()
            self._log("Stopping process...")
    
    def _process_finished(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress.stop()
        self.process = None


def main():
    root = tk.Tk()
    app = VideoClipperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
