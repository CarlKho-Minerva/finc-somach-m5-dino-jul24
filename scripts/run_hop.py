import urllib.parse
import math

nodes = {
    "Chase Center (Origin)": {"lat": 37.7680, "lon": -122.3877, "window": (0, 1440), "utility": 0, "dwell": 0},
    "Tavus HQ": {"lat": 37.7828, "lon": -122.3948, "window": (0, 240), "utility": 9, "dwell": 45, "loc": "35 Stillman St, San Francisco, CA 94107"},
    "YC Health x AI (Khosla)": {"lat": 37.7850, "lon": -122.4050, "window": (0, 180), "utility": 10, "dwell": 60, "loc": "San Francisco, CA"},
    "YC AI Infra Layer": {"lat": 37.7830, "lon": -122.3980, "window": (0, 240), "utility": 9, "dwell": 45, "loc": "San Francisco, CA"},
    "Corgi Cafe Hackathon": {"lat": 37.7880, "lon": -122.4080, "window": (0, 840), "utility": 10, "dwell": 120, "loc": "Corgi Cafe, San Francisco, CA"},
    "MRC x SF Marathon @ Amelie": {"lat": 37.7925, "lon": -122.4208, "window": (1200, 1380), "utility": 7, "dwell": 45, "loc": "1754 Polk St, San Francisco, CA 94109"},
    "Transpose HQ (Dev Tools)": {"lat": 37.7821, "lon": -122.3932, "window": (1440, 1620), "utility": 8, "dwell": 45, "loc": "27 S Park St Suite 100, San Francisco, CA 94107"},
    "Microsoft for Startups": {"lat": 37.7870, "lon": -122.3990, "window": (1440, 1620), "utility": 8, "dwell": 45, "loc": "San Francisco, CA"},
    "Google DeepMind YC Party": {"lat": 37.7890, "lon": -122.4010, "window": (1440, 1620), "utility": 9, "dwell": 45, "loc": "San Francisco, CA"},
    "AWS YC Party @ Exploratorium": {"lat": 37.8015, "lon": -122.3974, "window": (1440, 1650), "utility": 10, "dwell": 60, "loc": "The Exploratorium, Pier 15, San Francisco, CA 94111"}
}

def distance(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p)/2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p))/2
    return 12742 * math.asin(math.sqrt(a)) * 0.621371 # miles

def make_gcal_link(title, start_utc, end_utc, details, location):
    base = "https://calendar.google.com/calendar/render?action=TEMPLATE"
    params = {
        "text": title,
        "dates": f"{start_utc}/{end_utc}",
        "details": details,
        "location": location
    }
    return f"{base}&{urllib.parse.urlencode(params)}"

print("\n=== OPTIMIZED SUNDAY ROUTE (BIG TECH AT END) ===")
sunday_sequence = [
    ("MRC x SF Marathon @ Amelie", "20260726T210000Z", "20260726T223000Z", "Marathon afterparty drinks and networking."),
    ("Transpose HQ (Dev Tools)", "20260727T010000Z", "20260727T021500Z", "Dev tools, food, drinks."),
    ("Google DeepMind YC Party", "20260727T023000Z", "20260727T033000Z", "Google Cloud credits, catering, AI leads."),
    ("AWS YC Party @ Exploratorium", "20260727T033000Z", "20260727T043000Z", "Dinner stations, Zoox taxi, 21:00 live raffle.")
]

for name, start_utc, end_utc, desc in sunday_sequence:
    loc = nodes[name]["loc"]
    link = make_gcal_link(name, start_utc, end_utc, desc, loc)
    print(f"\n* Event: {name}")
    print(f"  Location: {loc}")
    print(f"  GCal Link: {link}")
