import streamlit as st
import pandas as pd
import math
import json
from datetime import time, timedelta, date
from time import sleep
import google.generativeai as genai
import folium
from streamlit_folium import folium_static
import io
import unicodedata
import requests

# ReportLab kütüphaneleri
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Supabase kütüphanesi
from supabase import create_client, Client

# --- GÜVENLİ API AYARLARI ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)

# --- SUPABASE BAĞLANTISI ---
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

# --- 1. MATEMATİKSEL VE OPTİMİZASYON FONKSİYONLARI ---
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    dlat, dlon = lat2_rad - lat1_rad, lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c 

def optimize_route(df, start_lat, start_lon, total_minutes):
    unvisited = df.copy()
    current_lat, current_lon = start_lat, start_lon
    route = []
    
    while not unvisited.empty and total_minutes > 0:
        unvisited['distance_km'] = unvisited.apply(
            lambda row: haversine(current_lat, current_lon, row['lat'], row['lon']), axis=1
        )
        nearest = unvisited.loc[unvisited['distance_km'].idxmin()]
        
        if nearest['distance_km'] > 3.0:
            travel_time = int((nearest['distance_km'] / 25.0) * 60)
        else:
            travel_time = int((nearest['distance_km'] / 4.0) * 60)
            
        if travel_time < 2: travel_time = 2
            
        time_needed = travel_time + nearest['ort_sure']
        
        if total_minutes >= time_needed:
            nearest_dict = nearest.to_dict()
            nearest_dict['travel_time'] = travel_time
            route.append(nearest_dict)
            
            total_minutes -= time_needed
            current_lat, current_lon = nearest['lat'], nearest['lon']
            unvisited = unvisited.drop(nearest.name)
        else:
            break
            
    return pd.DataFrame(route)

# --- 1.5 ULAŞIM VE BÜTÇE HESAPLAMA MOTORU (FLIXBUS + OSRM) ---
def koordinat_bul(sehir_adi):
    url = f"https://nominatim.openstreetmap.org/search?q={sehir_adi}&format=json&limit=1"
    headers = {"User-Agent": "RotaPlanlayiciApp/1.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5).json()
        if res:
            return float(res[0]["lat"]), float(res[0]["lon"])
    except: pass
    return None, None

def osrm_mesafe_cek(lat1, lon1, lat2, lon2):
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("code") == "Ok":
            return res["routes"][0]["distance"] / 1000.0
    except: pass
    return None

def flixbus_sehir_kodu_bul(sehir_adi):
    url = "https://global.api.flixbus.com/search/autocomplete/cities"
    params = {"q": sehir_adi, "lang": "en"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5).json()
        if res and len(res) > 0:
            return res[0]['id']
    except: pass
    return None

def flixbus_minimum_fiyat_cek(kalkis_id, varis_id, tarih):
    url = "https://global.api.flixbus.com/search/service/v4/search"
    params = {
        "from_city_id": kalkis_id,
        "to_city_id": varis_id,
        "departure_date": tarih,
        "products": '{"adult":1}',
        "currency": "EUR",
        "locale": "en",
        "search_by": "cities"
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=8).json()
        if "trips" in res and len(res["trips"]) > 0 and res["trips"][0].get("results"):
            seferler = res["trips"][0]["results"]
            fiyatlar = [detaylar["price"]["total"] for sefer_id, detaylar in seferler.items() if detaylar.get("status") == "available"]
            if fiyatlar:
                return min(fiyatlar)
    except: pass
    return None

def transit_maliyet_hesapla(kalkis_sehri, varis_sehri, tarih):
    k_id = flixbus_sehir_kodu_bul(kalkis_sehri)
    v_id = flixbus_sehir_kodu_bul(varis_sehri)
    
    ham_fiyat = None
    if k_id and v_id:
        ham_fiyat = flixbus_minimum_fiyat_cek(k_id, v_id, tarih)
        
    if ham_fiyat is not None:
        son_fiyat = ham_fiyat + 0.49 if ham_fiyat < 15.0 else ham_fiyat + 0.99
        return {"durum": "basarili", "kaynak": "Flixbus API", "fiyat": round(son_fiyat, 2), "mesaj": f"Otobüs Bileti ({kalkis_sehri} -> {varis_sehri})"}
        
    lat1, lon1 = koordinat_bul(kalkis_sehri)
    lat2, lon2 = koordinat_bul(varis_sehri)
    
    if lat1 and lon1 and lat2 and lon2:
        mesafe_km = osrm_mesafe_cek(lat1, lon1, lat2, lon2)
        if mesafe_km:
            return {"durum": "tahmini", "kaynak": "OSRM Mesafe", "fiyat": round(mesafe_km * 0.08, 2), "mesaj": f"Tahmini Karayolu Maliyeti ({round(mesafe_km, 1)} km)"}
            
    return {"durum": "varsayilan", "kaynak": "Varsayılan", "fiyat": 45.00, "mesaj": f"Standart Bölge Geçişi"}

# --- 2. EVRENSEL KARAKTER TEMİZLEME (PDF İÇİN) ---
def tr_to_en(text):
    text = str(text)
    text = text.replace('ı', 'i').replace('İ', 'I').replace('ö', 'o').replace('Ö', 'O').replace('ü', 'u').replace('Ü', 'U').replace('ç', 'c').replace('Ç', 'C').replace('ş', 's').replace('Ş', 'S').replace('ğ', 'g').replace('Ğ', 'G')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if unicodedata.category(c) != 'Mn')
    return text

