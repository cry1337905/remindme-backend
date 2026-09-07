import datetime
import json
import os
from pathlib import Path
from tkinter import messagebox
import urllib.parse
import webbrowser

import customtkinter as ctk
import requests
from tkinterdnd2 import DND_FILES, TkinterDnD

# ---------------------------------------------------------------------------
# KONFIGURATION & GLOBALER STATUS
# ---------------------------------------------------------------------------
API_URL = "https://remindme-backend1.onrender.com"

APP_DIR = Path.home() / ".remind_me_app"
APP_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = APP_DIR / "app_data.json"

CURRENT_TOKEN = None
CURRENT_USER = None  # Speichert die E-Mail-Adresse des angemeldeten Benutzers

LOCAL_TASK_STATUS = {}
LOCAL_COMMENTS = {}

# ---------------------------------------------------------------------------
# PERSISTENZ (SPEICHERN & LADEN)
# ---------------------------------------------------------------------------
def load_local_data():
    global LOCAL_TASK_STATUS
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                LOCAL_TASK_STATUS = data.get("statuses", {})
        except Exception as e:
            print(f"Fehler beim Laden lokaler Daten: {e}")

def save_local_data():
    try:
        data = {
            "statuses": LOCAL_TASK_STATUS
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Fehler beim Speichern lokaler Daten: {e}")

load_local_data()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# API CLIENT FUNKTIONEN
# ---------------------------------------------------------------------------
def extract_error_detail(response):
    try:
        res_json = response.json()
        if isinstance(res_json.get("detail"), str):
            return res_json["detail"]
        elif isinstance(res_json.get("detail"), list):
            err = res_json["detail"][0]
            field = err.get("loc", [""])[-1]
            msg = err.get("msg", "Ungültige Eingabe")
            return f"Feld '{field}': {msg}"
        return str(res_json)
    except Exception:
        return response.text or "Unbekannter Fehler"

def api_get_groups():
    if not CURRENT_TOKEN:
        return {}
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    try:
        response = requests.get(f"{API_URL}/groups", headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
    except requests.exceptions.RequestException:
        pass
    return {}

def api_save_group(name, members):
    if not CURRENT_TOKEN:
        return False, "Nicht eingeloggt."
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    payload = {"name": name, "members": members}
    try:
        response = requests.post(f"{API_URL}/groups", json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            return True, f"Gruppe '{name}' zentral gespeichert!"
        return False, f"Server-Fehler: {response.text}"
    except requests.exceptions.RequestException as e:
        return False, f"Netzwerkfehler: {e}"

def api_delete_group(name):
    if not CURRENT_TOKEN:
        return False, "Nicht eingeloggt."
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    encoded_name = urllib.parse.quote(name)

    attempts = [
        ("DELETE", f"{API_URL}/groups/{encoded_name}", {}, None),
        ("DELETE", f"{API_URL}/groups", {"name": name}, None),
        ("DELETE", f"{API_URL}/groups", {}, {"name": name}),
        ("POST", f"{API_URL}/groups/delete", {}, {"name": name}),
        ("POST", f"{API_URL}/groups/{encoded_name}/delete", {}, None),
    ]

    failed_paths = []

    for method, url, params, json_data in attempts:
        try:
            res = requests.request(
                method, url, headers=headers, params=params, json=json_data, timeout=10
            )
            if res.status_code in [200, 204]:
                return True, f"Gruppe '{name}' wurde gelöscht."
            else:
                failed_paths.append(f"{method} {url.replace(API_URL, '')} -> Status {res.status_code}")
        except requests.exceptions.RequestException:
            continue

    details = "\n".join(failed_paths)
    return False, f"Gruppe konnte nicht gelöscht werden.\nGetestete Endpunkte:\n{details}"

def api_register(payload):
    try:
        response = requests.post(
            f"{API_URL}/register",
            json=payload,
            timeout=60,
        )
        if response.status_code == 200:
            data = response.json()
            code_msg = f"\nDein Firmen-Code lautet: {data.get('company_code')}" if data.get("company_code") else ""
            return True, f"Registrierung erfolgreich!{code_msg}"
        else:
            return False, extract_error_detail(response)
    except requests.exceptions.Timeout:
        return False, "Zeitüberschreitung: Server braucht Zeit zum Hochfahren. Bitte erneut versuchen."
    except requests.exceptions.RequestException as e:
        return False, f"Netzwerkfehler: {e}"

def api_login(email, password):
    global CURRENT_TOKEN, CURRENT_USER
    clean_email = email.strip()
    try:
        response = requests.post(
            f"{API_URL}/login",
            json={"email": clean_email, "password": password},
            timeout=60,
        )
        if response.status_code == 200:
            data = response.json()
            CURRENT_TOKEN = data.get("access_token")
            CURRENT_USER = clean_email
            return True, "Login erfolgreich!"
        else:
            return False, extract_error_detail(response)
    except requests.exceptions.Timeout:
        return False, "Zeitüberschreitung: Server braucht Zeit zum Hochfahren. Bitte erneut versuchen."
    except requests.exceptions.RequestException as e:
        return False, f"Netzwerkfehler: {e}"

def api_get_tasks():
    if not CURRENT_TOKEN:
        return []
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    try:
        response = requests.get(f"{API_URL}/tasks", headers=headers, timeout=15)
        if response.status_code == 200:
            tasks = response.json()
            for t in tasks:
                t_id = str(t.get("id"))
                if t_id in LOCAL_TASK_STATUS:
                    t["status"] = LOCAL_TASK_STATUS[t_id]
            return tasks
        return []
    except requests.exceptions.RequestException:
        return []

def api_create_task(title, description, assignee, deadline):
    if not CURRENT_TOKEN:
        return False, "Nicht eingeloggt."
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    
    created_at_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    
    payload = {
        "title": title.strip(),
        "description": f"__CREATED_AT__:{created_at_str}\n" + description.strip(),
        "assignee": assignee.strip(),
        "deadline": deadline.strip(),
    }
    try:
        response = requests.post(
            f"{API_URL}/tasks", json=payload, headers=headers, timeout=15
        )
        if response.status_code == 200:
            return True, "Aufgabe erfolgreich erstellt!"
        return False, extract_error_detail(response)
    except requests.exceptions.RequestException as e:
        return False, f"Netzwerkfehler: {e}"

def api_update_task_status(task_id, status):
    str_id = str(task_id)
    LOCAL_TASK_STATUS[str_id] = status
    save_local_data()

    if not CURRENT_TOKEN:
        return True, "Status lokal aktualisiert."

    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    endpoints = [
        ("PATCH", f"{API_URL}/tasks/{str_id}/status", {"json": {"status": status}}),
        ("PUT", f"{API_URL}/tasks/{str_id}", {"json": {"status": status}}),
    ]

    for method, url, kwargs in endpoints:
        try:
            requests.request(method, url, headers=headers, timeout=5, **kwargs)
        except requests.exceptions.RequestException:
            pass

    return True, "Status aktualisiert."

def api_delete_task(task_id):
    if not CURRENT_TOKEN:
        return False, "Nicht eingeloggt."
    str_id = str(task_id)
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    try:
        response = requests.delete(
            f"{API_URL}/tasks/{str_id}", headers=headers, timeout=15
        )
        if response.status_code == 200:
            if str_id in LOCAL_TASK_STATUS:
                del LOCAL_TASK_STATUS[str_id]
                save_local_data()
            if str_id in LOCAL_COMMENTS:
                del LOCAL_COMMENTS[str_id]
            return True, "Aufgabe gelöscht."
        return False, extract_error_detail(response)
    except requests.exceptions.RequestException as e:
        return False, f"Netzwerkfehler: {e}"

def api_get_comments(task_id):
    str_id = str(task_id)
    if CURRENT_TOKEN:
        headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
        try:
            response = requests.get(f"{API_URL}/tasks/{str_id}/comments", headers=headers, timeout=5)
            if response.status_code == 200 and isinstance(response.json(), list):
                raw_list = response.json()
                for item in raw_list:
                    if isinstance(item, dict) and item.get("message", "").startswith("__TIME__:"):
                        parts = item["message"].split("\n", 1)
                        item["timestamp"] = parts[0].replace("__TIME__:", "").strip()
                        item["message"] = parts[1] if len(parts) > 1 else ""
                return raw_list
        except requests.exceptions.RequestException:
            pass

    return LOCAL_COMMENTS.get(str_id, [])

def api_add_comment(task_id, message, author=None):
    str_id = str(task_id)
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    sender = author or CURRENT_USER or "System"

    payload_message = f"__TIME__:{now_str}\n{message}"

    comment_obj = {
        "author": sender,
        "message": message,
        "timestamp": now_str,
    }

    if CURRENT_TOKEN:
        headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
        try:
            requests.post(
                f"{API_URL}/tasks/{str_id}/comments",
                json={"message": payload_message},
                headers=headers,
                timeout=5,
            )
            return True, "Kommentar hinzugefügt."
        except requests.exceptions.RequestException:
            pass

    if str_id not in LOCAL_COMMENTS:
        LOCAL_COMMENTS[str_id] = []
    LOCAL_COMMENTS[str_id].append(comment_obj)
    return True, "Kommentar lokal hinzugefügt."

def api_upload_file(task_id, file_path):
    if not CURRENT_TOKEN:
        return False, "Nicht eingeloggt."
    
    headers = {"Authorization": f"Bearer {CURRENT_TOKEN}"}
    
    try:
        with open(file_path, "rb") as f:
            files = {"file": f}
            response = requests.post(
                f"{API_URL}/tasks/{task_id}/upload",
                headers=headers,
                files=files,
                timeout=60
            )
            if response.status_code == 200:
                return True, "Datei erfolgreich hochgeladen."
            return False, extract_error_detail(response)
    except Exception as e:
        return False, f"Fehler beim Upload: {e}"


# ---------------------------------------------------------------------------
# POPUP-DIALOG: PASSWORT VERGESSEN
# ---------------------------------------------------------------------------
class ForgotPasswordWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Passwort zurücksetzen")
        self.geometry("420x500")
        self.resizable(False, False)

        self.transient(parent)
        self.grab_set()

        self.step = 1

        self.label_title = ctk.CTkLabel(
            self, text="Passwort vergessen", font=ctk.CTkFont(size=20, weight="bold")
        )
        self.label_title.pack(pady=(20, 10))

        self.label_info = ctk.CTkLabel(
            self,
            text="Gib deine registrierte E-Mail-Adresse ein.\nWir senden dir einen 6-stelligen Code.",
            wraplength=360,
            justify="center",
        )
        self.label_info.pack(pady=(0, 15))

        self.entry_email = ctk.CTkEntry(self, placeholder_text="E-Mail-Adresse", width=320)
        self.entry_email.pack(pady=10)

        self.entry_code = ctk.CTkEntry(self, placeholder_text="6-stelliger Code", width=320)
        self.entry_new_password = ctk.CTkEntry(
            self, placeholder_text="Neues Passwort", show="*", width=320
        )

        self.label_status = ctk.CTkLabel(self, text="", text_color="red", wraplength=360)
        self.label_status.pack(pady=5)

        self.btn_action = ctk.CTkButton(
            self, text="Code anfordern", width=320, command=self.handle_action
        )
        self.btn_action.pack(pady=15)

    def handle_action(self):
        self.label_status.configure(text="", text_color="red")

        if self.step == 1:
            email = self.entry_email.get().strip()
            if not email:
                self.label_status.configure(text="Bitte E-Mail-Adresse eingeben.")
                return

            self.btn_action.configure(state="disabled", text="Sende Anfrage...")

            try:
                response = requests.post(
                    f"{API_URL}/forgot-password", json={"email": email}, timeout=60
                )
                if response.status_code == 200:
                    self.step = 2
                    self.label_info.configure(
                        text=f"Ein Code wurde an {email} gesendet.\n(Prüfe dein E-Mail-Postfach oder das Render-Log!)"
                    )
                    self.entry_email.configure(state="disabled")
                    self.entry_code.pack(pady=10)
                    self.entry_new_password.pack(pady=10)
                    self.btn_action.configure(state="normal", text="Passwort zurücksetzen")
                else:
                    self.label_status.configure(
                        text=f"Fehler {response.status_code}: {extract_error_detail(response)}"
                    )
                    self.btn_action.configure(state="normal", text="Code anfordern")
            except requests.exceptions.Timeout:
                self.label_status.configure(
                    text="Zeitüberschreitung: Server wacht auf. Bitte erneut versuchen."
                )
                self.btn_action.configure(state="normal", text="Code anfordern")
            except Exception as e:
                self.label_status.configure(text=f"Verbindungsfehler: {str(e)}")
                self.btn_action.configure(state="normal", text="Code anfordern")

        elif self.step == 2:
            email = self.entry_email.get().strip()
            code = self.entry_code.get().strip()
            new_password = self.entry_new_password.get().strip()

            if not code or not new_password:
                self.label_status.configure(text="Bitte Code und neues Passwort eingeben.")
                return

            self.btn_action.configure(state="disabled", text="Setze zurück...")

            try:
                response = requests.post(
                    f"{API_URL}/reset-password",
                    json={"email": email, "code": code, "new_password": new_password},
                    timeout=60,
                )
                if response.status_code == 200:
                    messagebox.showinfo(
                        "Erfolg",
                        "Dein Passwort wurde erfolgreich zurückgesetzt!\nDu kannst dich jetzt anmelden.",
                    )
                    self.destroy()
                else:
                    self.label_status.configure(
                        text=f"Fehler {response.status_code}: {extract_error_detail(response)}"
                    )
                    self.btn_action.configure(state="normal", text="Passwort zurücksetzen")
            except Exception as e:
                self.label_status.configure(text=f"Verbindungsfehler: {str(e)}")
                self.btn_action.configure(state="normal", text="Passwort zurücksetzen")


# ---------------------------------------------------------------------------
# LOGIN & REGISTRIERUNG FENSTER
# ---------------------------------------------------------------------------
class LoginWindow(ctk.CTk):

    def __init__(self, on_login_success):
        super().__init__()
        self.on_login_success = on_login_success
        self.title("Anmeldung - Remind Me =)")
        self.geometry("420x580")
        self.resizable(False, False)

        self.build_login_ui()

    def build_login_ui(self):
        for widget in self.winfo_children():
            widget.destroy()

        ctk.CTkLabel(
            self,
            text="Willkommen bei Remind Me =)",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(pady=(25, 5))

        ctk.CTkLabel(self, text="Bitte melde dich mit deiner E-Mail an:").pack(pady=(0, 15))

        self.entry_email = ctk.CTkEntry(self, placeholder_text="E-Mail-Adresse", width=300)
        self.entry_email.pack(pady=8)
        self.entry_email.focus()

        self.entry_pass = ctk.CTkEntry(self, placeholder_text="Passwort", show="*", width=300)
        self.entry_pass.pack(pady=8)
        self.entry_pass.bind("<Return>", lambda e: self.handle_login())

        btn_login = ctk.CTkButton(self, text="Anmelden", width=300, command=self.handle_login)
        btn_login.pack(pady=(15, 5))

        btn_forgot = ctk.CTkButton(
            self,
            text="Passwort vergessen?",
            fg_color="transparent",
            text_color=("gray10", "gray80"),
            hover_color=("gray70", "gray30"),
            command=self.open_forgot_password,
        )
        btn_forgot.pack(pady=(0, 10))

        btn_register = ctk.CTkButton(
            self,
            text="Neues Konto erstellen",
            width=300,
            fg_color="#333333",
            hover_color="#444444",
            command=self.build_register_ui,
        )
        btn_register.pack(pady=5)

    def build_register_ui(self):
        for widget in self.winfo_children():
            widget.destroy()

        ctk.CTkLabel(
            self,
            text="Konto erstellen",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(pady=(20, 10))

        self.entry_reg_email = ctk.CTkEntry(self, placeholder_text="E-Mail-Adresse", width=300)
        self.entry_reg_email.pack(pady=6)

        self.entry_reg_username = ctk.CTkEntry(
            self, placeholder_text="Anzeigename (z. B. Thomas)", width=300
        )
        self.entry_reg_username.pack(pady=6)

        self.entry_reg_pass = ctk.CTkEntry(
            self, placeholder_text="Passwort", show="*", width=300
        )
        self.entry_reg_pass.pack(pady=6)

        self.mode_var = ctk.StringVar(value="create")

        radio_frame = ctk.CTkFrame(self, fg_color="transparent")
        radio_frame.pack(pady=6)

        ctk.CTkRadioButton(
            radio_frame, text="Firma gründen", variable=self.mode_var, value="create", command=self.toggle_company_mode
        ).pack(side="left", padx=10)
        ctk.CTkRadioButton(
            radio_frame, text="Firma beitreten", variable=self.mode_var, value="join", command=self.toggle_company_mode
        ).pack(side="left", padx=10)

        self.entry_company_name = ctk.CTkEntry(self, placeholder_text="Neuer Firmenname", width=300)
        self.entry_company_name.pack(pady=6)

        self.entry_company_code = ctk.CTkEntry(self, placeholder_text="Bestehender Firmen-Code (z.B. COMP-1234)", width=300)

        btn_reg = ctk.CTkButton(
            self, text="Registrieren", width=300, command=self.handle_register
        )
        btn_reg.pack(pady=(15, 8))

        btn_back = ctk.CTkButton(
            self,
            text="Zurück zum Login",
            width=300,
            fg_color="transparent",
            border_width=1,
            command=self.build_login_ui,
        )
        btn_back.pack(pady=5)

    def toggle_company_mode(self):
        if self.mode_var.get() == "create":
            self.entry_company_code.pack_forget()
            self.entry_company_name.pack(pady=6)
        else:
            self.entry_company_name.pack_forget()
            self.entry_company_code.pack(pady=6)

    def handle_login(self):
        email = self.entry_email.get().strip()
        pw = self.entry_pass.get().strip()
        if not email or not pw:
            messagebox.showwarning("Eingabefehler", "Bitte E-Mail-Adresse und Passwort eingeben.")
            return

        success, msg = api_login(email, pw)
        if success:
            self.destroy()
            self.on_login_success()
        else:
            messagebox.showerror("Anmeldung fehlgeschlagen", msg)

    def handle_register(self):
        email = self.entry_reg_email.get().strip()
        username = self.entry_reg_username.get().strip()
        pw = self.entry_reg_pass.get().strip()

        if not email or not username or not pw:
            messagebox.showwarning("Eingabefehler", "Bitte alle Pflichtfelder ausfüllen.")
            return

        payload = {
            "email": email,
            "username": username,
            "password": pw,
        }

        if self.mode_var.get() == "create":
            c_name = self.entry_company_name.get().strip()
            if not c_name:
                messagebox.showwarning("Eingabefehler", "Bitte gib einen Firmennamen ein.")
                return
            payload["company_name"] = c_name
        else:
            c_code = self.entry_company_code.get().strip()
            if not c_code:
                messagebox.showwarning("Eingabefehler", "Bitte gib einen Firmen-Code ein.")
                return
            payload["company_code"] = c_code

        success, msg = api_register(payload)
        if success:
            messagebox.showinfo("Erfolg", msg)
            self.build_login_ui()
        else:
            messagebox.showerror("Registrierung fehlgeschlagen", msg)

    def open_forgot_password(self):
        ForgotPasswordWindow(self)


# ---------------------------------------------------------------------------
# HAUPTANWENDUNG (MIT DRAG & DROP SUPPORT)
# ---------------------------------------------------------------------------
class RemindMeApp(ctk.CTk, TkinterDnD.DnDWrapper):

    def __init__(self):
        super().__init__()
        self.dnd_start()  # Startet die Drag & Drop Engine von tkinterdnd2

        self.title("Remind Me =) | Task Management")
        self.geometry("1150x750")

        self.selected_task = None
        self.timer_id = None
        self.last_color = None
        self.list_widgets = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self.create_left_panel()
        self.create_right_panel()

        self.load_task_list()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        self.cancel_timer()
        self.destroy()

    def cancel_timer(self):
        if self.timer_id:
            self.after_cancel(self.timer_id)
            self.timer_id = None

    def create_left_panel(self):
        left_frame = ctk.CTkFrame(self, corner_radius=15)
        left_frame.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        left_frame.grid_rowconfigure(5, weight=1)

        title_label = ctk.CTkLabel(
            left_frame,
            text="Remind Me =)",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_label.grid(row=0, column=0, padx=15, pady=(15, 2), sticky="w")

        user_label = ctk.CTkLabel(
            left_frame,
            text=f"Angemeldet als:\n{CURRENT_USER}",
            font=ctk.CTkFont(size=12),
            text_color="#1fa4e0",
            justify="left",
        )
        user_label.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        add_btn = ctk.CTkButton(
            left_frame, text="+ Neue Aufgabe", command=self.open_add_task_dialog
        )
        add_btn.grid(row=2, column=0, padx=15, pady=2, sticky="ew")

        group_btn = ctk.CTkButton(
            left_frame,
            text="👥 Gruppen verwalten",
            fg_color="#4a5568",
            hover_color="#2d3748",
            command=self.open_group_manager,
        )
        group_btn.grid(row=3, column=0, padx=15, pady=2, sticky="ew")

        logout_btn = ctk.CTkButton(
            left_frame,
            text="🚪 Abmelden",
            fg_color="#8b0000",
            hover_color="#550000",
            command=self.logout,
        )
        logout_btn.grid(row=4, column=0, padx=15, pady=5, sticky="ew")

        self.task_list_scroll = ctk.CTkScrollableFrame(
            left_frame, label_text="Aktuelle Aufgaben"
        )
        self.task_list_scroll.grid(
            row=5, column=0, padx=15, pady=(5, 15), sticky="nsew"
        )

    def logout(self):
        global CURRENT_TOKEN, CURRENT_USER
        CURRENT_TOKEN = None
        CURRENT_USER = None
        self.cancel_timer()
        self.destroy()
        start_app()

    def create_right_panel(self):
        self.right_frame = ctk.CTkFrame(self, corner_radius=15)
        self.right_frame.grid(
            row=0, column=1, padx=(0, 15), pady=15, sticky="nsew"
        )

        self.placeholder_label = ctk.CTkLabel(
            self.right_frame,
            text="Wähle eine Aufgabe aus der Liste links aus.",
            font=ctk.CTkFont(size=16),
        )
        self.placeholder_label.pack(expand=True)

    def reset_right_panel(self):
        self.cancel_timer()
        for widget in self.right_frame.winfo_children():
            widget.destroy()
        self.placeholder_label = ctk.CTkLabel(
            self.right_frame,
            text="Wähle eine Aufgabe aus der Liste links aus.",
            font=ctk.CTkFont(size=16),
        )
        self.placeholder_label.pack(expand=True)

    def format_time_difference(self, total_seconds):
        seconds = abs(int(total_seconds))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)

        if days > 0:
            return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def get_ampel_color(self, deadline_str, status="Offen"):
        if status == "Abgeschlossen":
            return "#6c757d", "Erfolgreich Erledigt (Gestoppt)"
        if status == "Fehlgeschlagen":
            return "#8b0000", "Nicht erfolgreich (Gestoppt)"

        status_prefix = " (In Bearbeitung)" if status == "In Bearbeitung" else ""

        try:
            deadline = datetime.datetime.strptime(
                deadline_str, "%d.%m.%Y %H:%M"
            )
            now = datetime.datetime.now()
            time_left = deadline - now
            seconds_left = time_left.total_seconds()

            if seconds_left < 0:
                elapsed_str = self.format_time_difference(seconds_left)
                return "#dc3545", f"Überfällig (+{elapsed_str}){status_prefix}"
            elif seconds_left <= 18000:
                countdown_str = self.format_time_difference(seconds_left)
                return "#ffc107", f"Demnächst fällig ({countdown_str}){status_prefix}"
            else:
                countdown_str = self.format_time_difference(seconds_left)
                return "#198754", f"Offen ({countdown_str}){status_prefix}"
        except ValueError:
            return "#6c757d", f"Keine Frist / Unbekannt{status_prefix}"

    def update_timer(self, deadline_str, status):
        color, ampel_text = self.get_ampel_color(deadline_str, status)

        if hasattr(self, "ampel_badge") and self.ampel_badge.winfo_exists():
            self.ampel_badge.configure(
                text=f" Ampel: {ampel_text} ",
                fg_color=color,
                text_color="white" if color != "#ffc107" else "black",
            )

        self.update_all_list_indicators()

        if status not in ["Abgeschlossen", "Fehlgeschlagen"]:
            self.timer_id = self.after(
                1000, lambda: self.update_timer(deadline_str, status)
            )

    def update_all_list_indicators(self):
        for t_id, data in self.list_widgets.items():
            indicator = data.get("indicator")
            sub_label = data.get("sub_label")
            deadline = data.get("deadline")
            assignee = data.get("assignee")
            status = LOCAL_TASK_STATUS.get(str(t_id), data.get("status", "Offen"))

            color, ampel_text = self.get_ampel_color(deadline, status)

            if indicator and indicator.winfo_exists():
                indicator.configure(fg_color=color)

            if sub_label and sub_label.winfo_exists():
                sub_label.configure(text=f"An: {assignee} | {ampel_text}")

    def load_task_list(self):
        for widget in self.task_list_scroll.winfo_children():
            widget.destroy()

        self.list_widgets.clear()
        tasks = api_get_tasks()

        for task in tasks:
            t_id = str(task.get("id"))
            title = task.get("title", "")
            deadline = task.get("deadline", "")
            assignee = task.get("assignee", "")
            status = LOCAL_TASK_STATUS.get(t_id, task.get("status", "Offen"))

            color, ampel_text = self.get_ampel_color(deadline, status)

            card = ctk.CTkFrame(self.task_list_scroll, corner_radius=10)
            card.pack(fill="x", padx=5, pady=5)

            status_indicator = ctk.CTkFrame(
                card, width=14, height=14, corner_radius=7, fg_color=color
            )
            status_indicator.pack(side="left", padx=10, pady=10)

            info_frame = ctk.CTkFrame(card, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, pady=5)

            lbl_title = ctk.CTkLabel(
                info_frame,
                text=title,
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
            )
            lbl_title.pack(fill="x")

            lbl_sub = ctk.CTkLabel(
                info_frame,
                text=f"An: {assignee} | {ampel_text}",
                font=ctk.CTkFont(size=11),
                text_color="gray",
                anchor="w",
            )
            lbl_sub.pack(fill="x")

            self.list_widgets[t_id] = {
                "indicator": status_indicator,
                "sub_label": lbl_sub,
                "deadline": deadline,
                "assignee": assignee,
                "status": status,
            }

            for widget in (card, info_frame, lbl_title, lbl_sub):
                widget.bind(
                    "<Button-1>",
                    lambda e, task_data=task: self.select_task(task_data),
                )

    def select_task(self, task):
        self.cancel_timer()
        self.selected_task = task

        t_id = str(task.get("id"))
        title = task.get("title", "")
        raw_desc = task.get("description", "")
        created_by = task.get("created_by", "Unbekannt")
        assignee = task.get("assignee", "")
        deadline = task.get("deadline", "")
        status = LOCAL_TASK_STATUS.get(t_id, task.get("status", "Offen"))

        created_at_time = ""
        clean_desc = raw_desc
        if raw_desc.startswith("__CREATED_AT__:"):
            parts = raw_desc.split("\n", 1)
            created_at_time = parts[0].replace("__CREATED_AT__:", "").strip()
            clean_desc = parts[1] if len(parts) > 1 else ""

        for widget in self.right_frame.winfo_children():
            widget.destroy()

        color, ampel_text = self.get_ampel_color(deadline, status)
        self.last_color = color

        header = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(fill="x")

        ctk.CTkLabel(
            title_frame, text=title, font=ctk.CTkFont(size=20, weight="bold")
        ).pack(side="left", anchor="w")

        delete_btn = ctk.CTkButton(
            title_frame,
            text="🗑️ Löschen",
            fg_color="#8b0000",
            hover_color="#550000",
            width=100,
            command=lambda: self.delete_current_task(t_id, created_by, title),
        )
        delete_btn.pack(side="right")

        created_info = f"{created_by} ({created_at_time})" if created_at_time else created_by
        meta_info = f"Erstellt von: {created_info} | Empfänger: {assignee} | Frist: {deadline or 'Keine'}"
        ctk.CTkLabel(header, text=meta_info, text_color="gray").pack(anchor="w", pady=(2, 0))

        status_box = ctk.CTkFrame(header, fg_color="transparent")
        status_box.pack(fill="x", pady=10)

        self.ampel_badge = ctk.CTkLabel(
            status_box,
            text=f" Ampel: {ampel_text} ",
            fg_color=color,
            text_color="white" if color != "#ffc107" else "black",
            corner_radius=5,
        )
        self.ampel_badge.pack(side="left", padx=(0, 10))

        if status not in ["Abgeschlossen", "Fehlgeschlagen"]:
            if status != "In Bearbeitung":
                btn_confirm = ctk.CTkButton(
                    status_box,
                    text="👍 Auftrag bestätigt",
                    fg_color="#0d6efd",
                    hover_color="#0b5ed7",
                    width=130,
                    command=lambda: self.mark_task_in_progress(t_id),
                )
                btn_confirm.pack(side="left", padx=3)

            btn_success = ctk.CTkButton(
                status_box,
                text="✔ Erfolgreich",
                fg_color="#198754",
                hover_color="#146c43",
                width=120,
                command=lambda: self.mark_task_success(t_id),
            )
            btn_success.pack(side="left", padx=3)

            btn_fail = ctk.CTkButton(
                status_box,
                text="✖ Nicht erfolgreich",
                fg_color="#dc3545",
                hover_color="#b02a37",
                width=130,
                command=lambda: self.mark_task_failed_dialog(t_id),
            )
            btn_fail.pack(side="left", padx=3)

        desc_box = ctk.CTkTextbox(self.right_frame, height=80)
        desc_box.pack(fill="x", padx=20, pady=5)
        desc_box.insert("1.0", f"Beschreibung:\n{clean_desc}")
        desc_box.configure(state="disabled")

        ctk.CTkLabel(
            self.right_frame,
            text="Chat, Verlauf & Dateien (Drag & Drop hierhin)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(10, 0))

        self.chat_scroll = ctk.CTkScrollableFrame(self.right_frame, height=200)
        self.chat_scroll.pack(fill="both", expand=True, padx=20, pady=5)

        # DRAG AND DROP ZIEL REGISTRIEREN
        self.chat_scroll.drop_target_register(DND_FILES)
        self.chat_scroll.dnd_bind('<<Drop>>', lambda event: self.handle_file_drop(event, t_id))

        chat_input_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        chat_input_frame.pack(fill="x", padx=20, pady=(0, 15))

        self.entry_msg = ctk.CTkEntry(
            chat_input_frame, placeholder_text="Nachricht schreiben oder Datei hineinziehen..."
        )
        self.entry_msg.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.entry_msg.bind("<Return>", lambda e: self.send_chat_message(t_id))

        btn_send = ctk.CTkButton(
            chat_input_frame, text="Senden", width=80, command=lambda: self.send_chat_message(t_id)
        )
        btn_send.pack(side="right")

        self.load_comments(t_id)
        self.update_timer(deadline, status)

    def handle_file_drop(self, event, task_id):
        raw_path = event.data.strip()
        if raw_path.startswith('{') and raw_path.endswith('}'):
            raw_path = raw_path[1:-1]
            
        file_path = Path(raw_path)
        
        if not file_path.is_file():
            messagebox.showerror("Fehler", "Ungültige Datei. Bitte ziehe eine Datei hierher.")
            return

        confirm = messagebox.askyesno("Datei-Upload", f"Möchtest du '{file_path.name}' wirklich an diese Aufgabe anhängen?")
        if confirm:
            success, msg = api_upload_file(task_id, file_path)
            if success:
                messagebox.showinfo("Erfolg", "Datei wurde erfolgreich hochgeladen!")
                self.load_comments(task_id)
            else:
                messagebox.showerror("Upload-Fehler", msg)

    def mark_task_in_progress(self, task_id):
        api_update_task_status(task_id, "In Bearbeitung")
        api_add_comment(
            task_id, f"STATUSGEÄNDERT: Auftrag wurde von {CURRENT_USER} angenommen.", author=CURRENT_USER
        )
        messagebox.showinfo("Status", "Auftrag wurde als 'In Bearbeitung' bestätigt.")
        self.reload_current_task(task_id)

    def mark_task_success(self, task_id):
        api_update_task_status(task_id, "Abgeschlossen")
        api_add_comment(
            task_id, f"STATUSGEÄNDERT: Aufgabe wurde von {CURRENT_USER} als 'Erfolgreich erledigt' markiert.", author=CURRENT_USER
        )
        messagebox.showinfo("Erfolg", "Aufgabe als abgeschlossen markiert.")
        self.reload_current_task(task_id)

    def mark_task_failed_dialog(self, task_id):
        dialog = ctk.CTkInputDialog(
            text="Bitte gib einen Grund für das Fehlschlagen an:",
            title="Nicht erfolgreich",
        )
        reason = dialog.get_input()

        if reason is not None:
            reason_clean = reason.strip()
            if not reason_clean:
                messagebox.showwarning(
                    "Eingabefehler", "Ein Grund muss angegeben werden!"
                )
                return

            api_update_task_status(task_id, "Fehlgeschlagen")
            api_add_comment(
                task_id,
                f"STATUSGEÄNDERT: Von {CURRENT_USER} als 'Nicht erfolgreich' markiert. Grund: {reason_clean}",
                author=CURRENT_USER
            )
            messagebox.showinfo(
                "Status aktualisiert", "Aufgabe wurde als fehlschlagen markiert."
            )
            self.reload_current_task(task_id)

    def reload_current_task(self, task_id):
        self.load_task_list()
        tasks = api_get_tasks()
        str_id = str(task_id)
        updated_task = next((t for t in tasks if str(t.get("id")) == str_id), None)
        if updated_task:
            self.select_task(updated_task)

    def load_comments(self, task_id):
        for widget in self.chat_scroll.winfo_children():
            widget.destroy()

        comments = api_get_comments(task_id)

        for c in comments:
            if isinstance(c, dict):
                author = c.get("author", c.get("username", CURRENT_USER or "System"))
                msg = c.get("message", c.get("text", c.get("content", "")))
                time = c.get("timestamp", c.get("created_at", ""))
            else:
                author, msg, time = CURRENT_USER or "System", str(c), ""

            msg_frame = ctk.CTkFrame(self.chat_scroll, corner_radius=8)
            msg_frame.pack(fill="x", padx=5, pady=3)

            lbl_header = ctk.CTkLabel(
                msg_frame,
                text=f"{author} ({time}):" if time else f"{author}:",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#1fa4e0" if author == CURRENT_USER else "gray",
                anchor="w",
            )
            lbl_header.pack(fill="x", padx=8, pady=(4, 0))

            if msg.startswith("FILE::"):
                try:
                    _, filename, download_url = msg.split("::", 2)
                    
                    file_box = ctk.CTkFrame(msg_frame, fg_color="transparent")
                    file_box.pack(fill="x", padx=8, pady=4)
                    
                    ctk.CTkLabel(
                        file_box, 
                        text=f"📎 {filename}", 
                        font=ctk.CTkFont(size=12, weight="bold")
                    ).pack(side="left")
                    
                    btn_download = ctk.CTkButton(
                        file_box,
                        text="💾 ÖFFNEN / SPEICHERN",
                        width=150,
                        height=24,
                        fg_color="#2b6cb0",
                        hover_color="#2c5282",
                        command=lambda url=download_url: webbrowser.open(url)
                    )
                    btn_download.pack(side="right")
                except Exception:
                    lbl_body = ctk.CTkLabel(
                        msg_frame, text=msg, font=ctk.CTkFont(size=12), anchor="w", justify="left"
                    )
                    lbl_body.pack(fill="x", padx=8, pady=(0, 4))
            else:
                lbl_body = ctk.CTkLabel(
                    msg_frame, text=msg, font=ctk.CTkFont(size=12), anchor="w", justify="left"
                )
                lbl_body.pack(fill="x", padx=8, pady=(0, 4))

    def send_chat_message(self, task_id):
        text = self.entry_msg.get().strip()
        if not text:
            return

        api_add_comment(task_id, text, author=CURRENT_USER)
        self.entry_msg.delete(0, "end")
        self.load_comments(task_id)

    def delete_current_task(self, task_id, creator_name, title):
        current_user_prefix = (
            CURRENT_USER.split("@")[0] if CURRENT_USER and "@" in CURRENT_USER else CURRENT_USER
        )

        is_creator = False
        if CURRENT_USER:
            c_user_low = CURRENT_USER.lower()
            creator_low = creator_name.lower()
            prefix_low = current_user_prefix.lower()

            is_creator = (
                c_user_low == creator_low
                or prefix_low in creator_low
                or creator_low in c_user_low
            )

        if not is_creator:
            messagebox.showerror(
                "Keine Berechtigung",
                f"Nur der Ersteller der Aufgabe ({creator_name}) darf diese löschen!",
            )
            return

        confirm = messagebox.askyesno(
            "Aufgabe löschen",
            f"Möchtest du die Aufgabe '{title}' wirklich unwiderruflich löschen?",
        )
        if confirm:
            success, msg = api_delete_task(task_id)
            if success:
                messagebox.showinfo("Gelöscht", "Die Aufgabe wurde erfolgreich gelöscht.")
                self.load_task_list()
                self.reset_right_panel()
            else:
                messagebox.showerror("Fehler beim Löschen", msg)

    # -----------------------------------------------------------------------
    # GRUPPENVERWALTUNG DIALOG
    # -----------------------------------------------------------------------
    def open_group_manager(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Gruppen verwalten (Zentral)")
        dialog.geometry("550x550")
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text="Gruppe anlegen oder bearbeiten", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=20, pady=(15, 5))

        ctk.CTkLabel(dialog, text="Gruppenname (z.B. MTA):").pack(
            anchor="w", padx=20, pady=(5, 0)
        )
        entry_gname = ctk.CTkEntry(dialog)
        entry_gname.pack(fill="x", padx=20, pady=5)

        ctk.CTkLabel(
            dialog, text="Mitglieder (kommagetrennt):"
        ).pack(anchor="w", padx=20, pady=(10, 0))
        entry_members = ctk.CTkEntry(dialog)
        entry_members.pack(fill="x", padx=20, pady=5)

        group_scroll = ctk.CTkScrollableFrame(dialog, height=200)

        def edit_group(gname, members):
            entry_gname.delete(0, "end")
            entry_gname.insert(0, gname)
            entry_members.delete(0, "end")
            entry_members.insert(0, ", ".join(members))

        def delete_group(gname):
            confirm = messagebox.askyesno(
                "Gruppe löschen", f"Möchtest du die Gruppe '{gname}' wirklich löschen?"
            )
            if confirm:
                success, msg = api_delete_group(gname)
                if success:
                    messagebox.showinfo("Erfolg", msg)
                    refresh_group_list()
                else:
                    messagebox.showerror("Fehler beim Löschen", msg)

        def refresh_group_list():
            for w in group_scroll.winfo_children():
                w.destroy()

            server_groups = api_get_groups()
            for gname, members in server_groups.items():
                card = ctk.CTkFrame(group_scroll)
                card.pack(fill="x", pady=4, padx=5)

                lbl = ctk.CTkLabel(
                    card,
                    text=f"👥 {gname}\n   Mitglieder: {', '.join(members)}",
                    anchor="w",
                    justify="left",
                )
                lbl.pack(side="left", padx=10, pady=5, fill="x", expand=True)

                btn_edit = ctk.CTkButton(
                    card,
                    text="✏️",
                    width=35,
                    fg_color="#3182ce",
                    hover_color="#2b6cb0",
                    command=lambda g=gname, m=members: edit_group(g, m),
                )
                btn_edit.pack(side="right", padx=(2, 5), pady=5)

                btn_del = ctk.CTkButton(
                    card,
                    text="🗑️",
                    width=35,
                    fg_color="#8b0000",
                    hover_color="#550000",
                    command=lambda g=gname: delete_group(g),
                )
                btn_del.pack(side="right", padx=2, pady=5)

        def save_group():
            gname = entry_gname.get().strip()
            members_raw = entry_members.get().strip()

            if not gname or not members_raw:
                messagebox.showerror("Fehler", "Bitte Name und mindestens ein Mitglied eingeben!")
                return

            members = [m.strip() for m in members_raw.split(",") if m.strip()]
            success, msg = api_save_group(gname, members)

            if success:
                messagebox.showinfo("Gespeichert", msg)
                refresh_group_list()
                entry_gname.delete(0, "end")
                entry_members.delete(0, "end")
            else:
                messagebox.showerror("Fehler beim Speichern", msg)

        ctk.CTkButton(dialog, text="Gruppe auf Server speichern", command=save_group).pack(
            pady=10
        )

        ctk.CTkLabel(
            dialog, text="Bestehende Server-Gruppen:", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=20, pady=(15, 5))

        group_scroll.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        refresh_group_list()

    # -----------------------------------------------------------------------
    # NEUE AUFGABE DIALOG
    # -----------------------------------------------------------------------
    def open_add_task_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Neue Aufgabe erstellen")
        dialog.geometry("480x540")
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Titel:").pack(anchor="w", padx=20, pady=(15, 0))
        entry_title = ctk.CTkEntry(dialog)
        entry_title.pack(fill="x", padx=20, pady=5)

        ctk.CTkLabel(dialog, text="Gruppe auswählen (optional):").pack(
            anchor="w", padx=20, pady=(10, 0)
        )

        server_groups = api_get_groups()
        assignee_options = ["-- Keine Gruppe --"] + list(server_groups.keys())
        combo_assignee = ctk.CTkOptionMenu(dialog, values=assignee_options)
        combo_assignee.pack(fill="x", padx=20, pady=5)

        ctk.CTkLabel(dialog, text="Zusätzliche Einzelperson / Freitext (optional):").pack(
            anchor="w", padx=20, pady=(5, 0)
        )
        entry_custom_assignee = ctk.CTkEntry(
            dialog, placeholder_text="z.B. Max Mustermann"
        )
        entry_custom_assignee.pack(fill="x", padx=20, pady=5)

        ctk.CTkLabel(dialog, text="Deadline (Format: DD.MM.YYYY HH:MM):").pack(
            anchor="w", padx=20, pady=(10, 0)
        )
        entry_deadline = ctk.CTkEntry(dialog)
        entry_deadline.pack(fill="x", padx=20, pady=5)

        default_deadline = (
            datetime.datetime.now() + datetime.timedelta(hours=6)
        ).strftime("%d.%m.%Y %H:%M")
        entry_deadline.insert(0, default_deadline)

        ctk.CTkLabel(dialog, text="Beschreibung:").pack(
            anchor="w", padx=20, pady=(10, 0)
        )
        entry_desc = ctk.CTkTextbox(dialog, height=90)
        entry_desc.pack(fill="x", padx=20, pady=5)

        def save():
            title = entry_title.get().strip()
            custom_person = entry_custom_assignee.get().strip()
            selected_group = combo_assignee.get()
            deadline = entry_deadline.get().strip()
            desc = entry_desc.get("1.0", "end-1c").strip()

            assignee_parts = []
            
            if selected_group and selected_group != "-- Keine Gruppe --":
                assignee_parts.append(selected_group)

            if custom_person:
                assignee_parts.append(custom_person)

            if assignee_parts:
                assignee = ", ".join(assignee_parts)
            else:
                assignee = "Unassigned"

            if not title:
                messagebox.showerror("Fehler", "Bitte mindestens einen Titel eingeben!")
                return

            success, msg = api_create_task(title, desc, assignee, deadline)
            if success:
                messagebox.showinfo("Erfolg", msg)
                dialog.destroy()
                self.load_task_list()
            else:
                messagebox.showerror("Fehler", msg)

        ctk.CTkButton(dialog, text="Aufgabe Speichern", command=save).pack(pady=20)


# ---------------------------------------------------------------------------
# PROGRAMMSTART
# ---------------------------------------------------------------------------
def start_app():
    def launch_main():
        app = RemindMeApp()
        app.mainloop()

    login = LoginWindow(on_login_success=launch_main)
    login.mainloop()


if __name__ == "__main__":
    start_app()