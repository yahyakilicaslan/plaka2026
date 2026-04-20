# =========================================================
# EvoSmart LPR v60.1 – RTSP Stabil Final (Single File)
# =========================================================

import os, cv2, time, threading, queue, requests, numpy as np, re
from flask import Flask, Response
from ultralytics import YOLO
from collections import Counter
from datetime import datetime

# =========================================================
# FFMPEG – RTSP GLOBAL AYAR (SADECE 1 KERE)
# =========================================================
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|"
    "buffer_size;524288|"
    "max_delay;200000|"
    "stimeout;5000000|"
    "reorder_queue_size;0|"
    "fflags;nobuffer+discardcorrupt|"
    "flags;low_delay|"
    "analyzeduration;1000000"
)

# =========================================================
# GENEL AYARLAR
# =========================================================
API_URL = "http://localhost:8000"
RESOLUTION = (640, 480)
JPEG_QUALITY = 70

app = Flask(__name__)
jpeg_buffers = {}
raw_frames = {}
buffer_lock = threading.Lock()
ocr_queue = queue.Queue(maxsize=5)

# =========================================================
# YOLO MODEL
# =========================================================
YOLO_MODEL = YOLO("yolov8n.pt")

# =========================================================
# PLAKA TEMİZLEYİCİ
# =========================================================
class TRPlateCleaner:
    @staticmethod
    def clean(text):
        text = re.sub(r'[^A-Z0-9]', '', text.upper())
        if re.match(r'^\d{2}[A-Z]{1,3}\d{2,4}$', text):
            return text
        return None

# =========================================================
# RTSP OKUYUCU (TEK SORUMLU)
# =========================================================
class RTSPStreamReader:
    def __init__(self, url, cam_id):
        self.url = url
        self.cam_id = cam_id
        self.cap = None
        self.frame = None
        self.lock = threading.Lock()
        self.running = False
        self.connected = False
        self.last_frame = 0

    def _open(self):
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FPS, 15)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            return cap
        return None

    def _loop(self):
        retry = 1
        while self.running:
            if self.cap is None:
                self.cap = self._open()
                if self.cap:
                    print(f"✅ [{self.cam_id}] RTSP Bağlandı")
                    self.connected = True
                    retry = 1
                else:
                    time.sleep(retry)
                    retry = min(retry * 2, 10)
                    continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                self.connected = False
                try:
                    self.cap.release()
                except:
                    pass
                self.cap = None
                time.sleep(1)
                continue

            with self.lock:
                self.frame = frame
                self.last_frame = time.time()

            self.connected = True
            time.sleep(0.005)

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def get_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

# =========================================================
# KAMERA THREAD
# =========================================================
class CameraStream(threading.Thread):
    def __init__(self, cam_id, rtsp_url):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.reader = RTSPStreamReader(rtsp_url, cam_id)
        self.reader.start()

    def run(self):
        while True:
            frame = self.reader.get_frame()
            if frame is None:
                time.sleep(0.02)
                continue

            frame = cv2.resize(frame, RESOLUTION)
            raw_frames[self.cam_id] = frame

            disp = frame.copy()
            _, jpeg = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            with buffer_lock:
                jpeg_buffers[self.cam_id] = jpeg.tobytes()

            time.sleep(0.01)

# =========================================================
# MERKEZ DEDEKTÖR (YOLO)
# =========================================================
class CentralDetector(threading.Thread):
    def run(self):
        print(">> Dedektör aktif")
        while True:
            time.sleep(0.1)
            with buffer_lock:
                cams = list(raw_frames.items())

            for cam_id, frame in cams:
                results = YOLO_MODEL(frame, conf=0.25, imgsz=640, verbose=False)
                for r in results:
                    for box in r.boxes.xyxy.cpu().numpy():
                        x1, y1, x2, y2 = map(int, box)
                        crop = frame[y1:y2, x1:x2]
                        if crop.size == 0:
                            continue
                        ocr_queue.put((cam_id, crop, frame.copy()))
                        break

# =========================================================
# OCR WORKER (BASİT)
# =========================================================
class OCRWorker(threading.Thread):
    def run(self):
        while True:
            cam_id, crop, full = ocr_queue.get()
            plate = TRPlateCleaner.clean("34ABC123")  # demo
            if plate:
                print(f"🚀 [{cam_id}] PLAKA: {plate}")
            ocr_queue.task_done()

# =========================================================
# MJPEG
# =========================================================
def mjpeg(cam_id):
    while True:
        with buffer_lock:
            data = jpeg_buffers.get(cam_id)
        if data:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
        time.sleep(0.03)

@app.route("/video/<cam_id>")
def video(cam_id):
    return Response(mjpeg(cam_id), mimetype="multipart/x-mixed-replace; boundary=frame")

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    print(">> Sistem başlatılıyor")

    OCRWorker(daemon=True).start()
    CentralDetector(daemon=True).start()

    cam1 = CameraStream("CAM-01", "rtsp://admin:admin123@10.7.0.107:554/rtsp/stream")
    cam1.start()

    print("✅ RTSP STABİL SİSTEM HAZIR")
    app.run("0.0.0.0", 5001, threaded=True)
