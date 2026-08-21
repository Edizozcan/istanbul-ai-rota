import streamlit as st
import pandas as pd
import math
import json
from datetime import datetime, timedelta
import google.generativeai as genai
import folium
from streamlit_folium import folium_static
import io
from supabase import create_client, Client

# ==========================================
# 1. SAYFA VE API AYARLARI
# ==========================================
st.set_page_config(page_title="Global Rota Planlayıcı", layout="wide")

# Gemini API Ayarı
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("Lütfen st.secrets içine GEMINI_API_KEY ekleyin.")

# ==========================================
# 2. SUPABASE VERİTABANI BAĞLANTISI
# ==========================================
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Supabase bağlantı hatası: {e}")
        return None

supabase = init_supabase()

# ==========================================
# 3. MİKRO-ÖNBELLEK (CACHE) FONKSİYONLARI
# ==========================================
def cache_den_getir(sehir_adi, gun_sayisi):
    """Veritabanında daha önce oluşturulmuş standart bir rota varsa onu getirir."""
    if supabase is None:
        return None
    try:
        response = supabase.table("sehir_rotalari_cache") \
            .select("rota_jsonb") \
            .eq("sehir_adi", sehir_adi) \
            .eq("gun_sayisi", gun_sayisi) \
            .execute()
        if len(response.data) > 0:
            return response.data[0]["rota_jsonb"]
        return None
    except Exception as e:
        print("Cache okuma hatası:", e)
        return None

def cache_e_kaydet(sehir_adi, gun_sayisi, rota_jsonb, ozel_istek_mi=False):
    """Kişisel özel istek içermeyen rotaları gelecekte kullanmak üzere kaydeder."""
    if supabase is None or ozel_istek_mi:
        return
    try:
        data = {
            "sehir_adi": sehir_adi,
            "gun_sayisi": gun_sayisi,
            "rota_jsonb": rota_jsonb,
            "ozel_istek_mi": ozel_istek_mi
        }
        supabase.table("sehir_rotalari_cache").insert(data).execute()
    except Exception as e:
        print("Cache yazma hatası:", e)

# ==========================================
# 4. YAPAY ZEKA MODÜLLERİ (PARSER VE JENERATÖR)
# ==========================================
def kullanici_niyetini_analiz_et(kullanici_girdisi):
    """Kullanıcının isteğini şehirlere ve gün sayılarına parçalar."""
    model = genai.GenerativeModel('gemini-3.6-flash')
    prompt = f"""
    Kullanıcının şu seyahat isteğini analiz et: "{kullanici_girdisi}"
    
    Bana SADECE şu formatta geçerli bir JSON listesi dön:
    [
        {{"sehir_adi": "Prag", "gun_sayisi": 3, "ozel_istek_mi": false}},
        {{"sehir_adi": "Viyana", "gun_sayisi": 2, "ozel_istek_mi": true}}
    ]
    
    KURALLAR:
    1. Kullanıcı belirli bir mekan, maç, aktivite vb. belirttiyse 'ozel_istek_mi': true yap.
    2. Sadece "X gün Y şehri" dediyse 'ozel_istek_mi': false yap.
    3. Markdown kullanma, saf JSON dön.
    """
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        ticks = chr(96) * 3
        raw_text = raw_text.replace(ticks + "json", "").replace(ticks, "").strip()
        return json.loads(raw_text)
    except Exception as e:
        st.error(f"Niyet analizi başarısız oldu: {e}")
        return []

def yapay_zekadan_sehir_rotasi_iste(sehir_adi, gun_sayisi, ana_istek):
    """Sadece cache'de olmayan veya özel istek içeren şehirler için rotayı sıfırdan çizer."""
    model = genai.GenerativeModel('gemini-3.6-flash')
    prompt = f"""
    Kullanıcının ana isteği: {ana_istek}
    Görev: Sadece {sehir_adi} şehri için {gun_sayisi} günlük mantıklı bir seyahat rotası oluştur.
    
    Çıktıyı SADECE JSON formatında ver. Örnek Format:
    [
        {{
            "gun": 1,
            "sehir": "{sehir_adi}",
            "mekanlar": [
                {{"name": "Mekan Adı", "lat": 40.0, "lon": 20.0, "kategori": "Tarihi", "ort_sure": 60}}
            ]
        }}
    ]
    """
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        ticks = chr(96) * 3
        raw_text = raw_text.replace(ticks + "json", "").replace(ticks, "").strip()
        return json.loads(raw_text)
    except Exception as e:
        print(f"{sehir_adi} için AI hatası:", e)
        return []