# --- 3. TEK PARÇA TÜM SEYAHATİ PDF YAPMA FONKSİYONU ---
def generate_full_travel_booklet(multi_day_plan, start_time_input, end_time_input, genel_toplam_maliyet):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('BookletTitle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor("#1f4e78"), spaceAfter=10, alignment=1)
    subtitle_style = ParagraphStyle('BookletSub', parent=styles['Normal'], fontSize=12, textColor=colors.HexColor("#595959"), spaceAfter=15, alignment=1)
    budget_style = ParagraphStyle('BudgetStyle', parent=styles['Heading2'], fontSize=16, textColor=colors.HexColor("#2e75b6"), spaceAfter=25, alignment=1)
    day_heading = ParagraphStyle('DayHeading', parent=styles['Heading2'], fontSize=16, textColor=colors.HexColor("#2f5597"), spaceAfter=10, spaceBefore=10)
    
    story.append(Paragraph(tr_to_en("KURESEL SEYAHAT KITAPCIGI"), title_style))
    story.append(Paragraph(tr_to_en("Global Rota Planlayici V2 ile Otonom Olarak Olusturulmustur"), subtitle_style))
    story.append(Paragraph(tr_to_en(f"Tahmini Toplam Tur Butcesi (Ulasim + Aktiviteler): {genel_toplam_maliyet} EUR"), budget_style))
    story.append(Spacer(1, 15))
    
    start_dt_base = pd.Timestamp(f"2000-01-01 {start_time_input}")
    end_dt_base = pd.Timestamp(f"2000-01-01 {end_time_input}")
    total_available_minutes = int((end_dt_base - start_dt_base).total_seconds() / 60)
    
    for i, day_data in enumerate(multi_day_plan):
        gun_no = day_data['gun']
        sehir_adi = day_data['sehir']
        df_day = pd.DataFrame(day_data['mekanlar'])
        
        if df_day.empty:
            continue
            
        baslangic_lat = df_day.iloc[0]['lat']
        baslangic_lon = df_day.iloc[0]['lon']
        
        gunluk_rota = optimize_route(df_day, baslangic_lat, baslangic_lon, total_available_minutes)
        
        story.append(Paragraph(tr_to_en(f"Gun {gun_no} - Sehir: {sehir_adi}"), day_heading))
        
        table_data = [[tr_to_en("Saat Araligi"), tr_to_en("Durak Adi"), tr_to_en("Kategori & Ucret"), tr_to_en("Gecis / Ziyaret")]]
        
        if 'transit' in day_data:
            t = day_data['transit']
            table_data.append([
                "SABAH", 
                tr_to_en(f"Transit: {t['mesaj']}"), 
                tr_to_en(f"Ulasim | {t['fiyat']} EUR"), 
                tr_to_en(f"Kaynak: {t['kaynak']}")
            ])

        current_time = start_dt_base
        for idx, row in gunluk_rota.iterrows():
            current_time += timedelta(minutes=int(row['travel_time']))
            varis_saati = current_time.strftime('%H:%M')
            current_time += timedelta(minutes=int(row['ort_sure']))
            cikis_saati = current_time.strftime('%H:%M')
            
            mekan_maliyet = row.get('tahmini_maliyet_eur', 0)
            
            zaman_str = f"{varis_saati} - {cikis_saati}"
            durak_str = tr_to_en(f"{idx+1}. {row['name']}")
            kat_str = tr_to_en(f"{row['kategori']} | {mekan_maliyet} EUR")
            sure_str = tr_to_en(f"Yol: {int(row['travel_time'])}dk | Sure: {row['ort_sure']}dk")
            
            table_data.append([zaman_str, durak_str, kat_str, sure_str])
            
        t = Table(table_data, colWidths=[80, 185, 105, 170])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2f5597")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#f9f9f9")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ]))
        
        story.append(t)
        story.append(Spacer(1, 15))
        
        if i < len(multi_day_plan) - 1:
            story.append(PageBreak())
            
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# --- 4. CACHE VE YAPAY ZEKA MODÜLLERİ ---
def cache_den_getir(sehir_adi, gun_sayisi):
    if supabase is None: return None
    try:
        response = supabase.table("sehir_rotalari_cache") \
            .select("rota_jsonb").eq("sehir_adi", sehir_adi).eq("gun_sayisi", gun_sayisi).execute()
        if len(response.data) > 0:
            return response.data[0]["rota_jsonb"]
        return None
    except Exception:
        return None

