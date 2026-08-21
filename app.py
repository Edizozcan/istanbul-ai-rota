import streamlit as st
import pandas as pd
import math
import json
from datetime import time, timedelta
import google.generativeai as genai
import folium
from streamlit_folium import folium_static 
import io
import streamlit as st
from supabase import create_client, Client

# --- 1. SUPABASE BAĞLANTISI ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# --- 2. AKILLI ÖNBELLEK (CACHE) FONKSİYONLARI ---
def cache_den_getir(sehir_adi, gun_sayisi):
    """Veritabanında daha önce oluşturulmuş bir rota varsa saniyesinde çeker."""
    try:
        response = supabase.table("sehir_rotalari_cache").select("rota_jsonb").eq("sehir_adi", sehir_adi).eq("gun_sayisi", gun_sayisi).execute()
        if len(response.data) > 0:
            return response.data[0]["rota_jsonb"]
        return None
    except Exception as e:
        print("Cache okuma hatası:", e)
        return None

def cache_e_kaydet(sehir_adi, gun_sayisi, rota_jsonb, ozel_istek_mi=False):
    """Yeni üretilen rotayı, içinde özel bir istek yoksa gelecekte kullanmak üzere kaydeder."""
    if not ozel_istek_mi:
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

# ReportLab kütüphaneleri
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# --- GÜVENLİ API AYARLARI ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)

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

# --- 2. TÜRKÇE KARAKTER TEMİZLEME (PDF İÇİN) ---
def tr_to_en(text):
    tr_chars = {'ş': 's', 'Ş': 'S', 'ğ': 'g', 'Ğ': 'G', 'ü': 'u', 'Ü': 'U', 
                'ş': 's', 'Ş': 'S', 'İ': 'I', 'ı': 'i', 'ö': 'o', 'Ö': 'O', 
                'ç': 'c', 'Ç': 'C'}
    for key, value in tr_chars.items():
        text = text.replace(key, value)
    return text

# --- 3. TEK PARÇA TÜM SEYAHATİ PDF YAPMA FONKSİYONU ---
def generate_full_travel_booklet(multi_day_plan, start_time_input, end_time_input):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'BookletTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor("#1f4e78"),
        spaceAfter=10,
        alignment=1 # Ortala
    )
    subtitle_style = ParagraphStyle(
        'BookletSub',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor("#595959"),
        spaceAfter=25,
        alignment=1
    )
    day_heading = ParagraphStyle(
        'DayHeading',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=colors.HexColor("#2f5597"),
        spaceAfter=10,
        spaceBefore=10
    )
    
    # Kapak / Başlık Bilgisi
    story.append(Paragraph(tr_to_en("KURESEL SEYAHAT KITAPCIGI"), title_style))
    story.append(Paragraph(tr_to_en("Global Rota Planlayici V2 ile Otonom Olarak Olusturulmustur"), subtitle_style))
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
        
        table_data = [[tr_to_en("Saat Araligi"), tr_to_en("Durak Adi"), tr_to_en("Kategori"), tr_to_en("Gecis / Ziyaret")]]
        
        current_time = start_dt_base
        for idx, row in gunluk_rota.iterrows():
            current_time += timedelta(minutes=int(row['travel_time']))
            varis_saati = current_time.strftime('%H:%M')
            current_time += timedelta(minutes=int(row['ort_sure']))
            cikis_saati = current_time.strftime('%H:%M')
            
            zaman_str = f"{varis_saati} - {cikis_saati}"
            durak_str = tr_to_en(f"{idx+1}. {row['name']}")
            kat_str = tr_to_en(str(row['kategori']))
            sure_str = tr_to_en(f"Yol: {int(row['travel_time'])}dk | Sure: {row['ort_sure']}dk")
            
            table_data.append([zaman_str, durak_str, kat_str, sure_str])
            
        t = Table(table_data, colWidths=[85, 190, 100, 180])
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
        
        # Her günden sonra yeni sayfaya geç (Son gün hariç)
        if i < len(multi_day_plan) - 1:
            story.append(PageBreak())
            
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- 4. YAPAY ZEKA ÇOKLU GÜN VERİ ÜRETİM MOTORU ---
def get_multi_day_plan_from_ai(user_prompt):
    try:
        model = genai.GenerativeModel('gemini-3.6-flash') 
    except Exception as e:
        st.error(f"Model yüklenirken hata oluştu: {e}")
        return []
    
    prompt = f"""
    Sen küresel bir seyahat planlama API'sisin.
    Kullanıcının özel isteği: "{user_prompt}"
    
    Lütfen bu isteğe uygun olarak GÜN GÜN planlanmış mantıklı bir seyahat rotası oluştur.
    Her gün için o gün bulunulan şehri ve gezilecek 5-6 mekanı belirle.
    Mekanların ENLEM (lat) ve BOYLAM (lon) koordinatlarını gerçeğe en yakın şekilde (noktadan sonra en az 4 hane) sağla.
    Kategori olarak: Tarihi, Müze, Gastronomi, Doğa vb. kullan. Ziyaret süresini (ort_sure) dakika olarak belirle.
    
    SADECE VE SADECE AŞAĞIDAKİ JSON FORMATINDA ÇIKTI VER. BAŞKA HİÇBİR AÇIKLAMA YAZMA:
    [
        {{
            "gun": 1,
            "sehir": "Prag",
            "mekanlar": [
                {{"name": "Charles Bridge", "lat": 50.0865, "lon": 14.4114, "kategori": "Tarihi", "ort_sure": 45}},
                {{"name": "Prague Castle", "lat": 50.0903, "lon": 14.3996, "kategori": "Müze", "ort_sure": 120}}
            ]
        }}
    ]
    """
    
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        ticks = chr(96) * 3
        raw_text = raw_text.replace(ticks + "json", "").replace(ticks, "").strip()
        plan_data = json.loads(raw_text)
        return plan_data
    except Exception as e:
        st.error(f"Yapay zeka planı oluştururken bir hata yaşadı: {e}")
        return []

