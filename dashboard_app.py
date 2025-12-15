import os
import pandas as pd
import numpy as np
import streamlit as st
import folium
from folium.plugins import MarkerCluster
from streamlit.components.v1 import html as st_html

# =========================
# Config
# =========================
st.set_page_config(page_title="Auditoría SNIP-SEGEPLAN", layout="wide")

OUT_DIR = os.getenv("SNIP_OUT_DIR", "data/output")
SNIP_PATH = os.path.join(OUT_DIR, "snip_2025Q4_snip.csv")
MUN_PATH  = os.path.join(OUT_DIR, "snip_2025Q4_municipios.csv")
COD_PATH  = os.path.join(OUT_DIR, "snip_2025Q4_codede.csv")
ENT_PATH  = os.path.join(OUT_DIR, "snip_2025Q4_entidades.csv")
BUD_PATH  = os.path.join(OUT_DIR, "snip_2025Q4_budget_inconsistencias.csv")

ESRI_WORLD_IMAGERY = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

# Para display
MONEY_COLS_SNIP = ["presupuesto_actual_vigente", "ejecutado_total_calc", "no_ejecutado_vigente"]
RATIO_COLS_SNIP = ["ratio_ejec_real"]
MONEY_COLS_AGG  = ["presupuesto_vigente", "ejecutado_total", "no_ejecutado", "presupuesto_inconsistencia_estado_momento"]
RATIO_COLS_AGG  = ["ratio_ejec_agg"]

# =========================
# Load
# =========================
@st.cache_data
def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)

snip = load_csv(SNIP_PATH)
muni = load_csv(MUN_PATH)
cod  = load_csv(COD_PATH)
ent  = load_csv(ENT_PATH)
bud  = load_csv(BUD_PATH)

# Ensure numeric types for key columns (filters/sorting)
for c in ["presupuesto_actual_vigente","ejecutado_total_calc","no_ejecutado_vigente","ratio_ejec_real",
          "riesgo_fiscal","flag_inconsistencia_estado_momento","months_since_last_exec","zero_run_max","slope_exec_12m","reversiones_fin"]:
    if c in snip.columns:
        snip[c] = pd.to_numeric(snip[c], errors="coerce")

