"""
EvoSmart P10 LED Panel Entegrasyonu
====================================

Bu dosya P10 LED panellere (NodeMCU/ESP8266/ESP32) 
plaka bilgisi göndermek için kullanılır.

API Endpoint: GET /api/led/status
Yanıt Format:
{
    "plate": "34ABC123",
    "owner": "A Blok / D:10 - Ahmet Yılmaz",
    "status": "IZINLI",  // IZINLI, MİSAFİR, YASAKLI, KOTA_DOLU
    "site": "Güneş Sitesi",
    "timestamp": "17.12.2024 15:30:45",
    "direction": "GIRIS",  // GIRIS veya CIKIS
    "quota_message": ""  // Kota doluysa mesaj içerir
}

NodeMCU Örnek Kod (Arduino):
-----------------------------
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>
#include <DMD2.h>  // P10 LED kütüphanesi

const char* ssid = "WIFI_ADI";
const char* password = "WIFI_SIFRE";
const char* serverUrl = "http://192.168.1.100:8000/api/led/status";

SoftDMD dmd(1, 1);  // 1x1 panel

String lastPlate = "";

void setup() {
    Serial.begin(115200);
    WiFi.begin(ssid, password);
    
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("WiFi Bağlandı!");
    
    dmd.setBrightness(255);
    dmd.begin();
}

void loop() {
    if (WiFi.status() == WL_CONNECTED) {
        HTTPClient http;
        http.begin(serverUrl);
        int httpCode = http.GET();
        
        if (httpCode == 200) {
            String payload = http.getString();
            
            StaticJsonDocument<512> doc;
            deserializeJson(doc, payload);
            
            String plate = doc["plate"].as<String>();
            String status = doc["status"].as<String>();
            
            if (plate != lastPlate && plate.length() > 0) {
                lastPlate = plate;
                
                // LED'e yaz
                dmd.clearScreen();
                
                if (status == "IZINLI") {
                    dmd.drawString(0, 0, "HOSGELDINIZ");
                    delay(2000);
                    dmd.clearScreen();
                    dmd.drawString(0, 0, plate.c_str());
                } else if (status == "YASAKLI") {
                    dmd.drawString(0, 0, "GIRIS YASAK");
                    delay(2000);
                    dmd.clearScreen();
                    dmd.drawString(0, 0, plate.c_str());
                } else if (status == "KOTA_DOLU") {
                    dmd.drawString(0, 0, "KOTA DOLU");
                    delay(2000);
                    dmd.clearScreen();
                    dmd.drawString(0, 0, plate.c_str());
                } else {
                    dmd.drawString(0, 0, "MISAFIR");
                    delay(2000);
                    dmd.clearScreen();
                    dmd.drawString(0, 0, plate.c_str());
                }
            }
        }
        http.end();
    }
    delay(500);  // 500ms aralıklarla kontrol
}
"""

import requests
import time
import sys

# API sunucu adresi
API_URL = "http://localhost:8000/api/led/status"

def get_plate_status():
    """API'den son plaka bilgisini al"""
    try:
        response = requests.get(API_URL, timeout=3)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Hata: {e}")
        return None

def format_for_p10(data):
    """P10 LED için metin formatla"""
    if not data or not data.get('plate'):
        return "BEKLENIYOR..."
    
    plate = data['plate']
    status = data.get('status', 'MİSAFİR')
    
    if status == 'IZINLI':
        return f"HOSGELDINIZ  {plate}"
    elif status == 'YASAKLI':
        return f"GIRIS YASAK  {plate}"
    elif status == 'KOTA_DOLU':
        return f"KOTA DOLU  {plate}"
    else:
        return f"MISAFIR  {plate}"

def simulate_led_display():
    """P10 LED simülasyonu (terminal'de)"""
    last_plate = ""
    
    print("\n" + "="*50)
    print("   P10 LED Simülasyonu")
    print("   Çıkmak için Ctrl+C")
    print("="*50 + "\n")
    
    while True:
        try:
            data = get_plate_status()
            
            if data and data.get('plate') and data['plate'] != last_plate:
                last_plate = data['plate']
                text = format_for_p10(data)
                
                # Terminal'de LED simülasyonu
                print("\n" + "┌" + "─"*48 + "┐")
                print("│" + text.center(48) + "│")
                print("└" + "─"*48 + "┘")
                print(f"  Zaman: {data.get('timestamp', '-')}")
                print(f"  Durum: {data.get('status', '-')}")
                print(f"  Yön: {data.get('direction', '-')}")
            
            time.sleep(0.5)
            
        except KeyboardInterrupt:
            print("\nÇıkış yapılıyor...")
            break

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--simulate":
        simulate_led_display()
    else:
        # Tek seferlik durum kontrolü
        data = get_plate_status()
        if data:
            print(f"Plaka: {data.get('plate', '-')}")
            print(f"Durum: {data.get('status', '-')}")
            print(f"Sahip: {data.get('owner', '-')}")
            print(f"Site: {data.get('site', '-')}")
            print(f"Zaman: {data.get('timestamp', '-')}")
        else:
            print("Veri alınamadı")
