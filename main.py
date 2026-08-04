"""NBA game momentum visualizer: data pipeline, charts, and command line app.

This module owns everything that is not user interface. It fetches data from
ESPN's public NBA endpoints, flattens play by play JSON into a pandas
DataFrame, derives the score differential and the lead changes, and builds the
Plotly figures. The command line application at the bottom of the file is one
consumer of that pipeline and the Streamlit app in app.py is the other.

Any function that transforms data or builds a figure belongs here so
that both entry points get it for free. Nothing in this module runs on import
because main() is guarded by an ``if __name__ == "__main__"`` block.

The pipeline runs in a fixed order:
    fetch_game_data -> build_team_map -> parse_plays -> compute_momentum
    -> find_lead_changes -> plot_momentum

Use:
    $ python main.py

Requires an internet connection. No API key is needed because the ESPN endpoints used
here are public and unauthenticated.

Attributes:
    BASE_URL (str): Root of the ESPN NBA API. All requests are built from it.
    DATA_DIR (str): Folder that CSV exports are written to.
    OUTPUT_HTML (str): Filename for the standalone chart the CLI writes.
"""
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
    """Fetch the full summary payload for one game from ESPN.

    The summary endpoint returns the header (teams, venue, status) and the
    plays array in a single response, so one request supplies everything the
    rest of the pipeline needs.

    Args:
        game_id (str): ESPN event ID, taken from fetch_completed_games().

    Returns:
        dict: The decoded JSON response, guaranteed to contain a "header" key.

    Raises:
        SystemExit: If the request fails or the response has no header section.
    """
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
    """Build a lookup from ESPN team ID to display name.

    Plays reference their team by ID only, so this map is what lets
    parse_plays() attribute each play to a readable team name.

    Args:
        header (dict): The "header" section of a game summary response.

    Returns:
        dict: Team ID (str) mapped to display name (str). Falls back to the
            short name, and then to the ID itself, when displayName is absent.
    """
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
    """Convert a game clock string into seconds remaining in the period.

    ESPN uses two formats: "7:42" for most of a period, and a bare decimal
    such as "41.6" inside the final minute. Both are handled here.

    Args:
        clock_str (str): Clock display value, possibly empty.

    Returns:
        float: Seconds remaining in the period. Returns 0 for empty or
            unrecognized input rather than raising, so one malformed play
            cannot break a whole game.
    """
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
    """Convert a period and clock into seconds elapsed since tipoff.

    This is what turns a set of per-period clocks into a single continuous
    x axis. Regulation periods are 720 seconds each and overtime periods are
    300 seconds each, which is the NBA rule and does not hold for other
    leagues.

    Args:
        period (int): Period number. 1 through 4 are regulation, 5 and above
            are overtime.
        clock_str (str): Clock display value at the moment of the play.

    Returns:
        float: Seconds since the opening tipoff. Returns 0 for a period below 1.
    """
    if period < 1:
        return 0

    if period <= 4:
        total_seconds = (period - 1) * 720
    else:
        total_seconds = (4 * 720) + ((period - 5) * 300)

    total_seconds += (720 if period <= 4 else 300) - clock_to_seconds(clock_str)

    return total_seconds

def period_label(period):
    """Turn a period number into a short human readable label.

    Args:
        period (int or None): Period number from the play data.

    Returns:
        str: "1st" through "4th" for regulation, "OT1", "OT2" and so on for
            overtime, and "Unknown" when the period is missing.
    """
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
    """Flatten ESPN's nested play JSON into flat row dicts.

    This is the boundary between ESPN's data shape and the project's own. Every
    downstream function reads the columns produced here by name, so this
    function and compute_momentum() together define the internal schema.
    Changing a key here means checking all three plotting functions and both
    entry points.

    Args:
        plays (list): The "plays" array from a game summary response.
        team_map (dict): Output of build_team_map(), used to resolve team IDs.

    Returns:
        list: One dict per play, with keys period, clock, team, type, text,
            scoreValue, homeScore, awayScore, scoringPlay, periodLabel,
            elapsedTime, and timeLabel. Returns an empty list when the game has
            no play by play data, which the callers check for.
    """

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
    """Add the derived momentum columns to a parsed play by play frame.

    "Momentum" here is the raw score differential, home minus away. It is not a
    weighted or rolling metric, so a long scoreless stretch reads as a flat
    line rather than as fading momentum. See the developer's guide for why that
    tradeoff was made and what a real momentum metric would involve.

    Args:
        df (pandas.DataFrame): Frame built from parse_plays() output.

    Returns:
        pandas.DataFrame: A copy with rows lacking scores dropped, the index
            reset, and three columns added: momentum (home minus away), swing
            (absolute change in momentum from the previous row, NaN in row 0),
            and gameMinutes (elapsed time in minutes, used as the x axis).
    """

    # Drop rows where scores are missing 
    df = df.dropna(subset=["homeScore", "awayScore"]).copy()

    df["homeScore"] = pd.to_numeric(df["homeScore"], errors="coerce").fillna(0)
    df["awayScore"] = pd.to_numeric(df["awayScore"], errors="coerce").fillna(0)

    df["momentum"] = df["homeScore"] - df["awayScore"]
    df["swing"]    = df["momentum"].diff().abs()
    df["gameMinutes"] = df["elapsedTime"] / 60.0
    df = df.reset_index(drop=True)

    return df


