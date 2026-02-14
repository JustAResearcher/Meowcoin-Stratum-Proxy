#!/usr/bin/env python3
"""
Meowcoin MeowPoW Solo Mining Stratum Proxy — GUI
=================================================

Tkinter GUI wrapper around the stratum proxy for solo mining Meowcoin.

Usage:
    python stratum_proxy_gui.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from typing import Optional

# Import the core proxy module (same directory)
from stratum_proxy import (
    NodeRPC,
    JobManager,
    StratumServer,
    address_to_scriptpubkey,
    COMMUNITY_FUND_ADDRESS,
    EPOCH_LENGTH,
    log as proxy_log,
)

# =============================================================================
# Colour palette — Meowcoin theme
# =============================================================================

BG_DARK      = "#1a1a2e"
BG_PANEL     = "#16213e"
BG_INPUT     = "#0f3460"
FG_TEXT       = "#e0e0e0"
FG_DIM        = "#8899aa"
FG_ACCENT     = "#e94560"
FG_GREEN      = "#00d26a"
FG_YELLOW     = "#f5a623"
FG_BLUE       = "#53c0f0"
BTN_START_BG  = "#00d26a"
BTN_START_FG  = "#1a1a2e"
BTN_STOP_BG   = "#e94560"
BTN_STOP_FG   = "#ffffff"
BTN_BROWSE_BG = "#0f3460"

# =============================================================================
# Logging handler that writes to the tkinter text widget
# =============================================================================

class TextWidgetHandler(logging.Handler):
    """Logging handler that appends to a tkinter ScrolledText widget."""

    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record) + "\n"
        # Schedule on the main thread
        try:
            self.text_widget.after(0, self._append, msg, record.levelno)
        except Exception:
            pass

    def _append(self, msg: str, levelno: int):
        self.text_widget.configure(state="normal")
        # Choose tag by level
        if levelno >= logging.ERROR:
            tag = "error"
        elif levelno >= logging.WARNING:
            tag = "warn"
        elif "BLOCK ACCEPTED" in msg:
            tag = "success"
        else:
            tag = "info"
        self.text_widget.insert(tk.END, msg, tag)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")

# =============================================================================
# Proxy runner — background thread with asyncio loop
# =============================================================================

class ProxyRunner:
    """Runs the StratumServer in a background thread."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[StratumServer] = None
        self.running = False

    def start(
        self,
        address: str,
        rpc_host: str,
        rpc_port: int,
        rpc_user: str,
        rpc_pass: str,
        cookie_dir: str,
        stratum_host: str,
        stratum_port: int,
        poll_interval: float,
    ):
        if self.running:
            return

        # Validate address
        address_to_scriptpubkey(address)

        # Build RPC client and test connectivity
        rpc = NodeRPC(
            host=rpc_host,
            port=rpc_port,
            user=rpc_user,
            password=rpc_pass,
            cookie_dir=cookie_dir,
        )
        info = rpc.call("getmininginfo")
        proxy_log.info(
            "Connected to node — chain=%s  height=%s  difficulty=%s",
            info.get("chain", "?"),
            info.get("blocks", "?"),
            info.get("difficulty", "?"),
        )

        job_mgr = JobManager(rpc, address)
        self._server = StratumServer(
            job_manager=job_mgr,
            host=stratum_host,
            port=stratum_port,
            poll_interval=poll_interval,
        )

        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._server.start())
        except Exception as e:
            proxy_log.error("Proxy stopped with error: %s", e)
        finally:
            self.running = False

    def stop(self):
        if not self.running or not self._loop:
            return
        # Close the asyncio server from the main thread
        async def _shutdown():
            if self._server and self._server._server:
                self._server._server.close()
                await self._server._server.wait_closed()
            # Close all miner sessions
            for session in list(self._server.sessions):
                await session.close()
            # Stop the event loop
            self._loop.stop()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
        self._thread.join(timeout=5)
        self.running = False
        proxy_log.info("Proxy stopped.")

    @property
    def miner_count(self) -> int:
        if self._server:
            return len(self._server.sessions)
        return 0

    @property
    def current_height(self) -> Optional[int]:
        if self._server and self._server.job_manager.current_job:
            return self._server.job_manager.current_job.height
        return None

    @property
    def current_epoch(self) -> Optional[int]:
        h = self.current_height
        return h // EPOCH_LENGTH if h else None

