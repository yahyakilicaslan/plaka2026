"""
====================================================================
 EVO SMART LPR - DESKTOP KAMERA MONITORÜ (v1.1)
 --------------------------------------------------------------------
 • 2x2 RAW kamera grid (doğrudan RTSP/WebCam - MJPEG yok)
 • Her kamera için GİRİŞ/ÇIKIŞ etiketi + kamera adı
 • CANLI PLAKA OVERLAY: Plaka okunduğunda araç bilgileri üst üste gelir,
   8 sn sonra otomatik kaybolur (plaka + sahip + site/blok + saat
   + kamera + yön + durum + bariyer)
 • Manuel "KAPI AÇ" butonu (ilgili NodeMCU'yu TETİK ASENKRON tetikler
   → UI donmaz, kamera akışı aksamaz)
 • Backend WebSocket'e bağlanır → log olaylarını canlı alır

 Kullanım:
   python desktop_camera.py
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

import cv2
import requests
import websocket  # websocket-client

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QImage, QPixmap, QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QSizePolicy,
    QGraphicsOpacityEffect, QMessageBox
)

# ---------------------------- Argparse --------------------------------
parser = argparse.ArgumentParser()
parser.add_argument("--api", default=os.environ.get("LPR_API", "http://127.0.0.1:8000"),
                    help="Backend API base URL (default http://127.0.0.1:8000)")
parser.add_argument("--slots", type=int, default=4, help="Kamera slot sayısı")
args, _ = parser.parse_known_args()

API_URL = args.api.rstrip("/")
# IPv6/IPv4 sorunu: localhost -> 127.0.0.1 zorla
API_URL = API_URL.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")
WS_URL = API_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
SLOT_COUNT = args.slots

# ------------------------- Kamera Thread -------------------------------
class CameraReaderThread(QThread):
    """RTSP/WebCam RAW okur, son kare'yi paylaşır. MJPEG KULLANMAZ."""
    frame_ready = pyqtSignal(int, object)
    status_changed = pyqtSignal(int, str)

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
                    time.sleep(0.03); continue
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    time.sleep(0.01); continue
                last_frame_t = time.time()
                self.frame_ready.emit(self.slot, frame)
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
                        if not msg: break
                        data = json.loads(msg)
                        self.event_received.emit(data)
                    except Exception:
                        break
                try: ws.close()
                except Exception: pass
            except Exception:
                pass
            time.sleep(2.0)


# ------------------------ HTTP Async Helper ---------------------------
def _async_http(method: str, url: str, on_done):
    """UI thread'i bloke etmeden HTTP çağrı yap."""
    def _run():
        try:
            if method == "POST":
                r = requests.post(url, timeout=3)
            else:
                r = requests.get(url, timeout=3)
            data = r.json() if r.status_code == 200 else {"status": "error", "message": r.text}
        except Exception as e:
            data = {"status": "error", "message": str(e)}
        on_done(data)
    threading.Thread(target=_run, daemon=True).start()


