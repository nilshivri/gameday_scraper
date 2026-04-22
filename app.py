import streamlit as st
import json
import re
import time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# --- KONFIGURATION ---
BASE_URL = "https://leaguesphere.app"
LOGIN_URL = f"{BASE_URL}/login/"
TEAM_LIST_URL = f"{BASE_URL}/passcheck/team/all/list/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LeagueSphereUnified/Web)"}

# --- UI SETUP ---
st.set_page_config(page_title="LeagueSphere Scraper", page_icon="🏈", layout="centered")

@st.cache_data
def load_team_mapping():
    try:
        with open('teams.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def get_full_team(abbr, mapping):
    if not abbr: return ""
    for k, v in mapping.items():
        if k.lower() == abbr.lower(): return v
    return abbr

# --- HILFSFUNKTIONEN ---
def clean(text): return " ".join(text.split()) if text else ""

def get_cell(cells, idx_dict, key):
    if key in idx_dict and idx_dict[key] < len(cells):
        return clean(cells[idx_dict[key]].get_text())
    return ""

def fetch_page_expanded(url, session):
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    html = resp.text.replace("<template ", "<tbody ").replace("</template>", "</tbody>")
    return BeautifulSoup(html, "html.parser")

def process_action(text, abbr, full_name, roster):
    if not text: return ""
    if abbr and abbr.lower() != full_name.lower():
        text = re.compile(rf'\b{re.escape(abbr)}\b', re.IGNORECASE).sub(full_name, text)
    if full_name and full_name.lower() not in text.lower():
        text = f"{full_name} {text}"
    def repl(match):
        num = match.group(1)
        return roster.get(num, f"#{num}")
    text = re.sub(r'#\s*(\d+)', repl, text)
    return re.sub(r'\s+', ' ', text).strip()

def translate_stat_player(player_text, mapping, rosters):
    if not player_text: return ""
    sorted_mapping = sorted(mapping.items(), key=lambda x: len(x[0]), reverse=True)
    for abbr, full_name in sorted_mapping:
        if abbr.lower() in player_text.lower() or full_name.lower() in player_text.lower():
            roster = rosters.get(full_name, {})
            return process_action(player_text, abbr, full_name, roster)
    return player_text

# --- PARSER ---
def parse_game_list(soup, gameday_id, mapping):
    games = []
    for table in soup.find_all("table"):
        headers = [clean(th.get_text()).lower() for th in table.find_all("th")]
        if "id" not in headers: continue
        idx = {h: i for i, h in enumerate(headers)}
        pkt_indices = [i for i, h in enumerate(headers) if h == "pkt"]
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells: continue
            gid_str = get_cell(cells, idx, "id")
            if not gid_str.isdigit(): continue
            h_abbr, a_abbr = get_cell(cells, idx, "heim"), get_cell(cells, idx, "gast")
            games.append({
                "game_id": int(gid_str), "url": f"{BASE_URL}/gamedays/gameday/{gameday_id}/game/{gid_str}",
                "start_time": get_cell(cells, idx, "start"), "field": get_cell(cells, idx, "feld"),
                "home_abbr": h_abbr, "away_abbr": a_abbr, 
                "home_team": get_full_team(h_abbr, mapping),
                "score_home": clean(cells[pkt_indices[0]].get_text()) if len(pkt_indices) > 0 else "",
                "score_away": clean(cells[pkt_indices[1]].get_text()) if len(pkt_indices) > 1 else "",
                "away_team": get_full_team(a_abbr, mapping),
                "group": get_cell(cells, idx, "platz"), "round": get_cell(cells, idx, "runde"),
                "status": get_cell(cells, idx, "status"), "plays": [] 
            })
        break
    return games

def parse_standings(soup, mapping):
    standings = []
    for table in soup.find_all("table"):
        headers_raw = [clean(th.get_text()) for th in table.find_all("th")]
        headers = [h.split()[0].lower() if h else "" for h in headers_raw]
        if "rang" not in headers or ("sq" not in headers and "lp" not in headers): continue
        idx = {h: i for i, h in enumerate(headers)}
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells: continue
            rank = get_cell(cells, idx, "rang")
            if not rank: continue
            team_name = ""
            if len(cells) > 1:
                x_html = cells[1].get("x-html", "")
                m = re.search(r'highlightMultiple\([`\'"](.*?)[`\'"]', x_html)
                if m: team_name = m.group(1)
                else:
                    x_show = row.get("x-show", "")
                    m2 = re.search(r'fuzzyMatch\(search,\s*[`\'"](.*?)[`\'"]', x_show)
                    team_name = m2.group(1) if m2 else clean(cells[1].get_text())
            standings.append({
                "rank": rank, "team": get_full_team(team_name, mapping),
                "win_ratio": get_cell(cells, idx, "sq"), "points_scored": get_cell(cells, idx, "ep"),
                "points_against": get_cell(cells, idx, "gp"), "point_diff": get_cell(cells, idx, "pd"),
                "wins": get_cell(cells, idx, "s"), "draws": get_cell(cells, idx, "u"),
                "losses": get_cell(cells, idx, "n"), "games_played": get_cell(cells, idx, "sp"),
                "round": get_cell(cells, idx, "runde"), "league_points": get_cell(cells, idx, "lp"),
            })
        break
    return standings

def parse_statistics(soup):
    scoring, defense = [], []
    for table in soup.find_all("table"):
        headers = [clean(th.get_text()).lower() for th in table.find_all("th")]
        if "touchdown" in headers and "punkte" in headers:
            idx = {h: i for i, h in enumerate(headers)}
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if not cells: continue
                scoring.append({
                    "rank": get_cell(cells, idx, "platz"), "player": get_cell(cells, idx, "spieler"),
                    "touchdowns": get_cell(cells, idx, "touchdown"), "extra_1pt": get_cell(cells, idx, "1-extra-punkt"),
                    "extra_2pt": get_cell(cells, idx, "2-extra-punkte"), "points": get_cell(cells, idx, "punkte"),
                })
        if "interceptions" in headers:
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 3: continue
                defense.append({"rank": clean(cells[0].get_text()), "player": clean(cells[1].get_text()), "interceptions": clean(cells[2].get_text()), "type": "interception"})
                if len(cells) >= 6 and clean(cells[3].get_text()):
                    defense.append({"rank": clean(cells[3].get_text()), "player": clean(cells[4].get_text()), "safeties": clean(cells[5].get_text()), "type": "safety"})
    return scoring, defense

def parse_game_plays(url, session, h_abbr, h_full, h_roster, a_abbr, a_full, a_roster):
    resp = session.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    plays = []
    for table in soup.find_all("table"):
        headers = [clean(th.get_text()).lower() for th in table.find_all("th")]
        if not any("spielstand" in h for h in headers): continue
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3: continue
            h_act = process_action(clean(cells[0].get_text()), h_abbr, h_full, h_roster)
            h_fail = bool(cells[0].find("s") or cells[0].find("del"))
            score = clean(cells[1].get_text())
            a_act = process_action(clean(cells[2].get_text()), a_abbr, a_full, a_roster)
            a_fail = bool(cells[2].find("s") or cells[2].find("del"))
            
            play_dict = {}
            if h_act: play_dict["home_action"] = h_act
            if h_fail: play_dict["home_failed"] = True
            if score: play_dict["score"] = score
            if a_act: play_dict["away_action"] = a_act
            if a_fail: play_dict["away_failed"] = True
            if play_dict: plays.append(play_dict)
        break 
    return plays

# --- HAUPT LOGIK ---
def scrape_unified(gameday_id, user, pw, log_cb, prog_cb, lp_win):
    session = requests.Session()
    session.headers.update(HEADERS)
    team_mapping = load_team_mapping()
    if team_mapping: log_cb(f"✅ teams.json geladen ({len(team_mapping)} Teams)")
    else: log_cb("⚠ Keine teams.json gefunden!")
    
    login_success = False
    if user and pw:
        log_cb(f"⬡ Sende Login-Daten...")
        resp = session.get(LOGIN_URL, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        csrf = soup.find("input", {"name": "csrfmiddlewaretoken"})
        session.post(LOGIN_URL, data={"username": user, "password": pw, "csrfmiddlewaretoken": csrf["value"] if csrf else "", "next": "/"}, headers={"Referer": LOGIN_URL}, timeout=10)
        check_resp = session.get(TEAM_LIST_URL, timeout=10)
        if "/login" in check_resp.url or "Anmelden" in check_resp.text:
            log_cb("⚠ Login fehlgeschlagen! Roster können nicht ausgelesen werden.")
        else:
            login_success = True
            log_cb("✅ Login erfolgreich!")

    url = f"{BASE_URL}/gamedays/gameday/{gameday_id}/"
    log_cb(f"⬡ Lade Spieltag {gameday_id}...")
    soup = fetch_page_expanded(url, session)
    
    data = {"gameday_id": gameday_id, "url": url, "name": "", "league": "", "date": "", "start_time": "", "address": "", "games": [], "standings": [], "scoring_plays": [], "defense_plays": [], "overall_standings": []}
    if soup.find("h1"): data["name"] = clean(soup.find("h1").get_text())
    for line in soup.get_text(separator="\n").splitlines():
        if "Liga:" in line: data["league"] = line.replace("Liga:", "").strip()
        elif "Datum:" in line: data["date"] = line.replace("Datum:", "").strip()
        elif "Turnierbeginn:" in line: data["start_time"] = line.replace("Turnierbeginn:", "").strip()

    data["games"] = parse_game_list(soup, gameday_id, team_mapping)
    data["standings"] = parse_standings(soup, team_mapping)
    data["scoring_plays"], data["defense_plays"] = parse_statistics(soup)

    l_map = {"DFFLF2": "dfflf2/", "DFFLF": "dfflf/", "DFFL2": "dffl2/", "DFFL": "dffl/"}
    suffix = next((v for k, v in l_map.items() if k in data["league"].upper()), None)
    if suffix:
        try:
            data["overall_standings"] = parse_standings(fetch_page_expanded(f"{BASE_URL}/leaguetable/{suffix}", session), team_mapping)
        except: pass

    rosters = {}
    if login_success:
        log_cb("⬡ Sammle Teamliste für Roster...")
        all_teams = []
        try:
            s_list = BeautifulSoup(session.get(TEAM_LIST_URL, timeout=10).text, "html.parser")
            for a in s_list.select("table a[href*='/passcheck/team/']"):
                all_teams.append({"name": a.get_text(strip=True), "url": urljoin(BASE_URL, a["href"])})
        except: pass

        needed_full_teams = set([g["home_team"] for g in data["games"]] + [g["away_team"] for g in data["games"]])
        for full_name in needed_full_teams:
            if not full_name: continue
            rosters[full_name] = {}
            match = next((t for t in all_teams if full_name.lower() == t["name"].lower().strip()), None)
            if not match: match = next((t for t in all_teams if full_name.lower() in t["name"].lower() or t["name"].lower() in full_name.lower()), None)

            if match:
                urls_to_fetch = [match["url"]]
                try:
                    main_soup = BeautifulSoup(session.get(match["url"], timeout=10).text, "html.parser")
                    for a in main_soup.select("ul.nav-pills a.nav-link"):
                        href = a.get("href", "")
                        if "/passcheck/team/" in href and not re.search(r'/\d{4}/?$', href):
                            sub_url = urljoin(BASE_URL, href)
                            if sub_url not in urls_to_fetch: urls_to_fetch.append(sub_url)
                    
                    for u in urls_to_fetch:
                        r_soup = main_soup if u == match["url"] else BeautifulSoup(session.get(u, timeout=10).text, "html.parser")
                        rt = r_soup.find("table")
                        if rt:
                            headers = [th.get_text(strip=True).lower() for th in rt.find_all("th")]
                            t_idx = headers.index("trikot") if "trikot" in headers else -1
                            v_idx = headers.index("vorname") if "vorname" in headers else -1
                            n_idx = headers.index("nachname") if "nachname" in headers else -1
                            if t_idx != -1 and v_idx != -1 and n_idx != -1:
                                for row in rt.find_all("tr")[1:]:
                                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                                    if len(cols) > max(t_idx, v_idx, n_idx):
                                        trikot, name = cols[t_idx], f"{cols[v_idx]} {cols[n_idx]}".strip()
                                        if trikot and trikot not in rosters[full_name]: rosters[full_name][trikot] = name
                except Exception as e: log_cb(f"  ⚠ Fehler bei {full_name}: {e}")
                log_cb(f"  ↳ {full_name}: {len(rosters[full_name])} Spieler geladen.")
            else: log_cb(f"  ⚠ Team nicht gefunden: {full_name}")

    total = len(data["games"])
    for i, g in enumerate(data["games"]):
        log_cb(f"  [{i+1}/{total}] Verarbeite: {g['home_team']} vs {g['away_team']}...")
        h_abbr, a_abbr = g.pop("home_abbr", ""), g.pop("away_abbr", "")
        h_full, a_full = g["home_team"], g["away_team"]
        g["plays"] = parse_game_plays(g["url"], session, h_abbr, h_full, rosters.get(h_full, {}), a_abbr, a_full, rosters.get(a_full, {}))
        prog_cb(int((i + 1) / total * 100))
        
    for stat in data["scoring_plays"]: stat["player"] = translate_stat_player(stat["player"], team_mapping, rosters)
    for stat in data["defense_plays"]: stat["player"] = translate_stat_player(stat["player"], team_mapping, rosters)
    return data

# --- STREAMLIT UI ---
st.title("🏈 LeagueSphere Scraper")
st.write("Exporte den vollständigen Spieltag inkl. Roster und Play-by-Plays.")

col1, col2 = st.columns(2)
with col1:
    gameday_id = st.text_input("Spieltag-ID", placeholder="z.B. 641")
with col2:
    lp_per_win = st.number_input("LP pro Sieg", value=2.0, step=0.5)

# Hier holt sich die App unsichtbar die Daten aus dem Tresor!
secret_user = st.secrets.get("LS_USERNAME", "")
secret_pass = st.secrets.get("LS_PASSWORD", "")

with st.expander("Zugangsdaten (Optional überschreiben)"):
    user_input = st.text_input("Username", value=secret_user)
    pass_input = st.text_input("Passwort", value=secret_pass, type="password")

if st.button("▶ Daten jetzt exportieren", type="primary"):
    if not gameday_id.isdigit():
        st.error("Bitte eine gültige Spieltag-ID eingeben.")
    else:
        progress_bar = st.progress(0)
        log_container = st.empty()
        logs = []

        def update_log(msg):
            logs.append(msg)
            # Behalte nur die letzten 15 Zeilen für die UI, damit es nicht laggt
            log_container.code("\n".join(logs[-15:]), language="text")

        def update_prog(val):
            progress_bar.progress(val)

        with st.spinner("Scraping läuft... Bitte warten."):
            try:
                final_data = scrape_unified(int(gameday_id), user_input, pass_input, update_log, update_prog, float(lp_per_win))
                
                d = final_data.get("date", "Unbekannt").split(",")[-1].strip()
                fn = re.sub(r'[\\/*?:"<>|]', "", f"Spieltag {final_data['league']} {final_data['name']} {d}.json")
                filename = re.sub(r'\s+', ' ', fn)
                
                json_string = json.dumps(final_data, ensure_ascii=False, indent=2)
                
                st.success("✅ Erfolgreich! Klicke unten, um die Datei herunterzuladen.")
                st.download_button(
                    label="📥 JSON Datei herunterladen",
                    data=json_string,
                    file_name=filename,
                    mime="application/json"
                )
            except Exception as e:
                st.error(f"Fehler während des Scrapings: {e}")