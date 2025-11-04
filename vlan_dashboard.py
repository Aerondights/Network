import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import re
from datetime import datetime

# Configuration de la page
st.set_page_config(
    page_title="Dashboard VLAN & IP",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personnalisé pour un design moderne
st.markdown("""
    <style>
    /* Général */
    .main {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 0;
    }
    
    .block-container {
        padding: 2rem 3rem;
        background: white;
        border-radius: 20px;
        margin: 2rem;
        box-shadow: 0 10px 40px rgba(0,0,0,0.1);
    }
    
    /* Métriques */
    [data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 700;
    }
    
    [data-testid="metric-container"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        color: white;
    }
    
    [data-testid="metric-container"] label {
        color: rgba(255,255,255,0.9) !important;
        font-size: 0.9rem;
        font-weight: 600;
    }
    
    [data-testid="stMetricValue"], [data-testid="stMetricDelta"] {
        color: white !important;
    }
    
    /* Titres */
    h1 {
        color: #1e3a8a;
        font-weight: 800;
        font-size: 2.5rem;
        margin-bottom: 0.5rem;
    }
    
    h2 {
        color: #3730a3;
        font-weight: 700;
        font-size: 1.8rem;
        margin-top: 2rem;
    }
    
    h3 {
        color: #4338ca;
        font-weight: 600;
        font-size: 1.3rem;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1e3a8a 0%, #3730a3 100%);
    }
    
    [data-testid="stSidebar"] .css-1d391kg {
        color: white;
    }
    
    /* Boutons radio */
    [data-testid="stSidebar"] label {
        color: white !important;
        font-weight: 600;
    }
    
    /* Dataframe */
    [data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    
    /* Selectbox */
    .stSelectbox > div > div {
        background-color: #f8fafc;
        border-radius: 10px;
        border: 2px solid #e2e8f0;
    }
    
    /* Info boxes */
    .stAlert {
        border-radius: 10px;
        border-left: 4px solid;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px 10px 0 0;
        padding: 10px 20px;
        font-weight: 600;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

@st.cache_data
def charger_donnees(dossier_donnees="./data"):
    """Charge automatiquement tous les fichiers CSV"""
    if not Path(dossier_donnees).exists():
        st.error(f"❌ Le dossier '{dossier_donnees}' n'existe pas!")
        return None, {}
    
    fichiers = list(Path(dossier_donnees).glob("*.csv"))
    
    if not fichiers:
        st.error(f"❌ Aucun fichier CSV trouvé dans '{dossier_donnees}'")
        return None, {}
    
    # Charger le fichier global VLAN
    fichiers_vlan = [f for f in fichiers if "CapaVLAN" in f.name]
    
    if not fichiers_vlan:
        st.error("❌ Aucun fichier '*_CapaVLAN.csv' trouvé!")
        return None, {}
    
    fichier_vlan = sorted(fichiers_vlan, key=lambda x: x.stat().st_mtime, reverse=True)[0]
    
    try:
        df_vlan = pd.read_csv(fichier_vlan)
        df_vlan.columns = df_vlan.columns.str.strip()
    except Exception as e:
        st.error(f"❌ Erreur lors du chargement du fichier VLAN: {e}")
        return None, {}
    
    # Charger tous les fichiers CSV d'adresses IP
    fichiers_ip = {}
    fichiers_ip_list = [f for f in fichiers if "CapaVLAN" not in f.name]
    
    for fichier in fichiers_ip_list:
        match = re.search(r'(\d+)', fichier.name)
        if match:
            vlan_id = int(match.group(1))
            try:
                df_ip = pd.read_csv(fichier)
                df_ip.columns = df_ip.columns.str.strip()
                df_ip['VLAN ID'] = vlan_id
                fichiers_ip[vlan_id] = df_ip
            except Exception as e:
                st.warning(f"⚠️ Erreur chargement {fichier.name}: {e}")
    
    return df_vlan, fichiers_ip

def obtenir_colonne(df, mots_cles):
    """Trouve une colonne contenant un des mots-clés"""
    for col in df.columns:
        for mot in mots_cles:
            if mot.lower() in col.lower():
                return col
    return None

def calculer_metriques(df_vlan, zone=None):
    """Calcule les métriques pour une zone donnée ou globales"""
    if zone and 'Zone' in df_vlan.columns:
        df = df_vlan[df_vlan['Zone'] == zone]
    else:
        df = df_vlan
    
    col_used = obtenir_colonne(df, ['Used Size'])
    col_free = obtenir_colonne(df, ['free size'])
    col_percent = obtenir_colonne(df, ['Used percent'])
    col_size = obtenir_colonne(df, ['Subnet Size'])
    
    total_vlans = len(df)
    total_ips_utilisees = int(df[col_used].sum()) if col_used else 0
    total_ips_libres = int(df[col_free].sum()) if col_free else 0
    total_ips = int(df[col_size].sum()) if col_size else 0
    utilisation_moyenne = round(df[col_percent].mean(), 2) if col_percent else 0
    
    return {
        'total_vlans': total_vlans,
        'total_ips_utilisees': total_ips_utilisees,
        'total_ips_libres': total_ips_libres,
        'total_ips': total_ips,
        'utilisation_moyenne': utilisation_moyenne
    }

def afficher_metrique_moderne(col, titre, valeur, icone, couleur="blue"):
    """Affiche une métrique avec un style moderne"""
    with col:
        st.markdown(f"""
            <div style="background: linear-gradient(135deg, {couleur} 0%, {couleur}dd 100%); 
                        padding: 1.5rem; border-radius: 15px; 
                        box-shadow: 0 4px 15px rgba(0,0,0,0.1); color: white;">
                <div style="font-size: 2rem; margin-bottom: 0.5rem;">{icone}</div>
                <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.5rem;">{titre}</div>
                <div style="font-size: 2rem; font-weight: 700;">{valeur}</div>
            </div>
        """, unsafe_allow_html=True)

def page_vue_globale(df_vlan, fichiers_ip):
    """Page de vue d'ensemble"""
    st.title("📊 Vue Globale de l'Infrastructure")
    
    # Métriques globales
    st.markdown("### 🌍 Métriques Globales")
    metriques_global = calculer_metriques(df_vlan)
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    afficher_metrique_moderne(col1, "Total VLANs", f"{metriques_global['total_vlans']}", "🌐", "#667eea")
    afficher_metrique_moderne(col2, "IPs Totales", f"{metriques_global['total_ips']:,}", "📦", "#764ba2")
    afficher_metrique_moderne(col3, "IPs Utilisées", f"{metriques_global['total_ips_utilisees']:,}", "✅", "#f59e0b")
    afficher_metrique_moderne(col4, "IPs Libres", f"{metriques_global['total_ips_libres']:,}", "🟢", "#10b981")
    afficher_metrique_moderne(col5, "Utilisation Moy.", f"{metriques_global['utilisation_moyenne']}%", "📈", "#ef4444")
    
    st.markdown("---")
    
    # Métriques par zone
    if 'Zone' in df_vlan.columns:
        zones = df_vlan['Zone'].unique()
        
        # Séparer PROD et HORS-PROD
        prod_zones = [z for z in zones if 'PROD' in str(z).upper() and 'HORS' not in str(z).upper()]
        hors_prod_zones = [z for z in zones if 'HORS' in str(z).upper() or 'PREPROD' in str(z).upper() or 'DEV' in str(z).upper() or 'TEST' in str(z).upper()]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("### 🟢 Production")
            if prod_zones:
                df_prod = df_vlan[df_vlan['Zone'].isin(prod_zones)]
                metriques_prod = calculer_metriques(df_prod)
                
                subcol1, subcol2 = st.columns(2)
                with subcol1:
                    st.metric("VLANs PROD", metriques_prod['total_vlans'])
                    st.metric("IPs Utilisées", f"{metriques_prod['total_ips_utilisees']:,}")
                with subcol2:
                    st.metric("Utilisation", f"{metriques_prod['utilisation_moyenne']}%")
                    st.metric("IPs Libres", f"{metriques_prod['total_ips_libres']:,}")
            else:
                st.info("Aucune zone PROD identifiée")
        
        with col2:
            st.markdown("### 🟡 Hors Production")
            if hors_prod_zones:
                df_hors_prod = df_vlan[df_vlan['Zone'].isin(hors_prod_zones)]
                metriques_hors_prod = calculer_metriques(df_hors_prod)
                
                subcol1, subcol2 = st.columns(2)
                with subcol1:
                    st.metric("VLANs Hors-PROD", metriques_hors_prod['total_vlans'])
                    st.metric("IPs Utilisées", f"{metriques_hors_prod['total_ips_utilisees']:,}")
                with subcol2:
                    st.metric("Utilisation", f"{metriques_hors_prod['utilisation_moyenne']}%")
                    st.metric("IPs Libres", f"{metriques_hors_prod['total_ips_libres']:,}")
            else:
                st.info("Aucune zone Hors-PROD identifiée")
    
    st.markdown("---")
    
    # Graphiques
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 📊 Top 15 VLANs les plus utilisés")
        col_percent = obtenir_colonne(df_vlan, ['Used percent'])
        
        if col_percent:
            top_vlans = df_vlan.nlargest(15, col_percent)[['Vlan Id', 'Name', col_percent]].copy()
            
            fig = px.bar(
                top_vlans,
                x='Vlan Id',
                y=col_percent,
                text=col_percent,
                color=col_percent,
                color_continuous_scale=['#10b981', '#f59e0b', '#ef4444'],
                labels={col_percent: 'Utilisation (%)'}
            )
            fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
            fig.update_layout(
                showlegend=False,
                height=400,
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.markdown("### 🌍 Distribution par Zone")
        if 'Zone' in df_vlan.columns:
            zone_counts = df_vlan['Zone'].value_counts()
            
            fig = px.pie(
                values=zone_counts.values,
                names=zone_counts.index,
                hole=0.5,
                color_discrete_sequence=px.colors.qualitative.Set3
            )
            fig.update_layout(
                height=400,
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
            st.plotly_chart(fig, use_container_width=True)
    
    # VLANs critiques
    st.markdown("### ⚠️ VLANs Critiques (>80% d'utilisation)")
    
    if col_percent:
        vlans_critiques = df_vlan[df_vlan[col_percent] > 80].copy()
        
        if not vlans_critiques.empty:
            vlans_critiques = vlans_critiques.sort_values(col_percent, ascending=False)
            
            # Sélectionner les colonnes à afficher
            colonnes = ['Vlan Id', 'Name']
            for col in ['Zone', 'Subnet Ip Used Size', 'Subnet Ip free size', col_percent]:
                if col in vlans_critiques.columns:
                    colonnes.append(col)
            
            # Colorer le dataframe
            def colorier_utilisation(val):
                if isinstance(val, (int, float)):
                    if val > 90:
                        return 'background-color: #fee2e2; color: #991b1b; font-weight: bold'
                    elif val > 80:
                        return 'background-color: #fef3c7; color: #92400e; font-weight: bold'
                return ''
            
            styled_df = vlans_critiques[colonnes].style.applymap(
                colorier_utilisation, 
                subset=[col_percent] if col_percent in colonnes else []
            )
            
            st.dataframe(styled_df, use_container_width=True, height=300)
        else:
            st.success("✅ Aucun VLAN en situation critique!")
    
    # Graphique d'évolution (si plusieurs zones)
    if 'Zone' in df_vlan.columns:
        st.markdown("### 📈 Utilisation par Zone")
        
        zone_utilisation = df_vlan.groupby('Zone').agg({
            col_percent: 'mean',
            'Vlan Id': 'count'
        }).reset_index()
        zone_utilisation.columns = ['Zone', 'Utilisation Moyenne (%)', 'Nombre de VLANs']
        
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=zone_utilisation['Zone'],
            y=zone_utilisation['Utilisation Moyenne (%)'],
            name='Utilisation Moyenne (%)',
            marker_color='#667eea',
            text=zone_utilisation['Utilisation Moyenne (%)'],
            texttemplate='%{text:.1f}%',
            textposition='outside'
        ))
        
        fig.add_trace(go.Scatter(
            x=zone_utilisation['Zone'],
            y=zone_utilisation['Nombre de VLANs'],
            name='Nombre de VLANs',
            yaxis='y2',
            marker_color='#f59e0b',
            mode='lines+markers',
            line=dict(width=3)
        ))
        
        fig.update_layout(
            yaxis=dict(title='Utilisation Moyenne (%)'),
            yaxis2=dict(title='Nombre de VLANs', overlaying='y', side='right'),
            height=400,
            hovermode='x unified',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        st.plotly_chart(fig, use_container_width=True)

def page_vlans(df_vlan):
    """Page des VLANs"""
    st.title("🌐 Gestion des VLANs")
    
    # Filtres
    st.markdown("### 🔍 Filtres")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        recherche = st.text_input("🔎 Rechercher (ID ou Nom)", "")
    
    with col2:
        if 'Zone' in df_vlan.columns:
            zones = ['Toutes'] + sorted(df_vlan['Zone'].unique().tolist())
            zone_filtre = st.selectbox("Zone", zones)
        else:
            zone_filtre = 'Toutes'
    
    with col3:
        col_percent = obtenir_colonne(df_vlan, ['Used percent'])
        if col_percent:
            utilisation_min = st.slider("Utilisation minimum (%)", 0, 100, 0)
        else:
            utilisation_min = 0
    
    # Appliquer les filtres
    df_filtre = df_vlan.copy()
    
    if recherche:
        mask = (
            df_filtre['Vlan Id'].astype(str).str.contains(recherche, case=False, na=False) |
            df_filtre['Name'].astype(str).str.contains(recherche, case=False, na=False)
        )
        df_filtre = df_filtre[mask]
    
    if zone_filtre != 'Toutes' and 'Zone' in df_filtre.columns:
        df_filtre = df_filtre[df_filtre['Zone'] == zone_filtre]
    
    if col_percent and utilisation_min > 0:
        df_filtre = df_filtre[df_filtre[col_percent] >= utilisation_min]
    
    # Statistiques filtrées
    st.markdown(f"### 📊 {len(df_filtre)} VLANs affichés")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("VLANs", len(df_filtre))
    with col2:
        col_used = obtenir_colonne(df_filtre, ['Used Size'])
        if col_used:
            st.metric("IPs Utilisées", f"{int(df_filtre[col_used].sum()):,}")
    with col3:
        col_free = obtenir_colonne(df_filtre, ['free size'])
        if col_free:
            st.metric("IPs Libres", f"{int(df_filtre[col_free].sum()):,}")
    with col4:
        if col_percent:
            st.metric("Util. Moyenne", f"{df_filtre[col_percent].mean():.1f}%")
    
    st.markdown("---")
    
    # Fonction de coloration
    def colorier_vlan(row):
        if col_percent and col_percent in row.index:
            val = row[col_percent]
            if val > 90:
                return ['background-color: #fee2e2'] * len(row)
            elif val > 80:
                return ['background-color: #fef3c7'] * len(row)
            elif val > 70:
                return ['background-color: #fff7ed'] * len(row)
            elif val < 30:
                return ['background-color: #dcfce7'] * len(row)
        return [''] * len(row)
    
    # Affichage du tableau avec style
    styled_df = df_filtre.style.apply(colorier_vlan, axis=1)
    
    st.dataframe(styled_df, use_container_width=True, height=600)
    
    # Légende des couleurs
    st.markdown("""
    <div style="display: flex; gap: 20px; margin-top: 20px; flex-wrap: wrap;">
        <div style="display: flex; align-items: center; gap: 10px;">
            <div style="width: 30px; height: 30px; background-color: #fee2e2; border-radius: 5px;"></div>
            <span>>90% (Critique)</span>
        </div>
        <div style="display: flex; align-items: center; gap: 10px;">
            <div style="width: 30px; height: 30px; background-color: #fef3c7; border-radius: 5px;"></div>
            <span>80-90% (Attention)</span>
        </div>
        <div style="display: flex; align-items: center; gap: 10px;">
            <div style="width: 30px; height: 30px; background-color: #fff7ed; border-radius: 5px;"></div>
            <span>70-80% (Élevé)</span>
        </div>
        <div style="display: flex; align-items: center; gap: 10px;">
            <div style="width: 30px; height: 30px; background-color: #dcfce7; border-radius: 5px;"></div>
            <span><30% (Faible)</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Export
    st.markdown("---")
    csv = df_filtre.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 Télécharger les VLANs (CSV)",
        data=csv,
        file_name=f"vlans_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )

def page_adresses_ip(df_vlan, fichiers_ip):
    """Page des adresses IP"""
    st.title("📋 Gestion des Adresses IP")
    
    if not fichiers_ip:
        st.warning("⚠️ Aucun fichier d'adresses IP trouvé")
        return
    
    # Combiner toutes les adresses IP
    df_all_ips = pd.concat(fichiers_ip.values(), ignore_index=True)
    
    # Filtres
    st.markdown("### 🔍 Filtres")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        vlan_options = ['Tous'] + sorted([int(v) for v in fichiers_ip.keys()])
        vlan_filtre = st.selectbox("VLAN", vlan_options)
    
    with col2:
        recherche_ip = st.text_input("🔎 Rechercher IP ou Nom", "")
    
    with col3:
        if 'Zone' in df_vlan.columns:
            zones = ['Toutes'] + sorted(df_vlan['Zone'].unique().tolist())
            zone_filtre_ip = st.selectbox("Zone", zones, key="zone_ip")
        else:
            zone_filtre_ip = 'Toutes'
    
    # Appliquer les filtres
    df_ip_filtre = df_all_ips.copy()
    
    if vlan_filtre != 'Tous':
        df_ip_filtre = df_ip_filtre[df_ip_filtre['VLAN ID'] == vlan_filtre]
    
    if recherche_ip:
        if 'IpAddress' in df_ip_filtre.columns and 'IpName' in df_ip_filtre.columns:
            mask = (
                df_ip_filtre['IpAddress'].astype(str).str.contains(recherche_ip, case=False, na=False) |
                df_ip_filtre['IpName'].astype(str).str.contains(recherche_ip, case=False, na=False)
            )
            df_ip_filtre = df_ip_filtre[mask]
    
    if zone_filtre_ip != 'Toutes' and 'Zone' in df_vlan.columns:
        # Joindre avec df_vlan pour filtrer par zone
        vlans_zone = df_vlan[df_vlan['Zone'] == zone_filtre_ip]['Vlan Id'].tolist()
        df_ip_filtre = df_ip_filtre[df_ip_filtre['VLAN ID'].isin(vlans_zone)]
    
    # Statistiques
    st.markdown(f"### 📊 {len(df_ip_filtre):,} adresses IP affichées")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total IPs", f"{len(df_ip_filtre):,}")
    with col2:
        vlans_uniques = df_ip_filtre['VLAN ID'].nunique()
        st.metric("VLANs", vlans_uniques)
    with col3:
        if 'IpAddress' in df_ip_filtre.columns:
            ips_uniques = df_ip_filtre['IpAddress'].nunique()
            st.metric("IPs Uniques", f"{ips_uniques:,}")
    
    st.markdown("---")
    
    # Graphique de répartition par VLAN
    if len(df_ip_filtre) > 0:
        st.markdown("### 📊 Répartition par VLAN")
        
        vlan_counts = df_ip_filtre['VLAN ID'].value_counts().head(20)
        
        fig = px.bar(
            x=vlan_counts.index,
            y=vlan_counts.values,
            labels={'x': 'VLAN ID', 'y': 'Nombre d\'IPs'},
            color=vlan_counts.values,
            color_continuous_scale='Viridis'
        )
        fig.update_layout(
            showlegend=False,
            height=300,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("---")
    
    # Fonction de coloration alternée par VLAN
    def colorier_ip(row):
        vlan = row['VLAN ID']
        if vlan % 2 == 0:
            return ['background-color: #f0f9ff'] * len(row)
        else:
            return ['background-color: #faf5ff'] * len(row)
    
    # Affichage du tableau
    if len(df_ip_filtre) > 0:
        styled_df = df_ip_filtre.style.apply(colorier_ip, axis=1)
        st.dataframe(styled_df, use_container_width=True, height=600)
    else:
        st.info("Aucune adresse IP ne correspond aux filtres")
    
    # Export
    st.markdown("---")
    if len(df_ip_filtre) > 0:
        csv = df_ip_filtre.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Télécharger les IPs (CSV)",
            data=csv,
            file_name=f"ips_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

def main():
    # Charger les données
    df_vlan, fichiers_ip = charger_donnees()
    
    if df_vlan is None:
        st.error("❌ Impossible de charger les données. Vérifiez le dossier 'data'")
        st.info("📁 Structure attendue:\n\n- data/\n  - YYYY-MM-DD_CapaVLAN.csv\n  - subnet_vlanXX.csv\n  - ...")
        return
    
    # Sidebar
    with st.sidebar:
        st.markdown("# 🌐 Dashboard VLAN")
        st.markdown("---")
        
        # Navigation
        page = st.radio(
            "Navigation",
            ["📊 Vue Globale", "🌐 VLANs", "📋 Adresses IP"],
            label_visibility="collapsed"
        )