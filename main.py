# NBA Game Visualizer - Version 2
# --------------------------------
# Pick a team and game and get a summary and interactive Plotly chart of the game's momentum swings.
import requests
import pandas as pd
import plotly.graph_objects as go
import os

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

# Path to  CSV 
DATA_DIR = "data"
OUTPUT_HTML = "output.html"

# Fetch JSON from ESPN's play-by-play endpoint
def fetch_game_data(game_id):
    url = (
        f"{BASE_URL}/summary?event={game_id}"
    )
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not reach ESPN API: {e}")
        raise SystemExit(1)

    data = response.json()

    if "header" not in data:
        print("[ERROR] ESPN returned no data for this game.")
        raise SystemExit(1)

    return data

# Extract team names from JSON header section
def build_team_map(header):
    team_map = {}
    competitions = header.get("competitions", [{}])
    competitors = competitions[0].get("competitors", [])

    for competitor in competitors:
        team_id = competitor.get("id", "unknown")
        team_name = (
            competitor.get("team", {}).get("displayName")
            or competitor.get("team", {}).get("name")
            or team_id
        )
        team_map[team_id] = team_name

    return team_map

def clock_to_seconds(clock_str):
    if not clock_str:
        return 0

    parts = clock_str.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    elif len(parts) == 1:
        return float(parts[0])
    else:
        return 0
    
def elapsed_time(period, clock_str):
    if period < 1:
        return 0

    if period <= 4:
        total_seconds = (period - 1) * 720
    else:
        total_seconds = (4 * 720) + ((period - 5) * 300)

    total_seconds += (720 if period <= 4 else 300) - clock_to_seconds(clock_str)

    return total_seconds

def period_label(period):
    if period is None:
        return "Unknown"
    elif period == 1:
        return "1st"
    elif period == 2:
        return "2nd"
    elif period == 3:
        return "3rd"
    elif period == 4:
        return "4th"
    else:
        return f"OT{period - 4}"

def parse_plays(plays, team_map):

    rows = []

    for play in plays:
        # Resolve which team made this play
        team_id = str(play.get("team", {}).get("id", ""))
        team_name = team_map.get(team_id, team_id or "Unknown")
        p_num = play.get("period", {}).get("number", None)
        clock = play.get("clock", {}).get("displayValue", "")

        row = {
            "period":       p_num,
            "clock":        clock,
            "team":         team_name,
            "type":         play.get("type", {}).get("text", ""),
            "text":         play.get("text", ""),
            "scoreValue":   play.get("scoreValue", 0),
            "homeScore":    play.get("homeScore", None),
            "awayScore":    play.get("awayScore", None),
            "scoringPlay":  play.get("scoringPlay", False),
            "periodLabel":  period_label(p_num),
            "elapsedTime":  elapsed_time(p_num or 0, clock),
            "timeLabel":   f"{period_label(p_num)} {clock}" .strip(),
        }
        rows.append(row)

    return rows

# Build momentum column

def compute_momentum(df):

    # Drop rows where scores are missing 
    df = df.dropna(subset=["homeScore", "awayScore"]).copy()

    df["homeScore"] = pd.to_numeric(df["homeScore"], errors="coerce").fillna(0)
    df["awayScore"] = pd.to_numeric(df["awayScore"], errors="coerce").fillna(0)

    df["momentum"] = df["homeScore"] - df["awayScore"]
    df["swing"]    = df["momentum"].diff().abs()
    df["gameMinutes"] = df["elapsedTime"] / 60.0
    df = df.reset_index(drop=True)

    return df


# Identify biggest momentum swings

def find_lead_changes(df):

    import numpy as np
    sign = np.sign(df["momentum"]).astype(int)
    prev = sign.replace(0, np.nan).ffill().shift()
    mask = (sign != 0) & (sign != prev)
    return df[mask.fillna(False)].copy()

# Quarter / OT divider lines along the game-minute scale
def add_period_markers(fig, max_minutes):

    boundaries = [12, 24, 36]
    labels = [("Q1", 6), ("Q2", 18), ("Q3", 30), ("Q4", 42)]

    # Extend markers if the game went to OT (each OT = 5 min)
    ot_num = 1
    ot_start = 48
    while max_minutes > ot_start:
        boundaries.append(ot_start)
        labels.append((f"OT{ot_num}", ot_start + 2.5))
        ot_start += 5
        ot_num += 1

    for boundary in boundaries:
        fig.add_vline(x=boundary, line_dash="dot",
                      line_color="rgba(0,0,0,0.18)", line_width=1)
    for label, x_pos in labels:
        fig.add_annotation(x=x_pos, y=1, yref="paper", text=label,
                           showarrow=False,
                           font=dict(size=11, color="rgba(0,0,0,0.45)"))

