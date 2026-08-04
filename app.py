"""Streamlit web front end for the NBA game momentum visualizer.

This module is the interface layer and nothing else. Every fetch, transform,
and plotting function is imported from main.py, so the web app and the command
line app always draw the same charts from the same data. If a function does
real work, it belongs in main.py, not here.

Two things about Streamlit shape this file. First, the whole script reruns top
to bottom on every widget interaction, so module level state does not survive a
click and any uncached network call would fire again every time. That is what
the three ``@st.cache_data`` wrappers below are for. Second, every widget needs
a unique key, which is why render_game_picker() takes a slot argument: it lets
two independent pickers coexist in compare mode.

Use:
    $ streamlit run app.py

Running ``python app.py`` also works. The guard at the top of the file detects
that Streamlit is not running and relaunches the script through it, which makes
the Run button in an editor behave the same way.
"""
# NBA Game Momentum Visualizer — Web App
# ---------------------------------------
# Run with:  streamlit run app.py
# Reuses all data + chart logic from main.py; only the UI layer is new.

import streamlit as st
import pandas as pd

# If started with plain `python app.py` (or VS Code's Run button),
# relaunch ourselves through Streamlit so it "just works".
if __name__ == "__main__":
    from streamlit import runtime
    if not runtime.exists():
        import sys
        from streamlit.web import cli as stcli
        sys.argv = ["streamlit", "run", sys.argv[0]]
        sys.exit(stcli.main())

# Import the existing pipeline — nothing in main.py runs on import
# because main() is guarded by `if __name__ == "__main__"`.
from main import (
    fetch_teams,
    fetch_completed_games,
    fetch_game_data,
    build_team_map,
    parse_plays,
    compute_momentum,
    find_lead_changes,
    plot_momentum,
    plot_comparison,
)

st.set_page_config(page_title="NBA Game Momentum Visualizer", layout="wide")
st.title("NBA Game Momentum Visualizer")

# --- Cached wrappers so we don't re-hit ESPN on every interaction ---
# Streamlit reruns the whole script on each click; caching makes that cheap.

@st.cache_data(ttl=3600, show_spinner="Loading teams...")
def get_teams():
    """Cached wrapper around fetch_teams().

    The team list changes at most once a season, so an hour of cache costs
    nothing and saves a request on every rerun.

    Returns:
        list: Team dicts with keys id, name, and abbr.
    """
    return fetch_teams()

@st.cache_data(ttl=600, show_spinner="Loading schedule...")
def get_games(team_id, season):
    """Cached wrapper around fetch_completed_games().

    A shorter cache than the team list, because an in-progress season gains a
    completed game every few days.

    Args:
        team_id (str): ESPN team ID.
        season (int or None): Season year, or None for the current season.

    Returns:
        list: Completed game dicts, newest first.
    """
    return fetch_completed_games(team_id, season)

@st.cache_data(ttl=3600, show_spinner="Loading play-by-play...")
def get_game_df(game_id):
    """Run the whole pipeline for one game and resolve the team names.

    This is the single call that the render branches below depend on. It wraps
    fetch, parse, and compute in one cached unit, and also works out which side
    was home, which the plotting functions need for their labels.

    Args:
        game_id (str): ESPN event ID.

    Returns:
        tuple: (df, home_name, away_name). The frame is None when the game has
            no play by play data, which both branches check for. Team names
            fall back to "Home" and "Away".
    """
    raw = fetch_game_data(game_id)
    header = raw.get("header", {})
    team_map = build_team_map(header)

    competitions = header.get("competitions", [{}])
    competitors = competitions[0].get("competitors", [])
    home_name, away_name = "Home", "Away"
    for c in competitors:
        name = c.get("team", {}).get("displayName", "")
        if c.get("homeAway") == "home":
            home_name = name
        else:
            away_name = name

    rows = parse_plays(raw.get("plays", []), team_map)
    if not rows:
        return None, home_name, away_name

    df = compute_momentum(pd.DataFrame(rows))
    return df, home_name, away_name

# --- One reusable game picker (team -> season -> game) ---
# `slot` makes each widget's key unique so two pickers can coexist in compare mode.

