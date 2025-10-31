import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import os

# Configuration de la page
st.set_page_config(
    page_title="Dashboard VLAN & IP",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personnalisé
st.markdown("""
    <style>
    .main {
        padding: 0rem 1rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    }
    </style>
""", unsafe_allow_html=True)

@st.cache_data
def charger_donnees_vlan(fichier_vlan):
    """Charge le fichier CSV global des VLANs"""
    try:
        df = pd.read_csv(fichier_vlan)
        # Nettoyage des noms de colonnes
        df.columns = df.columns.str.strip()
        return df
    except Exception as e:
        st.error(f"Erreur lors du chargement du fichier VLAN: {e}")
        return None

@st.cache_data
def charger_adresses_ip(fichier_ip):
    """Charge un fichier CSV d'adresses IP pour un VLAN spécifique"""
    try:
        df = pd.read_csv(fichier_ip)
        df.columns = df.columns.str.strip()
        return df
    except Exception as e:
        st.error(f"Erreur lors du chargement du fichier IP: {e}")
        return None

def calculer_metriques_globales(df_vlan):
    """Calcule les métriques globales de l'infrastructure"""
    total_vlans = len(df_vlan)
    total_ips_utilisees = df_vlan['Subnet Ip Used Size'].sum() if 'Subnet Ip Used Size' in df_vlan.columns else 0
    total_ips_libres = df_vlan['Subnet Ip free size'].sum() if 'Subnet Ip free size' in df_vlan.columns else 0
    utilisation_moyenne = df_vlan['Subnet Ip Used percent'].mean() if 'Subnet Ip Used percent' in df_vlan.columns else 0
    
    return {
        'total_vlans': total_vlans,
        'total_ips_utilisees': int(total_ips_utilisees),
        'total_ips_libres': int(total_ips_libres),
        'utilisation_moyenne': round(utilisation_moyenne, 2)
    }

def afficher_vue_globale(df_vlan):
    """Affiche la vue d'ensemble de tous les VLANs"""
    st.header("📊 Vue d'ensemble de l'infrastructure")
    
    metriques = calculer_metriques_globales(df_vlan)
    
    # Affichage des métriques principales
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total VLANs", metriques['total_vlans'])
    
    with col2:
        st.metric("IPs Utilisées", f"{metriques['total_ips_utilisees']:,}")
    
    with col3:
        st.metric("IPs Libres", f"{metriques['total_ips_libres']:,}")
    
    with col4:
        st.metric("Utilisation Moyenne", f"{metriques['utilisation_moyenne']}%")
    
    # Graphiques
    col1, col2 = st.columns(2)
    
    with col1:
        # Top 10 VLANs par utilisation
        st.subheader("🔝 Top 10 VLANs les plus utilisés")
        if 'Subnet Ip Used percent' in df_vlan.columns:
            top_vlans = df_vlan.nlargest(10, 'Subnet Ip Used percent')[
                ['Vlan Id', 'Name', 'Subnet Ip Used percent']
            ].copy()
            
            fig = px.bar(
                top_vlans,
                x='Vlan Id',
                y='Subnet Ip Used percent',
                text='Subnet Ip Used percent',
                labels={'Subnet Ip Used percent': 'Utilisation (%)'},
                color='Subnet Ip Used percent',
                color_continuous_scale='RdYlGn_r'
            )
            fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
            fig.update_layout(showlegend=False, height=400)
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # Distribution par zone
        st.subheader("🌍 Distribution par Zone")
        if 'Zone' in df_vlan.columns:
            zone_counts = df_vlan['Zone'].value_counts()
            fig = px.pie(
                values=zone_counts.values,
                names=zone_counts.index,
                hole=0.4
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
    
    # Tableau des VLANs critiques
    st.subheader("⚠️ VLANs nécessitant une attention (>80% utilisés)")
    if 'Subnet Ip Used percent' in df_vlan.columns:
        vlans_critiques = df_vlan[df_vlan['Subnet Ip Used percent'] > 80][
            ['Vlan Id', 'Name', 'Zone', 'Subnet Ip Used Size', 'Subnet Ip free size', 'Subnet Ip Used percent']
        ].sort_values('Subnet Ip Used percent', ascending=False)
        
        if not vlans_critiques.empty:
            st.dataframe(vlans_critiques, use_container_width=True)
        else:
            st.success("✅ Aucun VLAN en situation critique")
    
    # Tableau de recherche et filtrage
    st.subheader("🔍 Explorer tous les VLANs")
    
    col1, col2 = st.columns(2)
    with col1:
        recherche = st.text_input("🔎 Rechercher un VLAN (ID ou Nom)", "")
    with col2:
        if 'Zone' in df_vlan.columns:
            zones = ['Toutes'] + sorted(df_vlan['Zone'].unique().tolist())
            zone_filtre = st.selectbox("Filtrer par Zone", zones)
    
    df_filtre = df_vlan.copy()
    
    if recherche:
        df_filtre = df_filtre[
            df_filtre['Vlan Id'].astype(str).str.contains(recherche, case=False, na=False) |
            df_filtre['Name'].astype(str).str.contains(recherche, case=False, na=False)
        ]
    
    if 'Zone' in df_vlan.columns and zone_filtre != 'Toutes':
        df_filtre = df_filtre[df_filtre['Zone'] == zone_filtre]
    
    st.dataframe(df_filtre, use_container_width=True, height=400)

def afficher_detail_vlan(df_vlan, df_ip, vlan_selectionne):
    """Affiche les détails d'un VLAN spécifique"""
    info_vlan = df_vlan[df_vlan['Vlan Id'] == vlan_selectionne].iloc[0]
    
    st.header(f"🌐 VLAN {vlan_selectionne} - {info_vlan['Name']}")
    
    # Informations du VLAN
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Subnet", info_vlan.get('Sub et Name', 'N/A'))
    
    with col2:
        st.metric("Zone", info_vlan.get('Zone', 'N/A'))
    
    with col3:
        st.metric("Masque", info_vlan.get('Subnet Mask', 'N/A'))
    
    with col4:
        utilisation = info_vlan.get('Subnet Ip Used percent', 0)
        delta_color = "normal" if utilisation < 80 else "inverse"
        st.metric("Utilisation", f"{utilisation}%", delta_color=delta_color)
    
    # Statistiques détaillées
    st.subheader("📈 Statistiques du sous-réseau")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Taille totale", f"{info_vlan.get('Subnet Size', 0):,}")
        st.metric("Plage IP", f"{info_vlan.get('Up Start', 'N/A')} - {info_vlan.get('Ip End', 'N/A')}")
    
    with col2:
        st.metric("IPs Utilisées", f"{info_vlan.get('Subnet Ip Used Size', 0):,}")
        st.metric("IPs Libres", f"{info_vlan.get('Subnet Ip free size', 0):,}")
    
    with col3:
        st.metric("Pool Size", f"{info_vlan.get('Subnet Pool Size', 0):,}")
        st.metric("IPs Réservées", f"{info_vlan.get('Subnet Ip réserva le Size', 0):,}")
    
    # Graphique de répartition
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📊 Répartition des adresses IP")
        labels = ['Utilisées', 'Libres', 'Réservées']
        values = [
            info_vlan.get('Subnet Ip Used Size', 0),
            info_vlan.get('Subnet Ip free size', 0),
            info_vlan.get('Subnet Ip réserva le Size', 0)
        ]
        colors = ['#EF553B', '#00CC96', '#FFA15A']
        
        fig = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            hole=0.4,
            marker_colors=colors
        )])
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.subheader("📉 Jauge d'utilisation")
        utilisation_pct = info_vlan.get('Subnet Ip Used percent', 0)
        
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=utilisation_pct,
            domain={'x': [0, 1], 'y': [0, 1]},
            title={'text': "Utilisation (%)"},
            delta={'reference': 50},
            gauge={
                'axis': {'range': [None, 100]},
                'bar': {'color': "darkblue"},
                'steps': [
                    {'range': [0, 50], 'color': "lightgreen"},
                    {'range': [50, 80], 'color': "yellow"},
                    {'range': [80, 100], 'color': "red"}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': 90
                }
            }
        ))
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)
    
    # Liste des adresses IP
    if df_ip is not None and not df_ip.empty:
        st.subheader("📋 Liste des adresses IP")
        
        col1, col2 = st.columns(2)
        with col1:
            recherche_ip = st.text_input("🔎 Rechercher une IP ou un nom", "")
        
        df_ip_filtre = df_ip.copy()
        
        if recherche_ip:
            df_ip_filtre = df_ip_filtre[
                df_ip_filtre['IpAddress'].astype(str).str.contains(recherche_ip, case=False, na=False) |
                df_ip_filtre['IpName'].astype(str).str.contains(recherche_ip, case=False, na=False)
            ]
        
        st.dataframe(df_ip_filtre, use_container_width=True, height=400)
        
        # Bouton d'export
        csv = df_ip_filtre.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Télécharger les adresses IP (CSV)",
            data=csv,
            file_name=f"vlan_{vlan_selectionne}_addresses.csv",
            mime="text/csv",
        )
    else:
        st.info("Aucune adresse IP disponible pour ce VLAN ou fichier non trouvé.")

