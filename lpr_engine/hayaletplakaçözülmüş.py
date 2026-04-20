import cv2
import time
import requests
import threading
import numpy as np
import re
import os
import warnings
import logging
import queue
from flask import Flask, Response, send_from_directory
from ultralytics import YOLO
from collections import Counter
from datetime import datetime
import easyocr

# --- AYARLAR ---
warnings.simplefilter('ignore')
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|buffer_size;1024"

API_URL = "http://localhost:8000"
CURRENT_FILE_PATH = os.path.abspath(__file__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_FILE_PATH))
IMAGE_FOLDER = os.path.join(PROJECT_ROOT, 'captured_images')

if not os.path.exists(IMAGE_FOLDER): os.makedirs(IMAGE_FOLDER)

app = Flask(__name__)

# --- GLOBAL ---
jpeg_buffers = {} 
raw_frames = {}
detection_overlays = {} 
buffer_lock = threading.Lock()
# Kuyruk boyutu MİNİMUM (Gecikme olmasın diye)
ocr_queue = queue.Queue(maxsize=4) 

print("--- FINAL SYSTEM v56.0 (SECURITY PATCH + ZERO LAG) ---")

# --- MODEL ---
print(">> 1. Model Yükleniyor...")
MODEL_PATH = os.path.join(PROJECT_ROOT, 'lpr_engine', 'plate_model.pt')
if not os.path.exists(MODEL_PATH): MODEL_PATH = os.path.join(PROJECT_ROOT, 'plate_model.pt')

if os.path.exists(MODEL_PATH):
    GLOBAL_YOLO = YOLO(MODEL_PATH)
    TARGET_CLASSES = [0]
    IS_CUSTOM_MODEL = True
    print(">> ✅ Özel Model Aktif.")
else:
    GLOBAL_YOLO = YOLO("yolov8n.pt")
    TARGET_CLASSES = [2, 3]
    IS_CUSTOM_MODEL = False

print(">> 2. EasyOCR Hazırlanıyor...")
GLOBAL_READER = easyocr.Reader(['en'], gpu=False, verbose=False, quantize=True)

# --- TEMİZLEYİCİ ---
class TRPlateCleaner:
    @staticmethod
    def clean(text):
        if not text: return None
        raw = re.sub(r'[^A-Z0-9]', '', text.upper())
        if len(raw) < 7 or len(raw) > 9: return None
        if len(raw) == 9 and raw[-1] in ['1', 'I', 'J', 'L']: raw = raw[:-1]
        if len(raw) < 7: return None

        chars = list(raw)
        num_map = {'O': '0', 'D': '0', 'Q': '0', 'U': '0', 'Z': '2', 'I': '1', 'L': '1', 'B': '8', 'S': '5', 'G': '6'}
        let_map = {'0': 'O', '1': 'I', '2': 'Z', '5': 'S', '8': 'B', '4': 'A', '6': 'G'}

        if chars[0] in num_map: chars[0] = num_map[chars[0]]
        if chars[1] in num_map: chars[1] = num_map[chars[1]]
        if chars[-1] in num_map: chars[-1] = num_map[chars[-1]]
        if len(chars) > 2 and chars[2] in let_map: chars[2] = let_map[chars[2]]

        final = "".join(chars)
        # Regex: 2 Rakam + Harf + Rakam
        if re.match(r'^\d{2}[A-Z]{1,3}\d{2,4}$', final): return final
        return None