def cache_e_kaydet(sehir_adi, gun_sayisi, rota_jsonb, ozel_istek_mi):
    if supabase is None or ozel_istek_mi: return
    try:
        data = {"sehir_adi": sehir_adi, "gun_sayisi": gun_sayisi, "rota_jsonb": rota_jsonb, "ozel_istek_mi": ozel_istek_mi}
        supabase.table("sehir_rotalari_cache").insert(data).execute()
    except Exception:
        pass

def kullanici_niyetini_analiz_et(kullanici_girdisi):
    model = genai.GenerativeModel('gemini-3.6-flash')
    prompt = f"""
    Sen bir veri ayrıştırıcısısın. İsteği analiz et: "{kullanici_girdisi}"
    Bana SADECE şu formatta JSON dön:
    [
        {{"sehir_adi": "Prag", "gun_sayisi": 3, "ozel_istek_mi": false}},
        {{"sehir_adi": "Viyana", "gun_sayisi": 2, "ozel_istek_mi": true}}
    ]
    Kurallar: Özel istek/mekan/şart varsa 'ozel_istek_mi': true, yoksa false. Asla markdown kullanma.
    """
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(raw_text)
    except Exception:
        return []

def yapay_zekadan_sehir_rotasi_iste(sehir_adi, gun_sayisi, ana_istek):
    model = genai.GenerativeModel('gemini-3.6-flash')
    prompt = f"""
    Kullanıcının ana isteği: {ana_istek}
    Görev: SADECE {sehir_adi} şehri için {gun_sayisi} günlük rota oluştur. Her gün 5-6 mekan olsun.
    Mekanların ENLEM (lat) ve BOYLAM (lon) koordinatları gerçekçi olsun.
    
    YENİ GÖREV: Her mekan için Euro cinsinden tahmini bir maliyet ekle. (Örn: Ücretsiz parklar için 0, Müzeler için 10-25, Restoranlar için 15-40).
    
    SADECE JSON FORMATINDA ÇIKTI VER:
    [
        {{
            "gun": 1,
            "sehir": "{sehir_adi}",
            "mekanlar": [
                {{"name": "Mekan Adı", "lat": 40.0, "lon": 20.0, "kategori": "Tarihi", "ort_sure": 60, "tahmini_maliyet_eur": 15}}
            ]
        }}
    ]
    """
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(raw_text)
    except Exception:
        return None

