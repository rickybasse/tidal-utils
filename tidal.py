#!/usr/bin/env python3

import json, os, urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta
from _auth import tidal_access_token
from _utils import DEBUG, request, load_pickle, dump_pickle

API = "https://openapi.tidal.com/v2"
COUNTRY_CODE = "DE"
IGNORE = {
    # don't get me wrong I love classical music as much as any other genre
    # but there is so much music being released every week associated with
    # these artists that just pollutes the output, hence filtering them out
    "3518585", # Antonio Vivaldi
    "6386240", # Berlin Philharmoniker
    "4470006", # Claude Debussy
    "40078649", # Ennio Morricone
    "3610662", # Erik Satie
    "3917621", # Frédéric Chopin
    "15738612", # Johann Pachelbel
    "14753", # Johann Sebastian Bach
    "3901462", # Johann Strauss II
    "160", # John Williams
    "23858459", # Ludwig van Beethoven
    "3518587", # Wolfgang Amadeus Mozart
    "13769966", # Niccolò Paganini
}

client_id = os.getenv("T_CI")
client_secret = os.getenv("T_CS")
refresh_token = os.getenv("T_RT")
albums_pi = os.getenv("T_PI_ALBUMS")
eps_pi = os.getenv("T_PI_EPS")
singles_pi = os.getenv("T_PI_SINGLES")
date = os.getenv("DATE", (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
today = datetime.now().strftime("%Y-%m-%d")

@dataclass(frozen=True)
class Album:
    i: str
    release_date: str
    title: str
    type: str

def get_collection_tracks(url, token):
    r = request(url, token)
    next_url = r.get("links", {}).get("next")
    if next_url and next_url.startswith("/"): next_url = API + next_url
    return [item["id"] for item in r["data"]], next_url

def get_albums(artist_ids, token):
    url = f"{API}/artists?countryCode={COUNTRY_CODE}&include=albums" + "".join(f"&filter[id]={a}" for a in artist_ids)
    r = request(url, token)
    albums = []
    for a in r["included"]:
        if a["type"] == "albums":
            attrs = a["attributes"]
            albums.append(Album(
                i=a["id"],
                release_date=attrs.get("releaseDate"),
                title=attrs["title"],
                type=attrs["type"]
            ))
    return albums

def get_tracks(url, token):
    r = request(url, token)
    return [item["id"] for item in r["included"] if item["type"] == "tracks"]

def sync_playlist(playlist_id, albums, access_token, label):
    playlist_url = f"{API}/playlists/{playlist_id}/relationships/items?countryCode={COUNTRY_CODE}"
    all_items = []
    url = playlist_url
    while url:
        r = request(url, access_token)
        all_items.extend(r["data"])
        next_url = r.get("links", {}).get("next")
        url = API + next_url if next_url and next_url.startswith("/") else None
    for chunk in [all_items[i:i+20] for i in range(0, len(all_items), 20)]:
        data = json.dumps({"data": [
            {"type": "tracks", "id": item["id"], "meta": {"itemId": item["meta"]["itemId"]}}
            for item in chunk
        ]}).encode()
        request(playlist_url, access_token, data, "DELETE")
    print(f"{label}: cleaned")

    tracks = []
    albums_l = list(albums)
    for chunk in [albums_l[i:i+20] for i in range(0, len(albums_l), 20)]:
        url = f"{API}/albums?countryCode={COUNTRY_CODE}&include=items" + "".join(f"&filter[id]={a}" for a in chunk)
        tracks.extend(get_tracks(url, access_token))
    print(f"{label}: {len(albums)} albums, {len(tracks)} tracks")
    for chunk in [tracks[i:i+20] for i in range(0, len(tracks), 20)]:
        data = json.dumps({"data": [{"type": "tracks", "id": t_id} for t_id in chunk]}).encode()
        request(playlist_url, access_token, data, "POST")
    print(f"{label}: updated")

if __name__ == "__main__":
    access_token = tidal_access_token(client_id, client_secret, refresh_token)
    print(f"getting albums released after {date}")

    artists = load_pickle("tidal_artists.pkl")
    if not artists:
        url = f"{API}/userCollectionTracks/me/relationships/items?countryCode={COUNTRY_CODE}"
        track_ids = []
        while url:
            batch, url = get_collection_tracks(url, access_token)
            track_ids.extend(batch)
            if DEBUG: print(f"{len(batch)=}, {len(track_ids)=}")
        for chunk in [track_ids[i:i+20] for i in range(0, len(track_ids), 20)]:
            url = f"{API}/tracks?countryCode={COUNTRY_CODE}&include=artists" + "".join(f"&filter[id]={t}" for t in chunk)
            r = request(url, access_token)
            if DEBUG: print(f"{len(r['data'])=}, {len(artists)=}")
            for track in r["data"]: artists.add(track["relationships"]["artists"]["data"][0]["id"])
        dump_pickle(artists, "tidal_artists.pkl")

    print(f"liked artists: {len(artists)}")
    artists -= IGNORE
    print(f"liked artist - ignored: {len(artists)}")

    singles = load_pickle("tidal_singles.pkl")
    eps = load_pickle("tidal_eps.pkl")
    albums = load_pickle("tidal_albums.pkl")

    if not (singles and eps and albums):
        new_singles, new_eps, new_albums = [], [], []
        artists_list = list(artists)
        for i in range(0, len(artists_list), 20):
            chunk = artists_list[i:i+20]
            try:
                artist_albums = get_albums(chunk, access_token)
                if DEBUG: print(f"{i=}, {len(chunk)=}, {len(artist_albums)=}")
            except urllib.error.HTTPError as e:
                if e.code == 404: continue
                raise
            new_releases = [a for a in artist_albums if a.release_date and date <= a.release_date <= today]
            new_singles.extend([r for r in new_releases if r.type == "SINGLE"])
            new_eps.extend([r for r in new_releases if r.type == "EP"])
            new_albums.extend([r for r in new_releases if r.type == "ALBUM"])
            if DEBUG: [print(f"* {r.type} | {r.title}") for r in new_releases]
        singles = dict.fromkeys(r.i for r in new_singles)
        eps = dict.fromkeys(r.i for r in new_eps)
        albums = dict.fromkeys(r.i for r in new_albums)
        dump_pickle(singles, "tidal_singles.pkl")
        dump_pickle(eps, "tidal_eps.pkl")
        dump_pickle(albums, "tidal_albums.pkl")

    sync_playlist(singles_pi, singles, access_token, "singles")
    sync_playlist(eps_pi, eps, access_token, "eps")
    sync_playlist(albums_pi, albums, access_token, "albums")
    print("fresh tracks ready to be listened to!")
