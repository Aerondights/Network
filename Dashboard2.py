import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import os
import re
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import threading

class VLANDashboard:
    def __init__(self, root, dossier_donnees="./data"):
        self.root = root
        self.root.title("Dashboard VLAN & Gestion des Adresses IP")
        self.root.geometry("1400x900")
        self.root.configure(bg='#f0f2f6')
        
        self.dossier_donnees = dossier_donnees
        self.df_vlan = None
        self.fichiers_ip = {}
        self.vlan_selectionne = None
        
        # Charger les données au démarrage
        self.charger_toutes_donnees()
        
        # Créer l'interface
        self.creer_interface()
        
    def charger_toutes_donnees(self):
        """Charge automatiquement tous les fichiers CSV"""
        if not os.path.exists(self.dossier_donnees):
            messagebox.showerror("Erreur", f"Le dossier '{self.dossier_donnees}' n'existe pas!\n\nCréez un dossier 'data' et placez-y vos fichiers CSV.")
            return False
        
        fichiers = list(Path(self.dossier_donnees).glob("*.csv"))
        
        if not fichiers:
            messagebox.showerror("Erreur", f"Aucun fichier CSV trouvé dans '{self.dossier_donnees}'")
            return False
        
        # Charger le fichier global VLAN
        fichiers_vlan = [f for f in fichiers if "CapaVLAN" in f.name]
        
        if not fichiers_vlan:
            messagebox.showerror("Erreur", "Aucun fichier de type '*_CapaVLAN.csv' trouvé!")
            return False
        
        # Prendre le fichier le plus récent
        fichier_vlan = sorted(fichiers_vlan, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        
        try:
            self.df_vlan = pd.read_csv(fichier_vlan)
            self.df_vlan.columns = self.df_vlan.columns.str.strip()
            print(f"✓ Fichier VLAN chargé: {fichier_vlan.name}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Erreur lors du chargement du fichier VLAN:\n{e}")
            return False
        
        # Charger tous les fichiers CSV d'adresses IP
        fichiers_ip = [f for f in fichiers if "CapaVLAN" not in f.name]
        
        for fichier in fichiers_ip:
            # Extraire le VLAN ID du nom du fichier
            match = re.search(r'(\d+)', fichier.name)
            if match:
                vlan_id = int(match.group(1))
                try:
                    df_ip = pd.read_csv(fichier)
                    df_ip.columns = df_ip.columns.str.strip()
                    self.fichiers_ip[vlan_id] = df_ip
                    print(f"✓ Fichier IP chargé pour VLAN {vlan_id}: {fichier.name}")
                except Exception as e:
                    print(f"✗ Erreur chargement {fichier.name}: {e}")
        
        print(f"\n📊 Total: {len(self.df_vlan)} VLANs | {len(self.fichiers_ip)} fichiers IP")
        return True
    
    def creer_interface(self):
        """Crée l'interface graphique principale"""
        # Menu supérieur
        menu_frame = tk.Frame(self.root, bg='#2c3e50', height=60)
        menu_frame.pack(fill=tk.X, side=tk.TOP)
        
        titre = tk.Label(menu_frame, text="🌐 Dashboard VLAN", 
                        font=("Arial", 20, "bold"), bg='#2c3e50', fg='white')
        titre.pack(side=tk.LEFT, padx=20, pady=10)
        
        # Boutons de navigation
        btn_frame = tk.Frame(menu_frame, bg='#2c3e50')
        btn_frame.pack(side=tk.RIGHT, padx=20)
        
        self.btn_globale = tk.Button(btn_frame, text="📊 Vue Globale", 
                                     command=self.afficher_vue_globale,
                                     font=("Arial", 11), bg='#3498db', fg='white',
                                     padx=15, pady=5, relief=tk.FLAT, cursor='hand2')
        self.btn_globale.pack(side=tk.LEFT, padx=5)
        
        self.btn_detail = tk.Button(btn_frame, text="🔍 Détail VLAN", 
                                    command=self.afficher_vue_detail,
                                    font=("Arial", 11), bg='#95a5a6', fg='white',
                                    padx=15, pady=5, relief=tk.FLAT, cursor='hand2')
        self.btn_detail.pack(side=tk.LEFT, padx=5)
        
        # Frame principal avec scroll
        self.main_frame = tk.Frame(self.root, bg='#f0f2f6')
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Afficher la vue globale par défaut
        if self.df_vlan is not None:
            self.afficher_vue_globale()
        else:
            self.afficher_erreur_chargement()
    
    def afficher_erreur_chargement(self):
        """Affiche un message si les données ne sont pas chargées"""
        self.nettoyer_frame()
        
        msg = tk.Label(self.main_frame, 
                      text="❌ Erreur de chargement des données\n\nAssurez-vous d'avoir un dossier 'data' avec vos fichiers CSV",
                      font=("Arial", 14), bg='#f0f2f6', fg='#e74c3c')
        msg.pack(pady=100)
    
    def nettoyer_frame(self):
        """Nettoie le contenu du frame principal"""
        for widget in self.main_frame.winfo_children():
            widget.destroy()
    
    def calculer_metriques_globales(self):
        """Calcule les métriques globales"""
        total_vlans = len(self.df_vlan)
        total_ips_utilisees = self.df_vlan['Subnet Ip Used Size'].sum() if 'Subnet Ip Used Size' in self.df_vlan.columns else 0
        total_ips_libres = self.df_vlan['Subnet Ip free size'].sum() if 'Subnet Ip free size' in self.df_vlan.columns else 0
        utilisation_moyenne = self.df_vlan['Subnet Ip Used percent'].mean() if 'Subnet Ip Used percent' in self.df_vlan.columns else 0
        
        return {
            'total_vlans': total_vlans,
            'total_ips_utilisees': int(total_ips_utilisees),
            'total_ips_libres': int(total_ips_libres),
            'utilisation_moyenne': round(utilisation_moyenne, 2)
        }
    
    def afficher_vue_globale(self):
        """Affiche la vue d'ensemble"""
        self.nettoyer_frame()
        
        # Mettre à jour les boutons
        self.btn_globale.config(bg='#3498db')
        self.btn_detail.config(bg='#95a5a6')
        
        # Créer un canvas avec scrollbar
        canvas = tk.Canvas(self.main_frame, bg='#f0f2f6', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg='#f0f2f6')
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Titre
        titre = tk.Label(scrollable_frame, text="📊 Vue d'ensemble de l'infrastructure", 
                        font=("Arial", 18, "bold"), bg='#f0f2f6', fg='#2c3e50')
        titre.pack(pady=(0, 20))
        
        # Métriques
        metriques = self.calculer_metriques_globales()
        
        metriques_frame = tk.Frame(scrollable_frame, bg='#f0f2f6')
        metriques_frame.pack(fill=tk.X, pady=10)
        
        self.creer_carte_metrique(metriques_frame, "Total VLANs", 
                                  str(metriques['total_vlans']), "#3498db", 0)
        self.creer_carte_metrique(metriques_frame, "IPs Utilisées", 
                                  f"{metriques['total_ips_utilisees']:,}", "#e74c3c", 1)
        self.creer_carte_metrique(metriques_frame, "IPs Libres", 
                                  f"{metriques['total_ips_libres']:,}", "#2ecc71", 2)
        self.creer_carte_metrique(metriques_frame, "Utilisation Moyenne", 
                                  f"{metriques['utilisation_moyenne']}%", "#f39c12", 3)
        
        # Tableau des VLANs critiques
        self.afficher_vlans_critiques(scrollable_frame)
        
        # Tableau de tous les VLANs
        self.afficher_tableau_vlans(scrollable_frame)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def creer_carte_metrique(self, parent, titre, valeur, couleur, colonne):
        """Crée une carte de métrique"""
        frame = tk.Frame(parent, bg='white', relief=tk.RAISED, borderwidth=1)
        frame.grid(row=0, column=colonne, padx=10, sticky='ew')
        parent.grid_columnconfigure(colonne, weight=1)
        
        titre_label = tk.Label(frame, text=titre, font=("Arial", 10), 
                              bg='white', fg='#7f8c8d')
        titre_label.pack(pady=(15, 5))
        
        valeur_label = tk.Label(frame, text=valeur, font=("Arial", 24, "bold"), 
                               bg='white', fg=couleur)
        valeur_label.pack(pady=(0, 15))
    
    def afficher_vlans_critiques(self, parent):
        """Affiche les VLANs critiques (>80% utilisés)"""
        section = tk.Frame(parent, bg='#f0f2f6')
        section.pack(fill=tk.BOTH, pady=20)
        
        titre = tk.Label(section, text="⚠️ VLANs nécessitant une attention (>80% utilisés)", 
                        font=("Arial", 14, "bold"), bg='#f0f2f6', fg='#e74c3c')
        titre.pack(anchor='w', pady=(0, 10))
        
        if 'Subnet Ip Used percent' in self.df_vlan.columns:
            vlans_critiques = self.df_vlan[self.df_vlan['Subnet Ip Used percent'] > 80][
                ['Vlan Id', 'Name', 'Zone', 'Subnet Ip Used Size', 'Subnet Ip free size', 'Subnet Ip Used percent']
            ].sort_values('Subnet Ip Used percent', ascending=False)
            
            if not vlans_critiques.empty:
                self.creer_tableau(section, vlans_critiques, hauteur=150)
            else:
                msg = tk.Label(section, text="✅ Aucun VLAN en situation critique", 
                             font=("Arial", 11), bg='#d5f4e6', fg='#27ae60',
                             padx=20, pady=10)
                msg.pack(fill=tk.X)
    
    def afficher_tableau_vlans(self, parent):
        """Affiche le tableau de tous les VLANs"""
        section = tk.Frame(parent, bg='#f0f2f6')
        section.pack(fill=tk.BOTH, expand=True, pady=20)
        
        titre = tk.Label(section, text="🔍 Tous les VLANs", 
                        font=("Arial", 14, "bold"), bg='#f0f2f6', fg='#2c3e50')
        titre.pack(anchor='w', pady=(0, 10))
        
        # Barre de recherche
        search_frame = tk.Frame(section, bg='#f0f2f6')
        search_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(search_frame, text="🔎 Rechercher:", 
                font=("Arial", 10), bg='#f0f2f6').pack(side=tk.LEFT, padx=(0, 10))
        
        self.search_var = tk.StringVar()
        self.search_var.trace('w', lambda *args: self.filtrer_tableau())
        
        search_entry = tk.Entry(search_frame, textvariable=self.search_var, 
                               font=("Arial", 10), width=40)
        search_entry.pack(side=tk.LEFT)
        
        # Tableau
        self.tree_frame = tk.Frame(section, bg='white')
        self.tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.creer_tableau(self.tree_frame, self.df_vlan, hauteur=400, click_handler=self.on_vlan_double_click)
    
    def creer_tableau(self, parent, dataframe, hauteur=200, click_handler=None):
        """Crée un tableau Treeview"""
        # Scrollbars
        tree_scroll_y = ttk.Scrollbar(parent, orient="vertical")
        tree_scroll_x = ttk.Scrollbar(parent, orient="horizontal")
        
        # Treeview
        columns = list(dataframe.columns)
        tree = ttk.Treeview(parent, columns=columns, show='headings',
                           yscrollcommand=tree_scroll_y.set,
                           xscrollcommand=tree_scroll_x.set,
                           height=hauteur//25)
        
        tree_scroll_y.config(command=tree.yview)
        tree_scroll_x.config(command=tree.xview)
        
        # Définir les colonnes
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=120, anchor='center')
        
        # Ajouter les données
        for _, row in dataframe.iterrows():
            values = [str(row[col]) for col in columns]
            tree.insert('', 'end', values=values)
        
        # Double-clic
        if click_handler:
            tree.bind('<Double-1>', click_handler)
        
        tree.pack(side='left', fill='both', expand=True)
        tree_scroll_y.pack(side='right', fill='y')
        tree_scroll_x.pack(side='bottom', fill='x')
        
        return tree
    
    def filtrer_tableau(self):
        """Filtre le tableau en fonction de la recherche"""
        recherche = self.search_var.get().lower()
        
        if recherche == "":
            df_filtre = self.df_vlan
        else:
            df_filtre = self.df_vlan[
                self.df_vlan['Vlan Id'].astype(str).str.contains(recherche, case=False, na=False) |
                self.df_vlan['Name'].astype(str).str.contains(recherche, case=False, na=False)
            ]
        
        # Recréer le tableau
        for widget in self.tree_frame.winfo_children():
            widget.destroy()
        
        self.creer_tableau(self.tree_frame, df_filtre, hauteur=400, click_handler=self.on_vlan_double_click)
    
    def on_vlan_double_click(self, event):
        """Gère le double-clic sur un VLAN"""
        tree = event.widget
        selection = tree.selection()
        if selection:
            item = tree.item(selection[0])
            vlan_id = int(item['values'][0])
            self.vlan_selectionne = vlan_id
            self.afficher_vue_detail()
    
    def afficher_vue_detail(self):
        """Affiche la vue détaillée d'un VLAN"""
        if self.vlan_selectionne is None:
            # Demander de sélectionner un VLAN
            self.nettoyer_frame()
            self.btn_globale.config(bg='#95a5a6')
            self.btn_detail.config(bg='#3498db')
            
            titre = tk.Label(self.main_frame, text="🔍 Sélectionner un VLAN", 
                           font=("Arial", 18, "bold"), bg='#f0f2f6', fg='#2c3e50')
            titre.pack(pady=(20, 10))
            
            # Liste déroulante
            vlans_disponibles = sorted(self.df_vlan['Vlan Id'].unique())
            
            select_frame = tk.Frame(self.main_frame, bg='#f0f2f6')
            select_frame.pack(pady=20)
            
            tk.Label(select_frame, text="VLAN ID:", font=("Arial", 12), 
                    bg='#f0f2f6').pack(side=tk.LEFT, padx=10)
            
            vlan_var = tk.StringVar()
            vlan_combo = ttk.Combobox(select_frame, textvariable=vlan_var, 
                                     values=vlans_disponibles, width=30,
                                     font=("Arial", 11), state='readonly')
            vlan_combo.pack(side=tk.LEFT, padx=10)
            
            def valider():
                if vlan_var.get():
                    self.vlan_selectionne = int(vlan_var.get())
                    self.afficher_vue_detail()
            
            btn = tk.Button(select_frame, text="Afficher", command=valider,
                          font=("Arial", 11), bg='#3498db', fg='white',
                          padx=20, pady=5, cursor='hand2')
            btn.pack(side=tk.LEFT, padx=10)
            
            return
        
        self.nettoyer_frame()
        self.btn_globale.config(bg='#95a5a6')
        self.btn_detail.config(bg='#3498db')
        
        # Récupérer les infos du VLAN
        info_vlan = self.df_vlan[self.df_vlan['Vlan Id'] == self.vlan_selectionne].iloc[0]
        
        # Canvas avec scroll
        canvas = tk.Canvas(self.main_frame, bg='#f0f2f6', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg='#f0f2f6')
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # En-tête
        header = tk.Frame(scrollable_frame, bg='#f0f2f6')
        header.pack(fill=tk.X, pady=(0, 20))
        
        titre = tk.Label(header, text=f"🌐 VLAN {self.vlan_selectionne} - {info_vlan['Name']}", 
                        font=("Arial", 18, "bold"), bg='#f0f2f6', fg='#2c3e50')
        titre.pack(side=tk.LEFT)
        
        btn_retour = tk.Button(header, text="← Retour", command=lambda: setattr(self, 'vlan_selectionne', None) or self.afficher_vue_detail(),
                              font=("Arial", 10), bg='#95a5a6', fg='white',
                              padx=15, pady=5, cursor='hand2')
        btn_retour.pack(side=tk.RIGHT)
        
        # Métriques du VLAN
        metriques_frame = tk.Frame(scrollable_frame, bg='#f0f2f6')
        metriques_frame.pack(fill=tk.X, pady=10)
        
        infos = [
            ("Subnet", info_vlan.get('Sub et Name', 'N/A')),
            ("Zone", info_vlan.get('Zone', 'N/A')),
            ("Masque", info_vlan.get('Subnet Mask', 'N/A')),
            ("Utilisation", f"{info_vlan.get('Subnet Ip Used percent', 0)}%")
        ]
        
        for i, (label, value) in enumerate(infos):
            self.creer_carte_info(metriques_frame, label, value, i)
        
        # Statistiques détaillées
        stats_frame = tk.Frame(scrollable_frame, bg='white', relief=tk.RAISED, borderwidth=1)
        stats_frame.pack(fill=tk.X, pady=20, padx=10)
        
        tk.Label(stats_frame, text="📈 Statistiques du sous-réseau", 
                font=("Arial", 14, "bold"), bg='white', fg='#2c3e50').pack(pady=15)
        
        stats_grid = tk.Frame(stats_frame, bg='white')
        stats_grid.pack(padx=20, pady=(0, 15))
        
        statistiques = [
            ("Taille totale", f"{info_vlan.get('Subnet Size', 0):,}"),
            ("IPs Utilisées", f"{info_vlan.get('Subnet Ip Used Size', 0):,}"),
            ("IPs Libres", f"{info_vlan.get('Subnet Ip free size', 0):,}"),
            ("Pool Size", f"{info_vlan.get('Subnet Pool Size', 0):,}"),
            ("Plage IP", f"{info_vlan.get('Up Start', 'N/A')} - {info_vlan.get('Ip End', 'N/A')}"),
            ("IPs Réservées", f"{info_vlan.get('Subnet Ip réserva le Size', 0):,}")
        ]
        
        for i, (label, value) in enumerate(statistiques):
            row = i // 2
            col = i % 2
            
            stat_frame = tk.Frame(stats_grid, bg='white')
            stat_frame.grid(row=row, column=col, padx=30, pady=10, sticky='w')
            
            tk.Label(stat_frame, text=f"{label}:", font=("Arial", 10, "bold"), 
                    bg='white', fg='#7f8c8d').pack(anchor='w')
            tk.Label(stat_frame, text=value, font=("Arial", 12), 
                    bg='white', fg='#2c3e50').pack(anchor='w')
        
        # Liste des IPs
        if self.vlan_selectionne in self.fichiers_ip:
            df_ip = self.fichiers_ip[self.vlan_selectionne]
            
            ip_frame = tk.Frame(scrollable_frame, bg='#f0f2f6')
            ip_frame.pack(fill=tk.BOTH, expand=True, pady=20)
            
            tk.Label(ip_frame, text="📋 Liste des adresses IP", 
                    font=("Arial", 14, "bold"), bg='#f0f2f6', fg='#2c3e50').pack(anchor='w', pady=(0, 10))
            
            # Recherche
            search_frame = tk.Frame(ip_frame, bg='#f0f2f6')
            search_frame.pack(fill=tk.X, pady=(0, 10))
            
            tk.Label(search_frame, text="🔎 Rechercher:", 
                    font=("Arial", 10), bg='#f0f2f6').pack(side=tk.LEFT, padx=(0, 10))
            
            self.ip_search_var = tk.StringVar()
            self.ip_search_var.trace('w', lambda *args: self.filtrer_tableau_ip(ip_frame, df_ip))
            
            search_entry = tk.Entry(search_frame, textvariable=self.ip_search_var, 
                                   font=("Arial", 10), width=40)
            search_entry.pack(side=tk.LEFT)
            
            # Export
            btn_export = tk.Button(search_frame, text="📥 Exporter CSV", 
                                  command=lambda: self.exporter_csv(df_ip),
                                  font=("Arial", 10), bg='#27ae60', fg='white',
                                  padx=15, pady=5, cursor='hand2')
            btn_export.pack(side=tk.RIGHT)
            
            self.ip_table_frame = tk.Frame(ip_frame, bg='white')
            self.ip_table_frame.pack(fill=tk.BOTH, expand=True)
            
            self.creer_tableau(self.ip_table_frame, df_ip, hauteur=300)
        else:
            msg = tk.Label(scrollable_frame, 
                          text=f"ℹ️ Aucun fichier d'adresses IP trouvé pour le VLAN {self.vlan_selectionne}", 
                          font=("Arial", 11), bg='#fff3cd', fg='#856404',
                          padx=20, pady=15)
            msg.pack(fill=tk.X, pady=20)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def creer_carte_info(self, parent, titre, valeur, colonne):
        """Crée une carte d'information"""
        frame = tk.Frame(parent, bg='white', relief=tk.RAISED, borderwidth=1)
        frame.grid(row=0, column=colonne, padx=5, sticky='ew')
        parent.grid_columnconfigure(colonne, weight=1)
        
        titre_label = tk.Label(frame, text=titre, font=("Arial", 9), 
                              bg='white', fg='#7f8c8d')
        titre_label.pack(pady=(10, 2))
        
        valeur_label = tk.Label(frame, text=valeur, font=("Arial", 13, "bold"), 
                               bg='white', fg='#2c3e50')
        valeur_label.pack(pady=(0, 10))
    
    def filtrer_tableau_ip(self, parent, df_ip):
        """Filtre le tableau des IPs"""
        recherche = self.ip_search_var.get().lower()
        
        if recherche == "":
            df_filtre = df_ip
        else:
            df_filtre = df_ip[
                df_ip['IpAddress'].astype(str).str.contains(recherche, case=False, na=False) |
                df_ip['IpName'].astype(str).str.contains(recherche, case=False, na=False)
            ]
        
        for widget in self.ip_table_frame.winfo_children():
            widget.destroy()
        
        self.creer_tableau(self.ip_table_frame, df_filtre, hauteur=300)
    
    def exporter_csv(self, dataframe):
        """Exporte un DataFrame en CSV"""
        from tkinter import filedialog
        
        fichier = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"vlan_{self.vlan_selectionne}_export.csv"
        )
        
        if fichier:
            try:
                dataframe.to_csv(fichier, index=False, encoding='utf-8-sig')
                messagebox.showinfo("Succès", f"Fichier exporté:\n{fichier}")
            except Exception as e:
                messagebox.showerror("Erreur", f"Erreur lors de l'export:\n{e}")

def main():
    root = tk.Tk()
    app = VLANDashboard(root, dossier_donnees="./data")
    root.mainloop()

if __name__ == "__main__":
    main()
