import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, time as dtime
import itertools

# הגדרות תצוגה
st.set_page_config(
    page_title="מערכת שיבוץ וניהול משמרות",
    layout="wide",
    page_icon="🛡️",
    initial_sidebar_state="expanded"
)

# ==========================================
# עיצוב מותאם אישית (CSS) יוקרתי, נקי ונעים לעין
# ==========================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Rubik:ital,wght@0,300;0,400;0,500;0,700;1,400&display=swap');
    
    /* פונט גלובלי ויישור RTL */
    html, body, [class*="css"], .stApp {
        font-family: 'Rubik', sans-serif !important;
        direction: rtl;
        text-align: right;
        background-color: #F8FAFC;
    }
    
    /* תפריט הצד */
    [data-testid="stSidebar"] {
        direction: rtl;
        text-align: right;
        background-color: #FFFFFF;
        border-left: 1px solid #E2E8F0;
    }
    
    /* כרטיסיות עיצוב כלליות */
    .dashboard-card {
        background-color: #FFFFFF;
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 20px;
        border: 1px solid #E2E8F0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
    }
    
    /* כותרות מעוצבות */
    .section-title {
        color: #0F172A;
        font-weight: 700;
        font-size: 20px;
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .section-subtitle {
        color: #64748B;
        font-size: 14px;
        margin-bottom: 18px;
    }
    
    /* כפתורי Tabs מעוצבים */
    .stTabs [data-baseweb="tab-list"] {
        gap: 12px;
        background-color: transparent;
        border-bottom: 2px solid #E2E8F0;
        direction: rtl;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px 10px 0 0;
        padding: 10px 20px;
        font-size: 16px;
        font-weight: 600;
        color: #64748B;
        background-color: transparent;
        border: none;
    }
    .stTabs [aria-selected="true"] {
        color: #2563EB !important;
        border-bottom: 3px solid #2563EB !important;
        background-color: #EFF6FF !important;
    }

    /* קוביות מדדים (Metrics) */
    [data-testid="stMetricValue"] {
        font-family: 'Rubik', sans-serif !important;
        color: #1E293B !important;
        font-size: 28px !important;
        font-weight: 700 !important;
    }
    [data-testid="stMetricLabel"] {
        color: #64748B !important;
        font-size: 14px !important;
    }
    
    /* כפתור הרצה ראשי */
    div.stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #1E40AF 0%, #2563EB 100%);
        color: #FFFFFF;
        border-radius: 12px;
        height: 52px;
        font-size: 18px;
        font-weight: 600;
        width: 100%;
        border: none;
        box-shadow: 0 8px 16px rgba(37, 99, 235, 0.2);
        transition: all 0.2s ease-in-out;
    }
    div.stButton > button[kind="primary"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 20px rgba(37, 99, 235, 0.3);
    }
    </style>