# --- OCR İŞÇİSİ ---
class OCRWorker(threading.Thread):
    def __init__(self): super().__init__(); self.daemon = True
    def run(self):
        print(">> OCR Hazır.")
        while True:
            try:
                task = ocr_queue.get()
                if task is None: break
                self.process(*task)
                ocr_queue.task_done()
            except: pass
    
    def process(self, camera, img, full_frame):
        try:
            # 1. Görüntü İşleme (Hızlı)
            img = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            img_final = cv2.copyMakeBorder(gray, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=(255, 255, 255))
            
            results = GLOBAL_READER.readtext(img_final, detail=1, paragraph=False)
            if not results: return
            
            merged_text = ""
            avg_conf = 0.0
            count = 0
            
            for (_, text, conf) in results:
                if conf < 0.30: continue
                if "WEBCAM" in text.upper(): continue
                merged_text += text
                avg_conf += conf
                count += 1
            
            if count > 0: avg_conf = avg_conf / count
            if len(merged_text) < 4: return

            cleaned = TRPlateCleaner.clean(merged_text)
            
            if cleaned:
                # print(f"[{camera.camera_id}] OKUNDU: {cleaned} (%{int(avg_conf*100)})")
                
                # --- GÜVENLİK KİLİDİ ---
                # Eğer bu plaka son 10 saniye içinde zaten okunduysa ve kapı açıldıysa,
                # tekrar işleme alma (Arkadan gelen araca yanlışlıkla kapı açmasın diye)
                if cleaned == camera.last_sent_plate and (time.time() - camera.last_sent_time < 10.0):
                    # print(f"⛔ [{camera.camera_id}] {cleaned} zaten yeni geçti, yoksayılıyor.")
                    return

                camera.plate_buffer.append(cleaned)
                
                # --- HIZLI OYLAMA (3'te 2) ---
                if len(camera.plate_buffer) >= 3:
                    most_common, matches = Counter(camera.plate_buffer).most_common(1)[0]
                    
                    if matches >= 2:
                        print(f"🚀🚀🚀 [{camera.camera_id}] KESİNLEŞTİ: {most_common}")
                        
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"{most_common}_{ts}_{camera.camera_id}.jpg"
                        try: cv2.imwrite(os.path.join(IMAGE_FOLDER, filename), full_frame)
                        except: pass
                        
                        threading.Thread(target=camera.send_data, args=(most_common, filename)).start()
                        
                        # --- KRİTİK: HAFİZA TEMİZLİĞİ ---
                        camera.last_sent_plate = most_common
                        camera.last_sent_time = time.time()
                        camera.plate_buffer = [] # Tamponu SİL! (Eski plaka kalmasın)
                        
                        with buffer_lock:
                            if camera.camera_id in detection_overlays:
                                detection_overlays[camera.camera_id]['text'] = most_common
                                detection_overlays[camera.camera_id]['timer'] = time.time() + 4.0
                    else:
                        # Kararsızlık varsa bufferı kısalt (son 2'yi tut)
                         camera.plate_buffer = camera.plate_buffer[-2:]

        except Exception as e:
            print(f"Hata: {e}")