# Plotly graph
def plot_momentum(df, lead_changes, home_name="Home", away_name="Away"):

    fig = go.Figure()

    # --- Main momentum line ---
    fig.add_trace(go.Scatter(
        x=df["gameMinutes"],
        y=df["momentum"],
        mode="lines",
        name="Score differential",
        line=dict(color="#1d428a", width=2),   # NBA blue
        hovertemplate=(
            "%{customdata[0]}<br>"
            f"{home_name} %{{customdata[2]:.0f}} — {away_name} %{{customdata[3]:.0f}}<br>"
            "Differential: %{y}<br>"
            "%{customdata[1]}<extra></extra>"
        ),
        customdata=df[["timeLabel", "text", "homeScore", "awayScore"]],
    ))

    # --- Horizontal zero line (tied game) ---
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="gray",
        annotation_text="Tied",
        annotation_position="right",
    )

    # --- Top-swing markers ---
    if not lead_changes.empty:
        fig.add_trace(go.Scatter(
            x=lead_changes["gameMinutes"],
            y=lead_changes["momentum"],
            mode="markers+text",
            name="Lead change",
            marker=dict(color="#c8102e", size=12, symbol="star"),   # NBA red
            text=lead_changes["swing"].apply(lambda s: f"+{int(s)}"),
            textposition="top center",
            hovertemplate=(
                "%{customdata[0]}<br>"
                f"{home_name} %{{customdata[2]:.0f}} - {away_name} %{{customdata[3]:.0f}}<br>"
                "Differential: %{y}<br>"
                "%{customdata[1]}<extra></extra>"
            ),
            customdata=lead_changes[["timeLabel", "text", "homeScore", "awayScore"]],
        ))