# --- 5. ARAYÜZ VE GİRDİLER ---
st.set_page_config(page_title="Global Rota Planlayıcı V2 + Bütçe", layout="wide", initial_sidebar_state="expanded")

# CSS: Metin taşmalarını önle ve genel arayüz estetiğini iyileştir
st.markdown("""
    <style>
    .stMarkdown, .stText, p, div { word-wrap: break-word; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { border-radius: 4px 4px 0px 0px; padding: 10px 20px; }
    </style>
""", unsafe_allow_html=True)

# Popüler şehirler için görsel banner sözlüğü
sehir_gorselleri = {
    "prag": "https://images.unsplash.com/photo-1519677100203-a0e668c92439?auto=format&fit=crop&w=1200&q=80",
    "amsterdam": "https://images.unsplash.com/photo-1534351590666-13e3e96b5017?auto=format&fit=crop&w=1200&q=80",
    "koln": "https://images.unsplash.com/photo-1558223616-e5db369cfbb8?auto=format&fit=crop&w=1200&q=80",
    "viyana": "https://images.unsplash.com/photo-1516550893923-42d28e5677af?auto=format&fit=crop&w=1200&q=80",
    "paris": "https://images.unsplash.com/photo-1502602881469-411327c62c95?auto=format&fit=crop&w=1200&q=80",
    "berlin": "https://images.unsplash.com/photo-1560969184-10fe8719e047?auto=format&fit=crop&w=1200&q=80",
    "londra": "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad?auto=format&fit=crop&w=1200&q=80",
    "roma": "https://images.unsplash.com/photo-1552832230-c0197dd311b5?auto=format&fit=crop&w=1200&q=80"
}

with st.sidebar:
    st.header("🌍 Büyük Avrupa Turu")
    
    st.subheader("📅 Seyahat Tarihi")
    start_date = st.date_input("Tur Başlangıç Tarihi", value=date.today() + timedelta(days=5))
    
    st.subheader("Günlük Zaman Planı")
    col1, col2 = st.columns(2)
    with col1:
        start_time = st.time_input("Mesai Başlangıcı", time(9, 0))
    with col2:
        end_time = st.time_input("Mesai Bitişi", time(20, 0))
        
    st.markdown("---")
    st.subheader("🤖 Yapay Zeka Asistanı")
    user_ai_prompt = st.text_area(
        "Tüm seyahat planını detaylıca yaz:", 
        placeholder="Örn: 3 gün Prag, 2 gün Viyana. Bol bol tarihi yer görelim.",
        height=130
    )
    
    st.markdown("---")
    generate_btn = st.button("Rotayı ve Bütçeyi Hesapla 🚀", use_container_width=True)

# --- 6. ANA UYGULAMA MANTIĞI VE SEKMELER ---
st.title("🗺️ Global Rota Planlayıcı V2 (Bütçe Motorlu)")
st.caption("Seyahat rotanızı optimize eder, otobüs biletlerinizi bulur ve müze/yemek masraflarıyla genel bütçeyi hesaplar.")