# Identify lead changes

def find_lead_changes(df):
    """Find the plays where the lead changed hands.

    Works on the sign of the momentum column. Ties are excluded from the
    comparison by replacing zeros with NaN and forward filling, so a game that
    goes from a home lead to tied and back to a home lead does not register as
    two lead changes.

    Args:
        df (pandas.DataFrame): Output of compute_momentum().

    Returns:
        pandas.DataFrame: A copy containing only the rows where the lead
            flipped. Empty for a wire to wire win, which callers check for.
    """

    import numpy as np
    sign = np.sign(df["momentum"]).astype(int)
    prev = sign.replace(0, np.nan).ffill().shift()
    mask = (sign != 0) & (sign != prev)
    return df[mask.fillna(False)].copy()

# Quarter / OT divider lines along the game-minute scale
def add_period_markers(fig, max_minutes):
    """Draw quarter and overtime dividers onto a figure, in place.

    Called by both plot_momentum() and plot_comparison() so the two charts
    share one time scale treatment.

    Args:
        fig (plotly.graph_objects.Figure): Figure to annotate. Modified in place.
        max_minutes (float): Longest game minute value on the chart. Used to
            decide how many overtime dividers to add.

    Returns:
        None: The figure is modified in place.
    """

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
    """Build the single game momentum chart.

    The chart has four layers: the momentum line itself, a dashed zero line
    marking a tied game, star markers on the lead changes, and the quarter
    dividers added by add_period_markers(). Above the zero line the home team
    leads; below it the away team leads.

    Args:
        df (pandas.DataFrame): Output of compute_momentum().
        lead_changes (pandas.DataFrame): Output of find_lead_changes(). An
            empty frame is handled and simply produces no markers.
        home_name (str): Home team display name, used in hover text and title.
        away_name (str): Away team display name, used in hover text and title.

    Returns:
        plotly.graph_objects.Figure: Ready to render with st.plotly_chart(),
            write_html(), or show().
    """
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

    # --- Lead change markers ---
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
# GOTCHA: both lines are drawn as home minus away, so a team that was home in one game and away
# in the other appears above zero in one and below zero in the other. Easy to misread.
# TODO: accept a reference-team argument and negate the differential when that team was away.
def plot_comparison(games):
    """Overlay two games' momentum lines on one shared game time axis.

    Args:
        games (list): List of dicts, each with a "df" key holding a frame from
            compute_momentum() and a "label" key holding the display name for
            the legend and hover box. The list shape is the contract here, so a
            future three game overlay would extend this rather than replace it,
            though the colors list would need extending too.

    Returns:
        plotly.graph_objects.Figure: The overlay chart.
    """
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
    """Write a play by play frame to CSV, creating the folder if needed.

    Args:
        df (pandas.DataFrame): Frame to write.
        path (str): Destination path, including a folder component.

    Returns:
        None
    """

    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)

# Fetch all 30 NBA teams (one small request)
def fetch_teams():
    """Fetch the full NBA team list from ESPN.

    One small request that feeds both the CLI team prompt and the web app
    dropdown. The web app caches it for an hour.

    Returns:
        list: Dicts with keys id, name, and abbr, sorted alphabetically by name.

    Raises:
        SystemExit: If the request fails or the response shape is unexpected.
    """
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
    """Prompt at the terminal until the user picks one team.

    Accepts either a list index or a substring of the team name or its
    abbreviation. An ambiguous substring reprompts with the matches shown.
    Command line only; the web app uses a dropdown instead.

    Returns:
        dict: The selected team, with keys id, name, and abbr.
    """
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
    """Fetch one team's schedule and keep only the finished games.

    Live and scheduled games are filtered out, since the pipeline needs a
    complete play by play run to chart anything.

    Args:
        team_id (str): ESPN team ID.
        season (int or str, optional): Season year as ESPN labels it, meaning
            the year the season ends. 2026 is the 2025-26 season. Pass None to
            let ESPN decide the current season.

    Returns:
        list: Dicts with keys id, date, name, and score, newest first. Empty
            when the team and season combination has no finished games.

    Raises:
        SystemExit: If the schedule request fails.
    """
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
    """Prompt at the terminal for a season, then page through that team's games.

    Command line only. The web app renders the same data as a scrollable radio
    list instead.

    Args:
        team (dict): Team dict from choose_team().
        games_per_page (int): How many games to show per page.

    Returns:
        dict or None: The selected game, or None when the season has no
            completed games. main() treats None as a clean exit.
    """
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
    """Run the command line application end to end.

    Prompts for a team and a game, fetches and parses the play by play, prints
    a summary and every lead change, saves the parsed data to CSV under
    DATA_DIR, writes the chart to OUTPUT_HTML, and opens it in the browser.

    Returns:
        None

    Raises:
        SystemExit: With code 0 when the user backs out of game selection, and
            with code 1 when the game has no play data.
    """
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
