import subprocess
import sys

def uninstall_all():
    print(">> EvoSmart: Mevcut tüm kütüphaneler temizleniyor...")
    # Kurulu olan tüm paketleri listele
    try:
        result = subprocess.check_output([sys.executable, "-m", "pip", "freeze"])
        packages = [line.split("==")[0] for line in result.decode().splitlines() if "==" in line]
        
        if not packages:
            print(">> Kaldırılacak kütüphane bulunamadı.")
            return

        # Paketleri kaldır
        for pkg in packages:
            print(f"[-] Kaldırılıyor: {pkg}")
            subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", pkg])
        
        print("\n✅ Temizlik tamamlandı! Şimdi yeni kurulumu yapabilirsiniz.")
    except Exception as e:
        print(f"❌ Hata oluştu: {e}")

if __name__ == "__main__":
    uninstall_all()