# --- 5. ARAYÜZ VE GİRDİLER ---
st.set_page_config(page_title="Global Rota Planlayıcı V2 + Kitapçık PDF", layout="wide", initial_sidebar_state="expanded")

with st.sidebar:
    st.header("🌍 Büyük Avrupa Turu")
    
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
    generate_btn = st.button("Devasa Planı ve Kitapçığı Oluştur 🚀", use_container_width=True)

# --- 6. ANA UYGULAMA MANTIĞI VE SEKMELER ---
st.title("🗺️ Global Rota Planlayıcı V2 (Çoklu Şehir & Tekli PDF Kitapçık)")
st.caption("Yapay zeka planınızı gün gün hazırlar, sekmelerde sunar ve tüm turu tek bir profesyonel PDF kitapçık olarak indirmenizi sağlar.")

if generate_btn:
    if not user_ai_prompt:
        st.error("Lütfen hayalinizdeki seyahat planını yazın!")
    else:
        with st.spinner("🧠 Yapay zeka tüm günleri planlıyor ve kapsamlı PDF kitapçığını derliyor..."):
            multi_day_plan = get_multi_day_plan_from_ai(user_ai_prompt)
            
            if not multi_day_plan:
                st.warning("Veri çekilemedi. Lütfen tekrar deneyin.")
            else:
                st.balloons()
                
                # --- ANA SAYFADA TÜM TURU TEK PDF OLARAK İNDİRME BUTONU ---
                full_pdf_bytes = generate_full_travel_booklet(multi_day_plan, start_time, end_time)
                st.success("🎉 Devasa seyahat planınız başarıyla oluşturuldu! Aşağıdaki butondan tüm turu tek bir PDF Kitapçık olarak indirebilirsiniz:")
                st.download_button(
                    label="📥 TÜM SEYAHATİ PDF KİTAPÇIK OLARAK İNDİR (TÜM GÜNLER)",
                    data=full_pdf_bytes,
                    file_name="Avrupa_Turu_Seyahat_Kitapcigi.pdf",
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
                            map_col, timeline_col = st.columns([3, 2])
                            
                            with map_col:
                                map_center = [gunluk_rota['lat'].mean(), gunluk_rota['lon'].mean()]
                                m = folium.Map(location=map_center, zoom_start=13)
                                route_coords = []
                                
                                for idx, row in gunluk_rota.iterrows():
                                    coord = [row['lat'], row['lon']]
                                    route_coords.append(coord)
                                    folium.Marker(
                                        location=coord,
                                        tooltip=f"{idx+1}. {row['name']}",
                                        popup=folium.Popup(f"<b>{idx+1}. Durak:</b> {row['name']}<br><i>{row['kategori']}</i>", max_width=250),
                                        icon=folium.Icon(color="darkblue", icon="info-sign")
                                    ).add_to(m)
                                    
                                folium.PolyLine(locations=route_coords, color="red", weight=4, opacity=0.7, dash_array='10').add_to(m)
                                folium_static(m, width=550, height=450)
                                
                            with timeline_col:
                                st.subheader(f"📍 {city_name} Çizelgesi")
                                current_time = start_dt
                                st.info(f"**{current_time.strftime('%H:%M')}** | 🚶‍♂️ Güne Başlangıç")
                                
                                for idx, row in gunluk_rota.iterrows():
                                    current_time += timedelta(minutes=int(row['travel_time']))
                                    varis_saati = current_time.strftime('%H:%M')
                                    current_time += timedelta(minutes=int(row['ort_sure']))
                                    cikis_saati = current_time.strftime('%H:%M')
                                    
                                    st.warning(
                                        f"**{varis_saati} - {cikis_saati}** | 📍 {idx+1}. {row['name']}\n\n"
                                        f"*Kategori: {row['kategori']} | Geçiş: {int(row['travel_time'])} dk*"
                                    )