if generate_btn:
    if not user_ai_prompt:
        st.error("Lütfen hayalinizdeki seyahat planını yazın!")
    else:
        multi_day_plan = []
        genel_gun_sayaci = 1 
        genel_toplam_maliyet = 0.0
        
        with st.spinner("🧠 Yapay zeka niyetinizi ayrıştırıyor..."):
            istek_listesi = kullanici_niyetini_analiz_et(user_ai_prompt)
            
        if not istek_listesi:
            st.error("Girdiğiniz metin analiz edilemedi. Lütfen daha net yazın.")
        else:
            for islem in istek_listesi:
                sehir = islem["sehir_adi"]
                gun = islem["gun_sayisi"]
                ozel = islem["ozel_istek_mi"]
                
                with st.spinner(f"📍 {sehir} ({gun} Gün) için rota ve mekan maliyetleri hazırlanıyor..."):
                    sehir_plani = None
                    if not ozel:
                        sehir_plani = cache_den_getir(sehir, gun)
                        if sehir_plani:
                            st.success(f"⚡ {sehir} rotası önbellekten çekildi!")
                    
                    if not sehir_plani:
                        sehir_plani = yapay_zekadan_sehir_rotasi_iste(sehir, gun, user_ai_prompt)
                        if sehir_plani:
                            st.success(f"🧠 {sehir} rotası sıfırdan çizildi ve bütçelendirildi!")
                            cache_e_kaydet(sehir, gun, sehir_plani, ozel)
                    
                    if sehir_plani:
                        for gun_verisi in sehir_plani:
                            gun_verisi["gun"] = genel_gun_sayaci
                            multi_day_plan.append(gun_verisi)
                            genel_gun_sayaci += 1

            if not multi_day_plan:
                st.warning("Hiçbir şehir için rota oluşturulamadı. Lütfen tekrar deneyin.")
            else:
                
                # --- TRANSIT VE MALİYET HESAPLAMA ---
                with st.spinner("🚌 Ulaşım biletleri ve toplam bütçe hesaplanıyor..."):
                    for i in range(len(multi_day_plan)):
                        gunluk_maliyet = 0.0
                        for mekan in multi_day_plan[i]['mekanlar']:
                            gunluk_maliyet += mekan.get('tahmini_maliyet_eur', 0)
                        
                        if i > 0:
                            onceki_sehir = multi_day_plan[i-1]['sehir']
                            yeni_sehir = multi_day_plan[i]['sehir']
                            
                            if onceki_sehir != yeni_sehir:
                                gecis_tarihi_obj = start_date + timedelta(days=multi_day_plan[i]['gun'] - 1)
                                gecis_tarihi_str = gecis_tarihi_obj.strftime("%d.%m.%Y")
                                
                                transit_sonuc = transit_maliyet_hesapla(onceki_sehir, yeni_sehir, gecis_tarihi_str)
                                multi_day_plan[i]['transit'] = transit_sonuc
                                gunluk_maliyet += transit_sonuc['fiyat']
                        
                        genel_toplam_maliyet += gunluk_maliyet

                st.balloons()
                
                # --- DEVASA BÜTÇE EKRANI ---
                st.markdown("---")
                st.success(f"### 💶 Tahmini Toplam Tur Maliyeti: **{round(genel_toplam_maliyet, 2)} EUR**")
                st.caption("*(Ulaşım biletleri, müze girişleri, yeme-içme ve aktiviteler dahildir. Otel/Uçak hariçtir.)*")
                st.markdown("---")
                
                # --- PDF İNDİRME BUTONU ---
                full_pdf_bytes = generate_full_travel_booklet(multi_day_plan, start_time, end_time, round(genel_toplam_maliyet, 2))
                st.download_button(
                    label="📥 BÜTÇELİ SEYAHAT KİTAPÇIĞINI PDF OLARAK İNDİR",
                    data=full_pdf_bytes,
                    file_name="Avrupa_Turu_Seyahat_Kitapcigi_Butceli.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                st.markdown("---")
                
                tab_titles = [f"📅 Gün {day['gun']} ({day['sehir']})" for day in multi_day_plan]
                tabs = st.tabs(tab_titles)
                
                for i, tab in enumerate(tabs):
                    with tab:
                        day_data = multi_day_plan[i]
                        city_name = day_data['sehir']
                        df_day = pd.DataFrame(day_data['mekanlar'])
                        
                        # --- GÖRSEL BANNER (KAPAK FOTOĞRAFI) ---
                        sehir_key = tr_to_en(city_name).lower()
                        if sehir_key in sehir_gorselleri:
                            st.image(sehir_gorselleri[sehir_key], use_column_width=True, caption=f"✨ {city_name} Manzarası")
                        
                        if 'transit' in day_data:
                            t_info = day_data['transit']
                            if t_info['durum'] == 'basarili':
                                st.success(f"🚌 **Şehir Değişimi:** {t_info['mesaj']} | **Maliyet:** {t_info['fiyat']} € *(Kaynak: {t_info['kaynak']})*")
                            else:
                                st.warning(f"⚠️ **Şehir Değişimi (Tahmini):** {t_info['mesaj']} | **Maliyet:** {t_info['fiyat']} € *(Kaynak: {t_info['kaynak']})*")
                        
                        if df_day.empty:
                            continue
                            
                        baslangic_lat = df_day.iloc[0]['lat']
                        baslangic_lon = df_day.iloc[0]['lon']
                        
                        start_dt = pd.Timestamp(f"2000-01-01 {start_time}")
                        end_dt = pd.Timestamp(f"2000-01-01 {end_time}")
                        total_available_minutes = int((end_dt - start_dt).total_seconds() / 60)
                        
                        gunluk_rota = optimize_route(df_day, baslangic_lat, baslangic_lon, total_available_minutes)
                        
                        if gunluk_rota.empty:
                            st.warning("Zaman yetersiz!")
                        else:
                            # Sütun oranları [1, 1] yapılarak yazı taşmaları engellendi
                            map_col, timeline_col = st.columns([1, 1])
                            
                            with map_col:
                                map_center = [gunluk_rota['lat'].mean(), gunluk_rota['lon'].mean()]
                                m = folium.Map(location=map_center, zoom_start=13)
                                route_coords = []
                                
                                for idx, row in gunluk_rota.iterrows():
                                    coord = [row['lat'], row['lon']]
                                    route_coords.append(coord)
                                    mekan_maliyet = row.get('tahmini_maliyet_eur', 0)
                                    folium.Marker(
                                        location=coord,
                                        tooltip=f"{idx+1}. {row['name']} ({mekan_maliyet}€)",
                                        popup=folium.Popup(f"<b>{idx+1}. Durak:</b> {row['name']}<br><i>{row['kategori']}</i><br><b>Maliyet:</b> {mekan_maliyet}€", max_width=250),
                                        icon=folium.Icon(color="darkblue", icon="info-sign")
                                    ).add_to(m)
                                    
                                folium.PolyLine(locations=route_coords, color="red", weight=4, opacity=0.7, dash_array='10').add_to(m)
                                folium_static(m, width=400, height=450)
                                
                            with timeline_col:
                                st.subheader(f"📍 {city_name} Çizelgesi")
                                current_time = start_dt
                                st.info(f"**{current_time.strftime('%H:%M')}** | 🚶‍♂️ Güne Başlangıç")
                                
                                for idx, row in gunluk_rota.iterrows():
                                    current_time += timedelta(minutes=int(row['travel_time']))
                                    varis_saati = current_time.strftime('%H:%M')
                                    current_time += timedelta(minutes=int(row['ort_sure']))
                                    cikis_saati = current_time.strftime('%H:%M')
                                    
                                    mekan_maliyet = row.get('tahmini_maliyet_eur', 0)
                                    st.warning(
                                        f"**{varis_saati} - {cikis_saati}** | 📍 {idx+1}. {row['name']}\n\n"
                                        f"*Kategori: {row['kategori']} | 💶 {mekan_maliyet} € | Yol: {int(row['travel_time'])} dk*"
                                    )
