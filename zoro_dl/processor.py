import requests, uuid, subprocess, os, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
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
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(save_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
    else:
        print("Failed to download the file.")


class ZORO:
    def __init__(
        self,
        url,
        season="1",
        episode=None,
        resolution="1080p",
        dl_type="both",
        group_tag="NOGRP",
        save_dir="/content/drive/MyDrive/Encode/"  # Set your desired directory here
    ):
        self.zoro_url = url
        self.season = season
        self.requested_episode = episode
        self.resolution = resolution.replace("p", "")
        self.dl_type = dl_type
        self.zoro_id = extract_zoro_id(self.zoro_url)
        self.end_code = str(uuid.uuid4())
        self.custom_group_tag = group_tag
        self.separator = "-" * 70

        self.api = AnimeAPI()
        self.episodes = self.api.get_episodes(self.zoro_id)
        self.setup_episode_start_end()

        self.save_dir = save_dir  # Define save_dir as an attribute
        os.makedirs(self.save_dir, exist_ok=True)  # Create the directory if it doesn't exist

    def setup_episode_start_end(self):
        if self.requested_episode is None:
            self.episode_start = 1
            self.episode_end = int(len(self.episodes))
        elif "-" in self.requested_episode:
            self.episode_start, self.episode_end = map(
                int, self.requested_episode.split("-")
            )
        else:
            self.episode_start = int(self.requested_episode)
            self.episode_end = 0

    def get_stream_data(self, episode_number):
        print(colored_text("EXTRACTING STREAMS", "green"))

        episode_index = int(episode_number) - 1
        episode = self.episodes[episode_index]
        episode_id = episode["url"].split("?ep=")[-1]
        find_is_sub_dub = is_sub_dub(episode_id)
        title_season = (
            "0{}".format(self.season)
            if int(self.season) < 10
            else "{}".format(self.season)
        )
        title_episode = (
            "0{}".format(episode_number)
            if int(episode_number) < 10
            else "{}".format(episode_number)
        )

        watch_id = episode["id"].split("$episode")[0]
        watch_id_list = []

        if self.dl_type == "both" and find_is_sub_dub == "both":
            watch_id_list.extend(
                [
                    f"{watch_id}$episode${episode_id}$sub",
                    f"{watch_id}$episode${episode_id}$dub",
                ]
            )

        elif self.dl_type == "dub":
            watch_id_list.extend([f"{watch_id}$episode${episode_id}$dub"])

        elif self.dl_type == "sub":
            watch_id_list.extend([f"{watch_id}$episode${episode_id}$sub"])

        sources = []
        subtitles = []
        complete_data = {
            "sources": sources,
            "subtitles": subtitles,
            "malID": self.api.get_info(self.zoro_id, "malID"),
            "title": self.api.get_info(self.zoro_id, "title"),
            "episodeTitle": episode["title"],
            "season": int(self.season),
            "episode": episode_number,
            "name": f"{title_episode}. {episode['title']}",
        }

        with ThreadPoolExecutor(max_workers=2) as executor:
            executor.submit(self.fetch_video_sources, watch_id_list, sources)

            if self.dl_type == "both" or self.dl_type == "sub":
                executor.submit(
                    self.fetch_subtitles_sources, watch_id_list[0], subtitles
                )

        self.complete_data = complete_data
        self.video_sources = complete_data["sources"]
        self.subtitle_sources = complete_data["subtitles"]

        self.lang_file_name_data = (
            "JPN-ENG"
            if len(self.video_sources) > 1
            else ("JPN" if self.video_sources[0]["subOrdub"] == "sub" else "ENG")
        )

        self.subs_file_name_data = (
            "MULTI-SUBS" if len(self.subtitle_sources) > 1 else ("ENG-SUBS" if len(self.subtitle_sources) == 1 else "NO-SUBS")
        )

    def fetch_video_sources(self, watch_id_list, sources):
        for wID in watch_id_list:
            watch_info = self.api.get_watch_info(wID)
            stream_dict = {
                "url": watch_info["sources"][0]["url"],
                "subOrdub": wID.split("$")[-1],
            }
            sources.append(stream_dict)

    def fetch_subtitles_sources(self, watch_id, subtitles):
        subtitle_info = self.api.get_watch_info(watch_id.replace("dub", "sub"))
        subtitles_dict = [
            {
                "lang": sub_data["lang"],
                "lang_639_2": get_language_code(sub_data["lang"].split(" - ")[0]),
                "url": sub_data["url"],
            }
            for sub_data in subtitle_info["subtitles"]
            if sub_data["lang"] != "Thumbnails"
        ]
        subtitles.extend(subtitles_dict)

    def download_video(self):
        for data in self.video_sources:
            print(
                colored_text("[+] DOWNLOADING", "green"),
                colored_text("JPN" if data["subOrdub"] == "sub" else "ENG", "blue"),
                colored_text("VIDEO SOURCE", "green"),
            )

            cmd = [
                n_m3u8_dl_path,
                data["url"],
                "-sv",
                "res={}".format(self.resolution),
                "--save-name",
                "{}_{}_{}".format(
                    self.complete_data["malID"], data["subOrdub"], self.end_code
                ),
            ]
            self.out_folder_structure = "{} - S{}".format(
                self.complete_data["title"], self.complete_data["season"]
            )
            subprocess.check_call(cmd)

    def download_subs(self):
        print(
            colored_text(
                "[+] DOWNLOADING SUBTITLES (TOTAL - {} FOUND)".format(
                    len(self.subtitle_sources)
                ),
                "green",
            )
        )

        for subs in self.subtitle_sources:
            download_file(
                subs["url"],
                "subtitle_{}_{}.vtt".format(subs["lang_639_2"], self.end_code),
            )

    def mux_files(self):
        print(colored_text("[+] MUXING FILES", "green"))

        ffmpeg_opts = [
            "ffmpeg",
            "-y",
        ]

        for source in self.complete_data["sources"]:
            video_filename = f"{self.complete_data['malID']}_{source['subOrdub']}_{self.end_code}.mp4"
            ffmpeg_opts.extend(["-i", video_filename])

        if len(self.subtitle_sources) >= 1:
            for source in self.subtitle_sources:
                ffmpeg_opts.extend(
                    [
                        "-i",
                        "subtitle_{}_{}.vtt".format(
                            source["lang_639_2"], self.end_code
                        ),
                    ]
                )

        ffmpeg_opts.extend(["-map", "0:v:0"])
        ffmpeg_opts.extend(["-map", "0:a:0"])

        if self.dl_type == "both":
            ffmpeg_opts.extend(["-map", "1:a:0"])

        if len(self.subtitle_sources) >= 1:
            for i in range(len(self.subtitle_sources)):
                ffmpeg_opts.extend(["-map", f"{len(self.video_sources)+i}:s:0"])

        if len(self.subtitle_sources) >= 1:
            for i in range(len(self.subtitle_sources)):
                ffmpeg_opts.extend(
                    [
                        "-metadata:s:s:{0}".format(i),
                        f"language={self.subtitle_sources[i]['lang_639_2']}",
                    ]
                )

        language_value = {"sub": "jpn", "dub": "eng", "both": "jpn"}.get(
            self.dl_type, ""
        )

        ffmpeg_opts.extend(["-metadata:s:a:0", f"language={language_value}"])

        if self.dl_type == "both":
            ffmpeg_opts.extend(["-metadata:s:a:1", "language=eng"])

        ffmpeg_opts.extend(["-metadata:s:a:0", f"language={language_value}"])

        if self.dl_type == "both":
            ffmpeg_opts.extend(["-metadata:s:a:1", "language=eng"])

        ffmpeg_opts.extend(["-metadata", f"encoded_by={self.custom_group_tag}"])
        ffmpeg_opts.extend(["-metadata:s:a:0", "title=Japanese Audio"])

        if self.dl_type == "both":
            ffmpeg_opts.extend(["-metadata:s:a:1", "title=English Audio"])

        ffmpeg_opts.extend(
            [
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text",
                "{}".format(
                    os.path.join(
                        self.save_dir,
                        "[{}] {} - S{:02d}E{:02d} - {} [{}] [{}].mp4".format(
                            self.custom_group_tag,
                            self.complete_data["title"],
                            self.complete_data["season"],
                            self.complete_data["episode"],
                            self.complete_data["name"],
                            self.lang_file_name_data,
                            self.subs_file_name_data,
                        ),
                    )
                ),
            ]
        )

        print(colored_text("RUNNING FFMPEG COMMAND", "green"))
        subprocess.check_call(ffmpeg_opts)

        for source in self.video_sources:
            video_filename = f"{self.complete_data['malID']}_{source['subOrdub']}_{self.end_code}.mp4"
            os.remove(video_filename)

        for source in self.subtitle_sources:
            os.remove("subtitle_{}_{}.vtt".format(source["lang_639_2"], self.end_code))

    def execute(self):
        start_time = time.time()
        self.get_stream_data(self.episode_start)
        self.download_video()
        self.download_subs()
        self.mux_files()
        end_time = time.time()
        readable_time = get_readable_time(start_time, end_time)
        print(
            self.separator,
            colored_text("JOB COMPLETED IN - {}".format(readable_time), "green"),
            self.separator,
        )
