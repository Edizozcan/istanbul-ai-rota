import sqlite3
import pandas as pd

def create_curated_database():
    print("🧠 Yapay Zeka destekli 'Küratörlü İstanbul Veritabanı' oluşturuluyor...")
    
    # Özel olarak seçilmiş, koordinatları ve kategorileri doğrulanmış seçkin mekan listesi
    # Format: (İsim, Enlem, Boylam, Kategori, Ortalama Süre - Dk)
    premium_places = [
        # --- TARİHİ YERLER VE MÜZELER ---
        ("Ayasofya-i Kebir Cami-i Şerifi", 41.0082, 28.9784, "Tarihi/Turistik", 60),
        ("Sultanahmet Camii", 41.0054, 28.9768, "Tarihi/Turistik", 45),
        ("Topkapı Sarayı", 41.0115, 28.9833, "Müze", 150),
        ("Yerebatan Sarnıcı", 41.0084, 28.9766, "Müze", 60),
        ("Galata Kulesi", 41.0256, 28.9741, "Tarihi/Turistik", 45),
        ("Dolmabahçe Sarayı", 41.0396, 28.9986, "Müze", 120),
        ("İstanbul Modern Sanat Müzesi", 41.0250, 28.9825, "Müze", 90),
        ("SALT Galata", 41.0232, 28.9739, "Müze", 60),
        ("Rahmi M. Koç Müzesi", 41.0423, 28.9493, "Müze", 150),
        ("Balat Renkli Evler", 41.0315, 28.9472, "Tarihi/Turistik", 45),
        ("Kız Kulesi", 41.0211, 29.0041, "Tarihi/Turistik", 60),
        ("Rumeli Hisarı", 41.0837, 29.0560, "Tarihi/Turistik", 90),
        
        # --- GASTRONOMİ & YEREL LEZZETLER (Seçkin Mekanlar) ---
        ("Karaköy Güllüoğlu (Tarihi)", 41.0242, 28.9772, "Gastronomi", 45),
        ("Hafız Mustafa 1864 (Sirkeci)", 41.0143, 28.9765, "Gastronomi", 45),
        ("Mikla Restaurant (Michelin)", 41.0298, 28.9745, "Gastronomi", 90),
        ("Neolokal (Michelin)", 41.0234, 28.9733, "Gastronomi", 90),
        ("Tarihi Sultanahmet Köftecisi", 41.0075, 28.9754, "Gastronomi", 45),
        ("Sunset Grill & Bar", 41.0583, 29.0345, "Gastronomi", 120),
        ("Bebek Kahve", 41.0772, 29.0438, "Gastronomi", 60),
        ("Vefa Bozacısı", 41.0152, 28.9575, "Gastronomi", 30),
        ("Pandeli Restoran (Mısır Çarşısı)", 41.0165, 28.9705, "Gastronomi", 60),
        
        # --- PARK & DOĞA ---
        ("Gülhane Parkı", 41.0128, 28.9806, "Doğa", 60),
        ("Emirgan Korusu", 41.1090, 29.0520, "Doğa", 120),
        ("Maçka Demokrasi Parkı", 41.0436, 28.9950, "Doğa", 60),
        ("Bebek Parkı", 41.0768, 29.0435, "Doğa", 45),
        ("Yıldız Parkı", 41.0475, 29.0133, "Doğa", 90),
        ("Fenerbahçe Parkı", 40.9687, 29.0355, "Doğa", 60),
        ("Atatürk Arboretumu", 41.1764, 28.9836, "Doğa", 150)
    ]
    
    # Veritabanı bağlantısı
    conn = sqlite3.connect("istanbul_mekanlar.db")
    cursor = conn.cursor()
    
    # Eski tabloyu sil ve temiz bir sayfa aç
    cursor.execute('''DROP TABLE IF EXISTS mekanlar''')
    cursor.execute('''
        CREATE TABLE mekanlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isim TEXT,
            enlem REAL,
            boylam REAL,
            kategori TEXT,
            ort_sure INTEGER
        )
    ''')
    
    # Premium mekanları tabloya kaydet
    cursor.executemany('''
        INSERT INTO mekanlar (isim, enlem, boylam, kategori, ort_sure)
        VALUES (?, ?, ?, ?, ?)
    ''', premium_places)
    
    conn.commit()
    print(f"✅ Başarılı! {len(premium_places)} adet elit mekan istanbul_mekanlar.db dosyasına kaydedildi.")
    
    # Kontrol amaçlı ekrana birkaç veri yazdıralım
    df = pd.read_sql_query("SELECT * FROM mekanlar LIMIT 5", conn)
    print("\nÖrnek Mekanlar:\n", df)
    
    conn.close()

if __name__ == "__main__":
    create_curated_database()