""", unsafe_allow_html=True)

# ייבוא OR-Tools
try:
    from ortools.sat.python import cp_model
except ImportError:
    st.error("⚠️ שגיאה: חסרה ספריית ortools בשרת.")

# ==========================================
# 1. פונקציות עזר לזמנים
# ==========================================
def clock(text):
    if not text or pd.isna(text) or str(text).strip() == "": return None
    try:
        hh, mm = str(text).strip().split(":")
        return dtime(int(hh), int(mm))
    except: return None

def resolve_window(start_str, end_str, horizon_start_dt, horizon_hours=24):
    if not start_str or not end_str or pd.isna(start_str) or pd.isna(end_str) or str(start_str).strip() == "":
        return []
    ta, tb = clock(start_str), clock(end_str)
    if not ta or not tb: return []
    d0 = datetime.combine(horizon_start_dt.date(), ta)
    length = (datetime.combine(horizon_start_dt.date(), tb) - d0) % timedelta(days=1)
    if length == timedelta(0): length = timedelta(days=1)
    
    raw = [(d0 + timedelta(days=k), d0 + timedelta(days=k) + length) for k in range(-1, 3)]
    out = []
    horizon_end = horizon_start_dt + timedelta(hours=horizon_hours)
    for s, e in raw:
        lo, hi = max(s, horizon_start_dt), min(e, horizon_end)
        if lo < hi:
            out.append((int((lo - horizon_start_dt).total_seconds() // 60), 
                        int((hi - horizon_start_dt).total_seconds() // 60)))
    return out

def fmt(minute, horizon_start_dt):
    return (horizon_start_dt + timedelta(minutes=minute)).strftime("%H:%M")

# ==========================================
# דיאגנוסטיקה - למה האלגוריתם נכשל?
# ==========================================
def diagnose_failure(shifts, avail_off, avail_cmd, avail_sol, present_count, min_rest_hours, horizon_start_dt):
    timeline = {}
    for s in shifts:
        for t in range(s['start'], s['end']):
            if t not in timeline:
                timeline[t] = {'off': 0, 'cmd': 0, 'sol': 0, 'tasks': set()}
            timeline[t]['off'] += s['need_off']
            timeline[t]['cmd'] += s['need_cmd']
            timeline[t]['sol'] += s['need_sol']
            timeline[t]['tasks'].add(s['name'])
            
    for t in sorted(timeline.keys()):
        counts = timeline[t]
        time_str = fmt(t, horizon_start_dt)
        tasks_str = ", ".join(counts['tasks'])
        
        # בדיקה היררכית
        if counts['off'] > avail_off:
            return f"בשעה {time_str} חסרים קצינים. המשימות באותה שעה ({tasks_str}) דורשות יחד {counts['off']} קצינים במקביל, אך יש רק {avail_off} זמינים במוצב."
        if counts['off'] + counts['cmd'] > avail_off + avail_cmd:
            return f"בשעה {time_str} חסרה שדרת פיקוד (קצינים/מפקדים). המשימות ({tasks_str}) דורשות יחד {counts['off'] + counts['cmd']} מפקדים וקצינים, אך יש רק {avail_off + avail_cmd} זמינים."
            
        total_need = counts['off'] + counts['cmd'] + counts['sol']
        if total_need > present_count:
            return f"בשעה {time_str} חסר כוח אדם באופן כללי. המשימות באותה שעה ({tasks_str}) דורשות במקביל {total_need} אנשים, אך יש רק {present_count} נוכחים במוצב."
            
    return f"אין מספיק חיילים זמינים כדי לכסות גם את המשימות וגם את זמני המנוחה ({min_rest_hours} שעות). האלגוריתם נתקע כי לוחמים שסיימו משמרת חייבים לנוח, ולא נשארו מספיק לוחמים רעננים שיחליפו אותם. נסה להוריד את שעות המנוחה המינימליות או להוסיף כוח אדם."

# ==========================================
# 2. מנוע השיבוץ המתמטי (OR-Tools)
# ==========================================
def generate_schedule(tasks_df, people_df, min_rest_hours, horizon_start_dt, horizon_hours=24):
    horizon_min = horizon_hours * 60
    min_rest = int(round(min_rest_hours * 60))
    units_per_hour = 10

    shifts = []
    for idx, row in tasks_df.iterrows():
        if not bool(row.get('פעיל?', True)):
            continue
            
        name = row['שם משימה']
        kind = row['סוג']
        shift_hours = float(row['אורך משמרת (שעות)'])
        
        need_off = int(row.get('כמות קצינים', 0))
        need_cmd = int(row.get('כמות מפקדים', 0))
        need_sol = int(row.get('כמות לוחמים', 0))
        total_need = need_off + need_cmd + need_sol
        
        if total_need == 0: continue
        
        counts_as_work = bool(row.get('נחשב עבודה?', True))
        requires_rest = bool(row.get('דורש מנוחה?', True))
        
        windows = [(0, horizon_min)]
        if pd.notna(row['שעת התחלה (HH:MM)']) and pd.notna(row['שעת סיום (HH:MM)']) and str(row['שעת התחלה (HH:MM)']).strip() != "":
            w = resolve_window(row['שעת התחלה (HH:MM)'], row['שעת סיום (HH:MM)'], horizon_start_dt, horizon_hours)
            if w: windows = w
            
        length = int(round(shift_hours * 60))
        for w0, w1 in sorted(windows):
            cur = w0
            while cur < w1:
                end = min(cur + length, w1)
                units = int(round((end - cur) / 60 * units_per_hour)) if counts_as_work else 0
                shifts.append({
                    'idx': len(shifts), 'name': name, 'kind': kind, 
                    'start': cur, 'end': end, 
                    'need_off': need_off, 'need_cmd': need_cmd, 'need_sol': need_sol,
                    'units': units, 'req_rest': requires_rest
                })
                cur = end

    present = []
    hist_u = {}
    blocked = {}
    people_roles = {}
    
    for _, row in people_df.iterrows():
        pname = row['שם']
        if row.get('יצא הביתה?', False) or not str(pname).strip():
            continue
            
        present.append(pname)
        hist_u[pname] = int(round(float(row.get('שעות היסטוריות', 0)) * units_per_hour))
        people_roles[pname] = row.get('תפקיד', 'לוחם')
        
        unavail = resolve_window(row.get('לא זמין מ- (HH:MM)'), row.get('לא זמין עד- (HH:MM)'), horizon_start_dt, horizon_hours)
        p_block = set()
        
        for s in shifts:
            if any(max(0, min(s['end'], b) - max(s['start'], a)) > 0 for a, b in unavail):
                p_block.add(s['idx'])
        blocked[pname] = p_block

    if not present:
        return None, "אין אנשים זמינים במוצב להרכבת השיבוץ."

    avail_off = sum(1 for p in present if people_roles[p] == "קצין")
    avail_cmd = sum(1 for p in present if people_roles[p] == "מפקד")
    avail_sol = sum(1 for p in present if people_roles[p] == "לוחם")
    
    # בדיקת חוסר תפקידים ברמת המשימה הבודדת (בדיקה היררכית)
    for s in shifts:
        if s['need_off'] > avail_off:
            return None, f"חסרים קצינים: במשימה '{s['name']}' נדרשים {s['need_off']} קצינים."
        if s['need_off'] + s['need_cmd'] > avail_off + avail_cmd:
            return None, f"חסרים מפקדים: במשימה '{s['name']}' נדרשים {s['need_cmd']} מפקדים (יחד עם הקצינים חסר פיקוד)."
        if s['need_off'] + s['need_cmd'] + s['need_sol'] > avail_off + avail_cmd + avail_sol:
            return None, f"חסר כוח אדם: במשימה '{s['name']}' נדרשים {s['need_sol']} לוחמים."

    if not shifts:
        return None, "לא סומנו משימות פעילות לשיבוץ."

    total_units = sum(hist_u.values()) + sum((s['need_off'] + s['need_cmd'] + s['need_sol']) * s['units'] for s in shifts)
    target = int(round(total_units / len(present)))
    
    conflicts = []
    for a, b in itertools.combinations(shifts, 2):
        if a['start'] < b['end'] and b['start'] < a['end']:
            conflicts.append((a['idx'], b['idx']))
        else:
            r = min_rest if (a['req_rest'] and b['req_rest']) else 0
            if a['start'] < b['end'] + r and b['start'] < a['end'] + r:
                conflicts.append((a['idx'], b['idx']))

    m = cp_model.CpModel()
    x = {}
    for p in present:
        for s in shifts:
            if s['idx'] in blocked[p]: continue
            # יוצרים משתנה לכולם עבור כל משמרת פתוחה, כדי לאפשר למפקדים לרדת לרמת לוחם
            x[(p, s['idx'])] = m.new_bool_var(f"x_{p}_{s['idx']}")

    for s in shifts:
        staffed_off = sum(x[(p, s['idx'])] for p in present if people_roles[p] == "קצין" and (p, s['idx']) in x)
        staffed_cmd = sum(x[(p, s['idx'])] for p in present if people_roles[p] == "מפקד" and (p, s['idx']) in x)
        staffed_sol = sum(x[(p, s['idx'])] for p in present if people_roles[p] == "לוחם" and (p, s['idx']) in x)
        
        # אילוצים היררכיים (תחליפיות)
        # 1. חייבים מספיק קצינים לתפקיד קצין
        m.add(staffed_off >= s['need_off'])
        # 2. קצינים + מפקדים יכולים למלא תפקידי פיקוד
        m.add(staffed_off + staffed_cmd >= s['need_off'] + s['need_cmd'])
        # 3. סך הכל האנשים (כולל לוחמים) חייב להיות בדיוק המספר הנדרש
        m.add(staffed_off + staffed_cmd + staffed_sol == s['need_off'] + s['need_cmd'] + s['need_sol'])

    for p in present:
        for i, j in conflicts:
            if (p, i) in x and (p, j) in x:
                m.add_at_most_one([x[(p, i)], x[(p, j)]])
    
    sq = []
    for p in present:
        work = sum(s['units'] * x[(p, s['idx'])] for s in shifts if (p, s['idx']) in x)
        room = sum(s['units'] for s in shifts if (p, s['idx']) in x)
        lo, hi = hist_u[p] - target, hist_u[p] + room - target
        dev = m.new_int_var(lo, hi, f"dev_{p}")
        m.add(dev == hist_u[p] + work - target)
        sq_var = m.new_int_var(0, max(lo*lo, hi*hi), f"sq_{p}")
        m.add_multiplication_equality(sq_var, [dev, dev])
        sq.append(sq_var)
        
    m.minimize(sum(sq))
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.solve(m)
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = []
        for s in shifts:
            # בשביל התצוגה - נציג כל אדם תחת הדרגה האמיתית שלו, גם אם מילא מקום של לוחם!
            assigned_off = [p for p in present if people_roles[p] == "קצין" and (p, s['idx']) in x and solver.value(x[(p, s['idx'])])]
            assigned_cmd = [p for p in present if people_roles[p] == "מפקד" and (p, s['idx']) in x and solver.value(x[(p, s['idx'])])]
            assigned_sol = [p for p in present if people_roles[p] == "לוחם" and (p, s['idx']) in x and solver.value(x[(p, s['idx'])])]
            
            team_parts = []
            if assigned_off: team_parts.append(f"🎖️ {', '.join(assigned_off)}")
            if assigned_cmd: team_parts.append(f"⚔️ {', '.join(assigned_cmd)}")
            if assigned_sol: team_parts.append(f"🛡️ {', '.join(assigned_sol)}")
            
            schedule.append({
                "סדר": s['start'],
                "שעת התחלה": fmt(s['start'], horizon_start_dt),
                "שעת סיום": fmt(s['end'], horizon_start_dt),
                "סוג": s['kind'],
                "משימה": s['name'],
                "צוות משובץ": "   |   ".join(team_parts)
            })
        df = pd.DataFrame(schedule)
        df = df.sort_values(by=["סדר"]).drop(columns=["סדר"])
        return df, "השיבוץ הושלם בהצלחה!"
    else:
        diagnostic_msg = diagnose_failure(shifts, avail_off, avail_cmd, avail_sol, len(present), min_rest_hours, horizon_start_dt)
        return None, f"האלגוריתם לא הצליח לבנות שיבוץ תקין.\n\n**סיבת הכישלון המרכזית:** {diagnostic_msg}"

# ==========================================
# 3. ניהול נתונים (State)
# ==========================================
if "people_data" not in st.session_state:
    names_seed = [
        ("אלרואי (קצין)", "קצין"), ("גיא (מפקד)", "מפקד"), ("בן (מפקד)", "מפקד"),
        ("דן (מפקד)", "מפקד"), ("יוסי (מפקד)", "מפקד"), ("עומר", "לוחם"),
        ("תומר", "לוחם"), ("עידו", "לוחם"), ("איתי", "לוחם"), ("רועי", "לוחם"),
        ("נועם", "לוחם"), ("דניאל", "לוחם"), ("ינאי", "לוחם"), ("אלי", "לוחם"),
        ("רמי", "לוחם"), ("רון", "לוחם"), ("ליאור", "לוחם"), ("גל", "לוחם"),
        ("פבל", "לוחם"), ("פטריק", "לוחם"), ("מקס", "לוחם"), ("הראל", "לוחם"),
        ("סמי", "לוחם"), ("אבי", "לוחם"), ("נדב", "לוחם")
    ]
    st.session_state.people_data = pd.DataFrame([
        {"שם": name, "תפקיד": role, "שעות היסטוריות": 0.0, "יצא הביתה?": False, "לא זמין מ- (HH:MM)": "", "לא זמין עד- (HH:MM)": ""}
        for name, role in names_seed
    ])

if "tasks_data" not in st.session_state:
    st.session_state.tasks_data = pd.DataFrame([
        {"פעיל?": True, "שם משימה": "עמדת אדום (יום)", "סוג": "עמדה", "כמות קצינים": 0, "כמות מפקדים": 0, "כמות לוחמים": 1, "אורך משמרת (שעות)": 2.0, "שעת התחלה (HH:MM)": "06:00", "שעת סיום (HH:MM)": "20:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "עמדת אדום (לילה)", "סוג": "עמדה", "כמות קצינים": 0, "כמות מפקדים": 0, "כמות לוחמים": 2, "אורך משמרת (שעות)": 2.0, "שעת התחלה (HH:MM)": "20:00", "שעת סיום (HH:MM)": "06:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "עמדת כחול (24/7)", "סוג": "עמדה", "כמות קצינים": 0, "כמות מפקדים": 0, "כמות לוחמים": 1, "אורך משמרת (שעות)": 2.0, "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "עמדת לבן (24/7)", "סוג": "עמדה", "כמות קצינים": 0, "כמות מפקדים": 0, "כמות לוחמים": 1, "אורך משמרת (שעות)": 2.0, "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "עמדת שג (24/7)", "סוג": "עמדה", "כמות קצינים": 0, "כמות מפקדים": 0, "כמות לוחמים": 1, "אורך משמרת (שעות)": 2.0, "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "סיור 1 - לילה", "סוג": "סיור", "כמות קצינים": 0, "כמות מפקדים": 1, "כמות לוחמים": 6, "אורך משמרת (שעות)": 4.0, "שעת התחלה (HH:MM)": "00:00", "שעת סיום (HH:MM)": "04:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "סיור 2 - לילה", "סוג": "סיור", "כמות קצינים": 0, "כמות מפקדים": 1, "כמות לוחמים": 6, "אורך משמרת (שעות)": 4.0, "שעת התחלה (HH:MM)": "04:00", "שעת סיום (HH:MM)": "08:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "סיור 3 - יום", "סוג": "סיור", "כמות קצינים": 0, "כמות מפקדים": 1, "כמות לוחמים": 6, "אורך משמרת (שעות)": 4.0, "שעת התחלה (HH:MM)": "12:00", "שעת סיום (HH:MM)": "16:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "סיור 4 - יום", "סוג": "סיור", "כמות קצינים": 0, "כמות מפקדים": 1, "כמות לוחמים": 6, "אורך משמרת (שעות)": 4.0, "שעת התחלה (HH:MM)": "16:00", "שעת סיום (HH:MM)": "20:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
        {"פעיל?": True, "שם משימה": "כרמל א'", "סוג": "כוננות", "כמות קצינים": 0, "כמות מפקדים": 1, "כמות לוחמים": 5, "אורך משמרת (שעות)": 4.0, "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": False, "דורש מנוחה?": False},
        {"פעיל?": True, "שם משימה": "כרמל ב'", "סוג": "כוננות", "כמות קצינים": 0, "כמות מפקדים": 1, "כמות לוחמים": 5, "אורך משמרת (שעות)": 4.0, "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": False, "דורש מנוחה?": False},
        {"פעיל?": False, "שם משימה": "תורן מטבח", "סוג": "מטבח", "כמות קצינים": 0, "כמות מפקדים": 0, "כמות לוחמים": 1, "אורך משמרת (שעות)": 24.0, "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": True, "דורש מנוחה?": True},
    ])

# ==========================================
# 4. ממשק משתמש פרימיום
# ==========================================

# כותרת ראשית (Hero Banner)
st.markdown("""
<div style="background: linear-gradient(135deg, #0F172A 0%, #1E3A8A 100%); padding: 30px; border-radius: 20px; color: white; margin-bottom: 25px; box-shadow: 0 10px 25px rgba(15,23,42,0.15);">
    <h1 style="margin: 0; font-size: 32px; font-weight: 700;">🛡️ מערכת שיבוץ וניהול משמרות</h1>
    <p style="margin-top: 8px; margin-bottom: 0; font-size: 16px; opacity: 0.85;">חלוקת עומסים הוגנת ומדויקת על בסיס מנוע חישוב מתמטי</p>
