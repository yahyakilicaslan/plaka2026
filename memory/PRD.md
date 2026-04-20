# EVO SMART LPR — PRD

## Orijinal Problem (özet)
Kullanıcı sistemi incelememizi ve ticari seviye bir plaka tanıma sistemine
dönüştürmemizi istedi. Sorunlar:
- OCR sonrası "SORGULANIYOR → İzin → Bariyer" zinciri yavaş
- Dashboard'da MJPEG canlı akış tüm sistemi yoruyor (OCR'ı etkiliyor)
- Yan yana 2 araç gelince 2 plaka birden okunmalı
- Desktop kamera uygulaması → RAW görüntü → hızlı OCR
- Dashboard'da kamera yerine kare log kartları (araç + bilgi)
- Manuel kapı açma butonu desktop'ta olsun
- Giriş/Çıkış kamera tipi belirtilsin
- Tanımlı plakada ilgili NodeMCU tetiklensin

## Mimari (Yeni)
- `backend/main.py` (FastAPI, port 8000) — API + WS + Dashboard serve
- `lpr_engine/detector.py` (Flask, port 5001) — OpenVINO + PaddleOCR, çoklu plaka
- `desktop_camera.py` (PyQt5) — 2x2 RAW RTSP görüntü + manuel kapı butonu
- `frontend/dashboard.html` — Kamera yok, 3×4 log kart grid

## Kullanıcı Tercihleri
- Desktop: PyQt5
- OCR motoru: mevcut (OpenVINO + PaddleOCR) optimize
- İletişim: REST + WebSocket
- Log kartı: araç + plaka + zaman + kamera + yön + yetki + bariyer
- Manuel açma: sadece desktop'ta

## Uygulanan Değişiklikler (20.04.2026)

### Backend (`backend/main.py`)
- `PRAGMA journal_mode=WAL` + NORMAL sync + MEMORY temp → DB hızı
- NodeMCU tetik artık `BackgroundTasks` ile → `/api/log-plate` yanıtı anlık
- **Yeni** `POST /api/gate/test/{gate_id}` — dashboard'dan kapı testi
- **Yeni** `POST /api/gate/manual-open/{slot}` — desktop'tan manuel açma (WS broadcast dahil)
- **Yeni** `GET /api/cameras/slot/{slot}` — desktop için hızlı kamera+gate info

### LPR Motoru (`lpr_engine/detector.py`)
- `DETECTOR.detect()` — **çoklu plaka** (boxes[:4]) + IoU NMS + min boyut filtresi
- `CameraStream.plate_buffers` — plaka başına oy buffer'ı (dict)
- `detection_overlays[cid]['plates']` — listeye çevrildi, MJPEG her plaka için ayrı çizim
- OCR worker sayısı: 2 → 4, OCR queue: 5 → 20 (paralellik)
- MJPEG JPEG kalitesi 60 → 50 (dashboard bunu kullanmıyor artık)
- Plaka başına `last_sent_map` debounce → aynı anda 2 farklı plaka bloklanmaz

### Dashboard (`frontend/dashboard.html`)
- Kamera grid'i KALDIRILDI, sağ log şeridi KALDIRILDI
- Yeni: **3 sıra × 4 sütun = 12 kare log kartı** grid (son 12 geçiş)
- Her kart: araç görseli + durum rozeti + giriş/çıkış rozeti + bariyer ribbon + plaka (TR) + sahip + kamera + saat
- Sağ üstte "Kamera: Masaüstü Monitör" bilgisi
- WS `manual_open` event'ı → toast bildirim
- `startCameraFeeds` çağrısı kaldırıldı (MJPEG yok)

### Desktop Kamera Uygulaması (`desktop_camera.py`) — YENİ
- 2×2 grid, her hücre: video + üst bar (kamera adı + GİRİŞ/ÇIKIŞ + ONLINE durum) + alt bar (plaka göstergesi + bilgi + KAPI AÇ butonu)
- Kameralara **doğrudan RTSP/WebCam** bağlanır (MJPEG yok → OCR motoru sıkışmaz)
- `cv2.CAP_PROP_BUFFERSIZE=1` + 5 sn timeout'ta otomatik yeniden bağlanma
- Backend WebSocket (`/ws`) dinler → `type=log` olayında ilgili slot kartında plaka + durum + bariyer göstergesi
- `KAPI AÇ` butonu → `POST /api/gate/manual-open/{slot}` → slotun tanımlı NodeMCU'sunu tetikler. Kapı atanmamış kamerada buton pasif.
- `--api http://IP:8000` parametresi ile uzak backend'e bağlanabilir
- PyInstaller ile tek .exe olarak paketlenebilir

### Requirements & BAT
- `requirements.txt` → `PyQt5==5.15.10`, `websocket-client==1.7.0` eklendi
- `baslat.bat` → 4. adım olarak `desktop_camera.py` başlatma eklendi
- `debuglu_n.bat` → 6 adım (backend → engine → desktop → browser)

## Test Edildi
- Backend çalışıyor, endpoint'ler yanıt veriyor
- `/api/gate/manual-open/1` → ilgili kapıyı tetikliyor ("Ana Giriş manuel açıldı")
- `/api/gate/test/{id}` → çalışıyor (önceden yoktu, dashboard çağırıyordu)
- `/api/log-plate` → 2 ardışık plaka farklı araç olarak kaydedildi, WS broadcast OK
- Dashboard yeni log grid görsel olarak doğrulandı

## Bilinen Notlar
- Preview (Linux) ortamında PyQt5 wheel derlemesi başarısız (qmake yok).
  Windows'ta pre-built wheel mevcut → `debuglu_n.bat` kurulumu problemsiz.
- Desktop uygulama `localhost:8000`'e bağlanır; farklı IP için `--api` parametresi.

## Gelecek / Backlog (P1-P2)
- P1: Desktop tarafında da YOLO+OCR yapılabilir (backend engine'den bağımsız RAW kaynaklı plaka) → ultra-düşük gecikme
- P2: Dashboard log kartına "Manuel Aç" erişimi (isteğe bağlı, şu an sadece desktop)
- P2: NodeMCU sağlık kontrolü (ping) cihazlar sekmesinde
- P2: Kameralar için "direction auto-detect" (hareket yönü analizi)
- P2: Desktop uygulamada "canlı plaka kutusu" overlay (engine'den box bilgilerini WS üzerinden stream et)

## Sonraki Öncelikli Aksiyonlar
1. Windows'ta `debuglu_n.bat` çalıştırıp PyQt5 kurulumunu doğrulamak
2. 2 araç yan yana geldiğinde çoklu okuma testi
3. Manuel kapı açma butonu ile NodeMCU'ya gerçek tetik testi