# ==========================================
# 5. HARİTA (FOLIUM) VE YARDIMCI FONKSİYONLAR
# ==========================================
def harita_olustur(gun_verisi):
    """Belirli bir günün mekanlarını Folium haritasında çizer."""
    if not gun_verisi.get("mekanlar"):
        return None
        
    ilk_mekan = gun_verisi["mekanlar"][0]
    m = folium.Map(location=[ilk_mekan["lat"], ilk_mekan["lon"]], zoom_start=13)
    
    rota_koordinatlari = []
    for i, mekan in enumerate(gun_verisi["mekanlar"]):
        rota_koordinatlari.append([mekan["lat"], mekan["lon"]])
        folium.Marker(
            [mekan["lat"], mekan["lon"]],
            popup=f"{i+1}. {mekan['name']}",
            tooltip=mekan['name'],
            icon=folium.Icon(color="blue", icon="info-sign")
        ).add_to(m)
        
    if len(rota_koordinatlari) > 1:
        folium.PolyLine(rota_koordinatlari, color="red", weight=2.5, opacity=0.8).add_to(m)
        
    return m

# ==========================================
# 6. KULLANICI ARAYÜZÜ (UI) VE ANA DÖNGÜ
# ==========================================
st.title("🌍 Küresel Akıllı Rota Planlayıcı")
st.markdown("Yapay Zeka ve Mikro-Önbellek (Micro-Caching) Mimarisi ile Güçlendirildi.")

kullanici_istegi = st.text_area(
    "Nasıl bir rota hayal ediyorsun?",
    placeholder="Örn: 3 gün Prag, 2 gün Viyana (Viyana'da şinitzel yiyelim)...",
    height=100
)

if st.button("🚀 Rotayı Oluştur ve Optimize Et", use_container_width=True):
    if not kullanici_istegi:
        st.warning("Lütfen önce bir rota hayali yazın!")
    else:
        with st.spinner("Yapay zeka niyetinizi analiz ediyor..."):
            istek_listesi = kullanici_niyetini_analiz_et(kullanici_istegi)
            
        if not istek_listesi:
            st.error("Girdiğiniz metin analiz edilemedi. Lütfen daha net yazın.")
        else:
            tum_rota = []
            st.info(f"Rota Planı Çıkarılıyor: {', '.join([item['sehir_adi'] for item in istek_listesi])}")
            
            for islem in istek_listesi:
                sehir = islem["sehir_adi"]
                gun = islem["gun_sayisi"]
                ozel = islem["ozel_istek_mi"]
                
                with st.spinner(f"📍 {sehir} için veriler hesaplanıyor..."):
                    rota_parcasi = None
                    
                    if not ozel:
                        rota_parcasi = cache_den_getir(sehir, gun)
                        
                    if rota_parcasi:
                        st.success(f"⚡ {sehir} rotası önbellekten (veritabanından) anında çekildi!")
                    else:
                        rota_parcasi = yapay_zekadan_sehir_rotasi_iste(sehir, gun, kullanici_istegi)
                        if rota_parcasi:
                            st.success(f"🧠 {sehir} rotası yapay zeka tarafından özel olarak çizildi!")
                            cache_e_kaydet(sehir, gun, rota_parcasi, ozel)
                    
                    if rota_parcasi:
                        tum_rota.extend(rota_parcasi)
            
            # Ekrana Basma ve Haritalama Aşaması
            if tum_rota:
                st.subheader("🗓️ Nihai Seyahat Planınız")
                
                # Streamlit Tabs ile günleri ayırma
                tablar = st.tabs([f"Gün {g['gun']} - {g['sehir']}" for g in tum_rota])
                
                for i, gun_verisi in enumerate(tum_rota):
                    with tablar[i]:
                        kolon1, kolon2 = st.columns([1, 1])
                        
                        with kolon1:
                            st.markdown(f"### 📍 {gun_verisi['sehir']} Ziyaret Noktaları")
                            for idx, mekan in enumerate(gun_verisi.get("mekanlar", [])):
                                st.markdown(f"**{idx+1}. {mekan['name']}**")
                                st.caption(f"Kategori: {mekan.get('kategori', 'Genel')} | Süre: {mekan.get('ort_sure', 60)} dk")
                        
                        with kolon2:
                            harita = harita_olustur(gun_verisi)
                            if harita:
                                folium_static(harita, width=400, height=300)
                
                st.balloons()
