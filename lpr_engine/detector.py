"""
EvoSmart LPR v77.50 - Enterprise Ultra-Vision Engine
=============================================================
DÜZELTME: Kamerayı çökerten 'current_config_sig' hatası giderildi.
=============================================================
"""

import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import cv2
import time
import requests
import threading
import numpy as np
import re
import warnings
import logging
import json
from logging.handlers import RotatingFileHandler
import queue
from datetime import datetime
from collections import Counter
from flask import Flask, Response, send_from_directory, jsonify
from flask_cors import CORS
from openvino.runtime import Core

warnings.simplefilter('ignore')

API_URL = "http://localhost:8000"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_FOLDER = os.path.join(PROJECT_ROOT, 'captured_images')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
MODELS_DIR = os.path.join(PROJECT_ROOT, 'models')
PADDLE_BASE = os.path.join(MODELS_DIR, 'paddle_models')

os.makedirs(IMAGE_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log_file = os.path.join(PROJECT_ROOT, 'evosmart_lpr.log')
file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
file_handler.setFormatter(log_formatter)

logger = logging.getLogger('EvoSmart_LPR')
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

app = Flask(__name__)
CORS(app)

OCR_STATUS = {'status': 'active', 'paddle_status': 'loading', 'total_reads': 0, 'last_result': '---'}
ocr_status_lock = threading.Lock()

jpeg_buffers = {}
latest_frames = {}
latest_frames_lock = threading.Lock()

detection_overlays = {}
buffer_lock = threading.Lock()
ocr_queue = queue.Queue(maxsize=5)
api_queue = queue.Queue(maxsize=50)
engines = []
engine_map = {} 
api_workers = [] 

SYSTEM_SETTINGS = {
    'rtsp_transport': 'tcp', 
    'frame_skip': 2, 
    'ai_interval': 0.06,
    'ocr_conf_threshold': 0.40,
    'vote_count': 3,
    'vote_accept': 2,
    'debounce_time': 15,
    'api_worker_count': 3,
    'tracking_enabled': False,
    'gpu_acceleration': False,
    'motion_sleep': False,
    'anti_blur': False,
    'high_speed_tracker': False,
    'dynamic_turbo': False
}

print("\n" + "="*60)
print("   EVOSMART AI MOTOR DOĞRULAMA SİSTEMİ (ULTRA EDITION)")
print("="*60)
logger.info("Sistem başlatılıyor...")

class OpenVINODetector:
    def __init__(self, xml, bin):
        self.core = Core()
        self.model = self.core.read_model(model=xml, weights=bin)
        self.xml_path = xml
        self.bin_path = bin
        self.current_device = "CPU"
        self.lock = threading.Lock()
        self._compile_model()

    def _compile_model(self):
        print(f"🔄 [DETEKTÖR] OpenVINO Motoru {self.current_device} üzerinden derleniyor...")
        self.compiled_model = self.core.compile_model(model=self.model, device_name=self.current_device)
        self.output_layer = self.compiled_model.output(0)
        print(f"✅ [DETEKTÖR] Motor Hazır! Aktif Cihaz: {self.current_device}")

    def update_device(self, use_gpu):
        target_device = "AUTO" if use_gpu else "CPU"
        if target_device != self.current_device:
            with self.lock: 
                self.current_device = target_device
                try:
                    self._compile_model()
                except Exception as e:
                    print(f"⚠️ [UYARI] GPU desteği bulunamadı veya hata oluştu. CPU'ya dönülüyor... Hata: {e}")
                    self.current_device = "CPU"
                    self._compile_model()

    def detect(self, image):
        with self.lock:
            h, w = image.shape[:2]
            blob = cv2.resize(image, (640, 640))
            blob = blob.transpose((2, 0, 1))[np.newaxis, ...].astype(np.float32) / 255.0
            res = self.compiled_model([blob])[self.output_layer]
            res = np.squeeze(res).transpose()
            boxes = []
            for row in res:
                if row[4] > 0.65:
                    xc, yc, nw, nh = row[:4]
                    x1, y1 = int((xc - nw/2) * (w / 640)), int((yc - nh/2) * (h / 640))
                    x2, y2 = int((xc + nw/2) * (w / 640)), int((yc + nh/2) * (h / 640))
                    boxes.append([max(0, x1), max(0, y1), min(w, x2), min(h, y2)])
            return boxes[:1]

try:
    DETECTOR = OpenVINODetector(os.path.join(MODELS_DIR, 'license_plate_detector.xml'), os.path.join(MODELS_DIR, 'license_plate_detector.bin'))
except Exception as e:
    print(f"❌ [HATA] Dedektör Başlatılamadı: {e}")
    DETECTOR = None

class APIWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        
    def run(self):
        while True:
            try:
                payload = api_queue.get()
                frame = payload.pop("frame")
                fn = payload.pop("file_name")
                cv2.imwrite(os.path.join(IMAGE_FOLDER, fn), frame)
                payload["image_path"] = f"{API_URL}/images/{fn}"
                
                try:
                    resp = requests.post(f"{API_URL}/api/log-plate", json=payload, timeout=3)
                    if resp.status_code == 200:
                        data = resp.json()
                        cam_id = payload.get('camera_id')
                        plt_txt = payload.get('plate')
                        with buffer_lock:
                            if cam_id in detection_overlays and detection_overlays[cam_id].get('text') == plt_txt:
                                detection_overlays[cam_id]['status'] = data.get('plate_status', 'MİSAFİR')
                except Exception as e:
                    logger.error(f"❌ [API BAĞLANTI HATASI] Backend ile iletişim kurulamadı: {e}")
                finally:
                    api_queue.task_done()
            except Exception as e:
                pass

def adjust_api_workers(target_count):
    while len(api_workers) < target_count:
        w = APIWorker()
        w.start()
        api_workers.append(w)

class OCRWorker(threading.Thread):
    def __init__(self, worker_id):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        from paddleocr import PaddleOCR
        try:
            self.local_paddle = PaddleOCR(use_angle_cls=True, lang='en', use_gpu=False, show_log=False,
                                 det_model_dir=os.path.join(PADDLE_BASE, 'ch_PP-OCRv4_det'),
                                 rec_model_dir=os.path.join(PADDLE_BASE, 'en_PP-OCRv4_rec'),
                                 cls_model_dir=os.path.join(PADDLE_BASE, 'ch_ppocr_mobile_v2.0_cls'),
                                 enable_mkldnn=True, download_enabled=False)
            with ocr_status_lock:
                OCR_STATUS['paddle_status'] = 'ready'
            print(f"✅ [OKUYUCU] Worker-{self.worker_id} PaddleOCR v4 Başlatıldı")
        except Exception as e:
            print(f"❌ [HATA] Worker-{self.worker_id} PaddleOCR Başlatılamadı: {e}")
            self.local_paddle = None

    def format_turkish_plate(self, raw_plate):
        p = re.sub(r'[^A-Z0-9]', '', raw_plate.upper())
        if len(p) < 7 or len(p) > 9: return None
        il_kodu = list(p[:2])
        for i in range(2):
            if il_kodu[i] in ['O', 'D']: il_kodu[i] = '0'
            elif il_kodu[i] == 'I': il_kodu[i] = '1'
            elif il_kodu[i] == 'Z': il_kodu[i] = '2'
            elif il_kodu[i] == 'B': il_kodu[i] = '8'
            elif il_kodu[i] == 'S': il_kodu[i] = '5'
            elif il_kodu[i] == 'G': il_kodu[i] = '6'
        il_kodu_str = "".join(il_kodu)
        if not il_kodu_str.isdigit(): return None
        
        harf_bitis = -1
        for i in range(len(p)-1, 1, -1):
            if p[i].isalpha(): harf_bitis = i; break
        if harf_bitis == -1: return None
            
        orta_blok = list(p[2:harf_bitis+1])
        son_blok = list(p[harf_bitis+1:])
        
        for i in range(len(orta_blok)):
            if orta_blok[i] == '0': orta_blok[i] = 'D'
            elif orta_blok[i] == '8': orta_blok[i] = 'B'
            elif orta_blok[i] == '1': orta_blok[i] = 'I'
            elif orta_blok[i] == '5': orta_blok[i] = 'S'
            elif orta_blok[i] == '2': orta_blok[i] = 'Z'
            elif orta_blok[i] == '4': orta_blok[i] = 'A'
            
        for i in range(len(son_blok)):
            if son_blok[i] in ['O', 'D']: son_blok[i] = '0'
            elif son_blok[i] == 'B': son_blok[i] = '8'
            elif son_blok[i] == 'I': son_blok[i] = '1'
            elif son_blok[i] == 'Z': son_blok[i] = '2'
            elif son_blok[i] == 'S': son_blok[i] = '5'
            elif son_blok[i] == 'G': son_blok[i] = '6'
            
        orta_str = "".join(orta_blok)
        son_str = "".join(son_blok)
        final_plate = il_kodu_str + orta_str + son_str
        
        if re.match(r'^(0[1-9]|[1-7][0-9]|8[0-1])[A-Z]{1,3}[0-9]{2,4}$', final_plate): return final_plate
        return None

    def run(self):
        while True:
            try:
                cam, crop, full, box = ocr_queue.get(timeout=1)
                if self.local_paddle:
                    if SYSTEM_SETTINGS.get('anti_blur'):
                        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
                        crop = cv2.filter2D(crop, -1, kernel)

                    res = self.local_paddle.ocr(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cls=True)
                    if res and res[0]:
                        txt = "".join([l[1][0] for l in res[0] if l[1][1] > SYSTEM_SETTINGS['ocr_conf_threshold']])
                        formatted_plate = self.format_turkish_plate(txt)
                        if formatted_plate: self.process_vote(cam, formatted_plate, full, res[0][0][1][1], box)
                ocr_queue.task_done()
            except queue.Empty: continue

    def process_vote(self, cam, p, frame, conf, box):
        now = time.time()
        with cam.vote_lock:
            if p == cam.last_sent and (now - cam.last_time < SYSTEM_SETTINGS['debounce_time']): return
            
            cam.plate_buffer.append((p, conf, box))
            if len(cam.plate_buffer) >= SYSTEM_SETTINGS['vote_count']:
                plate_counts = {}
                plate_conf_sums = {}
                for plt, cnf, bx in cam.plate_buffer:
                    plate_counts[plt] = plate_counts.get(plt, 0) + 1
                    plate_conf_sums[plt] = plate_conf_sums.get(plt, 0) + cnf
                
                valid_plates = [plt for plt, count in plate_counts.items() if count >= SYSTEM_SETTINGS['vote_accept']]
                
                if valid_plates:
                    best_plate = max(valid_plates, key=lambda plt: plate_conf_sums[plt])
                    avg_conf = plate_conf_sums[best_plate] / plate_counts[best_plate]
                    best_box = next(bx for plt, cnf, bx in reversed(cam.plate_buffer) if plt == best_plate)
                    
                    if best_plate != cam.last_sent or (now - cam.last_time >= SYSTEM_SETTINGS['debounce_time']):
                        self.finalize_log(cam, best_plate, frame, avg_conf, now, best_box)
                    
                    cam.plate_buffer.clear()
                else:
                    cam.plate_buffer.pop(0)

    def finalize_log(self, cam, p, frame, conf, now, box):
        timestamp = datetime.now().strftime('%H:%M:%S')
        logger.info(f"🚀 [OKUNDU] Plaka: {p.ljust(10)} | Güven: %{int(conf*100)} | Kamera: {cam.camera_id}")
        
        with ocr_status_lock:
            OCR_STATUS['total_reads'] += 1
            OCR_STATUS['last_result'] = p
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"{p}_{ts}_{cam.camera_id}.jpg"
        
        try:
            api_queue.put_nowait({
                "plate": p, 
                "camera_id": cam.camera_id, 
                "confidence": float(conf), 
                "frame": frame,
                "file_name": fn
            })
        except queue.Full: pass

        cam.last_sent = p
        cam.last_time = now
        
        with buffer_lock:
            if cam.camera_id in detection_overlays:
                detection_overlays[cam.camera_id].update({'text': p, 'timer': now + 4.0, 'status': 'SORGULANIYOR', 'box': box})


class CameraStream(threading.Thread):
    def __init__(self, slot):
        super().__init__(daemon=True)
        self.slot_id = slot
        self.camera_id = f"CAM-0{slot}"
        self.config = None
        self.cap = None
        self.conn = False
        self.plate_buffer = []
        self.last_sent = ""
        self.last_time = 0
        self.last_frame_time = time.time()
        
        # EKSİK OLAN VE SİSTEMİ ÇÖKERTEN SATIR DÜZELTİLDİ:
        self.current_config_sig = None 
        
        self.frame_counter = 0
        self.vote_lock = threading.Lock()
        
        self.prev_gray = None
        self.tracking_points = None
        self.tracked_box = None
        self.last_ai_box = None

    def reconnect(self):
        if self.cap: self.cap.release(); self.cap = None
        self.conn = False
        self.prev_gray = None
        self.tracking_points = None
        self.last_ai_box = None
        time.sleep(3)

    def draw_dashed_box(self, img, pt1, pt2, color, thickness=2, dash_length=10):
        x1, y1 = min(pt1[0], pt2[0]), min(pt1[1], pt2[1])
        x2, y2 = max(pt1[0], pt2[0]), max(pt1[1], pt2[1])
        for x in range(x1, x2, dash_length * 2):
            cv2.line(img, (x, y1), (min(x + dash_length, x2), y1), color, thickness)
            cv2.line(img, (x, y2), (min(x + dash_length, x2), y2), color, thickness)
        for y in range(y1, y2, dash_length * 2):
            cv2.line(img, (x1, y), (x1, min(y + dash_length, y2)), color, thickness)
            cv2.line(img, (x2, y), (x2, min(y + dash_length, y2)), color, thickness)

    def run(self):
        while True:
            new_sig = f"{self.config['source']}_{self.config.get('rtsp_user')}_{self.config.get('rtsp_pass')}" if self.config else None
            if getattr(self, 'current_config_sig', None) != new_sig:
                self.reconnect()
                self.current_config_sig = new_sig
                if not new_sig:
                    with latest_frames_lock:
                        if self.camera_id in latest_frames: del latest_frames[self.camera_id]
                    with buffer_lock:
                        if self.camera_id in jpeg_buffers: del jpeg_buffers[self.camera_id]
                        if self.camera_id in detection_overlays: detection_overlays[self.camera_id] = {'boxes': [], 'text': '', 'timer': 0}
                    time.sleep(1)
                    continue

            if not self.conn and self.config:
                src = self.config['source']
                cam_type = self.config.get('type', 'WEBCAM')
                if cam_type == 'RTSP':
                    u = self.config.get('rtsp_user', '')
                    p = self.config.get('rtsp_pass', '')
                    if u and p and str(src).startswith('rtsp://'): src = src.replace('rtsp://', f'rtsp://{u}:{p}@')
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{SYSTEM_SETTINGS['rtsp_transport']}|stimeout;5000000"
                    self.cap = cv2.VideoCapture(int(src) if str(src).isdigit() else src, cv2.CAP_FFMPEG)
                else:
                    source_id = int(src) if str(src).isdigit() else src
                    if os.name == 'nt' and isinstance(source_id, int): self.cap = cv2.VideoCapture(source_id, cv2.CAP_DSHOW)
                    else: self.cap = cv2.VideoCapture(source_id)

                if self.cap and self.cap.isOpened(): 
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    self.conn = True
                    self.last_frame_time = time.time()
                else: 
                    self.reconnect(); continue
            
            if self.conn:
                if time.time() - self.last_frame_time > 5.0: self.reconnect(); continue
                ret = self.cap.grab()
                if not ret: self.reconnect(); continue
                self.last_frame_time = time.time()
                ret, frame = self.cap.retrieve()
                if not ret or frame is None: continue
                
                frame = cv2.resize(frame, (640, 480))
                self.frame_counter += 1
                
                with buffer_lock:
                    disp = frame.copy()
                    curr_gray = None
                    if SYSTEM_SETTINGS.get('tracking_enabled'):
                        curr_gray = cv2.cvtColor(disp, cv2.COLOR_BGR2GRAY)
                    
                    ov = detection_overlays.get(self.camera_id, {})
                    is_detecting = len(ov.get('boxes', [])) > 0
                    skip = 0 if (SYSTEM_SETTINGS.get('dynamic_turbo') and is_detecting) else SYSTEM_SETTINGS.get('frame_skip', 2)

                    try:
                        roi_path = os.path.join(DATA_DIR, "camera_roi.json")
                        if os.path.exists(roi_path):
                            with open(roi_path, 'r') as f:
                                all_rois = json.load(f)
                                cam_roi = all_rois.get(str(self.slot_id), {})
                                if cam_roi.get("enabled") and cam_roi.get("roi"):
                                    rx1, ry1 = int(cam_roi["roi"]["x1"]), int(cam_roi["roi"]["y1"])
                                    rx2, ry2 = int(cam_roi["roi"]["x2"]), int(cam_roi["roi"]["y2"])
                                    self.draw_dashed_box(disp, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2, 10)
                                    cv2.putText(disp, "OKUMA ALANI", (min(rx1, rx2), max(15, min(ry1, ry2) - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
                    except Exception: pass

                    current_time = time.time()
                    is_active = ov.get('text') and current_time < ov.get('timer', 0)
                    status = ov.get('status', 'SORGULANIYOR')

                    if is_active:
                        if status == 'IZINLI': box_color = (0, 255, 0) 
                        elif status in ['YASAKLI', 'KOTA_DOLU']: box_color = (0, 0, 255) 
                        elif status in ['MİSAFİR', 'MISAFIR']: box_color = (0, 255, 255) 
                        else: box_color = (255, 255, 255) 
                    else:
                        box_color = (255, 255, 255) 

                    current_boxes = ov.get('boxes', [])
                    display_boxes = []
                    
                    if SYSTEM_SETTINGS.get('tracking_enabled') and curr_gray is not None:
                        try:
                            if current_boxes:
                                ai_box = current_boxes[0]
                                if self.last_ai_box is None or list(ai_box) != self.last_ai_box:
                                    self.last_ai_box = list(ai_box)
                                    self.tracked_box = list(ai_box)
                                    cx, cy = (ai_box[0] + ai_box[2]) / 2.0, (ai_box[1] + ai_box[3]) / 2.0
                                    self.tracking_points = np.array([[[cx, cy]]], dtype=np.float32)
                                elif self.prev_gray is not None and self.tracking_points is not None:
                                    win = (31, 31) if SYSTEM_SETTINGS.get('high_speed_tracker') else (15, 15)
                                    p1, st, err = cv2.calcOpticalFlowPyrLK(self.prev_gray, curr_gray, self.tracking_points, None, winSize=win, maxLevel=2)
                                    if st is not None and len(st) > 0 and st[0][0] == 1:
                                        dx = p1[0][0][0] - self.tracking_points[0][0][0]
                                        dy = p1[0][0][1] - self.tracking_points[0][0][1]
                                        self.tracked_box[0] = max(0, min(640, self.tracked_box[0] + dx))
                                        self.tracked_box[1] = max(0, min(480, self.tracked_box[1] + dy))
                                        self.tracked_box[2] = max(0, min(640, self.tracked_box[2] + dx))
                                        self.tracked_box[3] = max(0, min(480, self.tracked_box[3] + dy))
                                        self.tracking_points = p1
                                    else:
                                        self.tracked_box = list(ai_box)
                                display_boxes = [self.tracked_box]
                            else:
                                self.last_ai_box = None
                                self.tracking_points = None
                                display_boxes = []
                        except Exception as e:
                            display_boxes = current_boxes
                            self.tracking_points = None
                            self.last_ai_box = None
                            
                        self.prev_gray = curr_gray
                    else:
                        display_boxes = current_boxes
                        self.prev_gray = None

                    for b in display_boxes:
                        bx1, by1, bx2, by2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                        if is_active:
                            overlay = disp.copy()
                            cv2.rectangle(overlay, (bx1, by1), (bx2, by2), box_color, -1)
                            cv2.addWeighted(overlay, 0.4, disp, 0.6, 0, disp)
                        thickness = 3 if is_active else 1
                        cv2.rectangle(disp, (bx1, by1), (bx2, by2), box_color, thickness)

                    if is_active:
                        p_text = ov['text']
                        if display_boxes:
                            bx1, by1, bx2, by2 = int(display_boxes[0][0]), int(display_boxes[0][1]), int(display_boxes[0][2]), int(display_boxes[0][3])
                        elif ov.get('box'):
                            bx1, by1, bx2, by2 = int(ov['box'][0]), int(ov['box'][1]), int(ov['box'][2]), int(ov['box'][3])
                        else:
                            bx1, by1, bx2, by2 = 20, 440, 100, 480
                        
                        plate_w, plate_h = 140, 34
                        start_x = max(0, bx1)
                        start_y = max(0, by1 - plate_h - 10)
                        if start_y < 10: start_y = by2 + 10
                        end_x = start_x + plate_w
                        end_y = start_y + plate_h
                        if end_x > 640: start_x = 640 - plate_w; end_x = 640
                            
                        cv2.putText(disp, status, (start_x, max(15, start_y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)
                        cv2.rectangle(disp, (start_x, start_y), (end_x, end_y), (255, 255, 255), -1)
                        cv2.rectangle(disp, (start_x, start_y), (end_x, end_y), (0, 0, 0), 1)
                        
                        blue_w = 24
                        cv2.rectangle(disp, (start_x, start_y), (start_x + blue_w, end_y), (255, 0, 0), -1)
                        cv2.putText(disp, "TR", (start_x + 2, end_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
                        cv2.putText(disp, p_text, (start_x + blue_w + 8, end_y - 8), cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 0, 0), 2)

                    _, jpeg = cv2.imencode('.jpg', disp, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    jpeg_buffers[self.camera_id] = jpeg.tobytes()

                if skip == 0 or self.frame_counter % (skip + 1) == 0:
                    with latest_frames_lock:
                        latest_frames[self.camera_id] = frame
                        
            time.sleep(0.001)

cam_prev_motion = {}

class CentralDetector(threading.Thread):
    def run(self):
        global cam_prev_motion
        while True:
            try:  
                with latest_frames_lock:
                    current_frames = list(latest_frames.items())
                    latest_frames.clear()

                rois = {}
                try:
                    roi_path = os.path.join(DATA_DIR, "camera_roi.json")
                    if os.path.exists(roi_path):
                        with open(roi_path, "r") as f: rois = json.load(f)
                except: pass

                for cid, frame in current_frames:
                    if SYSTEM_SETTINGS.get('motion_sleep'):
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        gray = cv2.GaussianBlur(gray, (21, 21), 0)
                        if cid not in cam_prev_motion:
                            cam_prev_motion[cid] = gray
                            continue
                        diff = cv2.absdiff(cam_prev_motion[cid], gray)
                        cam_prev_motion[cid] = gray
                        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                        if cv2.countNonZero(thresh) < 500:
                            continue

                    cam_obj = engine_map.get(cid)
                    roi_box = None
                    if cam_obj:
                        cam_roi = rois.get(str(cam_obj.slot_id), {})
                        if cam_roi.get("enabled") and cam_roi.get("roi"):
                            roi_box = cam_roi["roi"]

                    if DETECTOR:
                        try:
                            boxes = DETECTOR.detect(frame)
                        except Exception as e:
                            boxes = []
                            
                        filtered_boxes = []
                        for b in boxes:
                            if roi_box:
                                bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                                rx1, ry1, rx2, ry2 = roi_box["x1"], roi_box["y1"], roi_box["x2"], roi_box["y2"]
                                if not (rx1 <= bcx <= rx2 and ry1 <= bcy <= ry2):
                                    continue
                            filtered_boxes.append(b)
                        
                        with buffer_lock: 
                            if cid in detection_overlays: detection_overlays[cid]['boxes'] = filtered_boxes
                            
                        for b in filtered_boxes:
                            try:
                                y1, y2 = int(b[1]), int(b[3])
                                x1, x2 = int(b[0]), int(b[2])
                                crop = frame[y1:y2, x1:x2]
                                if cam_obj and crop.size > 0: 
                                    try: ocr_queue.put_nowait((cam_obj, crop, frame, b)) 
                                    except queue.Full: pass
                            except Exception: pass
            except Exception as main_loop_e:
                pass
            
            time.sleep(SYSTEM_SETTINGS.get('ai_interval', 0.06))


@app.route('/ocr-status')
def ocr_status(): 
    with ocr_status_lock:
        return jsonify(OCR_STATUS)

@app.route('/video_feed/<camera_id>')
def video_feed(camera_id):
    def gen():
        while True:
            f = jpeg_buffers.get(camera_id)
            if f:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + f + b'\r\n')
            time.sleep(0.05)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/images/<path:filename>')
def serve_image(filename): 
    return send_from_directory(IMAGE_FOLDER, filename)

if __name__ == "__main__":
    adjust_api_workers(SYSTEM_SETTINGS.get('api_worker_count', 3))

    for w_id in range(1, 3): 
        OCRWorker(worker_id=w_id).start()
        
    for i in range(1, 5): 
        c = CameraStream(i)
        c.start()
        engines.append(c)
        engine_map[c.camera_id] = c 
        
    with buffer_lock:
        for c in engines: 
            detection_overlays[c.camera_id] = {'boxes': [], 'text': '', 'timer': 0}
            
    CentralDetector().start()
    
    def sync():
        while True:
            try:
                r = requests.get(f"{API_URL}/api/cameras", timeout=2)
                if r.status_code == 200:
                    cams = r.json()
                    for e in engines: e.config = next((c for c in cams if c['slot'] == e.slot_id), None)
                
                r_set = requests.get(f"{API_URL}/api/settings", timeout=2)
                if r_set.status_code == 200:
                    data = r_set.json()
                    SYSTEM_SETTINGS['rtsp_transport'] = data.get('rtsp_transport', 'tcp')
                    SYSTEM_SETTINGS['frame_skip'] = int(data.get('frame_skip', 2))
                    SYSTEM_SETTINGS['ai_interval'] = float(data.get('ai_interval', 0.06))
                    SYSTEM_SETTINGS['ocr_conf_threshold'] = float(data.get('ocr_conf_threshold', 0.40))
                    SYSTEM_SETTINGS['vote_count'] = int(data.get('vote_count', 3))
                    SYSTEM_SETTINGS['vote_accept'] = int(data.get('vote_accept', 2))
                    SYSTEM_SETTINGS['debounce_time'] = int(data.get('debounce_time', 15))
                    
                    target_api_workers = int(data.get('api_worker_count', 3))
                    SYSTEM_SETTINGS['api_worker_count'] = target_api_workers
                    adjust_api_workers(target_api_workers)
                    
                    SYSTEM_SETTINGS['tracking_enabled'] = data.get('tracking_enabled', False)
                    SYSTEM_SETTINGS['motion_sleep'] = data.get('motion_sleep', False)
                    SYSTEM_SETTINGS['anti_blur'] = data.get('anti_blur', False)
                    SYSTEM_SETTINGS['high_speed_tracker'] = data.get('high_speed_tracker', False)
                    SYSTEM_SETTINGS['dynamic_turbo'] = data.get('dynamic_turbo', False)
                    
                    if DETECTOR:
                        DETECTOR.update_device(data.get('gpu_acceleration', False))
                        
            except Exception as e: 
                pass
            time.sleep(5)
            
    threading.Thread(target=sync, daemon=True).start()
    
    print("\n🚀 EvoSmart Engine 5001 portunda çalışıyor...\n")
    app.run(host="0.0.0.0", port=5001, threaded=True, debug=False, use_reloader=False)