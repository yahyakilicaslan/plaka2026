"""
====================================================================
 EVO SMART LPR - DESKTOP KAMERA MONITORÜ (v1.0)
 --------------------------------------------------------------------
 • 2x2 RAW kamera grid (doğrudan RTSP/WebCam - MJPEG yok)
 • Her kamera için GİRİŞ/ÇIKIŞ etiketi + kamera adı
 • Manuel "KAPI AÇ" butonu (ilgili NodeMCU'yu tetikler)
 • Backend WebSocket'e bağlanır → okunan plakayı durum+bariyerle gösterir
 • Tek başına .exe olarak paketlenebilir (PyInstaller)

 Kullanım:
   python desktop_camera.py                # normal
   python desktop_camera.py --api http://192.168.1.10:8000

 Windows build:
   pyinstaller --noconsole --onefile desktop_camera.py
====================================================================
"""
import sys
import os
import json
import time
import argparse
import threading
from datetime import datetime
from queue import Queue, Empty

import cv2
import requests
import websocket  # websocket-client

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QSizePolicy, QMessageBox
)

# ---------------------------- Argparse --------------------------------
parser = argparse.ArgumentParser()
parser.add_argument("--api", default=os.environ.get("LPR_API", "http://localhost:8000"),
                    help="Backend API base URL (default http://localhost:8000)")
parser.add_argument("--slots", type=int, default=4, help="Kamera slot sayısı")
args, _ = parser.parse_known_args()

API_URL = args.api.rstrip("/")
WS_URL = API_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
SLOT_COUNT = args.slots

# ------------------------- Kamera Thread -------------------------------
class CameraReaderThread(QThread):
    """RTSP/WebCam RAW okur, son kare'yi paylaşır. MJPEG KULLANMAZ."""
    frame_ready = pyqtSignal(int, object)
    status_changed = pyqtSignal(int, str)  # slot, "ONLINE"/"OFFLINE"/"BAGLANIYOR"

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.config = None
        self._running = True
        self._config_sig = None

    def set_config(self, cfg: dict):
        self.config = cfg

    def _build_src(self):
        if not self.config:
            return None
        src = self.config.get("source", "")
        if self.config.get("type") == "RTSP":
            u = self.config.get("rtsp_user", "") or ""
            p = self.config.get("rtsp_pass", "") or ""
            if u and p and str(src).startswith("rtsp://"):
                src = src.replace("rtsp://", f"rtsp://{u}:{p}@")
            return src
        return int(src) if str(src).isdigit() else src

    def stop(self):
        self._running = False

    def run(self):
        cap = None
        last_frame_t = time.time()
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"
        while self._running:
            sig = json.dumps(self.config, sort_keys=True) if self.config else None
            if sig != self._config_sig:
                if cap is not None:
                    try: cap.release()
                    except Exception: pass
                    cap = None
                self._config_sig = sig

            if not self.config:
                self.status_changed.emit(self.slot, "BOS")
                time.sleep(1.0)
                continue

            if cap is None:
                self.status_changed.emit(self.slot, "BAGLANIYOR")
                src = self._build_src()
                try:
                    if self.config.get("type") == "RTSP":
                        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
                    else:
                        if os.name == "nt" and isinstance(src, int):
                            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
                        else:
                            cap = cv2.VideoCapture(src)
                    if cap and cap.isOpened():
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        self.status_changed.emit(self.slot, "ONLINE")
                        last_frame_t = time.time()
                    else:
                        cap = None
                        self.status_changed.emit(self.slot, "OFFLINE")
                        time.sleep(2.0)
                        continue
                except Exception:
                    cap = None
                    self.status_changed.emit(self.slot, "OFFLINE")
                    time.sleep(2.0)
                    continue

            try:
                if time.time() - last_frame_t > 5.0:
                    try: cap.release()
                    except Exception: pass
                    cap = None
                    continue
                ok = cap.grab()
                if not ok:
                    time.sleep(0.05); continue
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    time.sleep(0.01); continue
                last_frame_t = time.time()
                self.frame_ready.emit(self.slot, frame)
                # ~30 fps üst limit
                time.sleep(0.01)
            except Exception:
                try: cap.release()
                except Exception: pass
                cap = None
                self.status_changed.emit(self.slot, "OFFLINE")
                time.sleep(1.0)

        if cap is not None:
            try: cap.release()
            except Exception: pass


