#!/usr/bin/env python3
"""Render the stat cards shown in the profile README.

Everything is computed from the GitHub API using the workflow's own token,
so the cards do not depend on a third-party rendering service staying up —
the public github-readme-stats instance answers 503 often enough that
hotlinking it left the README with broken images.

Writes assets/stats.svg, assets/streak.svg and assets/top-langs.svg.
"""

import datetime as dt
import html
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com/graphql"

# tokyonight, matching the theme the README used before.
BG = "#1a1b27"
TITLE = "#70a5fd"
TEXT = "#38bdae"
ACCENT = "#bf91f3"
VALUE = "#ffffff"
MUTED = "#8b949e"
FONT = "'Segoe UI', Ubuntu, Helvetica, sans-serif"

PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      restrictedContributionsCount
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def graphql(token, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stat-cards",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "GraphQL error"))
    return payload["data"]


def contribution_days(token, login, created_at):
    """Day -> contribution count, from account creation to today.

    The calendar only covers a year per query, so walk one year at a time.
    """
    days = {}
    hidden = 0
    today = dt.datetime.now(dt.timezone.utc)
    start = created_at
    while start <= today:
        end = min(start.replace(year=start.year + 1), today)
        data = graphql(
            token,
            CONTRIBUTIONS_QUERY,
            {
                "login": login,
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        collection = data["user"]["contributionsCollection"]
        hidden += collection["restrictedContributionsCount"]
        for week in collection["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]
        start = end + dt.timedelta(seconds=1)
    return days, hidden


def streaks(days):
    """Current and longest streak as (length, first_date, last_date)."""
    dates = sorted(days)
    longest = current = (0, None, None)
    run_start = None
    previous = None

    for date in dates:
        if days[date] > 0:
            if run_start is None or previous is None or \
                    dt.date.fromisoformat(date) - dt.date.fromisoformat(previous) > dt.timedelta(days=1):
                run_start = date
            previous = date
            length = (dt.date.fromisoformat(date) - dt.date.fromisoformat(run_start)).days + 1
            if length > longest[0]:
                longest = (length, run_start, date)
        else:
            run_start = None
            previous = None

    # A day with no commits yet does not break the streak until it is over,
    # so an empty today falls back to the run ending yesterday.
    today = dt.datetime.now(dt.timezone.utc).date()
    for end in (today, today - dt.timedelta(days=1)):
        key = end.isoformat()
        if days.get(key, 0) > 0:
            start = end
            while days.get((start - dt.timedelta(days=1)).isoformat(), 0) > 0:
                start -= dt.timedelta(days=1)
            current = ((end - start).days + 1, start.isoformat(), key)
            break

    return current, longest


def pretty_date(iso):
    return dt.date.fromisoformat(iso).strftime("%b %-d, %Y") if iso else ""


def date_range(start, end):
    """Compact range, dropping the repeated year so it fits the column."""
    if not start or not end:
        return "-"
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first == last:
        return pretty_date(end)
    head = first.strftime("%b %-d" if first.year == last.year else "%b %-d, %Y")
    return f"{head} - {last.strftime('%b %-d, %Y')}"


def text(x, y, value, fill, size=14, weight=400, anchor="start"):
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{html.escape(str(value))}</text>'
    )


def card(width, height, body):
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'fill="none" xmlns="http://www.w3.org/2000/svg">'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="4.5" '
        f'fill="{BG}" stroke="{BG}"/>{body}</svg>'
    )


def render_stats(user, stars, total_contributions):
    rows = [
        ("Total Stars Earned", stars),
        ("Total Contributions", total_contributions),
        ("Total PRs", user["pullRequests"]["totalCount"]),
        ("Total Issues", user["issues"]["totalCount"]),
        ("Public Repositories", user["repositories"]["totalCount"]),
        ("Followers", user["followers"]["totalCount"]),
    ]
    rows = [(label, value) for label, value in rows if value is not None]
    name = user["name"] or user["login"]
    body = [text(25, 35, f"{name}'s GitHub Stats", TITLE, 18, 600)]
    y = 68
    for label, value in rows:
        body.append(f'<circle cx="29" cy="{y - 5}" r="3.5" fill="{ACCENT}"/>')
        body.append(text(45, y, label, TEXT))
        body.append(text(470, y, f"{value:,}", VALUE, 14, 600, anchor="end"))
        y += 22
    return card(495, 195, "".join(body))


def render_streak(total, first_day, current, longest):
    today = dt.datetime.now(dt.timezone.utc).date()
    columns = [
        (82, f"{total:,}", "Total Contributions",
         f"{pretty_date(first_day)} - Present"),
        (412, str(longest[0]), "Longest Streak", date_range(longest[1], longest[2])),
    ]
    body = []
    for x, value, label, subtitle in columns:
        body.append(text(x, 78, value, TITLE, 28, 700, anchor="middle"))
        body.append(text(x, 110, label, ACCENT, 14, 600, anchor="middle"))
        body.append(text(x, 133, subtitle, MUTED, 11, anchor="middle"))

    for x in (165, 330):
        body.append(f'<line x1="{x}" y1="45" x2="{x}" y2="150" stroke="{MUTED}" stroke-opacity="0.4"/>')

    current_range = date_range(current[1], current[2]) if current[0] else today.strftime("%b %-d, %Y")
    body.append(f'<circle cx="247" cy="80" r="40" fill="none" stroke="{TITLE}" stroke-width="5"/>')
    body.append(text(247, 90, str(current[0]), ACCENT, 30, 700, anchor="middle"))
    body.append(text(247, 143, "Current Streak", ACCENT, 14, 600, anchor="middle"))
    body.append(text(247, 163, current_range, MUTED, 11, anchor="middle"))
    return card(495, 195, "".join(body))


def render_top_langs(sizes, colors, count=6):
    top = sorted(sizes.items(), key=lambda item: item[1], reverse=True)[:count]
    total = sum(size for _, size in top) or 1

    body = [text(25, 35, "Top Languages", TITLE, 18, 600)]

    x = 25.0
    bar = []
    for name, size in top:
        width = 250 * size / total
        bar.append(f'<rect x="{x:.2f}" y="0" width="{width:.2f}" height="8" fill="{colors.get(name) or MUTED}"/>')
        x += width
    body.append('<mask id="bar"><rect x="25" y="55" width="250" height="8" rx="4" fill="#fff"/></mask>')
    body.append(f'<g mask="url(#bar)" transform="translate(0 55)">{"".join(bar)}</g>')

    for index, (name, size) in enumerate(top):
        column = 25 + (index % 2) * 130
        row = 95 + (index // 2) * 24
        body.append(f'<circle cx="{column + 5}" cy="{row - 4}" r="5" fill="{colors.get(name) or MUTED}"/>')
        body.append(text(column + 18, row, f"{name} {100 * size / total:.1f}%", TEXT, 12))

    return card(300, 175, "".join(body))


def main():
    login = os.environ.get("STATS_USER", "shakibwebx")
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("No token: set STATS_TOKEN or GITHUB_TOKEN.")

    user = graphql(token, PROFILE_QUERY, {"login": login})["user"]
    created_at = dt.datetime.strptime(user["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )

    stars = 0
    sizes = {}
    colors = {}
    for repo in user["repositories"]["nodes"]:
        stars += repo["stargazerCount"]
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            sizes[name] = sizes.get(name, 0) + edge["size"]
            colors[name] = edge["node"]["color"]

    os.makedirs("assets", exist_ok=True)
    cards = {"assets/top-langs.svg": render_top_langs(sizes, colors)}

    # The contribution calendar is the one field a plain GITHUB_TOKEN may not
    # be allowed to read. Losing it should not cost the other cards, so keep
    # whatever streak card is already committed and carry on.
    try:
        days, hidden = contribution_days(token, login, created_at)
    except (urllib.error.URLError, RuntimeError) as error:
        print(f"skipping streak card: {error}", file=sys.stderr)
        print("set a STATS_TOKEN secret (a PAT with read:user) to enable it", file=sys.stderr)
        days = None

    if days is None:
        cards["assets/stats.svg"] = render_stats(user, stars, None)
    else:
        active = sorted(date for date, count in days.items() if count > 0)
        total = sum(days.values())
        print(f"{total} contributions visible to this token")
        if hidden:
            # Private contributions this token is not allowed to see. A PAT
            # belonging to the profile owner can read them; the default
            # GITHUB_TOKEN cannot, even with private contributions shown on
            # the profile.
            print(f"{hidden} private contributions hidden - set a STATS_TOKEN "
                  "secret (a PAT with read:user) to include them",
                  file=sys.stderr)
        current, longest = streaks(days)
        cards["assets/stats.svg"] = render_stats(user, stars, total)
        cards["assets/streak.svg"] = render_streak(
            total, active[0] if active else None, current, longest
        )
    for path, svg in cards.items():
        with open(path, "w") as handle:
            handle.write(svg + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError) as error:
        # Leave the committed cards in place rather than replacing them with
        # a broken one when the API is unreachable.
        sys.exit(f"Could not refresh cards: {error}")
