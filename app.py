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

# --- 1.5 ULAŞIM VE BÜTÇE HESAPLAMA MOTORU ---
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
        return {"durum": "basarili", "kaynak": "Flixbus API", "fiyat": round(son_fiyat, 2), "mesaj": f"Bus: {kalkis_sehri} -> {varis_sehri}"}
        
    lat1, lon1 = koordinat_bul(kalkis_sehri)
    lat2, lon2 = koordinat_bul(varis_sehri)
    
    if lat1 and lon1 and lat2 and lon2:
        mesafe_km = osrm_mesafe_cek(lat1, lon1, lat2, lon2)
        if mesafe_km:
            return {"durum": "tahmini", "kaynak": "OSRM Distance", "fiyat": round(mesafe_km * 0.08, 2), "mesaj": f"Estimated Car Route ({round(mesafe_km, 1)} km)"}
            
    return {"durum": "varsayilan", "kaynak": "Default", "fiyat": 45.00, "mesaj": f"Standard Transit"}

# --- 2. EVRENSEL KARAKTER TEMİZLEME VE ICS OLUŞTURUCU ---
def tr_to_en(text):
    text = str(text)
    text = text.replace('ı', 'i').replace('İ', 'I').replace('ö', 'o').replace('Ö', 'O').replace('ü', 'u').replace('Ü', 'U').replace('ç', 'c').replace('Ç', 'C').replace('ş', 's').replace('Ş', 'S').replace('ğ', 'g').replace('Ğ', 'G')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if unicodedata.category(c) != 'Mn')
    return text

def generate_ics_calendar(multi_day_plan, start_date_input, start_time_input, end_time_input, dil):
    cal_lines = []
    cal_lines.append("BEGIN:VCALENDAR")
    cal_lines.append("VERSION:2.0")
    cal_lines.append("PRODID:-//Global Rota Planlayici V2//TR")
    
    start_dt_base = pd.Timestamp(f"2000-01-01 {start_time_input}")
    end_dt_base = pd.Timestamp(f"2000-01-01 {end_time_input}")
    total_available_minutes = int((end_dt_base - start_dt_base).total_seconds() / 60)
    
    for day_data in multi_day_plan:
        gun_no = day_data['gun']
        sehir_adi = tr_to_en(day_data['sehir'])
        df_day = pd.DataFrame(day_data['mekanlar'])
        
        if df_day.empty: continue
            
        current_date = start_date_input + timedelta(days=gun_no - 1)
        date_str = current_date.strftime("%Y%m%d")
        
        baslangic_lat = df_day.iloc[0]['lat']
        baslangic_lon = df_day.iloc[0]['lon']
        
        gunluk_rota = optimize_route(df_day, baslangic_lat, baslangic_lon, total_available_minutes)
        current_time = start_dt_base
        
        for idx, row in gunluk_rota.iterrows():
            current_time += timedelta(minutes=int(row['travel_time']))
            start_time_str = current_time.strftime('%H%M%S')
            
            current_time += timedelta(minutes=int(row['ort_sure']))
            end_time_str = current_time.strftime('%H%M%S')
            
            mekan_maliyet = row.get('tahmini_maliyet_eur', 0)
            durak_adi = tr_to_en(row['name'])
            kat_adi = tr_to_en(row['kategori'])
            
            maliyet_etiket = "Maliyet / Cost"
            
            cal_lines.append("BEGIN:VEVENT")
            cal_lines.append(f"SUMMARY:{durak_adi} ({sehir_adi})")
            cal_lines.append(f"DTSTART:{date_str}T{start_time_str}")
            cal_lines.append(f"DTEND:{date_str}T{end_time_str}")
            cal_lines.append(f"DESCRIPTION:{kat_adi}\\n{maliyet_etiket}: {mekan_maliyet} EUR")
            cal_lines.append(f"LOCATION:{durak_adi}, {sehir_adi}")
            cal_lines.append("END:VEVENT")
            
    cal_lines.append("END:VCALENDAR")
    return "\n".join(cal_lines)