</div>
""", unsafe_allow_html=True)

# סיידבר מעוצב
with st.sidebar:
    st.markdown("### ⚙️ הגדרות לו\"ז")
    start_date = st.date_input("📅 תאריך תחילת הלוז:", value=datetime.today())
    start_time = st.time_input("⏰ שעת תחילת הלוז:", value=dtime(6, 0))
    horizon_start_dt = datetime.combine(start_date, start_time)
    
    st.markdown("---")
    min_rest_ui = st.slider("⏳ שעות מנוחה מינימליות:", min_value=1.0, max_value=8.0, value=2.0, step=0.5)
    
    st.markdown("---")
    st.markdown(f"""
    <div style="background-color: #F1F5F9; padding: 14px; border-radius: 10px; font-size: 13px; color: #475569;">
        📌 <b>טווח לו"ז מחושב:</b><br>
        החל מ-{horizon_start_dt.strftime('%d/%m בשעה %H:%M')}<br>
        למשך 24 שעות קדימה.
    </div>
    """, unsafe_allow_html=True)

# לשוניות הניווט (Tabs)
tab1, tab2, tab3 = st.tabs(["👥 1. ניהול כוח אדם", "📋 2. הגדרת משימות", "🚀 3. הרצה ותוצאות"])

# --- דף 1: כוח אדם ---
with tab1:
    st.markdown("""
    <div class="dashboard-card">
        <div class="section-title">👥 ניהול הסד"כ ואילוצי הנוכחות</div>
        <div class="section-subtitle">הגדר את שמות החיילים, תפקידיהם (לוחם / מפקד / קצין) ושעות אי-זמינות.</div>
    """, unsafe_allow_html=True)
    
    # חישוב נתונים חיים לכרטיסיות
    df_curr = st.session_state.people_data
    total_count = len(df_curr)
    home_count = sum(df_curr['יצא הביתה?'])
    active_count = total_count - home_count
    officers = sum((df_curr['תפקיד'] == 'קצין') & (~df_curr['יצא הביתה?']))
    commanders = sum((df_curr['תפקיד'] == 'מפקד') & (~df_curr['יצא הביתה?']))
    soldiers = sum((df_curr['תפקיד'] == 'לוחם') & (~df_curr['יצא הביתה?']))
    
    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    col_m1.metric("סה\"כ בסד\"כ", total_count)
    col_m2.metric("נוכחים במוצב", active_count)
    col_m3.metric("קצינים זמינים", officers)
    col_m4.metric("מפקדים זמינים", commanders)
    col_m5.metric("לוחמים זמינים", soldiers)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # שליטה מהירה בכמות השורות
    col_ctrl1, col_ctrl2 = st.columns([1, 4])
    with col_ctrl1:
        desired_count = st.number_input("שינוי גודל הסד\"כ:", min_value=1, max_value=100, value=total_count)
    with col_ctrl2:
        st.write("")
        st.write("")
        if st.button("🔄 התאם גודל רשימה"):
            if desired_count > total_count:
                new_rows = [{"שם": f"חייל {i+1}", "תפקיד": "לוחם", "שעות היסטוריות": 0.0, "יצא הביתה?": False, "לא זמין מ- (HH:MM)": "", "לא זמין עד- (HH:MM)": ""} for i in range(total_count, desired_count)]
                st.session_state.people_data = pd.concat([st.session_state.people_data, pd.DataFrame(new_rows)], ignore_index=True)
            elif desired_count < total_count:
                st.session_state.people_data = st.session_state.people_data.head(desired_count)
            st.rerun()

    # טבלת העריכה
    people_df = st.data_editor(
        st.session_state.people_data, 
        num_rows="dynamic", 
        use_container_width=True, 
        height=480,
        column_config={
            "תפקיד": st.column_config.SelectboxColumn("תפקיד", options=["לוחם", "מפקד", "קצין"], required=True),
            "יצא הביתה?": st.column_config.CheckboxColumn("בבית?"),
            "שעות היסטוריות": st.column_config.NumberColumn("שעות קודמות", format="%.1f שעות"),
        }
    )
    st.session_state.people_data = people_df
    st.markdown("</div>", unsafe_allow_html=True)

# --- דף 2: משימות ---
with tab2:
    st.markdown("""
    <div class="dashboard-card">
        <div class="section-title">📋 הגדרת עמדות, סיורים ומשימות</div>
        <div class="section-subtitle">אם אינך צריך משימה מסוימת היום (כמו מטבח), פשוט הורד ממנה את ה-V בעמודת "פעיל?" והמערכת תתעלם ממנה.</div>
    """, unsafe_allow_html=True)
    
    tasks_df = st.data_editor(
        st.session_state.tasks_data, 
        num_rows="dynamic", 
        use_container_width=True,
        height=520,
        column_config={
            "פעיל?": st.column_config.CheckboxColumn("פעיל?", default=True),
            "סוג": st.column_config.SelectboxColumn("סוג משימה", options=["עמדה", "סיור", "כוננות", "מטבח", "חפ\"ק"], required=True),
            "כמות קצינים": st.column_config.NumberColumn("🎖️ קצינים", min_value=0, max_value=5, default=0),
            "כמות מפקדים": st.column_config.NumberColumn("⚔️ מפקדים", min_value=0, max_value=10, default=0),
            "כמות לוחמים": st.column_config.NumberColumn("🛡️ לוחמים", min_value=0, max_value=20, default=1),
            "אורך משמרת (שעות)": st.column_config.NumberColumn("אורך (שעות)", min_value=0.5, max_value=24.0, step=0.5),
            "נחשב עבודה?": st.column_config.CheckboxColumn("נספר בשעות?"),
            "דורש מנוחה?": st.column_config.CheckboxColumn("מחייב מנוחה?")
        }
    )
    st.session_state.tasks_data = tasks_df
    st.markdown("</div>", unsafe_allow_html=True)

# --- דף 3: הרצה ותוצאות ---
with tab3:
    st.markdown("""
    <div class="dashboard-card">
        <div class="section-title">🚀 הפקת סידור עבודה אוטומטי</div>
        <div class="section-subtitle">המערכת תפתור את כל האילוצים בצורה מתמטית ותייצר שיבוץ הוגן ומדויק רק למשימות המסומנות כ'פעילות'.</div>
    """, unsafe_allow_html=True)
    
    if st.button("⚡ הפעל אלגוריתם שיבוץ", type="primary"):
        with st.spinner('המנוע המתמטי מחשב את חלוקת הנטל הטובה ביותר...'):
            result_df, msg = generate_schedule(st.session_state.tasks_data, st.session_state.people_data, min_rest_ui, horizon_start_dt)
            
            if result_df is not None:
                st.session_state.latest_result = result_df
                st.session_state.latest_msg = msg
            else:
                st.session_state.latest_result = None
                st.session_state.latest_msg = msg

    if "latest_result" in st.session_state and st.session_state.latest_result is not None:
        res = st.session_state.latest_result
        st.success(f"✔️ {st.session_state.latest_msg}")
        
        # סינון מהיר מעל הטבלה
        search_filter = st.text_input("🔍 חיפוש / סינון לפי שם חייל או עמדה:", "")
        if search_filter:
            filtered_df = res[res.apply(lambda r: r.astype(str).str.contains(search_filter).any(), axis=1)]
        else:
            filtered_df = res
            
        st.dataframe(
            filtered_df, 
            use_container_width=True, 
            hide_index=True, 
            height=580
        )
        
        # כפתור הורדה מעוצב
        csv = res.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 הורד סידור עבודה לקובץ Excel / CSV",
            data=csv,
            file_name=f"guard_roster_{start_date.strftime('%Y_%m_%d')}.csv",
            mime="text/csv",
        )
    elif "latest_msg" in st.session_state and st.session_state.latest_result is None:
        # הודעת שגיאה מסודרת עם פירוט סיבת הכישלון מהדיאגנוסטיקה
        st.error("האלגוריתם לא מצא פתרון תקין")
        st.warning(st.session_state.latest_msg)
        
    st.markdown("</div>", unsafe_allow_html=True)