# -------------------------- Plaka Overlay Kartı -----------------------
class PlateOverlay(QFrame):
    """Kamera görüntüsünün üzerine bindirilen, plaka bilgisi gösteren yüzer kart."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("""
            QFrame#PlateOverlay {
                background-color: rgba(11, 14, 20, 0.92);
                border: 2px solid #6366f1;
                border-radius: 10px;
            }
            QLabel { background: transparent; color: #E2E8F0; border: none; }
        """)
        self.setObjectName("PlateOverlay")

        # Satır 1: Plaka (büyük) + DURUM rozeti
        self.plate_lbl = QLabel("- - -")
        self.plate_lbl.setAlignment(Qt.AlignCenter)
        self.plate_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 26px; font-weight: 900;"
            "color: #000; background: #ffffff; border: 2px solid #000;"
            "border-radius: 6px; padding: 4px 12px;"
        )
        self.plate_lbl.setMinimumHeight(44)

        self.status_lbl = QLabel("OKUNUYOR")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setFixedWidth(140)
        self.status_lbl.setStyleSheet(
            "font-weight: 900; font-size: 13px; letter-spacing: 1px;"
            "color: #ffffff; background: #6366f1; border-radius: 6px; padding: 8px;"
        )

        row1 = QHBoxLayout(); row1.setSpacing(8)
        row1.addWidget(self.plate_lbl, stretch=1)
        row1.addWidget(self.status_lbl)

        # Satır 2: Sahip + adres (site/blok)
        self.owner_lbl = QLabel("<b style='color:#fff'>-</b>")
        self.owner_lbl.setStyleSheet("font-size: 13px;")
        self.owner_lbl.setWordWrap(True)

        # Satır 3: Kamera + yön + saat
        self.meta_lbl = QLabel("")
        self.meta_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")

        # Satır 4: Bariyer durumu (renk ribbonu)
        self.gate_lbl = QLabel("")
        self.gate_lbl.setAlignment(Qt.AlignCenter)
        self.gate_lbl.setStyleSheet(
            "font-weight: 800; font-size: 12px; letter-spacing: 1px;"
            "color: #ffffff; background: #6366f1; border-radius: 6px; padding: 6px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(row1)
        layout.addWidget(self.owner_lbl)
        layout.addWidget(self.meta_lbl)
        layout.addWidget(self.gate_lbl)

        # Opacity efekt (fade-in/out için)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)

        self._fade = QPropertyAnimation(self._opacity, b"opacity")
        self._fade.setDuration(350)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.fade_out)

        self.hide()

    def show_event(self, plate: str, owner: str, site: str, block: str,
                   camera: str, direction: str, status: str, gate_opened: bool,
                   timestamp: str):
        # Plaka
        self.plate_lbl.setText(plate or "- - -")

        # Durum rengi
        palette = {
            "IZINLI":    ("#10b981", "#064e3b", "İZİNLİ"),
            "MİSAFİR":  ("#f59e0b", "#78350f", "MİSAFİR"),
            "MISAFIR":  ("#f59e0b", "#78350f", "MİSAFİR"),
            "YASAKLI":   ("#ef4444", "#7f1d1d", "YASAKLI"),
            "KOTA_DOLU": ("#ef4444", "#7f1d1d", "KOTA DOLU"),
        }
        color, bg, text = palette.get(status, ("#6366f1", "#312e81", status or "SORGULANIYOR"))
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(
            f"font-weight: 900; font-size: 13px; letter-spacing: 1px;"
            f"color: #ffffff; background: {color}; border-radius: 6px; padding: 8px;"
        )
        self.setStyleSheet(f"""
            QFrame#PlateOverlay {{
                background-color: rgba(11, 14, 20, 0.93);
                border: 2px solid {color};
                border-radius: 10px;
            }}
            QLabel {{ background: transparent; color: #E2E8F0; border: none; }}
        """)

        # Sahip + adres
        owner_txt = owner or "Tanımsız Araç"
        addr = ""
        if site or block:
            addr = f" <span style='color:#94a3b8'>•</span> <span style='color:#cbd5e1'>{site} {block}</span>"
        self.owner_lbl.setText(
            f"<span style='color:#94a3b8;font-size:10px'>SAHİP</span><br>"
            f"<b style='color:#ffffff;font-size:14px'>{owner_txt}</b>{addr}"
        )

        # Meta: kamera + yön + saat
        yon_html = ("<span style='color:#f59e0b;font-weight:700'>ÇIKIŞ</span>"
                    if direction == "CIKIS"
                    else "<span style='color:#10b981;font-weight:700'>GİRİŞ</span>")
        self.meta_lbl.setText(
            f"<span style='color:#64748b'>KAMERA:</span> <b>{camera or '-'}</b> &nbsp;&nbsp; "
            f"<span style='color:#64748b'>YÖN:</span> {yon_html} &nbsp;&nbsp; "
            f"<span style='color:#64748b'>SAAT:</span> <b style='font-family:Consolas;color:#fbbf24'>{timestamp or ''}</b>"
        )

        # Bariyer durumu
        if status == "IZINLI" and gate_opened:
            self.gate_lbl.setText("✓ BARİYER AÇILDI")
            self.gate_lbl.setStyleSheet(
                "font-weight: 800; font-size: 12px; letter-spacing: 1px;"
                "color: #ffffff; background: #10b981; border-radius: 6px; padding: 6px;"
            )
        elif status == "IZINLI":
            self.gate_lbl.setText("İZİNLİ (TETİK KAPALI)")
            self.gate_lbl.setStyleSheet(
                "font-weight: 800; font-size: 12px; letter-spacing: 1px;"
                "color: #ffffff; background: #f59e0b; border-radius: 6px; padding: 6px;"
            )
        elif status == "YASAKLI":
            self.gate_lbl.setText("✗ YASAKLI ARAÇ - REDDEDİLDİ")
            self.gate_lbl.setStyleSheet(
                "font-weight: 800; font-size: 12px; letter-spacing: 1px;"
                "color: #ffffff; background: #ef4444; border-radius: 6px; padding: 6px;"
            )
        elif status == "KOTA_DOLU":
            self.gate_lbl.setText("OTOPARK KOTASI DOLU")
            self.gate_lbl.setStyleSheet(
                "font-weight: 800; font-size: 12px; letter-spacing: 1px;"
                "color: #ffffff; background: #ea580c; border-radius: 6px; padding: 6px;"
            )
        else:
            self.gate_lbl.setText("GEÇİŞ İZNİ VERİLMEDİ")
            self.gate_lbl.setStyleSheet(
                "font-weight: 800; font-size: 12px; letter-spacing: 1px;"
                "color: #ffffff; background: #f59e0b; border-radius: 6px; padding: 6px;"
            )

        self.show()
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hide_timer.start(8000)  # 8 sn sonra fade out

    def fade_out(self):
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)

        def _done():
            if self._opacity.opacity() < 0.05:
                self.hide()

        self._fade.finished.connect(_done)
        self._fade.start()


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

        # Video alanı
        self.video_label = QLabel("● BEKLENİYOR")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #000; color: #64748b; font-size: 14px;"
            "border: 1px solid #1e293b; border-radius: 8px;"
        )
        self.video_label.setMinimumSize(320, 240)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Plaka overlay (video üstüne bindirilecek)
        self.plate_overlay = PlateOverlay(self.video_label)
        self.plate_overlay.setMinimumWidth(360)
        self.plate_overlay.setMaximumWidth(520)

        # Üst bar
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
        self.status_dot.setFixedWidth(90)
        self.status_dot.setStyleSheet(
            "color: #ef4444; font-weight: 700; font-size: 10px; padding: 4px 8px;"
            "background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; border-radius: 6px;"
        )

        top_bar = QHBoxLayout(); top_bar.setSpacing(6)
        top_bar.addWidget(self.title_label, stretch=1)
        top_bar.addWidget(self.direction_label)
        top_bar.addWidget(self.status_dot)

        # Alt bar (manuel buton + son işlem)
        self.info_label = QLabel("Hazır")
        self.info_label.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 4px 8px;")

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
            QPushButton:disabled { background: #334155; color: #64748b; }
        """)
        self.open_btn.clicked.connect(self.manual_open)

        bottom_bar = QHBoxLayout(); bottom_bar.setSpacing(6)
        bottom_bar.addWidget(self.info_label, stretch=1)
        bottom_bar.addWidget(self.open_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8); root.setSpacing(6)
        root.addLayout(top_bar)
        root.addWidget(self.video_label, stretch=1)
        root.addLayout(bottom_bar)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Overlay'i video etiketinin sağ-üstüne konumla
        try:
            ow = min(max(360, int(self.video_label.width() * 0.55)), 520)
            oh = min(170, int(self.video_label.height() * 0.55))
            self.plate_overlay.setGeometry(
                self.video_label.width() - ow - 10, 10, ow, oh
            )
        except Exception:
            pass

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
            has_gate = bool(cfg.get("gate_id"))
            self.open_btn.setEnabled(has_gate)
            self.open_btn.setToolTip("" if has_gate else "Bu kameraya kapı atanmamış")
        else:
            self.title_label.setText(f"● SLOT {self.slot} (BOŞ)")
            self.open_btn.setEnabled(False)

    def set_status(self, status: str):
        palette = {
            "ONLINE":     ("#10b981", "ONLINE"),
            "BAGLANIYOR": ("#f59e0b", "BAĞLANIYOR"),
            "OFFLINE":    ("#ef4444", "OFFLINE"),
            "BOS":        ("#64748b", "BOŞ SLOT"),
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
            scale = min(target_w / w, target_h / h)
            nw, nh = max(2, int(w * scale)), max(2, int(h * scale))
            small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb.data, nw, nh, 3 * nw, QImage.Format_RGB888)
            self.video_label.setPixmap(QPixmap.fromImage(qimg))
            # Overlay her update'ta geometriyi tazele
            try:
                ow = min(max(360, int(self.video_label.width() * 0.55)), 520)
                oh = min(170, int(self.video_label.height() * 0.55))
                self.plate_overlay.setGeometry(
                    self.video_label.width() - ow - 10, 10, ow, oh
                )
            except Exception:
                pass
        except Exception:
            pass

    def show_plate_event(self, plate: str, status: str, owner: str,
                         gate_opened: bool, direction: str, camera: str,
                         site: str, block: str, timestamp: str):
        self.plate_overlay.show_event(
            plate=plate, owner=owner, site=site, block=block,
            camera=camera, direction=direction, status=status,
            gate_opened=gate_opened, timestamp=timestamp
        )
        # Alt info barda kısa özet
        color = {"IZINLI": "#10b981", "YASAKLI": "#ef4444",
                 "KOTA_DOLU": "#ef4444", "MİSAFİR": "#f59e0b",
                 "MISAFIR": "#f59e0b"}.get(status, "#6366f1")
        self.info_label.setText(
            f"<span style='color:#cbd5e1'>Son:</span> <b style='color:#fff'>{plate}</b> "
            f"<span style='color:{color};font-weight:700'>{status}</span>"
        )

    def manual_open(self):
        """Kapı açma — ASENKRON (UI thread donmasın)."""
        self.open_btn.setEnabled(False)
        self.open_btn.setText("GÖNDERİLİYOR...")
        self.info_label.setText("<span style='color:#f59e0b'>Kapıya tetik gönderiliyor...</span>")

        def _on_done(data):
            # UI güncelleme ana thread'te olmalı → QTimer.singleShot
            def _apply():
                self.open_btn.setEnabled(True)
                self.open_btn.setText("KAPI AÇ")
                if data.get("status") == "ok":
                    self.info_label.setText(
                        f"<span style='color:#10b981;font-weight:700'>✓ {data.get('message','Kapı açıldı')}</span>"
                    )
                    QTimer.singleShot(4000, lambda: self.info_label.setText("Hazır"))
                else:
                    QMessageBox.warning(self, "Kapı Açma Hatası",
                                        data.get("message", "Bilinmeyen hata"))
                    self.info_label.setText("Hazır")
            QTimer.singleShot(0, _apply)

        _async_http("POST", f"{self.api_url}/api/gate/manual-open/{self.slot}", _on_done)


# ----------------------------- Ana Pencere ----------------------------
class MainWindow(QMainWindow):
    def __init__(self, api_url: str, ws_url: str, slot_count: int = 4):
        super().__init__()
        self.setWindowTitle("EVO SMART LPR — Canlı Kamera Monitörü")
        self.resize(1280, 820)
        self.setStyleSheet("QMainWindow { background-color: #0B0E14; }")

        self.api_url = api_url
        self.ws_url = ws_url
        self._site_cache = {}  # plate -> (site, block) cache (hızlı lookup)

        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(12, 12, 12, 12); root.setSpacing(10)

        # Header
        header = QHBoxLayout()
        title = QLabel("<b style='color:#ffffff;font-size:16px;letter-spacing:1px'>EVO SMART</b> "
                       "<span style='color:#6366f1;font-size:11px;font-family:Consolas'>CANLI MONİTÖR</span>")
        self.status_hdr = QLabel("● Backend: bağlanılıyor")
        self.status_hdr.setStyleSheet("color:#f59e0b; font-weight:700; font-size:11px;")
        header.addWidget(title); header.addStretch(1); header.addWidget(self.status_hdr)
        root.addLayout(header)

        # Kamera grid
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

        # Config + residents refresh
        self.cfg_timer = QTimer(self)
        self.cfg_timer.timeout.connect(self.refresh_cameras)
        self.cfg_timer.start(8000)
        # Agresif ilk bağlantı: 0 / 1.5 / 3.5 sn
        QTimer.singleShot(0, self.refresh_cameras)
        QTimer.singleShot(1500, self.refresh_cameras)
        QTimer.singleShot(3500, self.refresh_cameras)

        self.res_timer = QTimer(self)
        self.res_timer.timeout.connect(self.refresh_residents)
        self.res_timer.start(60000)
        QTimer.singleShot(800, self.refresh_residents)

        # Baslangic teshis logu (cmd penceresinde gorunur)
        print("=" * 60)
        print(f"[EVO DESKTOP] API URL  : {api_url}")
        print(f"[EVO DESKTOP] WS URL   : {ws_url}")
        print(f"[EVO DESKTOP] Slot say.: {slot_count}")
        print("=" * 60)

    def refresh_cameras(self):
        url = f"{self.api_url}/api/cameras"

        def _apply_success(cams):
            self.status_hdr.setText(f"● Backend: bağlı ({self.api_url})")
            self.status_hdr.setStyleSheet("color:#10b981; font-weight:700; font-size:11px;")
            by_slot = {c.get("slot"): c for c in cams}
            for slot, w in self.cameras.items():
                cfg = by_slot.get(slot)
                w.set_config(cfg)
                self.readers[slot].set_config(cfg)

        def _apply_error(err_msg):
            short = (err_msg[:60] + "…") if len(err_msg) > 60 else err_msg
            self.status_hdr.setText(f"● Backend: OFFLINE — {short}")
            self.status_hdr.setStyleSheet("color:#ef4444; font-weight:700; font-size:11px;")

        def _run():
            try:
                r = requests.get(url, timeout=3)
                if r.status_code == 200:
                    try:
                        cams = r.json()
                    except Exception as e:
                        print(f"[BACKEND] JSON parse hatasi: {e}")
                        QTimer.singleShot(0, lambda: _apply_error(f"JSON parse: {e}"))
                        return
                    if isinstance(cams, list):
                        print(f"[BACKEND] Baglandi: {len(cams)} kamera config alindi. URL={url}")
                        QTimer.singleShot(0, lambda: _apply_success(cams))
                    else:
                        QTimer.singleShot(0, lambda: _apply_error("beklenmeyen format"))
                else:
                    print(f"[BACKEND] HTTP {r.status_code}: {url}")
                    QTimer.singleShot(0, lambda: _apply_error(f"HTTP {r.status_code}"))
            except Exception as e:
                print(f"[BACKEND] Baglanti hatasi: {url} -> {e}")
                err = str(e)
                QTimer.singleShot(0, lambda: _apply_error(err))
        threading.Thread(target=_run, daemon=True).start()

    def refresh_residents(self):
        """Plaka → (site, blok) hızlı cache."""
        def _run():
            try:
                r = requests.get(f"{self.api_url}/api/residents", timeout=5)
                if r.status_code == 200:
                    res = r.json()
                    cache = {}
                    for person in res:
                        site = person.get("site_name", "") or ""
                        block = person.get("block_name", "") or ""
                        flat = person.get("flat_number", "") or ""
                        for p in person.get("plates", []):
                            plate_txt = (p.get("plate") or "").upper().replace(" ", "")
                            if plate_txt:
                                cache[plate_txt] = {
                                    "site": site, "block": block,
                                    "flat": flat, "name": person.get("name", "")
                                }
                    self._site_cache = cache
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

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
                plate = (data.get("plate") or "").upper().replace(" ", "")
                info = self._site_cache.get(plate, {})
                site = info.get("site", "")
                block = info.get("block", "")
                # Backend'den gelen owner zaten "Blok / İsim" formatında
                owner = data.get("owner", "") or ""
                if not owner and info.get("name"):
                    owner = info["name"]
                ts = (data.get("time") or data.get("timestamp") or "").strip()
                w.show_plate_event(
                    plate=plate,
                    status=data.get("status", ""),
                    owner=owner,
                    gate_opened=bool(data.get("gate_opened")),
                    direction=data.get("direction", ""),
                    camera=w.camera_name,
                    site=site, block=block,
                    timestamp=ts,
                )
        elif t == "manual_open":
            slot = data.get("slot")
            w = self.cameras.get(slot)
            if w:
                w.info_label.setText(
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