# --- KAMERA ---
class CameraStream(threading.Thread):
    def __init__(self, slot_id):
        super().__init__(); self.slot_id = slot_id; self.camera_id = f"CAM-0{slot_id}"; self.config = None; self.cap = None; self.is_connected = False; self.running = True; self.cap_lock = threading.Lock(); self.plate_buffer = []; self.last_sent_plate = ""; self.last_sent_time = 0; self.update_black_frame("BAŞLATILIYOR")
    
    def update_config(self, new_config):
        if self.config == new_config: return
        with self.cap_lock:
            self.config = new_config; self.is_connected = False
            if self.cap: 
                try: self.cap.release()
                except: pass
                self.cap = None
        if self.config: print(f"[{self.camera_id}] Ayarlandı: {self.config['name']}")
        else: self.update_black_frame("SLOT BOŞ")
    
    def connect(self):
        with self.cap_lock:
            try:
                src = self.config['source']
                if self.config['type'] == 'WEBCAM':
                    if str(src).isdigit(): src = int(src)
                    self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW); self.cap.set(cv2.CAP_PROP_FPS, 30)
                else:
                    url = src.replace("rtsp://", f"rtsp://{self.config['rtsp_user']}:{self.config['rtsp_pass']}@") if self.config.get('rtsp_user') else src
                    if os.name == 'nt': self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    else: self.cap = cv2.VideoCapture(url)
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if self.cap.isOpened(): self.is_connected = True; return True
            except: pass
            self.is_connected = False; return False
    
    def update_black_frame(self, text):
        img = np.zeros((480, 640, 3), dtype=np.uint8); cv2.putText(img, text, (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2); cv2.putText(img, self.camera_id, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2); _, jpeg = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
        with buffer_lock: jpeg_buffers[self.camera_id] = jpeg.tobytes()
    
    def send_data(self, plate, image_filename):
        try: requests.post(f"{API_URL}/api/log-plate", json={"plate": plate, "confidence": 0.99, "camera_id": self.camera_id, "image_path": f"{API_URL}/images/{image_filename}"}, timeout=1.0)
        except: pass
    
    def run(self):
        while self.running:
            if not self.config: time.sleep(1); continue
            if not self.is_connected:
                self.update_black_frame("BAGLANIYOR..."); 
                if self.connect(): 
                    with buffer_lock: detection_overlays[self.camera_id] = {'boxes':[], 'text':'', 'timer':0}
                else: time.sleep(2); continue
            
            frame = None
            with self.cap_lock:
                if self.cap and self.cap.isOpened():
                    try: ret, frame = self.cap.read()
                    except: ret = False
                    if not ret: self.is_connected = False; self.cap.release()
            if frame is None: continue
            
            frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_NEAREST)
            with buffer_lock:
                raw_frames[self.camera_id] = frame 
                display_frame = frame 
                if self.camera_id in detection_overlays:
                    overlay = detection_overlays[self.camera_id]
                    for box in overlay.get('boxes', []):
                        cv2.rectangle(display_frame, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)
                    if overlay.get('text') and time.time() < overlay.get('timer', 0):
                        plate = overlay['text']
                        # Yeşil Kutu
                        cv2.rectangle(display_frame, (50, 50), (300, 110), (0, 128, 0), -1)
                        cv2.putText(display_frame, plate, (60, 95), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
                cv2.putText(display_frame, self.config['name'], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                _, jpeg = cv2.imencode('.jpg', display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
                jpeg_buffers[self.camera_id] = jpeg.tobytes()
            time.sleep(0.01)

class CentralDetector(threading.Thread):
    def __init__(self): super().__init__(); self.daemon = True; self.last_check = 0
    def run(self):
        print(">> Dedektör Devrede.")
        while True:
            if time.time() - self.last_check < 0.10: time.sleep(0.01); continue
            self.last_check = time.time()
            with buffer_lock: active_cams = list(raw_frames.items())
            if not active_cams: continue
            for cam_id, frame in active_cams:
                try:
                    results = GLOBAL_YOLO(frame, verbose=False, conf=0.25, classes=TARGET_CLASSES)
                    boxes_found = []
                    for r in results:
                        boxes = r.boxes.xyxy.cpu().numpy()
                        for box in boxes:
                            x1, y1, x2, y2 = map(int, box[:4])
                            if x1 < 50 and y1 < 50: continue
                            if (x2-x1) < 40: continue
                            boxes_found.append((x1, y1, x2, y2))
                            plate_crop = frame[y1:y2, x1:x2].copy()
                            
                            target_cam = None
                            for engine in engines: 
                                if engine.camera_id == cam_id: target_cam = engine; break
                            
                            if target_cam and plate_crop.size > 0 and ocr_queue.qsize() < 3:
                                try: ocr_queue.put_nowait((target_cam, plate_crop, frame.copy()))
                                except: pass
                            break 
                    with buffer_lock:
                        if cam_id in detection_overlays:
                            detection_overlays[cam_id]['boxes'] = boxes_found
                except: pass

def generate_mjpeg(camera_id):
    while True:
        with buffer_lock: jpeg_data = jpeg_buffers.get(camera_id)
        if jpeg_data: yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + jpeg_data + b'\r\n')
        else: time.sleep(0.1); continue
        time.sleep(0.05)

@app.route('/video_feed/<camera_id>')
def video_feed(camera_id): return Response(generate_mjpeg(camera_id), mimetype='multipart/x-mixed-replace; boundary=frame')
@app.route('/images/<path:filename>')
def serve_image(filename): return send_from_directory(IMAGE_FOLDER, filename)
def config_listener(streams):
    while True:
        try:
            res = requests.get(f"{API_URL}/api/cameras", timeout=2)
            if res.status_code == 200:
                data = res.json()
                slot_map = {1: None, 2: None, 3: None, 4: None}
                for cam in data: slot_map[cam['slot']] = cam
                for stream in streams: stream.update_config(slot_map[stream.slot_id])
        except: pass
        time.sleep(5) 

if __name__ == "__main__":
    OCRWorker().start()
    engines = [] 
    streams = engines 
    for i in range(4):
        stream = CameraStream(slot_id=i+1)
        t = threading.Thread(target=stream.run, daemon=True) 
        t.start()
        engines.append(stream)
    CentralDetector().start()
    threading.Thread(target=config_listener, args=(engines,), daemon=True).start()
    print(">> Sistem Hazır. Web Arayüzü Bekleniyor...")
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False, threaded=True)