# --- 3. DİNAMİK DİL DESTEKLİ PDF YAPMA FONKSİYONU ---
def generate_full_travel_booklet(multi_day_plan, start_time_input, end_time_input, genel_toplam_maliyet, seyahat_tarzi, seyahat_hizi, dil):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    
    # Çoklu Dil Sözlüğü (PDF İçin)
    dict_pdf = {
        "Türkçe": {
            "title": "KÜRESEL SEYAHAT KİTAPÇIĞI",
            "sub": "Global Rota Planlayıcı V2 ile Otonom Olarak Oluşturulmuştur",
            "budget": "Tahmini Toplam Tur Bütçesi (Ulaşım + Aktiviteler):",
            "day": "Gün", "city": "Şehir",
            "col1": "Saat Aralığı", "col2": "Durak Adı", "col3": "Kategori & Ücret", "col4": "Geçiş / Ziyaret",
            "transit": "Transit", "cost": "Maliyet", "source": "Kaynak",
            "morning": "SABAH", "route": "Yol", "duration": "Süre",
            "maps": "📍 Bu Günün Rotasını Google Haritalarda Canlı Başlat (Tıkla)"
        },
        "English": {
            "title": "GLOBAL TRAVEL BOOKLET",
            "sub": "Autonomously Generated with Global Route Planner V2",
            "budget": "Estimated Total Tour Budget (Transit + Activities):",
            "day": "Day", "city": "City",
            "col1": "Time Range", "col2": "Stop Name", "col3": "Category & Fee", "col4": "Transit / Visit",
            "transit": "Transit", "cost": "Cost", "source": "Source",
            "morning": "MORNING", "route": "Drive", "duration": "Stay",
            "maps": "📍 Start Today's Route Live on Google Maps (Click)"
        }
    }
    
    lang = dict_pdf.get(dil, dict_pdf["Türkçe"])
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('BookletTitle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor("#1e3a8a"), spaceAfter=10, alignment=1, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('BookletSub', parent=styles['Normal'], fontSize=11, textColor=colors.HexColor("#64748b"), spaceAfter=20, alignment=1)
    budget_style = ParagraphStyle('BudgetStyle', parent=styles['Heading2'], fontSize=13, textColor=colors.white, backColor=colors.HexColor("#10b981"), spaceAfter=30, alignment=1, borderPadding=(8, 15, 8, 15), borderRadius=5)
    day_heading = ParagraphStyle('DayHeading', parent=styles['Heading2'], fontSize=16, textColor=colors.HexColor("#1e40af"), spaceAfter=10, spaceBefore=20, fontName='Helvetica-Bold')
    maps_link_style = ParagraphStyle('MapsLink', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor("#2563eb"), spaceAfter=15, fontName='Helvetica-Bold')
    cell_style = ParagraphStyle('CellStyle', parent=styles['Normal'], fontSize=8, leading=11, textColor=colors.HexColor("#334155"))
    header_style = ParagraphStyle('HeaderStyle', parent=styles['Normal'], fontSize=9, leading=11, textColor=colors.white, fontName='Helvetica-Bold')
    
    story.append(Paragraph(tr_to_en(lang["title"]), title_style))
    tarz_ismi = tr_to_en(seyahat_tarzi.split(" ")[0])
    hiz_ismi = tr_to_en(seyahat_hizi.split(" ")[0])
    story.append(Paragraph(tr_to_en(f"{lang['sub']} | Mod: {tarz_ismi} - Pace: {hiz_ismi}"), subtitle_style))
    story.append(Paragraph(tr_to_en(f"{lang['budget']} {genel_toplam_maliyet} EUR"), budget_style))
    story.append(Spacer(1, 10))
    
    start_dt_base = pd.Timestamp(f"2000-01-01 {start_time_input}")
    end_dt_base = pd.Timestamp(f"2000-01-01 {end_time_input}")
    total_available_minutes = int((end_dt_base - start_dt_base).total_seconds() / 60)
    
    for i, day_data in enumerate(multi_day_plan):
        gun_no = day_data['gun']
        sehir_adi = day_data['sehir']
        df_day = pd.DataFrame(day_data['mekanlar'])
        
        if df_day.empty: continue
            
        baslangic_lat = df_day.iloc[0]['lat']
        baslangic_lon = df_day.iloc[0]['lon']
        
        gunluk_rota = optimize_route(df_day, baslangic_lat, baslangic_lon, total_available_minutes)
        coords = [f"{row['lat']},{row['lon']}" for _, row in gunluk_rota.iterrows()]
        gmaps_url = f"https://www.google.com/maps/dir/{'/'.join(coords)}"
        
        story.append(Paragraph(tr_to_en(f"{lang['day']} {gun_no} - {lang['city']}: {sehir_adi}"), day_heading))
        story.append(Paragraph(f'<a href="{gmaps_url}" color="blue">{tr_to_en(lang["maps"])}</a>', maps_link_style))
        
        table_data = [[
            Paragraph(tr_to_en(lang["col1"]), header_style), Paragraph(tr_to_en(lang["col2"]), header_style), 
            Paragraph(tr_to_en(lang["col3"]), header_style), Paragraph(tr_to_en(lang["col4"]), header_style)
        ]]
        
        if 'transit' in day_data:
            t = day_data['transit']
            table_data.append([
                Paragraph(lang["morning"], cell_style), Paragraph(tr_to_en(f"{lang['transit']}: {t['mesaj']}"), cell_style), 
                Paragraph(tr_to_en(f"{lang['cost']} | {t['fiyat']} EUR"), cell_style), Paragraph(tr_to_en(f"{lang['source']}: {t['kaynak']}"), cell_style)
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
            sure_str = tr_to_en(f"{lang['route']}: {int(row['travel_time'])}m | {lang['duration']}: {row['ort_sure']}m")
            
            table_data.append([
                Paragraph(zaman_str, cell_style), Paragraph(durak_str, cell_style), 
                Paragraph(kat_str, cell_style), Paragraph(sure_str, cell_style)
            ])
            
        t = Table(table_data, colWidths=[70, 190, 120, 150])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2563eb")), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8), ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#f8fafc"), colors.HexColor("#ffffff")]), 
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6), ('TOPPADDING', (0, 1), (-1, -1), 6),
        ]))
        
        story.append(t)
        story.append(Spacer(1, 15))
        if i < len(multi_day_plan) - 1:
            story.append(PageBreak())
            
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# --- 4. CACHE VE YAPAY ZEKA MODÜLLERİ ---
def cache_den_getir(sehir_adi, gun_sayisi, seyahat_tarzi, seyahat_hizi, sehir_butce_siniri, dil):
    if supabase is None: return None
    tarz_kisa = seyahat_tarzi.split(" ")[0]
    hiz_kisa = seyahat_hizi.split(" ")[0]
    cache_key = f"{sehir_adi}_{tarz_kisa}_{hiz_kisa}_{sehir_butce_siniri}_{dil}"
    try:
        response = supabase.table("sehir_rotalari_cache") \
            .select("rota_jsonb").eq("sehir_adi", cache_key).eq("gun_sayisi", gun_sayisi).execute()
        if len(response.data) > 0:
            return response.data[0]["rota_jsonb"]
        return None
    except Exception:
        return None

