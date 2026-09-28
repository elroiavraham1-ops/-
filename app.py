import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, time as dtime
import itertools

# ננסה לייבא את מנוע החישוב
try:
    from ortools.sat.python import cp_model
except ImportError:
    st.error("שגיאה: חסרה ספריית ortools. יש לוודא שהיא מותקנת.")

# ==========================================
# 1. הגדרות ופונקציות בסיס למנוע החישוב
# ==========================================
HORIZON_START = datetime(2026, 9, 28, 6, 0)
HORIZON_HOURS = 24

def clock(text):
    if not text or pd.isna(text) or text.strip() == "":
        return None
    try:
        hh, mm = str(text).strip().split(":")
        return dtime(int(hh), int(mm))
    except:
        return None

def resolve_window(start_str, end_str):
    if not start_str or not end_str or pd.isna(start_str) or pd.isna(end_str):
        return []
    ta = clock(start_str)
    tb = clock(end_str)
    if not ta or not tb:
        return []
    
    d0 = datetime.combine(HORIZON_START.date(), ta)
    length = (datetime.combine(HORIZON_START.date(), tb) - d0) % timedelta(days=1)
    if length == timedelta(0):
        length = timedelta(days=1)
    
    raw = [(d0 + timedelta(days=k), d0 + timedelta(days=k) + length) for k in range(-1, 3)]
    out = []
    horizon_end = HORIZON_START + timedelta(hours=HORIZON_HOURS)
    for s, e in raw:
        lo, hi = max(s, HORIZON_START), min(e, horizon_end)
        if lo < hi:
            out.append((int((lo - HORIZON_START).total_seconds() // 60), 
                        int((hi - HORIZON_START).total_seconds() // 60)))
    return out

def fmt(minute):
    return (HORIZON_START + timedelta(minutes=minute)).strftime("%H:%M")

# ==========================================
# 2. מנוע השיבוץ הראשי (OR-Tools)
# ==========================================
def generate_schedule(tasks_df, people_df, min_rest_hours):
    horizon_min = HORIZON_HOURS * 60
    min_rest = int(round(min_rest_hours * 60))
    units_per_hour = 10

    # עיבוד משימות מהטבלה
    shifts = []
    for idx, row in tasks_df.iterrows():
        name = row['שם משימה']
        kind = row['סוג']
        people_req = int(row['כמות אנשים'])
        shift_hours = float(row['אורך משמרת (שעות)'])
        req_role = row['תפקיד נדרש']
        
        counts_as_work = bool(row.get('נחשב עבודה?', True))
        requires_rest = bool(row.get('דורש מנוחה?', True))
        
        roles = [req_role] if pd.notna(req_role) and req_role.strip() != "" else []
        
        # זיהוי חלונות זמן
        windows = [(0, horizon_min)]
        if pd.notna(row['שעת התחלה (HH:MM)']) and pd.notna(row['שעת סיום (HH:MM)']):
            w = resolve_window(row['שעת התחלה (HH:MM)'], row['שעת סיום (HH:MM)'])
            if w: windows = w
            
        length = int(round(shift_hours * 60))
        for w0, w1 in sorted(windows):
            cur = w0
            while cur < w1:
                end = min(cur + length, w1)
                # משימה שלא נחשבת עבודה (כמו כרמל) תקבל 0 יחידות עומס
                units = int(round((end - cur) / 60 * units_per_hour)) if counts_as_work else 0
                shifts.append({
                    'idx': len(shifts), 'name': name, 'kind': kind, 
                    'start': cur, 'end': end, 'need': people_req, 
                    'units': units, 'roles': roles, 'req_rest': requires_rest
                })
                cur = end

    # עיבוד אנשים מהטבלה
    present = []
    hist_u = {}
    blocked = {}
    people_roles = {}
    
    for _, row in people_df.iterrows():
        pname = row['שם']
        if row.get('יצא הביתה?', False):
            continue
            
        present.append(pname)
        hist_u[pname] = int(round(float(row.get('שעות היסטוריות', 0)) * units_per_hour))
        
        is_cmd = row.get('מפקד?', False)
        people_roles[pname] = ["מפקד"] if is_cmd else []
        
        # אילוצי זמנים לאדם
        unavail = resolve_window(row.get('לא זמין מ- (HH:MM)'), row.get('לא זמין עד- (HH:MM)'))
        p_block = set()
        
        for s in shifts:
            # חפיפה של שעות חסימה
            if any(max(0, min(s['end'], b) - max(s['start'], a)) > 0 for a, b in unavail):
                p_block.add(s['idx'])
            # דרישת תפקיד
            if s['roles'] and not any(r in people_roles[pname] for r in s['roles']):
                p_block.add(s['idx'])
                
        blocked[pname] = p_block

    if not present:
        return None, "אין מספיק לוחמים זמינים לשיבוץ."

    total_units = sum(hist_u.values()) + sum(s['need'] * s['units'] for s in shifts)
    target = int(round(total_units / len(present)))
    
    # בניית ההתנגשויות (כפילויות ומנוחה)
    conflicts = []
    for a, b in itertools.combinations(shifts, 2):
        # תמיד אסור להיות בשני מקומות במקביל (חפיפה פיזית)
        if a['start'] < b['end'] and b['start'] < a['end']:
            conflicts.append((a['idx'], b['idx']))
        else:
            # בדיקת מנוחה - רק אם שתי המשימות דורשות מנוחה! אם אחת היא מנוחה (כרמל), מותר לשבץ ברצף
            r = min_rest if (a['req_rest'] and b['req_rest']) else 0
            if a['start'] < b['end'] + r and b['start'] < a['end'] + r:
                conflicts.append((a['idx'], b['idx']))

    # מודל ה-CP
    m = cp_model.CpModel()
    x = {}
    for p in present:
        for s in shifts:
            if s['idx'] not in blocked[p]:
                x[(p, s['idx'])] = m.new_bool_var(f"x_{p}_{s['idx']}")

    # כמות אנשים מדויקת בעמדה
    for s in shifts:
        staffed = sum(x[(p, s['idx'])] for p in present if (p, s['idx']) in x)
        m.add(staffed == s['need'])

    # אילוץ מנוחה וכפילויות
    for p in present:
        for i, j in conflicts:
            if (p, i) in x and (p, j) in x:
                m.add_at_most_one([x[(p, i)], x[(p, j)]])
    
    # מטרת הוגנות
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
            assigned = [p for p in present if (p, s['idx']) in x and solver.value(x[(p, s['idx'])])]
            schedule.append({
                "שעת התחלה": fmt(s['start']),
                "שעת סיום": fmt(s['end']),
                "סוג משימה": s['kind'],
                "שם משימה": s['name'],
                "צוות מוצב": ", ".join(assigned)
            })
        return pd.DataFrame(schedule), "השיבוץ הושלם בהצלחה!"
    else:
        return None, "לא נמצא פתרון. נסה להוריד שעות מנוחה או להוסיף לוחמים."

# ==========================================
# 3. ממשק המשתמש (UI) - Streamlit
# ==========================================
st.set_page_config(page_title="מערכת שיבוץ מתקדמת", layout="wide", page_icon="🛡️")
st.title("🛡️ מערכת שיבוץ וניהול משמרות אוטומטית")
st.markdown("ערוך את נתוני המשימות והצוות ישירות בטבלאות שלמטה. המערכת תשקלל את כל האילוצים ותפיק סידור עבודה הוגן.")

st.sidebar.header("הגדרות כלליות")
min_rest_ui = st.sidebar.number_input("שעות מנוחה מינימליות בין משמרות:", min_value=0.0, max_value=8.0, value=2.0, step=0.5)

# נתוני ברירת מחדל למשימות עם העמודות החדשות
default_tasks = pd.DataFrame([
    {"שם משימה": "עמדת אדום (לילה)", "סוג": "עמדה", "כמות אנשים": 2, "אורך משמרת (שעות)": 2.0, "תפקיד נדרש": "", "שעת התחלה (HH:MM)": "20:00", "שעת סיום (HH:MM)": "06:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
    {"שם משימה": "עמדת אדום (יום)", "סוג": "עמדה", "כמות אנשים": 1, "אורך משמרת (שעות)": 2.0, "תפקיד נדרש": "", "שעת התחלה (HH:MM)": "06:00", "שעת סיום (HH:MM)": "20:00", "נחשב עבודה?": True, "דורש מנוחה?": True},
    {"שם משימה": "כרמל א' - מפקד", "סוג": "כוננות", "כמות אנשים": 1, "אורך משמרת (שעות)": 4.0, "תפקיד נדרש": "מפקד", "שעת התחלה (HH:MM)": "12:00", "שעת סיום (HH:MM)": "20:00", "נחשב עבודה?": False, "דורש מנוחה?": False},
    {"שם משימה": "כרמל א' - לוחמים", "סוג": "כוננות", "כמות אנשים": 5, "אורך משמרת (שעות)": 4.0, "תפקיד נדרש": "", "שעת התחלה (HH:MM)": "12:00", "שעת סיום (HH:MM)": "20:00", "נחשב עבודה?": False, "דורש מנוחה?": False},
    {"שם משימה": "כרמל ב' - מפקד", "סוג": "כוננות", "כמות אנשים": 1, "אורך משמרת (שעות)": 4.0, "תפקיד נדרש": "מפקד", "שעת התחלה (HH:MM)": "22:00", "שעת סיום (HH:MM)": "06:00", "נחשב עבודה?": False, "דורש מנוחה?": False},
    {"שם משימה": "כרמל ב' - לוחמים", "סוג": "כוננות", "כמות אנשים": 5, "אורך משמרת (שעות)": 4.0, "תפקיד נדרש": "", "שעת התחלה (HH:MM)": "22:00", "שעת סיום (HH:MM)": "06:00", "נחשב עבודה?": False, "דורש מנוחה?": False},
    {"שם משימה": "תורן מטבח", "סוג": "מטבח", "כמות אנשים": 1, "אורך משמרת (שעות)": 24.0, "תפקיד נדרש": "", "שעת התחלה (HH:MM)": "", "שעת סיום (HH:MM)": "", "נחשב עבודה?": True, "דורש מנוחה?": True},
])

default_people = pd.DataFrame([
    {"שם": "אלרואי", "שעות היסטוריות": 0.0, "מפקד?": True, "יצא הביתה?": False, "לא זמין מ- (HH:MM)": "", "לא זמין עד- (HH:MM)": ""},
    {"שם": "גיא", "שעות היסטוריות": 12.5, "מפקד?": True, "יצא הביתה?": False, "לא זמין מ- (HH:MM)": "", "לא זמין עד- (HH:MM)": ""},
    {"שם": "בן", "שעות היסטוריות": 10.0, "מפקד?": False, "יצא הביתה?": False, "לא זמין מ- (HH:MM)": "00:00", "לא זמין עד- (HH:MM)": "06:00"},
    {"שם": "דן", "שעות היסטוריות": 8.0, "מפקד?": False, "יצא הביתה?": True, "לא זמין מ- (HH:MM)": "", "לא זמין עד- (HH:MM)": ""},
])

st.subheader("📋 הגדרת משימות ועמדות")
tasks_df = st.data_editor(
    default_tasks, 
    num_rows="dynamic", 
    use_container_width=True,
    column_config={
        "סוג": st.column_config.SelectboxColumn("סוג משימה", options=["עמדה", "כוננות", "מטבח"]),
        "תפקיד נדרש": st.column_config.SelectboxColumn("תפקיד נדרש", options=["", "מפקד"]),
        "נחשב עבודה?": st.column_config.CheckboxColumn("נחשב עבודה?"),
        "דורש מנוחה?": st.column_config.CheckboxColumn("דורש מנוחה?")
    }
)

st.subheader("👥 ניהול כוח אדם ואילוצים")
people_df = st.data_editor(default_people, num_rows="dynamic", use_container_width=True)

if st.button("🚀 הפעל שיבוץ אוטומטי", type="primary"):
    with st.spinner('מחשב את חלוקת הנטל ההוגנת ביותר...'):
        result_df, msg = generate_schedule(tasks_df, people_df, min_rest_ui)
        
        if result_df is not None:
            st.success(msg)
            st.subheader("📊 סידור העבודה המלא")
            st.dataframe(result_df, use_container_width=True, hide_index=True)
        else:
            st.error(msg)
