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

    # -------------------------------------
    # OUTIL DE SÉCURITÉ POUR L'ACCÈS AUX COLONNES
    # -------------------------------------
    def safe_get(self, row, *possible_names, default="N/A"):
        """Renvoie la première colonne existante parmi les noms possibles."""
        for name in possible_names:
            if name in row:
                return row[name]
        return default

    # -------------------------------------
    # CHARGEMENT DES DONNÉES
    # -------------------------------------
    def charger_toutes_donnees(self):
        if not os.path.exists(self.dossier_donnees):
            messagebox.showerror("Erreur", f"Le dossier '{self.dossier_donnees}' n'existe pas!\n\nCréez un dossier 'data' et placez-y vos fichiers CSV.")
            return False
        
        fichiers = list(Path(self.dossier_donnees).glob("*.csv"))
        
        if not fichiers:
            messagebox.showerror("Erreur", f"Aucun fichier CSV trouvé dans '{self.dossier_donnees}'")
            return False
        
        fichiers_vlan = [f for f in fichiers if "CapaVLAN" in f.name]
        if not fichiers_vlan:
            messagebox.showerror("Erreur", "Aucun fichier de type '*_CapaVLAN.csv' trouvé!")
            return False
        
        fichier_vlan = sorted(fichiers_vlan, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        try:
            self.df_vlan = pd.read_csv(fichier_vlan)
            self.df_vlan.columns = self.df_vlan.columns.str.strip()
            print(f"✓ Fichier VLAN chargé: {fichier_vlan.name}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Erreur lors du chargement du fichier VLAN:\n{e}")
            return False
        
        fichiers_ip = [f for f in fichiers if "CapaVLAN" not in f.name]
        for fichier in fichiers_ip:
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

    # -------------------------------------
    # INTERFACE GRAPHIQUE
    # -------------------------------------
    def creer_interface(self):
        menu_frame = tk.Frame(self.root, bg='#2c3e50', height=60)
        menu_frame.pack(fill=tk.X, side=tk.TOP)
        
        titre = tk.Label(menu_frame, text="🌐 Dashboard VLAN", 
                        font=("Arial", 20, "bold"), bg='#2c3e50', fg='white')
        titre.pack(side=tk.LEFT, padx=20, pady=10)
        
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
        
        self.main_frame = tk.Frame(self.root, bg='#f0f2f6')
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        if self.df_vlan is not None:
            self.afficher_vue_globale()
        else:
            self.afficher_erreur_chargement()

    def afficher_erreur_chargement(self):
        self.nettoyer_frame()
        msg = tk.Label(self.main_frame, 
                      text="❌ Erreur de chargement des données\n\nAssurez-vous d'avoir un dossier 'data' avec vos fichiers CSV",
                      font=("Arial", 14), bg='#f0f2f6', fg='#e74c3c')
        msg.pack(pady=100)

    def nettoyer_frame(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()

    # -------------------------------------
    # MÉTRIQUES GLOBALES
    # -------------------------------------
    def calculer_metriques_globales(self):
        total_vlans = len(self.df_vlan)
        col_used = next((c for c in self.df_vlan.columns if 'Used Size' in c), None)
        col_free = next((c for c in self.df_vlan.columns if 'free size' in c.lower()), None)
        col_percent = next((c for c in self.df_vlan.columns if 'Used percent' in c), None)
        
        total_ips_utilisees = self.df_vlan[col_used].sum() if col_used else 0
        total_ips_libres = self.df_vlan[col_free].sum() if col_free else 0
        utilisation_moyenne = self.df_vlan[col_percent].mean() if col_percent else 0
        
        return {
            'total_vlans': total_vlans,
            'total_ips_utilisees': int(total_ips_utilisees),
            'total_ips_libres': int(total_ips_libres),
            'utilisation_moyenne': round(utilisation_moyenne, 2)
        }

    # -------------------------------------
    # VUE DÉTAILLÉE D’UN VLAN
    # -------------------------------------
    def afficher_vue_detail(self):
        if self.vlan_selectionne is None:
            self.nettoyer_frame()
            self.btn_globale.config(bg='#95a5a6')
            self.btn_detail.config(bg='#3498db')
            
            titre = tk.Label(self.main_frame, text="🔍 Sélectionner un VLAN", 
                           font=("Arial", 18, "bold"), bg='#f0f2f6', fg='#2c3e50')
            titre.pack(pady=(20, 10))
            
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
        
        info_vlan = self.df_vlan[self.df_vlan['Vlan Id'] == self.vlan_selectionne].iloc[0]
        
        canvas = tk.Canvas(self.main_frame, bg='#f0f2f6', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg='#f0f2f6')
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        header = tk.Frame(scrollable_frame, bg='#f0f2f6')
        header.pack(fill=tk.X, pady=(0, 20))
        
        titre = tk.Label(header, text=f"🌐 VLAN {self.vlan_selectionne} - {self.safe_get(info_vlan, 'Name')}", 
                        font=("Arial", 18, "bold"), bg='#f0f2f6', fg='#2c3e50')
        titre.pack(side=tk.LEFT)
        
        btn_retour = tk.Button(header, text="← Retour", 
                              command=lambda: setattr(self, 'vlan_selectionne', None) or self.afficher_vue_detail(),
                              font=("Arial", 10), bg='#95a5a6', fg='white',
                              padx=15, pady=5, cursor='hand2')
        btn_retour.pack(side=tk.RIGHT)
        
        metriques_frame = tk.Frame(scrollable_frame, bg='#f0f2f6')
        metriques_frame.pack(fill=tk.X, pady=10)
        
        infos = [
            ("Subnet", self.safe_get(info_vlan, 'Subnet Name', 'Subnet')),
            ("Zone", self.safe_get(info_vlan, 'Zone')),
            ("Masque", self.safe_get(info_vlan, 'Subnet Mask')),
            ("Utilisation", f"{self.safe_get(info_vlan, 'Subnet Ip Used percent', default=0)}%")
        ]
        
        for i, (label, value) in enumerate(infos):
            self.creer_carte_info(metriques_frame, label, value, i)
        
        stats_frame = tk.Frame(scrollable_frame, bg='white', relief=tk.RAISED, borderwidth=1)
        stats_frame.pack(fill=tk.X, pady=20, padx=10)
        
        tk.Label(stats_frame, text="📈 Statistiques du sous-réseau", 
                font=("Arial", 14, "bold"), bg='white', fg='#2c3e50').pack(pady=15)
        
        stats_grid = tk.Frame(stats_frame, bg='white')
        stats_grid.pack(padx=20, pady=(0, 15))
        
        statistiques = [
            ("Taille totale", f"{self.safe_get(info_vlan, 'Subnet Size', default=0):,}"),
            ("IPs Utilisées", f"{self.safe_get(info_vlan, 'Subnet Ip Used Size', default=0):,}"),
            ("IPs Libres", f"{self.safe_get(info_vlan, 'Subnet Ip free size', default=0):,}"),
            ("Pool Size", f"{self.safe_get(info_vlan, 'Subnet Pool Size', default=0):,}"),
            ("Plage IP", f"{self.safe_get(info_vlan, 'Ip Start', 'Up Start')} - {self.safe_get(info_vlan, 'Ip End', 'Down End')}"),
            ("IPs Réservées", f"{self.safe_get(info_vlan, 'Subnet Ip reserved Size', default=0):,}")
        ]
        
        for i, (label, value) in enumerate(statistiques):
            row, col = divmod(i, 2)
            stat_frame = tk.Frame(stats_grid, bg='white')
            stat_frame.grid(row=row, column=col, padx=30, pady=10, sticky='w')
            tk.Label(stat_frame, text=f"{label}:", font=("Arial", 10, "bold"), bg='white', fg='#7f8c8d').pack(anchor='w')
            tk.Label(stat_frame, text=value, font=("Arial", 12), bg='white', fg='#2c3e50').pack(anchor='w')
        
        # Liste des IPs
        if self.vlan_selectionne in self.fichiers_ip:
            df_ip = self.fichiers_ip[self.vlan_selectionne]
            ip_frame = tk.Frame(scrollable_frame, bg='#f0f2f6')
            ip_frame.pack(fill=tk.BOTH, expand=True, pady=20)
            
            tk.Label(ip_frame, text="📋 Liste des adresses IP", 
                    font=("Arial", 14, "bold"), bg='#f0f2f6', fg='#2c3e50').pack(anchor='w', pady=(0, 10))
            
            search_frame = tk.Frame(ip_frame, bg='#f0f2f6')
            search_frame.pack(fill=tk.X, pady=(0, 10))
            
            tk.Label(search_frame, text="🔎 Rechercher:", font=("Arial", 10), bg='#f0f2f6').pack(side=tk.LEFT, padx=(0, 10))
            
            self.ip_search_var = tk.StringVar()
            self.ip_search_var.trace('w', lambda *args: self.filtrer_tableau_ip(ip_frame, df_ip))
            
            search_entry = tk.Entry(search_frame, textvariable=self.ip_search_var, font=("Arial", 10), width=40)
            search_entry.pack(side=tk.LEFT)
            
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

    # -------------------------------------
    # AUTRES MÉTHODES (tableaux, filtres, export)
    # -------------------------------------
    def creer_carte_info(self, parent, titre, valeur, colonne):
        frame = tk.Frame(parent, bg='white', relief=tk.RAISED, borderwidth=1)
        frame.grid(row=0, column=colonne, padx=5, sticky='ew')
        parent.grid_columnconfigure(colonne, weight=1)
        tk.Label(frame, text=titre, font=("Arial", 9), bg='white', fg='#7f8c8d').pack(pady=(10, 2))
        tk.Label(frame, text=valeur, font=("Arial", 13, "bold"), bg='white', fg='#2c3e50').pack(pady=(0, 10))

    def creer_tableau(self, parent, dataframe, hauteur=200, click_handler=None):
        tree_scroll_y = ttk.Scrollbar(parent, orient="vertical")
        tree_scroll_x = ttk.Scrollbar(parent, orient="horizontal")
        columns = list(dataframe.columns)
        tree = ttk.Treeview(parent, columns=columns, show='headings',
                           yscrollcommand=tree_scroll_y.set,
                           xscrollcommand=tree_scroll_x.set,
                           height=hauteur//25)
        tree_scroll_y.config(command=tree.yview)
        tree_scroll_x.config(command=tree.xview)
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=120, anchor='center')
        for _, row in dataframe.iterrows():
            tree.insert('', 'end', values=[str(row[c]) for c in columns])
        if click_handler:
            tree.bind('<Double-1>', click_handler)
        tree.pack(side='left', fill='both', expand=True)
        tree_scroll_y.pack(side='right', fill='y')
        tree_scroll_x.pack(side='bottom', fill='x')
        return tree

    def filtrer_tableau_ip(self, parent, df_ip):
        recherche = self.ip_search_var.get().lower()
        df_filtre = df_ip if not recherche else df_ip[
            df_ip['IpAddress'].astype(str).str.contains(recherche, case=False, na=False) |
            df_ip['IpName'].astype(str).str.contains(recherche, case=False, na=False)
        ]
        for w in self.ip_table_frame.winfo_children():
            w.destroy()
        self.creer_tableau(self.ip_table_frame, df_filtre, hauteur=300)

    def exporter_csv(self, dataframe):
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
