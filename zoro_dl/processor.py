import os
import subprocess
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests

# Import necessary utilities
from .utils import (
    extract_zoro_id,
    colored_text,
    is_sub_dub,
    get_language_code,
    n_m3u8_dl_path,
    get_video_resolution,
    get_readable_time,
)
from .anime_api import AnimeAPI


def download_file(url, save_path):
    """
    Download a file from a given URL and save it to the specified path.
    """
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(save_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
    except Exception as e:
        print(f"Failed to download the file from {url}. Error: {e}")


class ZORO:
    """
    A class to handle processing and downloading anime episodes from ZORO.
    """

    def __init__(self, url, season="1", episode=None, resolution="1080p", dl_type="both", group_tag="NOGRP", save_dir="/content/drive/MyDrive/Encode/"):
        """
        Initialize the ZORO class with required parameters.
        """
        self.zoro_url = url
        self.season = season
        self.requested_episode = episode
        self.resolution = resolution.replace("p", "")
        self.dl_type = dl_type
        self.zoro_id = extract_zoro_id(self.zoro_url)
        self.end_code = str(uuid.uuid4())
        self.custom_group_tag = group_tag
        self.separator = "-" * 70
        self.save_dir = save_dir

        self.api = AnimeAPI()
        self.episodes = self.api.get_episodes(self.zoro_id)
        self.setup_episode_start_end()

    def setup_episode_start_end(self):
        """
        Set up the starting and ending episode numbers based on the requested episode range.
        """
        if self.requested_episode is None:
            self.episode_start = 1
            self.episode_end = len(self.episodes)
        elif "-" in self.requested_episode:
            self.episode_start, self.episode_end = map(int, self.requested_episode.split("-"))
        else:
            self.episode_start = int(self.requested_episode)
            self.episode_end = 0

    def get_stream_data(self, episode_number):
        """
        Retrieve streaming and subtitle data for a specific episode.
        """
        print(colored_text("EXTRACTING STREAMS", "green"))

        try:
            episode_index = episode_number - 1
            episode = self.episodes[episode_index]
            episode_id = episode["url"].split("?ep=")[-1]
            find_is_sub_dub = is_sub_dub(episode_id)
            watch_id = episode["id"].split("$episode")[0]

            watch_id_list = []
            if self.dl_type == "both" and find_is_sub_dub == "both":
                watch_id_list.extend([
                    f"{watch_id}$episode${episode_id}$sub",
                    f"{watch_id}$episode${episode_id}$dub",
                ])
            elif self.dl_type == "dub":
                watch_id_list.append(f"{watch_id}$episode${episode_id}$dub")
            elif self.dl_type == "sub":
                watch_id_list.append(f"{watch_id}$episode${episode_id}$sub")

            sources, subtitles = [], []
            complete_data = {
                "sources": sources,
                "subtitles": subtitles,
                "malID": self.api.get_info(self.zoro_id, "malID"),
                "title": self.api.get_info(self.zoro_id, "title"),
                "episodeTitle": episode["title"],
                "season": int(self.season),
                "episode": episode_number,
            }

            with ThreadPoolExecutor(max_workers=2) as executor:
                executor.submit(self.fetch_video_sources, watch_id_list, sources)
                executor.submit(self.fetch_subtitles_sources, watch_id_list[0], subtitles)

            self.complete_data = complete_data
            self.video_sources = sources
            self.subtitle_sources = subtitles
        except Exception as e:
            print(f"[+] ERROR - Stream data extraction failed for episode {episode_number}. Error: {e}")

    def fetch_video_sources(self, watch_id_list, sources):
        """
        Fetch video sources for given watch IDs.
        """
        for wID in watch_id_list:
            try:
                watch_info = self.api.get_watch_info(wID)
                sources.append({
                    "url": watch_info["sources"][0]["url"],
                    "subOrdub": wID.split("$")[-1],
                })
            except Exception as e:
                print(f"Failed to fetch video source for {wID}. Error: {e}")

    def fetch_subtitles_sources(self, watch_id, subtitles):
        """
        Fetch subtitle sources for the given watch ID.
        """
        try:
            subtitle_info = self.api.get_watch_info(watch_id.replace("dub", "sub"))
            subtitles.extend([
                {
                    "lang": sub_data["lang"],
                    "lang_639_2": get_language_code(sub_data["lang"].split(" - ")[0]),
                    "url": sub_data["url"],
                }
                for sub_data in subtitle_info["subtitles"]
                if sub_data["lang"] != "Thumbnails"
            ])
        except Exception as e:
            print(f"Failed to fetch subtitles for {watch_id}. Error: {e}")

    def mux_files(self):
        """
        Mux video and subtitle files into MKV, saving to the specified directory.
        """
        print(colored_text("[+] MUXING FILES", "green"))
        ffmpeg_opts = ["ffmpeg", "-y"]

        # Verify and add video files
        for source in self.video_sources:
            video_filename = f"{self.complete_data['malID']}_{source['subOrdub']}_{self.end_code}.mp4"
            if not os.path.exists(video_filename):
                print(f"[+] ERROR - Video file not found: {video_filename}")
                return
            ffmpeg_opts.extend(["-i", video_filename])

        # Verify and add subtitle files
        for subtitle in self.subtitle_sources:
            subtitle_filename = f"subtitle_{subtitle['lang_639_2']}_{self.end_code}.vtt"
            if os.path.exists(subtitle_filename):
                ffmpeg_opts.extend(["-i", subtitle_filename])

        # Map streams and add metadata
        ffmpeg_opts.extend(["-map", "0:v:0", "-map", "0:a:0"])
        ffmpeg_opts.extend(["-c", "copy", os.path.join(self.save_dir, f"{self.end_code}.mkv")])

        # Execute FFmpeg
        try:
            subprocess.check_call(ffmpeg_opts)
            print("[+] SUCCESS - Muxing completed.")
        except subprocess.CalledProcessError as e:
            print(f"[+] ERROR - FFmpeg failed. Command: {' '.join(ffmpeg_opts)}. Error: {e}")

    def start_dl(self):
        """
        Start downloading and processing episodes.
        """
        for ep_index in range(self.episode_start, self.episode_end + 1):
            self.get_stream_data(ep_index)
            self.mux_files()