# =============================================================================
# Main GUI application
# =============================================================================

class StratumProxyApp:
    """Tkinter GUI for the Meowcoin Stratum Proxy."""

    ICON_MEOW = None  # Set dynamically if icon exists

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Meowcoin Stratum Proxy")
        self.root.configure(bg=BG_DARK)
        self.root.minsize(750, 620)
        self.root.geometry("850x700")

        # Try to set a window icon
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "meowcoin.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception:
            pass

        self.runner = ProxyRunner()

        self._build_ui()
        self._setup_logging()
        self._update_status_loop()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Style configuration
        style.configure("Dark.TFrame", background=BG_DARK)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("Dark.TLabel", background=BG_DARK, foreground=FG_TEXT,
                         font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=BG_PANEL, foreground=FG_TEXT,
                         font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=BG_DARK, foreground=FG_ACCENT,
                         font=("Segoe UI", 14, "bold"))
        style.configure("Status.TLabel", background=BG_PANEL, foreground=FG_DIM,
                         font=("Segoe UI", 9))
        style.configure("StatusVal.TLabel", background=BG_PANEL, foreground=FG_BLUE,
                         font=("Segoe UI", 9, "bold"))

        # ── Title bar ──
        title_frame = ttk.Frame(self.root, style="Dark.TFrame")
        title_frame.pack(fill="x", padx=12, pady=(10, 2))

        ttk.Label(
            title_frame, text="⛏  Meowcoin Stratum Proxy",
            style="Header.TLabel",
        ).pack(side="left")

        self.status_dot = tk.Label(
            title_frame, text="●", fg=FG_DIM, bg=BG_DARK,
            font=("Segoe UI", 14),
        )
        self.status_dot.pack(side="right", padx=(0, 4))
        self.status_label = ttk.Label(
            title_frame, text="Stopped", style="Dark.TLabel",
        )
        self.status_label.pack(side="right")

        # ── Configuration panel ──
        cfg_outer = ttk.Frame(self.root, style="Dark.TFrame")
        cfg_outer.pack(fill="x", padx=12, pady=6)

        cfg = ttk.LabelFrame(
            cfg_outer, text="  Configuration  ",
            style="Panel.TFrame",
        )
        cfg.pack(fill="x")
        cfg.configure(labelwidget=self._make_section_label(cfg_outer, "  Configuration  "))
        cfg_inner = ttk.Frame(cfg, style="Panel.TFrame")
        cfg_inner.pack(fill="x", padx=10, pady=8)

        # Row builder
        row = [0]
        def add_field(label_text, default="", width=50, show=None):
            lbl = ttk.Label(cfg_inner, text=label_text, style="Panel.TLabel")
            lbl.grid(row=row[0], column=0, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value=default)
            entry = tk.Entry(
                cfg_inner, textvariable=var, width=width,
                bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                relief="flat", font=("Consolas", 10),
                show=show,
            )
            entry.grid(row=row[0], column=1, sticky="ew", pady=3)
            row[0] += 1
            return var, entry

        cfg_inner.columnconfigure(1, weight=1)

        self.var_address, _ = add_field("Mining Address:", "")
        self.var_rpc_host, _ = add_field("RPC Host:", "127.0.0.1")
        self.var_rpc_port, _ = add_field("RPC Port:", "8332")
        self.var_rpc_user, _ = add_field("RPC User:", "", width=50)
        self.var_rpc_pass, _ = add_field("RPC Password:", "", width=50, show="•")

        # Hint label — leave blank for cookie auth
        hint_lbl = tk.Label(
            cfg_inner,
            text="(leave User/Password blank to use cookie auth)",
            bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8, "italic"),
        )
        hint_lbl.grid(row=row[0], column=1, sticky="w", pady=(0, 2))
        row[0] += 1

        # Cookie dir with browse button
        lbl = ttk.Label(cfg_inner, text="Cookie Dir:", style="Panel.TLabel")
        lbl.grid(row=row[0], column=0, sticky="w", padx=(0, 8), pady=3)

        cookie_frame = ttk.Frame(cfg_inner, style="Panel.TFrame")
        cookie_frame.grid(row=row[0], column=1, sticky="ew", pady=3)
        cookie_frame.columnconfigure(0, weight=1)

        self.var_cookie_dir = tk.StringVar(value="")
        cookie_entry = tk.Entry(
            cookie_frame, textvariable=self.var_cookie_dir,
            bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
            relief="flat", font=("Consolas", 10),
        )
        cookie_entry.grid(row=0, column=0, sticky="ew")

        browse_btn = tk.Button(
            cookie_frame, text="Browse…", command=self._browse_cookie,
            bg=BTN_BROWSE_BG, fg=FG_TEXT, relief="flat",
            font=("Segoe UI", 9), padx=8, cursor="hand2",
        )
        browse_btn.grid(row=0, column=1, padx=(4, 0))
        row[0] += 1

        self.var_stratum_host, _ = add_field("Stratum Host:", "0.0.0.0")
        self.var_stratum_port, _ = add_field("Stratum Port:", "3333")
        self.var_poll, _ = add_field("Poll Interval (s):", "1.0")

        # ── Buttons ──
        btn_frame = ttk.Frame(self.root, style="Dark.TFrame")
        btn_frame.pack(fill="x", padx=12, pady=4)

        self.btn_start = tk.Button(
            btn_frame, text="▶  Start Proxy", command=self._on_start,
            bg=BTN_START_BG, fg=BTN_START_FG, activebackground="#00b85c",
            relief="flat", font=("Segoe UI", 11, "bold"),
            padx=20, pady=6, cursor="hand2",
        )
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_stop = tk.Button(
            btn_frame, text="■  Stop Proxy", command=self._on_stop,
            bg=BTN_STOP_BG, fg=BTN_STOP_FG, activebackground="#c93050",
            relief="flat", font=("Segoe UI", 11, "bold"),
            padx=20, pady=6, cursor="hand2", state="disabled",
        )
        self.btn_stop.pack(side="left")

        # ── Log output ──
        log_outer = ttk.Frame(self.root, style="Dark.TFrame")
        log_outer.pack(fill="both", expand=True, padx=12, pady=(4, 2))

        log_label = self._make_section_label(log_outer, "  Log Output  ")

        self.log_text = scrolledtext.ScrolledText(
            log_outer, wrap="word", state="disabled",
            bg="#0d1117", fg=FG_TEXT, insertbackground=FG_TEXT,
            font=("Consolas", 9), relief="flat", height=14,
            selectbackground="#1f6feb",
        )
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

        # Text tags for log colours
        self.log_text.tag_configure("info", foreground=FG_TEXT)
        self.log_text.tag_configure("warn", foreground=FG_YELLOW)
        self.log_text.tag_configure("error", foreground=FG_ACCENT)
        self.log_text.tag_configure("success", foreground=FG_GREEN,
                                     font=("Consolas", 9, "bold"))

        # ── Status bar ──
        status_bar = ttk.Frame(self.root, style="Panel.TFrame")
        status_bar.pack(fill="x", padx=0, pady=0)

        pad = {"padx": (12, 20), "pady": 4}

        ttk.Label(status_bar, text="Miners:", style="Status.TLabel").pack(
            side="left", **pad)
        self.lbl_miners = ttk.Label(status_bar, text="0", style="StatusVal.TLabel")
        self.lbl_miners.pack(side="left")

        ttk.Label(status_bar, text="Height:", style="Status.TLabel").pack(
            side="left", **pad)
        self.lbl_height = ttk.Label(status_bar, text="—", style="StatusVal.TLabel")
        self.lbl_height.pack(side="left")

        ttk.Label(status_bar, text="Epoch:", style="Status.TLabel").pack(
            side="left", **pad)
        self.lbl_epoch = ttk.Label(status_bar, text="—", style="StatusVal.TLabel")
        self.lbl_epoch.pack(side="left")

        ttk.Label(status_bar, text="Uptime:", style="Status.TLabel").pack(
            side="left", **pad)
        self.lbl_uptime = ttk.Label(status_bar, text="—", style="StatusVal.TLabel")
        self.lbl_uptime.pack(side="left")

        self._start_time: Optional[float] = None

    def _make_section_label(self, parent, text):
        lbl = tk.Label(
            parent, text=text, bg=BG_DARK, fg=FG_BLUE,
            font=("Segoe UI", 10, "bold"),
        )
        return lbl

    # ------------------------------------------------------------------ Logging

    def _setup_logging(self):
        handler = TextWidgetHandler(self.log_text)
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                              datefmt="%H:%M:%S")
        )
        proxy_log.addHandler(handler)
        proxy_log.setLevel(logging.INFO)

        # Also capture root logger for any stray messages
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    # ------------------------------------------------------------------ Actions

    def _browse_cookie(self):
        d = filedialog.askdirectory(title="Select node data directory")
        if d:
            self.var_cookie_dir.set(d)

    def _on_start(self):
        address = self.var_address.get().strip()
        if not address:
            messagebox.showerror("Error", "Mining address is required.")
            return

        try:
            address_to_scriptpubkey(address)
        except Exception as e:
            messagebox.showerror("Invalid Address", str(e))
            return

        rpc_host = self.var_rpc_host.get().strip() or "127.0.0.1"
        try:
            rpc_port = int(self.var_rpc_port.get().strip() or "8332")
        except ValueError:
            messagebox.showerror("Error", "RPC Port must be a number.")
            return

        stratum_host = self.var_stratum_host.get().strip() or "0.0.0.0"
        try:
            stratum_port = int(self.var_stratum_port.get().strip() or "3333")
        except ValueError:
            messagebox.showerror("Error", "Stratum Port must be a number.")
            return

        try:
            poll_interval = float(self.var_poll.get().strip() or "1.0")
        except ValueError:
            messagebox.showerror("Error", "Poll interval must be a number.")
            return

        try:
            self.runner.start(
                address=address,
                rpc_host=rpc_host,
                rpc_port=rpc_port,
                rpc_user=self.var_rpc_user.get().strip(),
                rpc_pass=self.var_rpc_pass.get().strip(),
                cookie_dir=self.var_cookie_dir.get().strip(),
                stratum_host=stratum_host,
                stratum_port=stratum_port,
                poll_interval=poll_interval,
            )
        except Exception as e:
            messagebox.showerror("Start Failed", str(e))
            return

        self._start_time = time.time()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_dot.configure(fg=FG_GREEN)
        self.status_label.configure(text="Running")

    def _on_stop(self):
        try:
            self.runner.stop()
        except Exception:
            pass
        self._start_time = None
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_dot.configure(fg=FG_DIM)
        self.status_label.configure(text="Stopped")
        self.lbl_miners.configure(text="0")
        self.lbl_height.configure(text="—")
        self.lbl_epoch.configure(text="—")
        self.lbl_uptime.configure(text="—")

    # ------------------------------------------------------------------ Status

    def _update_status_loop(self):
        """Periodic UI update for status bar values."""
        if self.runner.running:
            self.lbl_miners.configure(text=str(self.runner.miner_count))
            h = self.runner.current_height
            self.lbl_height.configure(text=str(h) if h else "—")
            e = self.runner.current_epoch
            self.lbl_epoch.configure(text=str(e) if e is not None else "—")
            if self._start_time:
                elapsed = int(time.time() - self._start_time)
                hrs, rem = divmod(elapsed, 3600)
                mins, secs = divmod(rem, 60)
                self.lbl_uptime.configure(text=f"{hrs:02d}:{mins:02d}:{secs:02d}")
        elif self.btn_start["state"] == "disabled":
            # Proxy died unexpectedly — reset UI
            self._on_stop()

        self.root.after(1000, self._update_status_loop)

    # ------------------------------------------------------------------ Run

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        if self.runner.running:
            if not messagebox.askokcancel(
                "Quit",
                "The proxy is still running. Stop it and exit?",
            ):
                return
            self._on_stop()
        self.root.destroy()


# =============================================================================
# Entry point
# =============================================================================

def main():
    app = StratumProxyApp()
    app.run()


if __name__ == "__main__":
    main()