def render_game_picker(slot):
    """Draw a complete team, season, and game picker in the sidebar.

    One function serves both single game mode and compare mode. The slot
    argument is appended to every widget key, producing team_1, season_2 and so
    on, which is what keeps two pickers from colliding in session state. Follow
    the same pattern if a third picker is ever needed.

    Args:
        slot (int): Unique identifier for this picker's widget keys.

    Returns:
        dict or None: The selected game dict, or None when the chosen team and
            season have no completed games. Callers treat None as a stop signal.
    """

    teams = get_teams()
    team = st.selectbox(
        "Team",
        teams,
        key=f"team_{slot}",
        format_func=lambda t: f"{t['abbr']} — {t['name']}",
    )

    season = st.selectbox(
        "Season",
        [None] + list(range(2026, 2002, -1)),
        index=1,   # default to the most recent completed season
        key=f"season_{slot}",
        format_func=lambda s: "Current" if s is None else f"{s - 1}–{str(s)[2:]}",
    )

    games = get_games(team["id"], season)

    if not games:
        st.warning("No completed games for that team/season. Try another season.")
        return None

    st.caption(f"{len(games)} completed games — scroll the list below")
    with st.container(height=260):
        game = st.radio(
            "Game",
            games,
            key=f"game_{slot}",
            format_func=lambda g: f"{g['date']}  {g['name']}  ({g['score']})",
            label_visibility="collapsed",
        )
    return game

# --- Sidebar: mode toggle + picker(s) ---

with st.sidebar:
    mode = st.radio("Mode", ["Single game", "Compare two games"])

    if mode == "Single game":
        st.header("Pick a game")
        game = render_game_picker(1)
        if game is None:
            st.stop()
    else:
        st.header("Game 1")
        game_a = render_game_picker(1)
        st.divider()
        st.header("Game 2")
        game_b = render_game_picker(2)
        if game_a is None or game_b is None:
            st.stop()
# --- Main panel: summary + chart ---

# --- Main panel ---

if mode == "Single game":
    df, home_name, away_name = get_game_df(game["id"])

    if df is None or df.empty:
        st.error("No play data found for this game.")
        st.stop()

    final_home = int(df["homeScore"].iloc[-1])
    final_away = int(df["awayScore"].iloc[-1])
    lead_changes = find_lead_changes(df)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(f"{home_name} (home)", final_home)
    col2.metric(f"{away_name} (away)", final_away)
    col3.metric("Lead changes", len(lead_changes))
    col4.metric("Total plays", len(df))

    fig = plot_momentum(df, lead_changes, home_name=home_name, away_name=away_name)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander(f"Lead changes ({len(lead_changes)})"):
        if lead_changes.empty:
            st.write("None — a wire-to-wire win.")
        for _, row in lead_changes.iterrows():
            leader = home_name if row["momentum"] > 0 else away_name
            score = f"{int(row['homeScore'])}–{int(row['awayScore'])}"
            st.write(f"**{row['timeLabel']}** — {leader} take the lead ({score}): {row['text']}")

    st.download_button(
        "Download play-by-play CSV",
        df.to_csv(index=False),
        file_name=f"play_by_play_{game['id']}.csv",
        mime="text/csv",
    )

else:
    if game_a["id"] == game_b["id"]:
        st.info("You've picked the same game twice — choose a different Game 2 to compare.")
        st.stop()

    df_a, home_a, away_a = get_game_df(game_a["id"])
    df_b, home_b, away_b = get_game_df(game_b["id"])

    if df_a is None or df_a.empty or df_b is None or df_b.empty:
        st.error("No play data found for one of these games.")
        st.stop()

    label_a = f"{game_a['name']} ({game_a['date']})"
    label_b = f"{game_b['name']} ({game_b['date']})"

    col1, col2 = st.columns(2)
    col1.metric(label_a, f"{int(df_a['homeScore'].iloc[-1])}–{int(df_a['awayScore'].iloc[-1])}",
                help=f"{home_a} (home) vs {away_a} (away), final score home–away")
    col2.metric(label_b, f"{int(df_b['homeScore'].iloc[-1])}–{int(df_b['awayScore'].iloc[-1])}",
                help=f"{home_b} (home) vs {away_b} (away), final score home–away")

    fig = plot_comparison([
        {"df": df_a, "label": label_a},
        {"df": df_b, "label": label_b},
    ])
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Each line is one game's score differential (home − away) over shared game time. "
        "Above the dashed line the home team leads; below it, the away team."
    )