for df in [muni, cod, ent]:
    for c in ["presupuesto_vigente","ejecutado_total","no_ejecutado","ratio_ejec_agg",
              "score_concentracion_baja_ejec","n_inconsistencia_estado_momento","presupuesto_inconsistencia_estado_momento"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

# =========================
# Formatting helpers
# =========================
def fmt_int(x):
    if pd.isna(x): return "—"
    try: return f"{int(round(float(x))):,}"
    except: return "—"

def fmt_float(x, decimals=2):
    if pd.isna(x): return "—"
    try: return f"{float(x):,.{decimals}f}"
    except: return "—"

def fmt_money(x, decimals=2):
    if pd.isna(x): return "—"
    try: return f"Q {float(x):,.{decimals}f}"
    except: return "—"

def fmt_pct(x, decimals=1):
    if pd.isna(x): return "—"
    try: return f"{100*float(x):,.{decimals}f}%"
    except: return "—"

def format_table(df: pd.DataFrame, money_cols=None, ratio_cols=None) -> pd.DataFrame:
    money_cols = set(money_cols or [])
    ratio_cols = set(ratio_cols or [])
    out = df.copy()
    for col in out.columns:
        if col == "snip":
            continue
        if col in money_cols:
            out[col] = out[col].apply(fmt_money)
        elif col in ratio_cols:
            out[col] = out[col].apply(fmt_pct)
        else:
            if pd.api.types.is_numeric_dtype(out[col]):
                if pd.api.types.is_float_dtype(out[col]):
                    out[col] = out[col].apply(fmt_float)
                else:
                    out[col] = out[col].apply(fmt_int)
    return out


SNIP_URL_TMPL = (
    "https://sistemas.segeplan.gob.gt/guest/"
    "SNPPKG$PL_PROYECTOS.INFORMACION?prmIdSnip={snip}"
)

def snip_link(snip):
    if pd.isna(snip):
        return "—"
    try:
        snip = int(snip)
        return f'<a href="{SNIP_URL_TMPL.format(snip=snip)}" target="_blank">{snip}</a>'
    except:
        return "—"
    
def add_snip_link_column(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "snip" in out.columns:
        out["snip"] = out["snip"].apply(snip_link)
        # out = out.drop(columns=["snip"])
    return out



# =========================
# Selection state (map filter)
# =========================
# =========================
# Selection state (map filter)
# =========================
def ensure_state():
    if "map_filter" not in st.session_state:
        st.session_state["map_filter"] = {"kind": "ALL"}  # default: all points

def reset_map():
    prev = st.session_state.get("map_filter", {"kind": "ALL"})
    new = {"kind": "ALL"}
    st.session_state["map_filter"] = new
    # rerun solo si cambió (evita loops)
    if prev != new:
        st.rerun()

def set_map_filter(kind: str, payload: dict):
    prev = st.session_state.get("map_filter", {"kind": "ALL"})
    new = {"kind": kind, **payload}
    st.session_state["map_filter"] = new
    # rerun solo si cambió (evita loops)
    if prev != new:
        st.rerun()

ensure_state()


# =========================
# Map filtering logic
# =========================
def filtered_points(df_snip: pd.DataFrame, mf: dict) -> pd.DataFrame:
    d = df_snip.copy()

    # only points with coords
    if not {"latitud","longitud"}.issubset(d.columns):
        return d.iloc[0:0]

    d["latitud"] = pd.to_numeric(d["latitud"], errors="coerce")
    d["longitud"] = pd.to_numeric(d["longitud"], errors="coerce")
    d = d.dropna(subset=["latitud","longitud"])

    kind = mf.get("kind", "ALL")

    if kind == "ALL":
        return d

    if kind == "SNIP":
        return d[d["snip"] == mf.get("snip")]

    if kind == "MUNICIPIO":
        return d[(d["departamento"] == mf.get("departamento")) & (d["municipio"] == mf.get("municipio"))]

    if kind == "CODEDE":
        return d[d["departamento"] == mf.get("departamento")]

    if kind == "ENTIDAD":
        return d[d["entidad_ejecutora"] == mf.get("entidad_ejecutora")]

    if kind == "ESTADO":
        return d[d["estado_auditoria"] == mf.get("estado_auditoria")]

    if kind == "FLAG_INCONSISTENCIA":
        return d[d["flag_inconsistencia_estado_momento"].fillna(0).astype(int) == 1]

    return d

def render_map(df_points: pd.DataFrame, title: str):
    if df_points.empty:
        st.info("No hay puntos para el filtro actual.")
        return

    center_lat = float(df_points["latitud"].median())
    center_lon = float(df_points["longitud"].median())

    m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles=ESRI_WORLD_IMAGERY,
        attr="Esri — World Imagery",
        name="Satélite",
        overlay=False,
        control=True
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OSM", overlay=False, control=True).add_to(m)

    cluster = MarkerCluster(name="Proyectos").add_to(m)

    # performance cap
    max_markers = 5000
    pts = df_points.head(max_markers)
    

    for _, r in pts.iterrows():
        snip_val = r.get("snip", "")
        snip_href = SNIP_URL_TMPL.format(snip=snip_val)
        popup_html = f"""
        <div style="font-size:12px; max-width:360px;">
          <b>SNIP:</b>
          <a href="{snip_href}" target="_blank">{snip_val}</a><br/>
          <b>Proyecto:</b> {str(r.get("nombre_de_proyecto",""))[:120]}<br/>
          <b>Ubicación:</b> {r.get("departamento","")} / {r.get("municipio","")}<br/>
          <b>Estado auditoría:</b> {r.get("estado_auditoria","")}<br/>
          <b>Vigente:</b> {fmt_money(r.get("presupuesto_actual_vigente", np.nan))}<br/>
          <b>Ejecución:</b> {fmt_pct(r.get("ratio_ejec_real", np.nan))}<br/>
          <b>Inconsistencia estado/momento:</b> {"Sí" if int(r.get("flag_inconsistencia_estado_momento",0) or 0)==1 else "No"}
        </div>
        """
        folium.CircleMarker(
            location=[float(r["latitud"]), float(r["longitud"])],
            radius=4,
            weight=1,
            fill=True,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=450),
        ).add_to(cluster)

    folium.LayerControl(collapsed=True).add_to(m)

    st.subheader(title)
    st.caption(f"Puntos: {len(df_points):,} (mostrando hasta {min(len(df_points), max_markers):,})")
    st_html(m._repr_html_(), height=520)

# =========================
# Header + KPI + Map
# =========================
st.title("Auditoría SNIP / SEGEPLAN")

cA, cB = st.columns([1, 6])
with cA:
    if st.button("Reset (Todos los puntos)"):
        reset_map()
with cB:
    mf = st.session_state["map_filter"]
    st.write(f"**Filtro actual del mapa:** `{mf}`")

# KPIs globales
total_snip = int(snip["snip"].nunique())
tot_pres = float(snip["presupuesto_actual_vigente"].sum(skipna=True))
tot_ejec = float(snip["ejecutado_total_calc"].sum(skipna=True))
ratio_global = (tot_ejec / tot_pres) if tot_pres else np.nan

k1, k2, k3, k4 = st.columns(4)
k1.metric("SNIP únicos", f"{total_snip:,}")
k2.metric("Presupuesto vigente (suma)", fmt_money(tot_pres))
k3.metric("Ejecutado estimado (suma)", fmt_money(tot_ejec))
k4.metric("Ejecución global (ejec/vig)", fmt_pct(ratio_global))

# Mapa arriba
pts = filtered_points(snip, st.session_state["map_filter"])
render_map(pts, "Mapa de proyectos (según selección en tablas)")

st.divider()

# =========================
# Tabs + clickable tables
# =========================
tab_over, tab_snip, tab_mun, tab_cod, tab_ent, tab_cal = st.tabs(
    ["Panorama", "Explorador SNIP", "Municipios", "CODEDE", "Entidades", "Calidad/Consistencia"]
)

# -------- Panorama (tablas que también controlan el mapa) --------
with tab_over:
    st.subheader("Estado auditoría: conteos y dinero (clic para filtrar mapa por estado)")
    by_estado = snip.groupby("estado_auditoria", dropna=False).agg(
        proyectos=("snip","nunique"),
        presupuesto=("presupuesto_actual_vigente","sum"),
        ejecutado=("ejecutado_total_calc","sum"),
        no_ejecutado=("no_ejecutado_vigente","sum"),
        inconsistencias=("flag_inconsistencia_estado_momento","sum"),
    ).reset_index()
    by_estado["ratio_ejec"] = by_estado["ejecutado"] / by_estado["presupuesto"]

    # selección
    disp = format_table(by_estado, money_cols=["presupuesto","ejecutado","no_ejecutado"], ratio_cols=["ratio_ejec"])
    sel = st.dataframe(
        disp, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row"
    )
    if sel and sel.selection and sel.selection.rows:
        r = sel.selection.rows[0]
        estado_sel = by_estado.iloc[r]["estado_auditoria"]
        set_map_filter("ESTADO", {"estado_auditoria": estado_sel})

    st.subheader("Top 30 SNIP por riesgo fiscal (clic para filtrar mapa a ese SNIP)")
    top = snip.sort_values(["riesgo_fiscal","no_ejecutado_vigente"], ascending=False).head(30)
    cols = [
        "snip","nombre_de_proyecto","departamento","municipio","entidad_ejecutora",
        "estado_auditoria","momento_presupuestario","flag_inconsistencia_estado_momento",
        "presupuesto_actual_vigente","ejecutado_total_calc","ratio_ejec_real","no_ejecutado_vigente",
    ]
    cols = [c for c in cols if c in top.columns]
    disp2 = format_table(top[cols], money_cols=MONEY_COLS_SNIP, ratio_cols=RATIO_COLS_SNIP)

    sel2 = st.dataframe(
        disp2, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row"
    )
    if sel2 and sel2.selection and sel2.selection.rows:
        r = sel2.selection.rows[0]
        snip_sel = int(top.iloc[r]["snip"])
        set_map_filter("SNIP", {"snip": snip_sel})

# -------- Explorador SNIP (tabla completa, clic SNIP) --------
with tab_snip:
    st.subheader("Explorador SNIP (clic en fila para filtrar mapa)")
    st.caption("Tip: ordena por 'riesgo_fiscal' o 'no_ejecutado_vigente' si quieres priorizar.")

    df = snip.copy().sort_values(["riesgo_fiscal","no_ejecutado_vigente"], ascending=False)
    cols = [
        "snip","nombre_de_proyecto","departamento","municipio","entidad_ejecutora",
        "estado_auditoria","estado_reportado_ult","momento_presupuestario",
        "presupuesto_actual_vigente","ejecutado_total_calc","ratio_ejec_real","no_ejecutado_vigente",
        "months_since_last_exec","zero_run_max","slope_exec_12m","reversiones_fin",
        "flag_inconsistencia_estado_momento"
    ]
    cols = [c for c in cols if c in df.columns]
    disp = format_table(df[cols].head(2000), money_cols=MONEY_COLS_SNIP, ratio_cols=RATIO_COLS_SNIP)

    sel = st.dataframe(
        disp, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row"
    )
    if sel and sel.selection and sel.selection.rows:
        r = sel.selection.rows[0]
        snip_sel = int(df.iloc[r]["snip"])
        set_map_filter("SNIP", {"snip": snip_sel})

# -------- Municipios (clic -> municipio) --------
with tab_mun:
    st.subheader("Municipios: concentración de baja ejecución (clic en fila para ver puntos del municipio)")
    topm = muni.sort_values("score_concentracion_baja_ejec", ascending=False)

    # Asegurar columnas de llave
    key_cols = [c for c in ["departamento","municipio"] if c in topm.columns]
    disp = format_table(topm, money_cols=MONEY_COLS_AGG, ratio_cols=RATIO_COLS_AGG)

    sel = st.dataframe(
        disp, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row"
    )
    if sel and sel.selection and sel.selection.rows:
        r = sel.selection.rows[0]
        dep = topm.iloc[r]["departamento"]
        mu  = topm.iloc[r]["municipio"]
        set_map_filter("MUNICIPIO", {"departamento": dep, "municipio": mu})

# -------- CODEDE (clic -> depto) --------
with tab_cod:
    st.subheader("CODEDE (Departamento): concentración de baja ejecución (clic en fila para ver puntos del departamento)")
    topc = cod.sort_values("score_concentracion_baja_ejec", ascending=False)

    disp = format_table(topc, money_cols=MONEY_COLS_AGG, ratio_cols=RATIO_COLS_AGG)
    sel = st.dataframe(
        disp, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row"
    )
    if sel and sel.selection and sel.selection.rows:
        r = sel.selection.rows[0]
        dep = topc.iloc[r]["departamento"]
        set_map_filter("CODEDE", {"departamento": dep})

# -------- Entidades (clic -> entidad) --------
with tab_ent:
    st.subheader("Entidades ejecutoras: concentración de baja ejecución (clic en fila para ver puntos de la entidad)")
    tope = ent.sort_values("score_concentracion_baja_ejec", ascending=False)

    disp = format_table(tope, money_cols=MONEY_COLS_AGG, ratio_cols=RATIO_COLS_AGG)
    sel = st.dataframe(
        disp, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row"
    )
    if sel and sel.selection and sel.selection.rows:
        r = sel.selection.rows[0]
        ee = tope.iloc[r]["entidad_ejecutora"]
        set_map_filter("ENTIDAD", {"entidad_ejecutora": ee})

# -------- Calidad / Consistencia (clic -> inconsistencias) --------
with tab_cal:
    st.subheader("Inconsistencia: Finalizado (auditoría) vs Momento presupuestario activo (clic para mapear solo esos puntos)")
    if "flag_inconsistencia_estado_momento" in snip.columns:
        n_inc = int(snip["flag_inconsistencia_estado_momento"].fillna(0).sum())
        q_inc = float(snip.loc[snip["flag_inconsistencia_estado_momento"] == 1, "presupuesto_actual_vigente"].sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Proyectos con inconsistencia", f"{n_inc:,}")
        c2.metric("Presupuesto asociado (vigente)", fmt_money(q_inc))
        if c3.button("Ver estos puntos en el mapa"):
            set_map_filter("FLAG_INCONSISTENCIA", {})

        sample = snip.loc[snip["flag_inconsistencia_estado_momento"] == 1].sort_values("presupuesto_actual_vigente", ascending=False).head(50)
        cols = ["snip","nombre_de_proyecto","departamento","municipio","entidad_ejecutora","momento_presupuestario",
                "presupuesto_actual_vigente","ratio_ejec_real"]
        cols = [c for c in cols if c in sample.columns]
        st.dataframe(
            format_table(sample[cols], money_cols=["presupuesto_actual_vigente"], ratio_cols=["ratio_ejec_real"]),
            use_container_width=True, hide_index=True
        )