# ----------------------- WebSocket Dinleyici --------------------------
class WSThread(QThread):
    event_received = pyqtSignal(dict)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                ws = websocket.WebSocket()
                ws.connect(self.url, timeout=5)
                while self._running:
                    try:
                        msg = ws.recv()
                        if not msg:
                            break
                        data = json.loads(msg)
                        self.event_received.emit(data)
                    except Exception:
                        break
                try: ws.close()
                except Exception: pass
            except Exception:
                pass
            time.sleep(2.0)


# -------------------------- Kamera Widget -----------------------------
class CameraWidget(QFrame):
    def __init__(self, slot: int, api_url: str, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.api_url = api_url
        self.camera_name = f"CAM-0{slot}"
        self.direction = "GIRIS"
        self.setFrameStyle(QFrame.NoFrame)
        self.setStyleSheet("""
            QFrame { background-color: #0B0E14; border: 1px solid #1e293b; border-radius: 10px; }
            QLabel { color: #E2E8F0; background: transparent; border: none; }
        """)

        self.video_label = QLabel("● BEKLEN\u0130YOR")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #000; color: #64748b; font-size: 14px;"
            "border: 1px solid #1e293b; border-radius: 8px;"
        )
        self.video_label.setMinimumSize(320, 240)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Üst bar: kamera adı + giriş/çıkış + durum
        self.title_label = QLabel(f"● {self.camera_name}")
        self.title_label.setStyleSheet(
            "color: #ffffff; font-weight: 700; font-size: 13px; padding: 6px 10px;"
            "background: rgba(15, 23, 42, 0.9); border-radius: 6px;"
        )
        self.direction_label = QLabel("GİRİŞ")
        self.direction_label.setAlignment(Qt.AlignCenter)
        self.direction_label.setFixedWidth(80)
        self._set_direction_style("GIRIS")

        self.status_dot = QLabel("OFFLINE")
        self.status_dot.setAlignment(Qt.AlignCenter)
        self.status_dot.setFixedWidth(80)
        self.status_dot.setStyleSheet(
            "color: #ef4444; font-weight: 700; font-size: 10px; padding: 4px 8px;"
            "background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; border-radius: 6px;"
        )

        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)
        top_bar.addWidget(self.title_label, stretch=1)
        top_bar.addWidget(self.direction_label)
        top_bar.addWidget(self.status_dot)

        # Alt bar: okunan plaka bilgisi + manuel buton
        self.plate_label = QLabel("- - -")
        self.plate_label.setAlignment(Qt.AlignCenter)
        self.plate_label.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 20px; font-weight: 800;"
            "color: #ffffff; background: rgba(99,102,241,0.15);"
            "border: 2px solid #6366f1; border-radius: 6px; padding: 6px 10px;"
        )
        self.plate_label.setMinimumWidth(140)

        self.status_label = QLabel("Hazır")
        self.status_label.setStyleSheet(
            "color: #94a3b8; font-size: 11px; padding: 4px 8px;"
        )

        self.open_btn = QPushButton("KAPI AÇ")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setFixedWidth(110)
        self.open_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #059669);
                color: white; font-weight: 800; font-size: 12px; letter-spacing: 0.05em;
                border: none; border-radius: 6px; padding: 8px 14px;
            }
            QPushButton:hover { background: #10b981; }
            QPushButton:pressed { background: #047857; }
        """)
        self.open_btn.clicked.connect(self.manual_open)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(6)
        bottom_bar.addWidget(self.plate_label, stretch=1)
        bottom_bar.addWidget(self.status_label, stretch=2)
        bottom_bar.addWidget(self.open_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(top_bar)
        root.addWidget(self.video_label, stretch=1)
        root.addLayout(bottom_bar)

        # Plaka timer - 6 sn sonra temizle
        self._plate_clear_timer = QTimer(self)
        self._plate_clear_timer.setSingleShot(True)
        self._plate_clear_timer.timeout.connect(self._clear_plate)

    def _set_direction_style(self, direction: str):
        self.direction = direction
        if direction == "CIKIS":
            self.direction_label.setText("ÇIKIŞ")
            self.direction_label.setStyleSheet(
                "color: #f59e0b; font-weight: 800; font-size: 10px; padding: 4px 8px;"
                "background: rgba(245,158,11,0.15); border: 1px solid #f59e0b; border-radius: 6px;"
            )
        else:
            self.direction_label.setText("GİRİŞ")
            self.direction_label.setStyleSheet(
                "color: #10b981; font-weight: 800; font-size: 10px; padding: 4px 8px;"
                "background: rgba(16,185,129,0.15); border: 1px solid #10b981; border-radius: 6px;"
            )

    def set_config(self, cfg: dict):
        if cfg:
            self.camera_name = cfg.get("name") or f"CAM-0{self.slot}"
            self.title_label.setText(f"● {self.camera_name}")
            self._set_direction_style(cfg.get("direction") or "GIRIS")
            # Butonun aktif/pasif durumu
            has_gate = bool(cfg.get("gate_id"))
            self.open_btn.setEnabled(has_gate)
            if not has_gate:
                self.open_btn.setToolTip("Bu kameraya kapı atanmamış")
            else:
                self.open_btn.setToolTip("")
        else:
            self.title_label.setText(f"● SLOT {self.slot} (BOŞ)")
            self.open_btn.setEnabled(False)

    def set_status(self, status: str):
        palette = {
            "ONLINE":  ("#10b981", "ONLINE"),
            "BAGLANIYOR": ("#f59e0b", "BAĞLANIYOR"),
            "OFFLINE": ("#ef4444", "OFFLINE"),
            "BOS":     ("#64748b", "BOŞ SLOT"),
        }
        color, text = palette.get(status, ("#64748b", status))
        self.status_dot.setText(text)
        self.status_dot.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 10px; padding: 4px 8px;"
            f"background: rgba(255,255,255,0.05); border: 1px solid {color}; border-radius: 6px;"
        )

    def update_frame(self, frame):
        try:
            if frame is None:
                return
            h, w = frame.shape[:2]
            target_w = max(1, self.video_label.width())
            target_h = max(1, self.video_label.height())
            # Aspect korumalı ölçeklendirme
            scale = min(target_w / w, target_h / h)
            nw, nh = max(2, int(w * scale)), max(2, int(h * scale))
            small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb.data, nw, nh, 3 * nw, QImage.Format_RGB888)
            self.video_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception:
            pass

    def show_plate_event(self, plate: str, status: str, owner: str, gate_opened: bool, direction: str):
        self.plate_label.setText(plate)
        # Durum rengi
        color = {
            "IZINLI":    ("#10b981", "#064e3b"),
            "MİSAFİR":  ("#f59e0b", "#78350f"),
            "MISAFIR":  ("#f59e0b", "#78350f"),
            "YASAKLI":   ("#ef4444", "#7f1d1d"),
            "KOTA_DOLU": ("#ef4444", "#7f1d1d"),
        }.get(status, ("#6366f1", "#1e293b"))
        self.plate_label.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 22px; font-weight: 800;"
            f"color: #ffffff; background: {color[1]};"
            f"border: 2px solid {color[0]}; border-radius: 6px; padding: 6px 10px;"
        )
        bar = "AÇILDI" if gate_opened else "KAPALI"
        bar_color = "#10b981" if gate_opened else "#ef4444"
        self.status_label.setText(
            f"<span style='color:#94a3b8'>Sahip:</span> <b>{owner or '-'}</b> &nbsp; "
            f"<span style='color:{color[0]};font-weight:700'>{status}</span> &nbsp; "
            f"<span style='color:#94a3b8'>Bariyer:</span> "
            f"<span style='color:{bar_color};font-weight:700'>{bar}</span>"
        )
        self._plate_clear_timer.start(8000)

    def _clear_plate(self):
        self.plate_label.setText("- - -")
        self.plate_label.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 20px; font-weight: 800;"
            "color: #ffffff; background: rgba(99,102,241,0.15);"
            "border: 2px solid #6366f1; border-radius: 6px; padding: 6px 10px;"
        )
        self.status_label.setText("Hazır")

    def manual_open(self):
        try:
            r = requests.post(f"{self.api_url}/api/gate/manual-open/{self.slot}", timeout=3)
            data = r.json() if r.status_code == 200 else {"status": "error", "message": r.text}
        except Exception as e:
            data = {"status": "error", "message": str(e)}
        if data.get("status") == "ok":
            self.status_label.setText(f"<span style='color:#10b981;font-weight:700'>✓ {data.get('message','Kapı açıldı')}</span>")
            QTimer.singleShot(4000, lambda: self.status_label.setText("Hazır"))
        else:
            QMessageBox.warning(self, "Kapı Açma Hatası", data.get("message", "Bilinmeyen hata"))


# ----------------------------- Ana Pencere ----------------------------
class MainWindow(QMainWindow):
    def __init__(self, api_url: str, ws_url: str, slot_count: int = 4):
        super().__init__()
        self.setWindowTitle("EVO SMART LPR — Canlı Kamera Monitörü")
        self.resize(1280, 820)
        self.setStyleSheet("QMainWindow { background-color: #0B0E14; }")

        self.api_url = api_url
        self.ws_url = ws_url

        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(12, 12, 12, 12); root.setSpacing(10)

        # Başlık bar
        header = QHBoxLayout()
        title = QLabel("<b style='color:#ffffff;font-size:16px;letter-spacing:1px'>EVO SMART</b> "
                       "<span style='color:#6366f1;font-size:11px;font-family:Consolas'>CANLI MONİTÖR</span>")
        self.status_hdr = QLabel("● Backend: bağlanılıyor")
        self.status_hdr.setStyleSheet("color:#f59e0b; font-weight:700; font-size:11px;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status_hdr)
        root.addLayout(header)

        # 2x2 kamera grid
        grid = QGridLayout(); grid.setSpacing(10)
        self.cameras = {}
        for i in range(1, slot_count + 1):
            w = CameraWidget(i, api_url)
            r_, c_ = divmod(i - 1, 2)
            grid.addWidget(w, r_, c_)
            self.cameras[i] = w
        root.addLayout(grid, stretch=1)

        # Thread'ler
        self.readers = {}
        for i in range(1, slot_count + 1):
            t = CameraReaderThread(i, self)
            t.frame_ready.connect(self._on_frame)
            t.status_changed.connect(self._on_status)
            t.start()
            self.readers[i] = t

        self.ws = WSThread(ws_url, self)
        self.ws.event_received.connect(self._on_ws_event)
        self.ws.start()

        # Config refresh
        self.cfg_timer = QTimer(self)
        self.cfg_timer.timeout.connect(self.refresh_cameras)
        self.cfg_timer.start(5000)
        QTimer.singleShot(200, self.refresh_cameras)

    def refresh_cameras(self):
        try:
            r = requests.get(f"{self.api_url}/api/cameras", timeout=3)
            if r.status_code == 200:
                cams = r.json()
                by_slot = {c.get("slot"): c for c in cams}
                self.status_hdr.setText("● Backend: bağlı")
                self.status_hdr.setStyleSheet("color:#10b981; font-weight:700; font-size:11px;")
                for slot, w in self.cameras.items():
                    cfg = by_slot.get(slot)
                    w.set_config(cfg)
                    self.readers[slot].set_config(cfg)
        except Exception:
            self.status_hdr.setText("● Backend: OFFLINE")
            self.status_hdr.setStyleSheet("color:#ef4444; font-weight:700; font-size:11px;")

    def _on_frame(self, slot, frame):
        w = self.cameras.get(slot)
        if w: w.update_frame(frame)

    def _on_status(self, slot, status):
        w = self.cameras.get(slot)
        if w: w.set_status(status)

    def _on_ws_event(self, data):
        t = data.get("type")
        if t == "log":
            cam_id = data.get("camera", "")
            try:
                slot = int(cam_id.split("-")[1])
            except Exception:
                return
            w = self.cameras.get(slot)
            if w:
                w.show_plate_event(
                    data.get("plate", ""),
                    data.get("status", ""),
                    data.get("owner", ""),
                    bool(data.get("gate_opened")),
                    data.get("direction", ""),
                )
        elif t == "manual_open":
            slot = data.get("slot")
            w = self.cameras.get(slot)
            if w:
                w.status_label.setText(
                    f"<span style='color:#10b981;font-weight:700'>✓ Manuel açıldı: {data.get('door','')}</span>"
                )

    def closeEvent(self, event):
        for t in self.readers.values():
            try: t.stop(); t.quit()
            except Exception: pass
        try: self.ws.stop(); self.ws.quit()
        except Exception: pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    win = MainWindow(API_URL, WS_URL, SLOT_COUNT)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