# --- Quarter / OT divider lines along the game-minute scale ---
    add_period_markers(fig, df["gameMinutes"].max())


    fig.update_layout(
        title=f"NBA Game Visualizer — {home_name} vs {away_name}",
        xaxis_title="Game Minutes",
        yaxis_title="Score differential (home − away)",
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(dtick=6,range=[0, max(48, df["gameMinutes"].max())]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig

# Overlay two games' momentum lines on one shared game-time chart
def plot_comparison(games):
    fig = go.Figure()
    colors = ["#1d428a", "#c8102e"]   # NBA blue / NBA red

    for game, color in zip(games, colors):
        df = game["df"]
        fig.add_trace(go.Scatter(
            x=df["gameMinutes"],
            y=df["momentum"],
            mode="lines",
            name=game["label"],
            line=dict(color=color, width=2),
            hovertemplate=(
                "%{customdata[0]}<br>"
                "Differential: %{y}<br>"
                "%{customdata[1]}<extra>" + game["label"] + "</extra>"
            ),
            customdata=df[["timeLabel", "text"]],
        ))

    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="gray",
        annotation_text="Tied",
        annotation_position="right",
    )

    max_minutes = max(g["df"]["gameMinutes"].max() for g in games)
    add_period_markers(fig, max_minutes)

    fig.update_layout(
        title="Game Comparison — " + "  vs  ".join(g["label"] for g in games),
        xaxis_title="Game Minutes",
        yaxis_title="Score differential (home − away)",
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(dtick=6, range=[0, max(48, max_minutes)]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig

def save_csv(df, path):

    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)

# Fetch all 30 NBA teams (one small request)
def fetch_teams():
    url = f"{BASE_URL}/teams?limit=32"
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not fetch team list: {e}")
        raise SystemExit(1)

    data = response.json()
    teams = []
    try:
        team_items = data["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError):
        print("[ERROR] Unexpected team list format from ESPN.")
        raise SystemExit(1)

    for item in team_items:
        t = item.get("team", {})
        teams.append({
            "id":   t.get("id", ""),
            "name": t.get("displayName", "Unknown"),
            "abbr": t.get("abbreviation", ""),
        })

    return sorted(teams, key=lambda t: t["name"])

# Pick a team by number OR by typing part of a name/abbreviation
def choose_team():
    teams = fetch_teams()

    print("\nNBA Teams:")
    for i, t in enumerate(teams, 1):
        print(f"  {i:2d}. {t['abbr']:4s} {t['name']}")

    while True:
        raw = input("\nPick a team (number, or type a name/abbreviation): ").strip()
        if not raw:
            continue

        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(teams):
                return teams[idx - 1]
            print(f"Please enter a number between 1 and {len(teams)}.")
            continue

        query = raw.lower()
        matches = [t for t in teams
                   if query in t["name"].lower() or query == t["abbr"].lower()]

        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            print("Multiple matches, be more specific:")
            for t in matches:
                print(f"  - {t['name']} ({t['abbr']})")
        else:
            print("No team matched that. Try again.")

# Fetch a team's schedule, keep only completed games
def fetch_completed_games(team_id, season=None):
    url = f"{BASE_URL}/teams/{team_id}/schedule"
    if season:
        url += f"?season={season}"

    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not fetch schedule: {e}")
        raise SystemExit(1)

    events = response.json().get("events", [])
    games = []

    for event in events:
        comp = event.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {})
        if not status.get("completed", False):
            continue   # skip unplayed games

        score_bits = []
        for c in comp.get("competitors", []):
            abbr = c.get("team", {}).get("abbreviation", "?")
            score = c.get("score", {})
            # schedule endpoint returns score as a dict, summary as a string
            score_val = score.get("displayValue", "?") if isinstance(score, dict) else score
            score_bits.append(f"{abbr} {score_val}")

        games.append({
            "id":    event.get("id", ""),
            "date":  event.get("date", "")[:10],
            "name":  event.get("shortName", event.get("name", "Unknown matchup")),
            "score": " — ".join(score_bits),
        })

    games.sort(key=lambda g: g["date"], reverse=True)
    return games

# Show recent games 10 at a time and let the user pick one
def choose_game(team, games_per_page=10):
    season = input(f"\nSeason year for {team['name']} (Enter for current, e.g. 2026 = the 2025-26 season): ").strip()
    games = fetch_completed_games(team["id"], season or None)

    if not games:
        print("No completed games found. Try another season year.")
        return None

    page = 0
    while True:
        start = page * games_per_page
        chunk = games[start:start + games_per_page]
        if not chunk:
            print("No more games.")
            page = 0
            continue

        print(f"\nRecent games for {team['name']} ({start + 1}–{start + len(chunk)} of {len(games)}):")
        for i, g in enumerate(chunk, start + 1):
            print(f"  {i:2d}. {g['date']}  {g['name']:12s} {g['score']}")

        raw = input("\nPick a game number ('m' for more, 'b' to go back): ").strip().lower()
        if raw == "m":
            page += 1
        elif raw == "b":
            page = max(0, page - 1)
        elif raw.isdigit() and 1 <= int(raw) <= len(games):
            return games[int(raw) - 1]
        else:
            print("Invalid choice.")


def main():
    team = choose_team()
    game_choice = choose_game(team)
    if game_choice is None:
        raise SystemExit(0)
    game_id = game_choice["id"]

    raw = fetch_game_data(game_id)
    header = raw.get("header", {})
    plays_raw = raw.get("plays", [])
    team_map = build_team_map(header)
    competitions = header.get("competitions", [{}])
    competitors  = competitions[0].get("competitors", [])
    home_name, away_name = "Home", "Away"
    for c in competitors:
        name = c.get("team", {}).get("displayName", "")
        if c.get("homeAway") == "home":
            home_name = name
        else:
            away_name = name

    rows = parse_plays(plays_raw, team_map)
    if not rows:
        print("[ERROR] No play data found for this game.")
        raise SystemExit(1)


    df = pd.DataFrame(rows)
    df = compute_momentum(df)

    # Summary
    final_home = int(df["homeScore"].iloc[-1]) if not df.empty else "?"
    final_away = int(df["awayScore"].iloc[-1]) if not df.empty else "?"
    print("\n" + "=" * 50)
    print(f"  Game ID   : {game_id}")
    print(f"  {home_name} (home) vs {away_name} (away)")
    print(f"  Final score: {home_name} {final_home} – {away_name} {final_away}")
    print(f"  Total plays: {len(df)}")
    print("=" * 50 + "\n")

    lead_changes = find_lead_changes(df)
    print(f"Lead changes: {len(lead_changes)}")
    if lead_changes.empty:
        print("  None — wire-to-wire win!")
    for _, row in lead_changes.iterrows():
        leader = home_name if row["momentum"] > 0 else away_name
        print(f"  {row['timeLabel']:>10s}  {leader} take the lead  ({row['text'][:60]})")
    print()

    csv_path = os.path.join(DATA_DIR, f"play_by_play_{game_id}.csv")
    save_csv(df, csv_path)

    # Build chart and open in browser
    fig = plot_momentum(df, lead_changes, home_name=home_name, away_name=away_name)
    fig.write_html(OUTPUT_HTML)
    print(f"[chart] Chart saved to {OUTPUT_HTML}")
    fig.show()   # opens in default browser


if __name__ == "__main__":
    main()