def main():
    st.title("🌐 Dashboard VLAN & Gestion des Adresses IP")
    
    # Sidebar pour la navigation et configuration
    with st.sidebar:
        st.image("https://via.placeholder.com/150x50/4285F4/FFFFFF?text=VLAN+Manager", use_column_width=True)
        st.title("Navigation")
        
        # Upload des fichiers
        st.subheader("📁 Charger les données")
        fichier_vlan = st.file_uploader("Fichier VLAN global (CSV)", type=['csv'], key='vlan')
        
        st.divider()
        
        if fichier_vlan is not None:
            df_vlan = charger_donnees_vlan(fichier_vlan)
            
            if df_vlan is not None:
                # Sélection de la vue
                vue = st.radio(
                    "Vue",
                    ["📊 Vue Globale", "🔍 Détail VLAN"],
                    label_visibility="collapsed"
                )
                
                vlan_selectionne = None
                fichiers_ip = None
                
                if vue == "🔍 Détail VLAN":
                    st.subheader("Sélectionner un VLAN")
                    vlans_disponibles = sorted(df_vlan['Vlan Id'].unique())
                    vlan_selectionne = st.selectbox(
                        "VLAN ID",
                        vlans_disponibles,
                        format_func=lambda x: f"VLAN {x} - {df_vlan[df_vlan['Vlan Id']==x]['Name'].iloc[0]}"
                    )
                    
                    st.divider()
                    st.subheader("📄 Fichier IP du VLAN")
                    fichiers_ip = st.file_uploader(
                        f"Fichier CSV pour VLAN {vlan_selectionne}",
                        type=['csv'],
                        key='ip'
                    )
                
                st.divider()
                st.info(f"📊 **{len(df_vlan)}** VLANs chargés")
                
                # Affichage du contenu principal
                if vue == "📊 Vue Globale":
                    afficher_vue_globale(df_vlan)
                else:
                    df_ip = None
                    if fichiers_ip is not None:
                        df_ip = charger_adresses_ip(fichiers_ip)
                    
                    afficher_detail_vlan(df_vlan, df_ip, vlan_selectionne)
        else:
            st.info("👆 Veuillez charger le fichier CSV des VLANs pour commencer")
            
            # Instructions
            st.divider()
            st.subheader("📖 Instructions")
            st.markdown("""
            1. **Chargez** le fichier CSV global des VLANs
            2. **Explorez** la vue globale ou sélectionnez un VLAN
            3. **Chargez** le fichier CSV des IPs pour un VLAN spécifique
            4. **Analysez** les données et exportez si nécessaire
            """)

if __name__ == "__main__":
    main()