def cache_e_kaydet(sehir_adi, gun_sayisi, rota_jsonb, ozel_istek_mi, seyahat_tarzi, seyahat_hizi, sehir_butce_siniri, dil):
    if supabase is None or ozel_istek_mi: return
    tarz_kisa = seyahat_tarzi.split(" ")[0]
    hiz_kisa = seyahat_hizi.split(" ")[0]
    cache_key = f"{sehir_adi}_{tarz_kisa}_{hiz_kisa}_{sehir_butce_siniri}_{dil}"
    try:
        data = {"sehir_adi": cache_key, "gun_sayisi": gun_sayisi, "rota_jsonb": rota_jsonb, "ozel_istek_mi": ozel_istek_mi}
        supabase.table("sehir_rotalari_cache").insert(data).execute()
    except Exception:
        pass

def kullanici_niyetini_analiz_et(kullanici_girdisi):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Sen bir veri ayrıştırıcısısın. İsteği analiz et: "{kullanici_girdisi}"
    Bana SADECE şu formatta JSON dön:
    [
        {{"sehir_adi": "Prag", "gun_sayisi": 3, "ozel_istek_mi": false}}
    ]
    Şehir adlarını daima yaygın İngilizce veya Türkçe kökleriyle yaz (Örn: Praha yerine Prag veya Prague).
    Kurallar: Özel istek/mekan/şart varsa 'ozel_istek_mi': true, yoksa false. Asla markdown kullanma.
    """
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(raw_text)
    except Exception:
        return []

def yapay_zekadan_sehir_rotasi_iste(sehir_adi, gun_sayisi, ana_istek, seyahat_tarzi, seyahat_hizi, sehir_butce_siniri, dil):
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Bütçe, Hız ve Dil Kuralları
    butce_kurali = "0-15 EUR. Ücretsiz müzeler, parklar." if "Ekonomik" in seyahat_tarzi else ("15-40 EUR. Popüler lokasyonlar." if "Standart" in seyahat_tarzi else "50-150 EUR. Lüks restoranlar, VIP turlar.")
    hiz_kurali = "Günde 7-9 mekan (süre 30-45dk)." if "Hızlı" in seyahat_hizi else ("Günde 3-4 mekan (süre 90-150dk)." if "Yavaş" in seyahat_hizi else "Günde 5-6 mekan (süre 60-90dk).")
    
    hard_cap_kurali = f"\nKESİN KURAL: Tüm mekanların 'tahmini_maliyet_eur' TOPLAMI {sehir_butce_siniri} Euro'yu GEÇMEMELİDİR!" if sehir_butce_siniri > 0 else ""
    lang_kurali = f"\nÇOK ÖNEMLİ: Çıktıdaki 'name' ve 'kategori' değerlerini tamamen {dil} dilinde yazmalısın. Şehir adını da {dil} olarak güncelle."

    prompt = f"""
    Kullanıcının ana isteği: {ana_istek}
    Görev: SADECE {sehir_adi} şehri için {gun_sayisi} günlük rota oluştur. 
    YENİ GÖREV (HIZ): {hiz_kurali}
    YENİ GÖREV (TARZ): {butce_kurali} {hard_cap_kurali}
    {lang_kurali}
    
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
st.set_page_config(page_title="Global Route Planner", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .stMarkdown, .stText, p, div { word-wrap: break-word; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { border-radius: 4px 4px 0px 0px; padding: 10px 20px; }
    a { text-decoration: none; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

sehir_gorselleri = {
    "prag": "https://images.unsplash.com/photo-1519677100203-a0e668c92439", "prague": "https://images.unsplash.com/photo-1519677100203-a0e668c92439",
    "amsterdam": "https://images.unsplash.com/photo-1534351590666-13e3e96b5017",
    "koln": "https://images.unsplash.com/photo-1572889650742-9907159781ce", "cologne": "https://images.unsplash.com/photo-1572889650742-9907159781ce",
    "viyana": "https://images.unsplash.com/photo-1516550893923-42d28e5677af", "vienna": "https://images.unsplash.com/photo-1516550893923-42d28e5677af",
    "paris": "https://images.unsplash.com/photo-1499856871958-5b9627545d1a",
    "berlin": "https://images.unsplash.com/photo-1560969184-10fe8719e047",
    "londra": "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad", "london": "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad",
    "roma": "https://images.unsplash.com/photo-1552832230-c0197dd311b5", "rome": "https://images.unsplash.com/photo-1552832230-c0197dd311b5",
}

with st.sidebar:
    # Çoklu Dil Seçicisi
    uygulama_dili = st.selectbox("🌐 Dil / Language", ["Türkçe", "English"], index=0)
    
    st.header("🌍 Büyük Avrupa Turu" if uygulama_dili == "Türkçe" else "🌍 Grand Europe Tour")
    
    start_date = st.date_input("📅 Tur Başlangıç Tarihi" if uygulama_dili == "Türkçe" else "📅 Tour Start Date", value=date.today() + timedelta(days=5))
    
    st.markdown("---")
    st.subheader("🎒 Seyahat Dinamikleri" if uygulama_dili == "Türkçe" else "🎒 Travel Dynamics")
    seyahat_tarzi = st.selectbox("Bütçe ve Konfor" if uygulama_dili == "Türkçe" else "Budget & Comfort", ["Ekonomik (Öğrenci)", "Standart (Turist)", "Premium (Lüks)"] if uygulama_dili == "Türkçe" else ["Economic (Backpacker)", "Standard (Tourist)", "Premium (Luxury)"], index=1)
    seyahat_hizi = st.selectbox("Seyahat Hızı" if uygulama_dili == "Türkçe" else "Pace", ["Yavaş (Günde 3-4 Mekan)", "Normal (Günde 5-6 Mekan)", "Hızlı (Günde 7-9 Mekan)"] if uygulama_dili == "Türkçe" else ["Slow (3-4 Places/Day)", "Normal (5-6 Places/Day)", "Fast (7-9 Places/Day)"], index=1)
    maksimum_butce = st.number_input("Maksimum Bütçe (EUR) - Opsiyonel" if uygulama_dili == "Türkçe" else "Max Total Budget (EUR) - Optional", min_value=0, value=0, step=50)
    
    st.markdown("---")
    st.subheader("Zaman Planı" if uygulama_dili == "Türkçe" else "Daily Schedule")
    col1, col2 = st.columns(2)
    with col1:
        start_time = st.time_input("Mesai Başlangıcı" if uygulama_dili == "Türkçe" else "Start Time", time(9, 0))
    with col2:
        end_time = st.time_input("Mesai Bitişi" if uygulama_dili == "Türkçe" else "End Time", time(20, 0))
        
    st.markdown("---")
    st.subheader("🤖 Yapay Zeka Asistanı" if uygulama_dili == "Türkçe" else "🤖 AI Assistant")
    user_ai_prompt = st.text_area("Seyahat planını yaz:" if uygulama_dili == "Türkçe" else "Write your travel plan:", placeholder="Örn: 2 gün Prag, 1 gün Viyana..." if uygulama_dili == "Türkçe" else "Ex: 2 days in Paris, 1 day in Rome...", height=130)
    
    st.markdown("---")
    generate_btn = st.button("Rotayı Hesapla 🚀" if uygulama_dili == "Türkçe" else "Generate Route 🚀", use_container_width=True)

# --- 6. ANA UYGULAMA MANTIĞI ---
st.title("🗺️ Global Route Planner V2" if uygulama_dili == "English" else "🗺️ Global Rota Planlayıcı V2")

if "plan_olusturuldu" not in st.session_state:
    st.session_state.plan_olusturuldu = False

if generate_btn:
    if not user_ai_prompt:
        st.error("Lütfen hayalinizdeki seyahat planını yazın!" if uygulama_dili == "Türkçe" else "Please write your dream travel plan!")
    else:
        multi_day_plan = []
        genel_gun_sayaci = 1 
        genel_toplam_maliyet = 0.0
        
        with st.spinner("Niyetiniz ayrıştırılıyor..." if uygulama_dili == "Türkçe" else "Analyzing your request..."):
            istek_listesi = kullanici_niyetini_analiz_et(user_ai_prompt)
            
        if not istek_listesi:
            st.error("Metin analiz edilemedi." if uygulama_dili == "Türkçe" else "Could not analyze the text.")
        else:
            toplam_gun = sum([islem["gun_sayisi"] for islem in istek_listesi])
            
            for islem in istek_listesi:
                sehir = islem["sehir_adi"]
                gun = islem["gun_sayisi"]
                ozel = islem["ozel_istek_mi"]
                
                sehir_butcesi = 0
                if maksimum_butce > 0 and toplam_gun > 0:
                    sehir_butcesi = int((maksimum_butce / toplam_gun) * gun)
                
                with st.spinner(f"📍 {sehir} ({gun} {'Gün' if uygulama_dili=='Türkçe' else 'Days'}) hesaplanıyor..." if uygulama_dili == "Türkçe" else f"📍 Calculating {sehir}..."):
                    sehir_plani = None
                    if not ozel:
                        sehir_plani = cache_den_getir(sehir, gun, seyahat_tarzi, seyahat_hizi, sehir_butcesi, uygulama_dili)
                        if sehir_plani:
                            st.success(f"⚡ {sehir} cache'den çekildi!" if uygulama_dili == "Türkçe" else f"⚡ {sehir} loaded from cache!")
                    
                    if not sehir_plani:
                        sehir_plani = yapay_zekadan_sehir_rotasi_iste(sehir, gun, user_ai_prompt, seyahat_tarzi, seyahat_hizi, sehir_butcesi, uygulama_dili)
                        if sehir_plani:
                            st.success(f"🧠 {sehir} rotası çizildi!" if uygulama_dili == "Türkçe" else f"🧠 {sehir} route generated!")
                            cache_e_kaydet(sehir, gun, sehir_plani, ozel, seyahat_tarzi, seyahat_hizi, sehir_butcesi, uygulama_dili)
                        else:
                            st.error(f"❌ {sehir} API Limit!" if uygulama_dili == "Türkçe" else f"❌ {sehir} API Limit Error!")
                    
                    if sehir_plani:
                        for gun_verisi in sehir_plani:
                            gun_verisi["gun"] = genel_gun_sayaci
                            multi_day_plan.append(gun_verisi)
                            genel_gun_sayaci += 1
                sleep(4)

            if not multi_day_plan:
                st.warning("Rota oluşturulamadı." if uygulama_dili == "Türkçe" else "Could not generate route.")
            else:
                with st.spinner("Ulaşım ve bütçe toplanıyor..." if uygulama_dili == "Türkçe" else "Calculating transit & budget..."):
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

                st.session_state.multi_day_plan = multi_day_plan
                st.session_state.genel_toplam_maliyet = genel_toplam_maliyet
                st.session_state.seyahat_tarzi = seyahat_tarzi
                st.session_state.seyahat_hizi = seyahat_hizi
                st.session_state.uygulama_dili = uygulama_dili
                
                st.session_state.full_pdf_bytes = generate_full_travel_booklet(
                    multi_day_plan, start_time, end_time, round(genel_toplam_maliyet, 2), seyahat_tarzi, seyahat_hizi, uygulama_dili
                )
                ics_str = generate_ics_calendar(multi_day_plan, start_date, start_time, end_time, uygulama_dili)
                st.session_state.ics_bytes = ics_str.encode('utf-8')
                
                st.session_state.plan_olusturuldu = True
                st.rerun()

if st.session_state.plan_olusturuldu:
    
    # --- YENİ: KITALARARASI MAKRO HARİTA BÖLÜMÜ ---
    st.markdown("### 🌍 " + ("Kıtalararası Tur Haritası" if st.session_state.uygulama_dili == "Türkçe" else "Intercontinental Tour Map"))
    macro_coords = []
    macro_cities = []
    
    # Şehirlerin sadece ilk duraklarının koordinatlarını al
    for day in st.session_state.multi_day_plan:
        if day['mekanlar']:
            lat = day['mekanlar'][0]['lat']
            lon = day['mekanlar'][0]['lon']
            sehir = day['sehir']
            if sehir not in macro_cities:
                macro_coords.append([lat, lon])
                macro_cities.append(sehir)
                
    if len(macro_coords) > 0:
        # Haritayı tüm şehirlerin ortasına odakla
        avg_lat = sum([c[0] for c in macro_coords]) / len(macro_coords)
        avg_lon = sum([c[1] for c in macro_coords]) / len(macro_coords)
        m_macro = folium.Map(location=[avg_lat, avg_lon], zoom_start=5)
        
        for i, coord in enumerate(macro_coords):
            folium.Marker(
                location=coord, 
                tooltip=f"{i+1}. Şehir: {macro_cities[i]}" if st.session_state.uygulama_dili == "Türkçe" else f"Stop {i+1}: {macro_cities[i]}",
                icon=folium.Icon(color="red", icon="star")
            ).add_to(m_macro)
            
        if len(macro_coords) > 1:
            folium.PolyLine(locations=macro_coords, color="blue", weight=3, dash_array='5', tooltip="Rota" if st.session_state.uygulama_dili=="Türkçe" else "Route").add_to(m_macro)
            
        folium_static(m_macro, width=900, height=350)
    # ---------------------------------------------
    
    st.markdown("---")
    if maksimum_butce > 0 and st.session_state.genel_toplam_maliyet > maksimum_butce:
        st.warning(f"⚠️ **Dikkat:** Bütçeyi aştınız. / Budget limit exceeded.")
        
    st.success(f"### 💶 {'Toplam Tur Maliyeti' if st.session_state.uygulama_dili == 'Türkçe' else 'Total Tour Cost'}: **{round(st.session_state.genel_toplam_maliyet, 2)} EUR**")
    st.markdown("---")
    
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            label="📥 PDF İNDİR" if st.session_state.uygulama_dili == "Türkçe" else "📥 DOWNLOAD PDF",
            data=st.session_state.full_pdf_bytes,
            file_name="Avrupa_Turu.pdf" if st.session_state.uygulama_dili == "Türkçe" else "Europe_Tour.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    with col_dl2:
        st.download_button(
            label="📅 TAKVİME EKLE (.ICS)" if st.session_state.uygulama_dili == "Türkçe" else "📅 ADD TO CALENDAR (.ICS)",
            data=st.session_state.ics_bytes,
            file_name="Avrupa_Turu_Takvimi.ics" if st.session_state.uygulama_dili == "Türkçe" else "Europe_Tour_Calendar.ics",
            mime="text/calendar",
            use_container_width=True
        )
        
    st.markdown("---")
    
    tab_titles = [f"📅 {'Gün' if st.session_state.uygulama_dili == 'Türkçe' else 'Day'} {day['gun']} ({day['sehir']})" for day in st.session_state.multi_day_plan]
    tabs = st.tabs(tab_titles)
    
    for i, tab in enumerate(tabs):
        with tab:
            day_data = st.session_state.multi_day_plan[i]
            city_name = day_data['sehir']
            df_day = pd.DataFrame(day_data['mekanlar'])
            
            sehir_key_raw = tr_to_en(city_name).strip().lower()
            eslesen_gorsel = None
            
            for key, url in sehir_gorselleri.items():
                if key in sehir_key_raw:
                    eslesen_gorsel = url
                    break
                    
            if eslesen_gorsel:
                st.image(eslesen_gorsel, use_container_width=True, caption=f"✨ {city_name}")
            
            if 'transit' in day_data:
                t_info = day_data['transit']
                if t_info['durum'] == 'basarili':
                    st.success(f"🚌 **Transit:** {t_info['mesaj']} | **{'Maliyet' if st.session_state.uygulama_dili == 'Türkçe' else 'Cost'}:** {t_info['fiyat']} €")
                else:
                    st.warning(f"⚠️ **Transit:** {t_info['mesaj']} | **{'Maliyet' if st.session_state.uygulama_dili == 'Türkçe' else 'Cost'}:** {t_info['fiyat']} €")
            
            if df_day.empty:
                continue
                
            baslangic_lat = df_day.iloc[0]['lat']
            baslangic_lon = df_day.iloc[0]['lon']
            
            start_dt = pd.Timestamp(f"2000-01-01 {start_time}")
            end_dt = pd.Timestamp(f"2000-01-01 {end_time}")
            total_available_minutes = int((end_dt - start_dt).total_seconds() / 60)
            
            gunluk_rota = optimize_route(df_day, baslangic_lat, baslangic_lon, total_available_minutes)
            
            if gunluk_rota.empty:
                st.warning("Zaman yetersiz! / Not enough time!")
            else:
                coords_list = [f"{row['lat']},{row['lon']}" for _, row in gunluk_rota.iterrows()]
                gmaps_url = f"https://www.google.com/maps/dir/{'/'.join(coords_list)}"
                
                st.markdown(f"### [📍 {'Google Haritalarda Canlı Başlat' if st.session_state.uygulama_dili == 'Türkçe' else 'Start Live Navigation on Google Maps'}]({gmaps_url})")
                st.write("")
                
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
                    st.subheader(f"📍 {city_name} {'Çizelgesi' if st.session_state.uygulama_dili == 'Türkçe' else 'Timeline'}")
                    current_time = start_dt
                    st.info(f"**{current_time.strftime('%H:%M')}** | 🚶‍♂️ {'Güne Başlangıç' if st.session_state.uygulama_dili == 'Türkçe' else 'Start of the Day'}")
                    
                    for idx, row in gunluk_rota.iterrows():
                        current_time += timedelta(minutes=int(row['travel_time']))
                        varis_saati = current_time.strftime('%H:%M')
                        current_time += timedelta(minutes=int(row['ort_sure']))
                        cikis_saati = current_time.strftime('%H:%M')
                        
                        mekan_maliyet = row.get('tahmini_maliyet_eur', 0)
                        st.warning(
                            f"**{varis_saati} - {cikis_saati}** | 📍 {idx+1}. {row['name']}\n\n"
                            f"*[{row['kategori']}] | 💶 {mekan_maliyet} € | {'Yol' if st.session_state.uygulama_dili == 'Türkçe' else 'Drive'}: {int(row['travel_time'])}m*"
                        )
